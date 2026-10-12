"""Pure-Python helpers for Smart Layers rule lists.

No GTK imports here, so this module is unit-testable without a display and can
be shared by the mock service. Rule lists are treated as immutable values: every
helper returns a new list and never mutates its input.
"""

from __future__ import annotations

import re
from dataclasses import replace

from ..appid import BASE_APP_ID, is_own_app_class
from ..models import AppRule, Layer, RuleMatch
from .i18n import _

OWN_APP_CLASS = BASE_APP_ID   # the installed window class; see is_own_app_class()


# --- matching ---------------------------------------------------------------

def validate_pattern(pattern: str | None) -> str | None:
    """Return an error message for an invalid regex, or ``None`` if it is fine.

    An empty or ``None`` pattern is valid (it means "any title").
    """
    if not pattern:
        return None
    try:
        re.compile(pattern)
    except re.error as exc:
        return str(exc)
    return None


def _class_pattern_matches(pattern: str, app_class: str) -> bool:
    try:
        return re.fullmatch(pattern, app_class) is not None
    except re.error:
        return False


def rule_matches(rule: AppRule, app_class: str, title: str,
                 steam_app_id: str | None = None) -> bool:
    """Mirror of the engine's matching (display only).

    APP: exact class. CLASS_PATTERN: ``re.fullmatch`` on the class.
    STEAM_GAME: the window is a Steam game (``steam_app_id`` set) and the rule
    is for any game (``app_class == ""``) or this game's app id. The optional
    title pattern must then ``re.search`` the title.
    """
    if not rule.enabled:
        return False
    if rule.match == RuleMatch.CLASS_PATTERN:
        if not _class_pattern_matches(rule.app_class, app_class):
            return False
    elif rule.match == RuleMatch.STEAM_GAME:
        if not steam_app_id or (rule.app_class and rule.app_class != steam_app_id):
            return False
    elif rule.app_class != app_class:
        return False
    if rule.title_pattern:
        try:
            return re.search(rule.title_pattern, title) is not None
        except re.error:
            return False
    return True


def find_matching_rule(rules: list[AppRule], app_class: str, title: str,
                       steam_app_id: str | None = None) -> tuple[int, AppRule] | None:
    """First enabled matching rule wins. Returns ``(position, rule)``."""
    for pos, rule in enumerate(rules):
        if rule_matches(rule, app_class, title, steam_app_id):
            return pos, rule
    return None


def target_layer(rules: list[AppRule], fallback: int | None, app_class: str, title: str,
                 steam_app_id: str | None = None
                 ) -> tuple[int | None, AppRule | None]:
    """Layer Smart Layers would apply for this focus, and the rule that won."""
    hit = find_matching_rule(rules, app_class, title, steam_app_id)
    if hit is not None:
        return hit[1].layer_index, hit[1]
    return fallback, None


def is_catch_all(rule: AppRule) -> bool:
    """Group rules that can match many apps: All Steam Games and class patterns."""
    return (rule.match == RuleMatch.CLASS_PATTERN
            or (rule.match == RuleMatch.STEAM_GAME and not rule.app_class))


def classes_matching(pattern: str, classes: list[str]) -> list[str]:
    """Distinct classes (in order) that ``pattern`` fullmatches; [] if invalid."""
    if validate_pattern(pattern) is not None or not pattern:
        return []
    return list(dict.fromkeys(c for c in classes if _class_pattern_matches(pattern, c)))


# --- list editing -----------------------------------------------------------

def move_rule(rules: list[AppRule], position: int, delta: int) -> list[AppRule]:
    """Move the rule at ``position`` by ``delta`` places (clamped)."""
    return move_rule_to(rules, position, position + delta)


def move_rule_to(rules: list[AppRule], src: int, dest: int) -> list[AppRule]:
    """Move rule ``src`` so it ends up at index ``dest`` (clamped)."""
    out = list(rules)
    if not 0 <= src < len(out):
        return out
    dest = max(0, min(dest, len(out) - 1))
    if dest == src:
        return out
    rule = out.pop(src)
    out.insert(dest, rule)
    return out


def remove_rule(rules: list[AppRule], position: int) -> list[AppRule]:
    return [r for i, r in enumerate(rules) if i != position]


