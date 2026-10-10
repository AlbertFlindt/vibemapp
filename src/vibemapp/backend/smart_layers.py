"""Smart Layers: pick a keyboard layer from the focused application.

No GTK dependency. Timers go through an injectable ``Scheduler``. The default
uses GLib, imported lazily, and tests use a manual clock.

Rules file: ``$XDG_CONFIG_HOME/vibemapp/smart-layers.json``::

    {"version": 1,
     "layouts": {"<layout_id>": {"fallback": 0 | null,
                                 "rules": [{AppRule fields}, ...]}}}

It's written atomically (temp file in the same directory, fsync, rename).
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from dataclasses import asdict
from typing import Any, Callable, Protocol

from ..models import AppRule, RuleMatch

log = logging.getLogger(__name__)

APP_ID = "io.github.albertflindt.Vibemapp"
FILE_VERSION = 1
DEFAULT_LAYOUT_KEY = "_default"   # rules for firmware that reports no layout id
DEFAULT_DEBOUNCE_MS = 50


def default_rules_path() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "vibemapp", "smart-layers.json")


# --- persistence ------------------------------------------------------------------

def rule_to_json(rule: AppRule) -> dict[str, Any]:
    data = asdict(rule)
    data["match"] = RuleMatch(rule.match).value
    return data


def rule_from_json(obj: Any) -> AppRule | None:
    if not isinstance(obj, dict):
        return None
    app_class = obj.get("app_class")
    layer = obj.get("layer_index")
    try:
        match = RuleMatch(obj.get("match") or RuleMatch.APP.value)   # missing -> APP (v0.1/0.2 files)
    except ValueError:
        log.warning("Skipping rule with unknown match kind %r", obj.get("match"))
        return None
    if not isinstance(app_class, str) or isinstance(layer, bool) \
            or not isinstance(layer, int) or layer < 0:
        return None
    if not app_class and match != RuleMatch.STEAM_GAME:   # "" = any Steam game
        return None
    pattern = obj.get("title_pattern")
    icon = obj.get("icon_name")
    name = obj.get("display_name")
    return AppRule(
        app_class=app_class,
        display_name=name if isinstance(name, str) and name else (app_class or "Steam"),
        layer_index=layer,
        title_pattern=pattern if isinstance(pattern, str) and pattern else None,
        enabled=bool(obj.get("enabled", True)),
        icon_name=icon if isinstance(icon, str) and icon else None,
        match=match,
    )


def _valid_fallback(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


class RulesStore:
    """Rules per Oryx layout id, persisted to a JSON file."""

    def __init__(self, path: str | None = None) -> None:
        self.path = path or default_rules_path()
        self._layouts: dict[str, tuple[list[AppRule], int | None]] = {}
        self._extra: dict[str, Any] = {}
        self.load()

    def load(self) -> None:
        self._layouts = {}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            log.warning("Can't read %s: %s", self.path, exc)
            self._backup_corrupt()
            return
        if not isinstance(data, dict):
            return
        layouts = data.get("layouts")
        if not isinstance(layouts, dict):
            return
        for layout_id, entry in layouts.items():
            if not isinstance(entry, dict):
                continue
            rules = [r for r in (rule_from_json(o) for o in entry.get("rules") or [])
                     if r is not None]
            self._layouts[str(layout_id)] = (rules, _valid_fallback(entry.get("fallback")))

    def _backup_corrupt(self) -> None:
        try:
            os.replace(self.path, self.path + ".corrupt")
        except OSError:
            pass

    def layout_ids(self) -> list[str]:
        return list(self._layouts)

    def get(self, layout_id: str) -> tuple[list[AppRule], int | None]:
        rules, fallback = self._layouts.get(layout_id, ([], None))
        return list(rules), fallback

    def set(self, layout_id: str, rules: list[AppRule], fallback: int | None) -> None:
        self._layouts[layout_id] = (list(rules), fallback)
        self.save()

    def to_json(self) -> dict[str, Any]:
        return {
            "version": FILE_VERSION,
            "layouts": {
                lid: {"fallback": fb, "rules": [rule_to_json(r) for r in rules]}
                for lid, (rules, fb) in self._layouts.items()
            },
        }

    def save(self) -> None:
        directory = os.path.dirname(self.path) or "."
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".smart-layers.", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.to_json(), f, indent=2, ensure_ascii=False)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


# --- matching ------------------------------------------------------------------------

_regex_cache: dict[str, re.Pattern[str] | None] = {}


def _compile(pattern: str) -> re.Pattern[str] | None:
    """Compiled + cached; invalid patterns are logged once and never match."""
    if pattern not in _regex_cache:
        try:
            _regex_cache[pattern] = re.compile(pattern)
        except re.error as exc:
            log.warning("Invalid regex %r: %s", pattern, exc)
            _regex_cache[pattern] = None
    return _regex_cache[pattern]


def valid_pattern(pattern: str) -> bool:
    return _compile(pattern) is not None


def _class_matches(rule: AppRule, app_class: str, steam_app_id: str | None) -> bool:
    kind = RuleMatch(rule.match)
    if kind == RuleMatch.APP:
        return rule.app_class == app_class
    if kind == RuleMatch.CLASS_PATTERN:
        rx = _compile(rule.app_class)
        return rx is not None and rx.fullmatch(app_class) is not None
    if kind == RuleMatch.STEAM_GAME:
        if steam_app_id is None:
            return False
        return rule.app_class == "" or rule.app_class == steam_app_id
    return False


def rule_matches(rule: AppRule, app_class: str, title: str,
                 steam_app_id: str | None = None) -> bool:
    if not rule.enabled or not _class_matches(rule, app_class, steam_app_id):
        return False
    if rule.title_pattern:
        rx = _compile(rule.title_pattern)
        if rx is None or rx.search(title) is None:
            return False
    return True


def match_rule(rules: list[AppRule], app_class: str, title: str,
               steam_app_id: str | None = None) -> AppRule | None:
    """First enabled matching rule (see ``RuleMatch``) whose title regex also matches."""
    for rule in rules:
        if rule_matches(rule, app_class, title, steam_app_id):
            return rule
    return None


# --- scheduling ---------------------------------------------------------------------

class Scheduler(Protocol):
    def schedule(self, delay_ms: int, fn: Callable[[], None]) -> object: ...
    def cancel(self, handle: object) -> None: ...


class GLibScheduler:
    def schedule(self, delay_ms: int, fn: Callable[[], None]) -> object:
        from gi.repository import GLib

        def run() -> bool:
            fn()
            return GLib.SOURCE_REMOVE
        return GLib.timeout_add(max(0, delay_ms), run)

    def cancel(self, handle: object) -> None:
        from gi.repository import GLib
        GLib.source_remove(handle)  # type: ignore[arg-type]


# --- engine ---------------------------------------------------------------------------

class SmartLayersEngine:
    """Turns focus changes into set-layer requests.

    * ``send_layer(n)`` is called to switch the keyboard (``layer_move``).
    * ``on_applied(app_class, n)`` is called after a switch (``""`` for fallback).
    * ``on_rules_changed()`` is called when the active rule set changes.

    The engine is paused unless both ``enabled`` and ``connected`` are true.
    Enabling or connecting re-evaluates the current focus immediately.
    """

    def __init__(self, store: RulesStore,
                 send_layer: Callable[[int], None],
                 on_applied: Callable[[str, int], None] | None = None,
                 on_rules_changed: Callable[[], None] | None = None,
                 *, scheduler: Scheduler | None = None,
                 debounce_ms: int = DEFAULT_DEBOUNCE_MS,
                 own_app_class: str = APP_ID) -> None:
        self._store = store
        self._send_layer = send_layer
        self._on_applied = on_applied
        self._on_rules_changed = on_rules_changed
        self._scheduler: Scheduler = scheduler or GLibScheduler()
        self._debounce_ms = debounce_ms
        self._own_app_class = own_app_class

        self._layout_id: str | None = None
        self._rules: list[AppRule] = []
        self._fallback: int | None = None
        self._enabled = False
        self._connected = False
        self.layer_count: int | None = None   # known number of layers, for validation

        self._focus: tuple[str, str] | None = None
        self._focus_steam_app_id: str | None = None
        self._last_sent: int | None = None
        self._reported: int | None = None
        self._pending: object | None = None

    # -- state --

    @property
    def layout_id(self) -> str | None:
        return self._layout_id

    @property
    def rules(self) -> list[AppRule]:
        return list(self._rules)

    @property
    def fallback(self) -> int | None:
        return self._fallback

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def focus(self) -> tuple[str, str] | None:
        return self._focus

    @property
    def last_sent(self) -> int | None:
        return self._last_sent

    def set_layout(self, layout_id: str | None) -> None:
        """Load the rule set for ``layout_id`` (None means no layout, so no rules)."""
        self._layout_id = layout_id
        if layout_id is None:
            self._rules, self._fallback = [], None
        else:
            self._rules, self._fallback = self._store.get(layout_id)
        self._last_sent = None
        self._notify_rules()
        self.reevaluate()

    def set_rules(self, rules: list[AppRule]) -> None:
        if self._layout_id is None:
            raise RuntimeError("no layout loaded")
        self._rules = list(rules)
        self._store.set(self._layout_id, self._rules, self._fallback)
        self._notify_rules()
        self.reevaluate()

    def set_fallback(self, index: int | None) -> None:
        if self._layout_id is None:
            raise RuntimeError("no layout loaded")
        self._fallback = index
        self._store.set(self._layout_id, self._rules, self._fallback)
        self._notify_rules()
        self.reevaluate()

    def set_enabled(self, enabled: bool) -> None:
        if enabled == self._enabled:
            return
        self._enabled = enabled
        if enabled:
            self.reevaluate()
        else:
            self._cancel_pending()

    def set_connected(self, connected: bool) -> None:
        self._connected = connected
        self._last_sent = None
        self._reported = None
        if connected:
            self.reevaluate()
        else:
            self._cancel_pending()

    def keyboard_layer_changed(self, layer: int) -> None:
        self._reported = layer

    def focus_changed(self, app_class: str, title: str,
                      steam_app_id: str | None = None) -> None:
        if app_class == self._own_app_class:
            return     # don't reset the layer when our own window takes focus
        self._focus = (app_class, title)
        self._focus_steam_app_id = steam_app_id
        if self._active:
            self._schedule(self._debounce_ms)

    def reevaluate(self) -> None:
        """Evaluate the current focus right now (no debounce)."""
        self._cancel_pending()
        if self._active:
            self._evaluate()

    # -- internals --

    @property
    def _active(self) -> bool:
        return self._enabled and self._connected

    def _notify_rules(self) -> None:
        if self._on_rules_changed is not None:
            self._on_rules_changed()

    def _cancel_pending(self) -> None:
        if self._pending is not None:
            self._scheduler.cancel(self._pending)
            self._pending = None

    def _schedule(self, delay_ms: int) -> None:
        self._cancel_pending()

        def fire() -> None:
            self._pending = None
            if self._active:
                self._evaluate()
        self._pending = self._scheduler.schedule(delay_ms, fire)

    def target_for(self, app_class: str, title: str,
                   steam_app_id: str | None = None) -> tuple[AppRule | None, int | None]:
        rule = match_rule(self._rules, app_class, title, steam_app_id)
        if rule is not None:
            return rule, rule.layer_index
        return None, self._fallback

    def _evaluate(self) -> None:
        if self._focus is None:
            return
        app_class, title = self._focus
        rule, target = self.target_for(app_class, title, self._focus_steam_app_id)
        if target is None:
            return
        if self.layer_count is not None and not 0 <= target < self.layer_count:
            log.warning("Smart Layers target layer %d doesn't exist (have %d)",
                        target, self.layer_count)
            return
        if target == self._last_sent and target == self._reported:
            return
        self._last_sent = target
        self._send_layer(target)
        if self._on_applied is not None:
            self._on_applied(rule.app_class if rule else "", target)


__all__ = ["APP_ID", "RulesStore", "SmartLayersEngine", "match_rule", "rule_matches",
           "rule_from_json", "rule_to_json", "GLibScheduler", "Scheduler",
           "DEFAULT_LAYOUT_KEY", "default_rules_path"]
