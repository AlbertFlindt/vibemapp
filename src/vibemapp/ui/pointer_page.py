"""Trackpad page: the keyboard's trackpad or trackball (ZSA Navigator).

A live drawing of the device at the top (``PointerView``), the on-screen
pointer activity settings (``pointer-pill*``, shown by ``ui/hold_pill.py``)
and the overlay's ``overlay-pointer`` switch. Its title follows the device;
without one the window keeps it grayed out in the sidebar (``window.py``).
The live view asks for ``pointer_tracking`` only while the page is mapped.
"""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from ..models import PointerAction, PointerActivity  # noqa: E402
from ..service import KeyboardService  # noqa: E402
from .i18n import _  # noqa: E402
from .overlay_page import INSTALL_COMMAND, layer_shell_available  # noqa: E402
from .pointer_activity import (ACTIONS, device_title, idle_hint,  # noqa: E402
                               live_label, want_pointer)
from .pointer_view import PointerView  # noqa: E402,F401  (used by the template)
from .resources import ui_file  # noqa: E402

PILL_KEY = "pointer-pill"
ACTIONS_KEY = "pointer-pill-actions"
OVERLAY_KEY = "overlay-pointer"
SPIN_KEYS = ("pointer-pill-linger-ms", "pointer-pill-fade-ms")


def toggle_action(actions: list[str], action: str, on: bool) -> list[str]:
    """``pointer-pill-actions`` with ``action`` added or removed, in ACTIONS
    order; unknown values are kept at the end."""
    chosen = set(actions)
    if on:
        chosen.add(action)
    else:
        chosen.discard(action)
    return [a for a in ACTIONS if a in chosen] + [a for a in actions
                                                  if a not in ACTIONS and a in chosen]