def update_rule(rules: list[AppRule], position: int, **changes: object) -> list[AppRule]:
    out = list(rules)
    if 0 <= position < len(out):
        out[position] = replace(out[position], **changes)  # type: ignore[arg-type]
    return out


def _same_target(a: AppRule, b: AppRule) -> bool:
    return a.match == b.match and a.app_class == b.app_class


def add_rule(rules: list[AppRule], rule: AppRule) -> list[AppRule]:
    """Insert a rule where it can actually win (first match wins).

    * Specific rules (an app, a single Steam game) go above the first
      catch-all rule (All Steam Games, class patterns).
    * A rule with a title pattern goes above the pattern-less rule for the
      same target, which would otherwise always match first.
    * Catch-all rules are appended.
    """
    out = list(rules)
    candidates: list[int] = []
    if not is_catch_all(rule):
        candidates += [i for i, r in enumerate(out) if is_catch_all(r)][:1]
    if rule.title_pattern:
        candidates += [i for i, r in enumerate(out)
                       if _same_target(r, rule) and not r.title_pattern][:1]
    if candidates:
        out.insert(min(candidates), rule)
    else:
        out.append(rule)
    return out


def _covers(earlier: AppRule, later: AppRule) -> bool:
    """True if ``earlier`` (enabled, no title pattern) matches every window
    ``later`` could match, so ``later`` can never win."""
    if earlier.match == RuleMatch.APP:
        return later.match == RuleMatch.APP and later.app_class == earlier.app_class
    if earlier.match == RuleMatch.STEAM_GAME:
        if later.match != RuleMatch.STEAM_GAME:
            return False
        return not earlier.app_class or earlier.app_class == later.app_class
    # Class pattern: covers exact-class rules it fullmatches and identical patterns.
    if later.match == RuleMatch.APP:
        return _class_pattern_matches(earlier.app_class, later.app_class)
    return later.match == RuleMatch.CLASS_PATTERN and later.app_class == earlier.app_class


def shadowed_rules(rules: list[AppRule]) -> set[int]:
    """Positions of enabled rules that can never match because an earlier
    enabled rule without a title pattern always matches first."""
    blockers: list[AppRule] = []
    shadowed: set[int] = set()
    for i, rule in enumerate(rules):
        if not rule.enabled:
            continue
        if any(_covers(b, rule) for b in blockers):
            shadowed.add(i)
        elif not rule.title_pattern:
            blockers.append(rule)
    return shadowed


def clamp_rules_to_layers(rules: list[AppRule], n_layers: int) -> list[AppRule]:
    """Rules pointing at layers that no longer exist are pinned to the last one."""
    if n_layers <= 0:
        return list(rules)
    return [r if r.layer_index < n_layers else replace(r, layer_index=n_layers - 1)
            for r in rules]


# --- labels -----------------------------------------------------------------

def layer_label(layer: Layer | None, index: int | None, *,
                keep_label: str | None = None) -> str:
    """``"Layer 2 · Media"``; just ``"Layer 2"`` if the title is the default."""
    if index is None:
        return keep_label if keep_label is not None else _("Don't Change")
    default = _("Layer {index}").format(index=index)
    if (layer is None or not layer.title or layer.title.strip() == default
            or layer.title.strip() == f"Layer {index}"):
        return default
    # Translators: "Layer 2 · Media" — layer number, then its Oryx title
    return _("{layer} · {title}").format(layer=default, title=layer.title)


def app_class_from_desktop_id(desktop_id: str | None,
                              startup_wm_class: str | None) -> str:
    """Hyprland window class for an installed app: StartupWMClass if set,
    otherwise the desktop id without the ``.desktop`` suffix."""
    if startup_wm_class:
        return startup_wm_class
    if not desktop_id:
        return ""
    return desktop_id[:-len(".desktop")] if desktop_id.endswith(".desktop") else desktop_id


def prettify_class(app_class: str) -> str:
    """Readable fallback name for a bare window class (``org.gnome.Nautilus``
    → ``Nautilus``, ``kitty`` → ``Kitty``)."""
    name = app_class.rsplit(".", 1)[-1] if "." in app_class else app_class
    name = name.replace("-", " ").replace("_", " ").strip()
    return name[:1].upper() + name[1:] if name else app_class
