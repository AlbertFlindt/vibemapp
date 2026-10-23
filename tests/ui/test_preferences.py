from __future__ import annotations

import unittest

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from vibemapp.ui import preferences as prefs  # noqa: E402
from vibemapp.ui.settings import MemorySettings  # noqa: E402

from .test_overlay_page import FakeService, layout  # noqa: E402


@unittest.skipUnless(Gtk.init_check(), "needs a display")
class SilentLayersTests(unittest.TestCase):
    def setUp(self) -> None:
        Adw.init()
        self.settings = MemorySettings()
        self.service = FakeService()

    def dialog(self) -> prefs.PreferencesDialog:
        dlg = prefs.PreferencesDialog(self.settings, service=self.service)
        self.addCleanup(dlg.emit, "closed")
        return dlg

    def load(self, *titles: str) -> None:
        self.service.layout = layout(*titles)
        self.service.emit("layout-loaded", self.service.layout)

    def test_hint_without_layout_then_layer_rows(self) -> None:
        dlg = self.dialog()
        self.assertEqual(dlg._layer_rows, {})
        self.assertEqual(len(dlg._silent_rows), 1)
        self.load("Base", "Mouse", "Layer 2")
        self.assertEqual(sorted(dlg._layer_rows), [0, 1, 2])
        self.assertEqual(dlg._layer_rows[1].get_title(), "Layer 1 · Mouse")
        self.assertEqual(dlg._layer_rows[2].get_title(), "Layer 2")
        self.service.layout = None
        self.service.emit("disconnected")
        self.assertEqual(dlg._layer_rows, {})
        self.assertEqual(len(dlg._silent_rows), 1)

    def test_toggle_writes_and_keeps_unknown_layers(self) -> None:
        self.settings.set_value("notify-excluded-layers", GLib.Variant("ai", [7]))
        self.load("Base", "Mouse")
        dlg = self.dialog()
        self.assertFalse(dlg._layer_rows[1].get_active())
        dlg._layer_rows[1].set_active(True)
        self.assertEqual(prefs.get_silent_layers(self.settings), [1, 7])
        dlg._layer_rows[1].set_active(False)
        self.assertEqual(prefs.get_silent_layers(self.settings), [7])

    def test_follows_setting_changes(self) -> None:
        self.load("Base", "Mouse")
        dlg = self.dialog()
        prefs.set_silent_layers(self.settings, [0, 1])
        self.assertTrue(dlg._layer_rows[0].get_active())
        self.assertTrue(dlg._layer_rows[1].get_active())

    def test_insensitive_while_notifications_are_off(self) -> None:
        dlg = self.dialog()
        self.settings.set_value("notify-layer-changes", GLib.Variant("b", False))
        self.assertFalse(dlg.silent_layers_row.get_sensitive())
        self.settings.set_value("notify-layer-changes", GLib.Variant("b", True))
        self.assertTrue(dlg.silent_layers_row.get_sensitive())

    def test_close_disconnects(self) -> None:
        dlg = prefs.PreferencesDialog(self.settings, service=self.service)
        dlg.emit("closed")
        self.load("Base", "Mouse")
        self.assertEqual(dlg._layer_rows, {})


if __name__ == "__main__":
    unittest.main()
