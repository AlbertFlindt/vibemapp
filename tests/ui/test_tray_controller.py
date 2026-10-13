from __future__ import annotations

import unittest

from gi.repository import GObject

from vibemapp.mock_service import MockKeyboardService
from vibemapp.ui import tray_controller as tc
from vibemapp.ui.controller import DeviceController
from vibemapp.ui.settings import MemorySettings

from .test_mock_service import spin


class FakeTray(GObject.Object):
    __gtype_name__ = "KmlTestFakeTray"
    __gsignals__ = {
        "activate": (GObject.SignalFlags.RUN_LAST, None, ()),
        "menu-item-activated": (GObject.SignalFlags.RUN_LAST, None, (str,)),
    }
    available = GObject.Property(type=bool, default=True)
    instances: list = []

    def __init__(self, **kwargs: object) -> None:
        super().__init__()
        self.kwargs = kwargs
        self.menu: list = []
        self.tooltip = ("", "")
        self.closed = False
        FakeTray.instances.append(self)

    def set_menu(self, items: list) -> None:
        self.menu = items

    def set_tooltip(self, title: str, body: str = "") -> None:
        self.tooltip = (title, body)

    def close(self) -> None:
        self.closed = True


class TrayControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._orig = tc.TrayIcon
        if tc.TrayMenuItem is None:
            self.skipTest("tray module not available")
        tc.TrayIcon = FakeTray
        FakeTray.instances = []
        self.svc = MockKeyboardService(speed=50, autostart=False)
        self.settings = MemorySettings()
        self.ctl = DeviceController(self.svc, self.settings)
        self.calls: list[str] = []
        self.tray = tc.TrayController(
            self.svc, self.ctl, self.settings,
            on_toggle_window=lambda: self.calls.append("toggle"),
            on_show_window=lambda: self.calls.append("show"),
            on_quit=lambda: self.calls.append("quit"),
            on_hide_window=lambda: self.calls.append("hide"),
            on_preferences=lambda: self.calls.append("preferences"))

    def tearDown(self) -> None:
        tc.TrayIcon = self._orig
        self.tray.shutdown()
        self.svc.shutdown()

    def item(self, item_id: str):
        return next(i for i in FakeTray.instances[-1].menu if i.id == item_id)

    def test_menu_tooltip_and_actions(self) -> None:
        fake = FakeTray.instances[-1]
        self.assertTrue(self.tray.active)
        self.assertEqual([i.id for i in fake.menu],
                         ["status", "sep1", "show", "smart-layers", "sep2", "preferences",
                          "quit"])
        self.assertFalse(self.item("status").enabled)
        self.assertEqual(self.item("status").label, "No keyboard connected")
        self.assertEqual(fake.tooltip[1], "No keyboard connected")

        self.svc.simulate_plug()
        self.assertTrue(spin(lambda: self.ctl.state == "ready"))
        self.assertEqual(fake.tooltip, ("Voyager", "Layer 0 · Base"))
        self.assertEqual(self.item("status").label, "Voyager · Layer 0 · Base")

        # Toggles aren't flipped by the tray: we flip state and resend the menu.
        self.svc.props.smart_layers_enabled = False
        fake.emit("menu-item-activated", "smart-layers")
        self.assertTrue(self.svc.props.smart_layers_enabled)
        self.assertTrue(self.item("smart-layers").toggle)

        fake.emit("activate")
        fake.emit("menu-item-activated", "show")
        fake.emit("menu-item-activated", "preferences")
        fake.emit("menu-item-activated", "status")      # insensitive header: no-op
        fake.emit("menu-item-activated", "quit")
        self.assertEqual(self.calls, ["toggle", "show", "preferences", "quit"])

    def test_show_item_follows_window_visibility(self) -> None:
        fake = FakeTray.instances[-1]
        self.assertEqual(self.item("show").label, "Show Vibemapp")
        before = fake.menu
        self.tray.window_visible = True
        self.assertIsNot(fake.menu, before)               # menu re-sent
        self.assertEqual(self.item("show").label, "Hide Vibemapp")
        fake.emit("menu-item-activated", "show")
        self.tray.window_visible = False
        self.assertEqual(self.item("show").label, "Show Vibemapp")
        fake.emit("menu-item-activated", "show")
        self.assertEqual(self.calls, ["hide", "show"])

    def test_hide_falls_back_to_toggle(self) -> None:
        self.tray.shutdown()
        tray = tc.TrayController(self.svc, self.ctl, self.settings,
                                 on_toggle_window=lambda: self.calls.append("toggle"),
                                 on_show_window=lambda: self.calls.append("show"),
                                 on_quit=lambda: self.calls.append("quit"))
        try:
            tray.window_visible = True
            FakeTray.instances[-1].emit("menu-item-activated", "show")
            FakeTray.instances[-1].emit("menu-item-activated", "preferences")
            self.assertEqual(self.calls, ["toggle"])
        finally:
            tray.shutdown()

    def test_overlay_item(self) -> None:
        self.tray.shutdown()
        tray = tc.TrayController(self.svc, self.ctl, self.settings,
                                 on_toggle_window=lambda: None, on_show_window=lambda: None,
                                 on_quit=lambda: None,
                                 on_toggle_overlay=lambda: self.calls.append("overlay"))
        try:
            fake = FakeTray.instances[-1]
            self.assertEqual([i.id for i in fake.menu],
                             ["status", "sep1", "show", "smart-layers", "overlay", "sep2",
                              "preferences", "quit"])
            self.assertEqual(self.item("overlay").label, "Show Overlay")
            self.assertTrue(self.item("overlay").toggle)      # default mode "selected"
            fake.emit("menu-item-activated", "overlay")
            self.assertEqual(self.calls, ["overlay"])
            self.settings.set_string("overlay-mode", "off")
            self.assertFalse(self.item("overlay").toggle)
            self.settings.set_string("overlay-mode", "always")
            self.assertTrue(self.item("overlay").toggle)
        finally:
            tray.shutdown()

    def test_setting_off_closes_tray(self) -> None:
        fake = FakeTray.instances[-1]
        self.settings.set_boolean("show-in-tray", False)
        self.assertTrue(fake.closed)
        self.assertFalse(self.tray.active)
        self.settings.set_boolean("show-in-tray", True)
        self.assertTrue(self.tray.active)
        self.assertEqual(len(FakeTray.instances), 2)


if __name__ == "__main__":
    unittest.main()
