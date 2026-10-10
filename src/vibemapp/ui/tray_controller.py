"""Tray integration: keeps the StatusNotifierItem in sync with the app (SPEC 6.2).

The tray module is optional: if it can't be imported, or no
StatusNotifierWatcher accepts the item, ``active`` stays False and the app
falls back to its normal window/background behaviour.
"""

from __future__ import annotations

import logging
from typing import Callable

from gi.repository import Gio, GObject

from ..service import KeyboardService
from .controller import DeviceController
from .i18n import _
from .resources import APP_ID
from .rules_model import layer_label

log = logging.getLogger(__name__)

try:
    from ..tray import TrayIcon, TrayMenuItem
except ImportError as exc:  # tray module missing: run without a tray
    log.info("Tray support unavailable: %s", exc)
    TrayIcon = None  # type: ignore[assignment,misc]
    TrayMenuItem = None  # type: ignore[assignment,misc]


class TrayController(GObject.Object):
    __gtype_name__ = "KmlTrayController"

    # True while the tray icon is shown by a tray host (and enabled).
    active = GObject.Property(type=bool, default=False)

    def __init__(self, service: KeyboardService, controller: DeviceController,
                 settings: Gio.Settings, *, on_toggle_window: Callable[[], None],
                 on_show_window: Callable[[], None], on_quit: Callable[[], None]) -> None:
        super().__init__()
        self.service = service
        self.controller = controller
        self.settings = settings
        self._on_toggle_window = on_toggle_window
        self._on_show_window = on_show_window
        self._on_quit = on_quit
        self._tray = None
        self._tray_handlers: list[int] = []
        self._handlers = [
            (service, service.connect("notify::smart-layers-enabled", lambda *_a: self._sync())),
            (service, service.connect("layer-changed", lambda *_a: self._sync())),
            (service, service.connect("layout-loaded", lambda *_a: self._sync())),
            (controller, controller.connect("notify::state", lambda *_a: self._sync())),
            (settings, settings.connect("changed::show-in-tray",
                                        lambda *_a: self._apply_setting())),
        ]
        self._apply_setting()

    @staticmethod
    def supported() -> bool:
        return TrayIcon is not None

    # --- lifecycle -------------------------------------------------------

    def _apply_setting(self) -> None:
        want = self.settings.get_boolean("show-in-tray") and TrayIcon is not None
        if want and self._tray is None:
            try:
                self._tray = TrayIcon(item_id="vibemapp", title=_("Vibemapp"),
                                      icon_name=APP_ID)
            except Exception:  # noqa: BLE001 — no session bus etc.: no tray
                log.exception("Could not create the tray icon")
                self._tray = None
                return
            self._tray_handlers = [
                self._tray.connect("notify::available", lambda *_a: self._update_active()),
                self._tray.connect("activate", lambda *_a: self._on_toggle_window()),
                self._tray.connect("menu-item-activated", self._on_menu_item),
            ]
            self._sync()
        elif not want and self._tray is not None:
            self._close_tray()
        self._update_active()

    def _close_tray(self) -> None:
        if self._tray is None:
            return
        for handler in self._tray_handlers:
            self._tray.disconnect(handler)
        self._tray_handlers = []
        self._tray.close()
        self._tray = None

    def shutdown(self) -> None:
        for obj, handler in self._handlers:
            obj.disconnect(handler)
        self._handlers = []
        self._close_tray()
        self._update_active()

    def _update_active(self) -> None:
        active = self._tray is not None and bool(self._tray.props.available)
        if active != self.active:
            self.active = active

    # --- content ---------------------------------------------------------

    def _layer_text(self) -> str:
        layout = self.controller.layout
        if self.controller.device is None:
            return _("No keyboard connected")
        if layout is None:
            return _("Layout not loaded")
        index = self.service.get_active_layer()
        layer = layout.layers[index] if 0 <= index < len(layout.layers) else None
        return layer_label(layer, index)

    def _sync(self) -> None:
        if self._tray is None:
            return
        device = self.controller.device
        layer_text = self._layer_text()
        self._tray.set_tooltip(device.name if device else _("Vibemapp"), layer_text)
        self._tray.set_menu([
            TrayMenuItem("show", _("Show Vibemapp")),
            TrayMenuItem("sep1", "", separator=True),
            TrayMenuItem("smart-layers", _("Smart Layers"),
                         toggle=bool(self.service.props.smart_layers_enabled)),
            TrayMenuItem("layer", layer_text, enabled=False),
            TrayMenuItem("sep2", "", separator=True),
            TrayMenuItem("quit", _("Quit")),
        ])

    def _on_menu_item(self, _tray: object, item_id: str) -> None:
        if item_id == "show":
            self._on_show_window()
        elif item_id == "smart-layers":
            self.service.props.smart_layers_enabled = not self.service.props.smart_layers_enabled
        elif item_id == "quit":
            self._on_quit()
