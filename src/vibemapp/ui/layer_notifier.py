"""Opt-in desktop notification when Smart Layers switches the layer.

``layer_notice`` is the pure decision (unit-tested); ``LayerNotifier`` wires it
to the service's ``rule-applied`` signal. It sends through an injected
``send(notification_id, title, body)`` so it works without a window and in tests.
"""

from __future__ import annotations

from collections.abc import Callable

from ..models import FocusedWindow
from ..service import KeyboardService
from . import rules_model as rm
from .i18n import _

SETTINGS_KEY = "notify-layer-changes"
# One fixed id: each notification replaces the previous one instead of stacking.
NOTIFICATION_ID = "layer-changed"

Send = Callable[[str, str, str], None]


def layer_notice(*, enabled: bool, window_active: bool, target: int,
                 active_layer: int, layer_title: str,
                 app_name: str | None, fallback: bool) -> tuple[str, str] | None:
    """``(title, body)`` to show, or ``None`` when nothing should be shown.

    Shown only when the setting is on, the layer really changes and the main
    window isn't active (rule edits re-assert the layer while you're in the
    app, and the live view already shows it). ``app_name`` is the focused app
    or game, if known; ``fallback`` means no rule matched.
    """
    if not enabled or window_active or target == active_layer:
        return None
    if fallback:
        body = (_("No rule for {app}, using the fallback layer").format(app=app_name)
                if app_name else _("No rule matched, using the fallback layer"))
    elif app_name:
        body = _("Switched for {app}").format(app=app_name)
    else:
        body = _("Switched by a Smart Layers rule")
    return layer_title, body


def focused_app_name(window: FocusedWindow,
                     app_name: Callable[[str], str] | None = None) -> str:
    """Display name of the focused window's app, or the Steam game's name."""
    if window.steam_app_id:
        return window.steam_game_name or _("Steam Game {id}").format(id=window.steam_app_id)
    if app_name is None:
        from .apps import AppIndex
        return AppIndex.get().display_name(window.app_class)
    return app_name(window.app_class)


class LayerNotifier:
    """Notifies on ``rule-applied`` when the ``notify-layer-changes`` key is on."""

    def __init__(self, service: KeyboardService, settings: object, send: Send,
                 is_window_active: Callable[[], bool], *,
                 app_name: Callable[[str], str] | None = None) -> None:
        self._service = service
        self._settings = settings
        self._send = send
        self._is_window_active = is_window_active
        self._app_name = app_name
        self._handlers: list[tuple[object, int]] = [
            (service, service.connect("rule-applied", self._on_rule_applied)),
        ]

    def shutdown(self) -> None:
        for obj, handler in self._handlers:
            obj.disconnect(handler)
        self._handlers = []

    def _on_rule_applied(self, service: KeyboardService, app_class: str,
                         layer: int) -> None:
        # Cheap checks first: most of the time the setting is off.
        if not self._settings.get_boolean(SETTINGS_KEY):
            return
        # Emitted before the keyboard confirms, so this is still the old layer.
        active_layer = service.get_active_layer()
        window_active = self._is_window_active()
        if layer == active_layer or window_active:
            return   # skip the app lookup; layer_notice would say no anyway
        app_name, fallback = self._describe(service, app_class)
        layout = service.get_layout()
        layers = layout.layers if layout is not None else ()
        layer_obj = layers[layer] if 0 <= layer < len(layers) else None
        notice = layer_notice(
            enabled=True, window_active=window_active, target=layer,
            active_layer=active_layer, layer_title=rm.layer_label(layer_obj, layer),
            app_name=app_name, fallback=fallback)
        if notice is not None:
            self._send(NOTIFICATION_ID, *notice)

    def _describe(self, service: KeyboardService,
                  app_class: str) -> tuple[str | None, bool]:
        """``(app name, is fallback)`` for the window that caused the switch.

        The signal's ``app_class`` is the rule's, not the window's (a regex for
        pattern rules, an app id or "" for Steam rules), so the name comes from
        the focused window. "" is both "fallback" and "All Steam Games", so the
        rules are checked again to tell them apart.
        """
        window = service.get_focused_window()
        if window is None or rm.is_own_app_class(window.app_class):
            return None, app_class == ""
        fallback = False
        if app_class == "":
            _target, rule = rm.target_layer(
                service.get_rules(), service.get_fallback_layer(),
                window.app_class, window.title, window.steam_app_id)
            fallback = rule is None
        return focused_app_name(window, self._app_name), fallback