@Gtk.Template(filename=ui_file("pointer_page.ui"))
class PointerPage(Adw.Bin):
    __gtype_name__ = "KmlPointerPage"

    window_title: Adw.WindowTitle = Gtk.Template.Child()
    stack: Gtk.Stack = Gtk.Template.Child()
    install_label: Gtk.Label = Gtk.Template.Child()
    live_view: PointerView = Gtk.Template.Child()
    live_label: Gtk.Label = Gtk.Template.Child()
    pill_row: Adw.SwitchRow = Gtk.Template.Child()
    actions_row: Adw.ExpanderRow = Gtk.Template.Child()
    click_row: Adw.SwitchRow = Gtk.Template.Child()
    tap_row: Adw.SwitchRow = Gtk.Template.Child()
    scroll_row: Adw.SwitchRow = Gtk.Template.Child()
    move_row: Adw.SwitchRow = Gtk.Template.Child()
    linger_row: Adw.SpinRow = Gtk.Template.Child()
    fade_row: Adw.SpinRow = Gtk.Template.Child()
    overlay_row: Adw.SwitchRow = Gtk.Template.Child()

    def __init__(self) -> None:
        super().__init__()
        self.service: KeyboardService | None = None
        self.settings: Gio.Settings | None = None
        self._add_toast: Callable[[Adw.Toast], None] | None = None
        self._handlers: list[tuple[object, int]] = []
        self._syncing = False
        self._layer_shell = True
        self.install_label.set_label(INSTALL_COMMAND)
        self.connect("map", lambda *_a: self._sync_tracking())
        self.connect("unmap", lambda *_a: self._sync_tracking())

    def setup(self, service: KeyboardService, settings: Gio.Settings,
              add_toast: Callable[[Adw.Toast], None]) -> None:
        self.service = service
        self.settings = settings
        self._add_toast = add_toast
        self._layer_shell = layer_shell_available()
        connect = self._handlers.append
        for key in (PILL_KEY, ACTIONS_KEY, OVERLAY_KEY, *SPIN_KEYS):
            connect((settings, settings.connect(f"changed::{key}", lambda *_a: self._sync())))
        connect((service, service.connect("pointer-changed", lambda *_a: self._sync_device())))
        connect((service, service.connect("pointer-activity", self._on_activity)))
        self.pill_row.connect("notify::active", self._on_switch, PILL_KEY)
        self.overlay_row.connect("notify::active", self._on_switch, OVERLAY_KEY)
        for row, action in self._action_rows():
            row.connect("notify::active", self._on_action_active, action)
        for row, key in zip((self.linger_row, self.fade_row), SPIN_KEYS):
            row.connect("notify::value", self._on_spin, key)
        self._sync()
        self._sync_device()

    def teardown(self) -> None:
        for obj, handler in self._handlers:
            obj.disconnect(handler)
        self._handlers = []
        if self.service is not None:
            want_pointer(self.service, "page", False)

    def _action_rows(self) -> tuple[tuple[Adw.SwitchRow, str], ...]:
        return tuple(zip((self.click_row, self.tap_row, self.scroll_row, self.move_row),
                         ACTIONS))

    # --- device ----------------------------------------------------------

    def _sync_device(self) -> None:
        kind = self.service.get_pointer_kind() if self.service is not None else None
        self.window_title.set_title(device_title(kind))
        self.live_view.set_kind(kind)
        self.live_label.set_label(idle_hint(kind))
        if not self._layer_shell:
            self.stack.set_visible_child_name("unavailable")
        else:
            self.stack.set_visible_child_name("settings" if kind is not None else "none")
        self._sync_tracking()

    def _sync_tracking(self) -> None:
        if self.service is None:
            return
        on = (self.get_mapped() and self.service.get_pointer_kind() is not None
              and self._handlers != [])
        want_pointer(self.service, "page", on)

    def _on_activity(self, _service: object, activity: PointerActivity) -> None:
        if not self.get_mapped():
            return
        self.live_view.feed(activity)
        if activity.action == PointerAction.IDLE and not activity.buttons:
            self.live_label.set_label(idle_hint(activity.kind))
            return
        text = live_label(activity)
        if text:
            self.live_label.set_label(text)

    # --- settings -> widgets ---------------------------------------------

    def _sync(self) -> None:
        settings = self.settings
        chosen = set(settings.get_value(ACTIONS_KEY).unpack())

        def apply() -> None:
            self.pill_row.set_active(settings.get_boolean(PILL_KEY))
            self.overlay_row.set_active(settings.get_boolean(OVERLAY_KEY))
            for row, action in self._action_rows():
                row.set_active(action in chosen)
            for row, key in zip((self.linger_row, self.fade_row), SPIN_KEYS):
                if int(row.get_value()) != settings.get_int(key):
                    row.set_value(settings.get_int(key))

        self._syncing = True
        try:
            apply()
        finally:
            self._syncing = False

    # --- widgets -> settings ---------------------------------------------

    def _on_switch(self, row: Adw.SwitchRow, _pspec: object, key: str) -> None:
        if not self._syncing and row.get_active() != self.settings.get_boolean(key):
            self.settings.set_boolean(key, row.get_active())

    def _on_action_active(self, row: Adw.SwitchRow, _pspec: object, action: str) -> None:
        if self._syncing:
            return
        current = [str(a) for a in self.settings.get_value(ACTIONS_KEY).unpack()]
        value = toggle_action(current, action, row.get_active())
        if value != current:
            self.settings.set_value(ACTIONS_KEY, GLib.Variant("as", value))

    def _on_spin(self, row: Adw.SpinRow, _pspec: object, key: str) -> None:
        if not self._syncing and int(row.get_value()) != self.settings.get_int(key):
            self.settings.set_int(key, int(row.get_value()))

    @Gtk.Template.Callback()
    def on_copy_install_clicked(self, button: Gtk.Button) -> None:
        button.get_clipboard().set(INSTALL_COMMAND)
        if self._add_toast is not None:
            self._add_toast(Adw.Toast(title=_("Copied to clipboard")))


__all__ = ["PointerPage", "toggle_action"]
