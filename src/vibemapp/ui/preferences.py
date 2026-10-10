"""Preferences dialog, backed by GSettings."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, Gtk  # noqa: E402

from .i18n import _  # noqa: E402
from .resources import ui_file  # noqa: E402

AUTOSTART_SNIPPET = """hl.on("hyprland.start", function ()
  hl.exec_cmd("vibemapp --background")
end)"""


@Gtk.Template(filename=ui_file("preferences.ui"))
class PreferencesDialog(Adw.PreferencesDialog):
    __gtype_name__ = "KmlPreferencesDialog"

    tray_row: Adw.SwitchRow = Gtk.Template.Child()
    background_row: Adw.SwitchRow = Gtk.Template.Child()
    smart_layers_row: Adw.SwitchRow = Gtk.Template.Child()
    auto_connect_row: Adw.SwitchRow = Gtk.Template.Child()
    snippet_label: Gtk.Label = Gtk.Template.Child()

    def __init__(self, settings: Gio.Settings, tray: object | None = None) -> None:
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
                         ("auto-connect", self.auto_connect_row)):
            settings.bind(key, row, "active", Gio.SettingsBindFlags.DEFAULT)
        settings.connect("changed::show-in-tray", lambda *_a: self._sync_tray())
        self._sync_tray()

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

    def _on_closed(self, *_args: object) -> None:
        if self._tray is not None and self._tray_handler:
            self._tray.disconnect(self._tray_handler)
            self._tray_handler = 0

    @Gtk.Template.Callback()
    def on_copy_clicked(self, button: Gtk.Button) -> None:
        button.get_clipboard().set(AUTOSTART_SNIPPET)
        self.add_toast(Adw.Toast(title=_("Copied to clipboard")))
