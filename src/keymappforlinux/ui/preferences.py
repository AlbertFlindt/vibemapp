"""Preferences dialog, backed by GSettings."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, Gtk  # noqa: E402

from .i18n import _  # noqa: E402
from .resources import ui_file  # noqa: E402

EXEC_LINE = "exec-once = keymappforlinux --background"


@Gtk.Template(filename=ui_file("preferences.ui"))
class PreferencesDialog(Adw.PreferencesDialog):
    __gtype_name__ = "KmlPreferencesDialog"

    background_row: Adw.SwitchRow = Gtk.Template.Child()
    smart_layers_row: Adw.SwitchRow = Gtk.Template.Child()
    auto_connect_row: Adw.SwitchRow = Gtk.Template.Child()
    exec_row: Adw.ActionRow = Gtk.Template.Child()

    def __init__(self, settings: Gio.Settings) -> None:
        super().__init__()
        self.exec_row.set_title(EXEC_LINE)
        for key, row in (("run-in-background", self.background_row),
                         ("smart-layers-on-startup", self.smart_layers_row),
                         ("auto-connect", self.auto_connect_row)):
            settings.bind(key, row, "active", Gio.SettingsBindFlags.DEFAULT)

    @Gtk.Template.Callback()
    def on_copy_clicked(self, button: Gtk.Button) -> None:
        button.get_clipboard().set(EXEC_LINE)
        self.add_toast(Adw.Toast(title=_("Copied to clipboard")))
