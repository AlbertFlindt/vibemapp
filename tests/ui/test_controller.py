from __future__ import annotations

import unittest

from keymappforlinux.mock_service import MockKeyboardService
from keymappforlinux.ui.controller import DeviceController
from keymappforlinux.ui.settings import MemorySettings

from .test_mock_service import spin


class ControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.svc = MockKeyboardService(speed=50, autostart=False)
        self.settings = MemorySettings()
        self.ctl = DeviceController(self.svc, self.settings)
        spin(lambda: False, timeout=0.05)

    def tearDown(self) -> None:
        self.svc.shutdown()

    def test_auto_connect_flow(self) -> None:
        self.assertEqual(self.ctl.state, "empty")
        self.svc.simulate_plug()
        self.assertEqual(self.ctl.state, "connecting")
        self.assertTrue(spin(lambda: self.ctl.state == "ready"))
        self.svc.simulate_unplug()
        self.assertEqual(self.ctl.state, "empty")

    def test_manual_connect(self) -> None:
        self.svc.props.auto_connect = False
        self.svc.simulate_plug()
        self.assertEqual(self.ctl.state, "available")
        self.ctl.connect_device()
        self.assertTrue(spin(lambda: self.ctl.state == "ready"))
        self.ctl.disconnect_device()
        self.assertEqual(self.ctl.state, "available")

    def test_connect_failure(self) -> None:
        self.svc._denied = True
        errors: list[str] = []
        self.ctl.connect("error", lambda _c, msg: errors.append(msg))
        self.svc.simulate_plug()
        self.assertEqual(self.ctl.state, "failed", "no connect attempt for access_denied")
        self.assertIn("udev", self.ctl.message)
        self.assertEqual(errors, [], "connect errors go to the status page, not toasts")

    def test_no_oryx(self) -> None:
        self.svc._oryx = False
        self.svc.simulate_plug()
        self.assertTrue(spin(lambda: self.ctl.state == "no-layout"))


if __name__ == "__main__":
    unittest.main()
