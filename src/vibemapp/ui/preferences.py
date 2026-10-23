"""Preferences dialog, backed by GSettings."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from collections.abc import Iterable  # noqa: E402

from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from ..models import Layout  # noqa: E402
from ..service import KeyboardService  # noqa: E402
from .i18n import _  # noqa: E402
from .overlay_page import toggle_layer  # noqa: E402
from .resources import ui_file  # noqa: E402
from .rules_model import layer_label  # noqa: E402

AUTOSTART_SNIPPET = """hl.on("hyprland.start", function ()
  hl.exec_cmd("vibemapp --background")
end)"""

SILENT_LAYERS_KEY = "notify-excluded-layers"


def get_silent_layers(settings: Gio.Settings) -> list[int]:
    return [int(i) for i in settings.get_value(SILENT_LAYERS_KEY).unpack()]


def set_silent_layers(settings: Gio.Settings, layers: Iterable[int]) -> None:
    value = sorted({int(i) for i in layers})
    if value != get_silent_layers(settings):
        settings.set_value(SILENT_LAYERS_KEY, GLib.Variant("ai", value))


@Gtk.Template(filename=ui_file("preferences.ui"))
class PreferencesDialog(Adw.PreferencesDialog):
    __gtype_name__ = "KmlPreferencesDialog"

    tray_row: Adw.SwitchRow = Gtk.Template.Child()
    background_row: Adw.SwitchRow = Gtk.Template.Child()
    smart_layers_row: Adw.SwitchRow = Gtk.Template.Child()
    auto_connect_row: Adw.SwitchRow = Gtk.Template.Child()
    notify_layer_row: Adw.SwitchRow = Gtk.Template.Child()
    silent_layers_row: Adw.ExpanderRow = Gtk.Template.Child()
    snippet_label: Gtk.Label = Gtk.Template.Child()

    def __init__(self, settings: Gio.Settings, tray: object | None = None,
                 service: KeyboardService | None = None) -> None:
        super().__init__()
        self.snippet_label.set_label(AUTOSTART_SNIPPET)
        self._tray = tray
        self._settings = settings
        self._tray_handler = (tray.connect("notify::active", lambda *_a: self._sync_tray())
                              if tray is not None else 0)
        self.connect("closed", self._on_closed)
        for key, row in (("show-in-tray", self.tray_row),
                         ("run-in-background", self.background_row),
                         ("smart-layers-on-startup", self.smart_layers_row),
                         ("auto-connect", self.auto_connect_row),
                         ("notify-layer-changes", self.notify_layer_row)):
            settings.bind(key, row, "active", Gio.SettingsBindFlags.DEFAULT)
        self._handlers = [
            (settings, settings.connect("changed::show-in-tray", lambda *_a: self._sync_tray())),
            (settings, settings.connect(f"changed::{SILENT_LAYERS_KEY}",
                                        lambda *_a: self._sync_silent_layers())),
        ]
        self._service = service
        if service is not None:
            for signal in ("layout-loaded", "disconnected"):
                self._handlers.append((service, service.connect(
                    signal, lambda *_a: self._sync_silent_layers())))
        self._silent_rows: list[Gtk.Widget] = []
        self._layer_rows: dict[int, Adw.SwitchRow] = {}
        self._layout: Layout | None = None
        self._built = False
        self._syncing = False
        self._sync_tray()
        self._sync_silent_layers()

    def _sync_tray(self) -> None:
        if self._tray is None:
            self.tray_row.set_subtitle(_("Tray support isn't available in this build"))
            self.tray_row.set_sensitive(False)
        elif self._settings.get_boolean("show-in-tray") and not self._tray.props.active:
            self.tray_row.set_subtitle(_("No tray found. Your desktop needs a "
                                         "StatusNotifierItem tray."))
        else:
            self.tray_row.set_subtitle(
                _("Closing the window keeps Vibemapp running in the tray"))

    # --- silent layers ----------------------------------------------------

    def _sync_silent_layers(self) -> None:
        layout = self._service.get_layout() if self._service is not None else None
        if not self._built or layout is not self._layout:
            self._rebuild_silent_rows(layout)
        silent = set(get_silent_layers(self._settings))
        self._syncing = True
        try:
            for index, row in self._layer_rows.items():
                row.set_active(index in silent)
        finally:
            self._syncing = False

    def _rebuild_silent_rows(self, layout: Layout | None) -> None:
        self._built = True
        self._layout = layout
        for row in self._silent_rows:
            self.silent_layers_row.remove(row)
        self._silent_rows = []
        self._layer_rows = {}
        if layout is None or not layout.layers:
            hint = Adw.ActionRow(title=_("Connect a keyboard to choose layers"),
                                 use_markup=False)
            hint.add_css_class("dim-label")
            self._add_silent_row(hint)
            return
        for layer in layout.layers:
            row = Adw.SwitchRow(title=layer_label(layer, layer.index), use_markup=False)
            row.connect("notify::active", self._on_silent_layer_active, layer.index)
            self._add_silent_row(row)
            self._layer_rows[layer.index] = row

    def _add_silent_row(self, row: Gtk.Widget) -> None:
        self.silent_layers_row.add_row(row)
        self._silent_rows.append(row)

    def _on_silent_layer_active(self, row: Adw.SwitchRow, _pspec: object, index: int) -> None:
        if self._syncing:
            return
        # Saved layers that aren't in this layout stay in the list.
        set_silent_layers(self._settings, toggle_layer(
            get_silent_layers(self._settings), index, row.get_active()))

    def _on_closed(self, *_args: object) -> None:
        if self._tray is not None and self._tray_handler:
            self._tray.disconnect(self._tray_handler)
            self._tray_handler = 0
        for obj, handler in self._handlers:
            obj.disconnect(handler)
        self._handlers = []

    @Gtk.Template.Callback()
    def on_copy_clicked(self, button: Gtk.Button) -> None:
        button.get_clipboard().set(AUTOSTART_SNIPPET)
        self.add_toast(Adw.Toast(title=_("Copied to clipboard")))
