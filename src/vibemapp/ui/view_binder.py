"""Wires a KeyboardService's live key and layer events to a KeyboardView.

Shared by the Layout page and the on-screen overlay. The binder only
forwards key presses and releases, clears them when the keyboard goes away,
and tells its owner about layer changes. Which layer the view shows is up to
the owner (the Layout page follows the keyboard and its own layer selector).
"""

from __future__ import annotations

from typing import Callable, Protocol

from ..service import KeyboardService


class PressView(Protocol):
    def set_key_pressed(self, index: int, pressed: bool) -> None: ...
    def clear_pressed(self) -> None: ...


class KeyboardViewBinder:
    """Connects ``service`` signals to ``views`` until ``disconnect()``.

    ``on_layer_changed(index)`` runs after the views were updated.
    With ``reset_on_layer_change`` the views drop their pressed state on every
    layer change and take the keys still held from
    ``service.get_pressed_keys()`` (the overlay). ``active()`` gates key
    events, e.g. "a layout is shown"; the default is "the service has a layout".
    """

    def __init__(self, service: KeyboardService, *views: PressView,
                 on_layer_changed: Callable[[int], None] | None = None,
                 reset_on_layer_change: bool = False,
                 active: Callable[[], bool] | None = None) -> None:
        self.service = service
        self.views: list[PressView] = list(views)
        self._on_layer_changed = on_layer_changed
        self._reset_on_layer_change = reset_on_layer_change
        self._active = active or (lambda: service.get_layout() is not None)
        self._handlers = [
            service.connect("key-event", self._on_key_event),
            service.connect("layer-changed", self._on_layer),
            service.connect("disconnected", lambda *_a: self.clear()),
        ]

    def set_views(self, *views: PressView) -> None:
        """Replace the bound views (e.g. one board ↔ two split halves) and
        bring the new ones up to date with the keys held right now."""
        self.views = list(views)
        self.sync_pressed()

    def disconnect(self) -> None:
        for handler in self._handlers:
            self.service.disconnect(handler)
        self._handlers = []

    def clear(self) -> None:
        for view in self.views:
            view.clear_pressed()

    def sync_pressed(self) -> None:
        """Clear the views, then mark the keys the keyboard reports held."""
        self.clear()
        if not self._active():
            return
        for index in sorted(self.service.get_pressed_keys()):
            for view in self.views:
                view.set_key_pressed(index, True)

    def _on_key_event(self, _service: KeyboardService, index: int, pressed: bool) -> None:
        if not self._active():
            return
        for view in self.views:
            view.set_key_pressed(index, pressed)

    def _on_layer(self, _service: KeyboardService, index: int) -> None:
        if self._reset_on_layer_change:
            self.sync_pressed()
        if self._on_layer_changed is not None:
            self._on_layer_changed(index)
