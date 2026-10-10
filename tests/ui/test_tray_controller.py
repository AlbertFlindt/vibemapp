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
            on_quit=lambda: self.calls.append("quit"))

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
                         ["show", "sep1", "smart-layers", "layer", "sep2", "quit"])
        self.assertFalse(self.item("layer").enabled)
        self.assertEqual(fake.tooltip[1], "No keyboard connected")

        self.svc.simulate_plug()
        self.assertTrue(spin(lambda: self.ctl.state == "ready"))
        self.assertEqual(fake.tooltip, ("Voyager", "Layer 0 · Base"))

        # Toggles aren't flipped by the tray: we flip state and resend the menu.
        self.svc.props.smart_layers_enabled = False
        fake.emit("menu-item-activated", "smart-layers")
        self.assertTrue(self.svc.props.smart_layers_enabled)
        self.assertTrue(self.item("smart-layers").toggle)

        fake.emit("activate")
        fake.emit("menu-item-activated", "show")
        fake.emit("menu-item-activated", "quit")
        self.assertEqual(self.calls, ["toggle", "show", "quit"])

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
