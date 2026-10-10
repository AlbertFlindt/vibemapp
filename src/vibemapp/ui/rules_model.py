"""Pure-Python helpers for Smart Layers rule lists.

No GTK imports here, so this module is unit-testable without a display and can
be shared by the mock service. Rule lists are treated as immutable values: every
helper returns a new list and never mutates its input.
"""

from __future__ import annotations

import re
from dataclasses import replace

from ..models import AppRule, Layer
from .i18n import _

OWN_APP_CLASS = "io.github.albertflindt.Vibemapp"


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


def rule_matches(rule: AppRule, app_class: str, title: str) -> bool:
    if not rule.enabled or rule.app_class != app_class:
        return False
    if rule.title_pattern:
        try:
            return re.search(rule.title_pattern, title) is not None
        except re.error:
            return False
    return True


def find_matching_rule(rules: list[AppRule], app_class: str,
                       title: str) -> tuple[int, AppRule] | None:
    """First enabled matching rule wins. Returns ``(position, rule)``."""
    for pos, rule in enumerate(rules):
        if rule_matches(rule, app_class, title):
            return pos, rule
    return None


def target_layer(rules: list[AppRule], fallback: int | None,
                 app_class: str, title: str) -> tuple[int | None, AppRule | None]:
    """Layer Smart Layers would apply for this focus, and the rule that won."""
    hit = find_matching_rule(rules, app_class, title)
    if hit is not None:
        return hit[1].layer_index, hit[1]
    return fallback, None


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


def add_rule(rules: list[AppRule], rule: AppRule) -> list[AppRule]:
    """Append a rule.

    A rule with a title pattern is more specific than one without, so it is
    inserted before the first pattern-less rule for the same app; otherwise
    it could never win.
    """
    out = list(rules)
    if rule.title_pattern:
        for i, existing in enumerate(out):
            if existing.app_class == rule.app_class and not existing.title_pattern:
                out.insert(i, rule)
                return out
    out.append(rule)
    return out


def shadowed_rules(rules: list[AppRule]) -> set[int]:
    """Positions of enabled rules that can never match because an earlier
    enabled rule for the same app has no title pattern."""
    catch_all: set[str] = set()
    shadowed: set[int] = set()
    for i, rule in enumerate(rules):
        if not rule.enabled:
            continue
        if rule.app_class in catch_all:
            shadowed.add(i)
        elif not rule.title_pattern:
            catch_all.add(rule.app_class)
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
