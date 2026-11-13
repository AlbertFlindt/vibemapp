"""MockKeyboardService: the simulated Navigator trackpad / trackball."""

from __future__ import annotations

import os
import unittest

from vibemapp import mock_service
from vibemapp.mock_service import MockKeyboardService
from vibemapp.models import PointerAction as A, PointerKind

from ._util import drain, spin


class MockPointerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.svc = MockKeyboardService(speed=8, autostart=False)
        self.acts: list = []
        self.changes: list = []
        self.svc.connect("pointer-activity", lambda _s, a: self.acts.append(a))
        self.svc.connect("pointer-changed",
                         lambda s: self.changes.append(s.get_pointer_kind()))
        self.svc.simulate_plug()
        self.assertTrue(spin(lambda: self.svc.get_connected_device() is not None))

    def tearDown(self) -> None:
        self.svc.shutdown()

    def test_kind_follows_connection(self) -> None:
        self.assertEqual(self.svc.get_pointer_kind(), PointerKind.TRACKPAD)
        self.assertEqual(self.changes, [PointerKind.TRACKPAD])
        self.svc.disconnect_device()
        self.assertIsNone(self.svc.get_pointer_kind())
        self.assertEqual(self.changes[-1], None)

    def test_simulated_reports_use_the_real_interpreter(self) -> None:
        self.svc.simulate_trackpad([(0.5, 0.5)], now=0.0)
        self.assertEqual(self.acts, [])                  # not tracking: nothing
        self.svc.props.pointer_tracking = True
        self.svc.shutdown()                               # stop the script; drive by hand
        self.svc._device = self.svc._make_device()
        self.svc.simulate_trackpad([(0.5, 0.5)], now=0.0)
        self.svc.simulate_trackpad([], now=0.05)
        self.assertEqual([a.action for a in self.acts], [A.TOUCH, A.TAP, A.IDLE])

    def test_script_plays_while_tracking(self) -> None:
        self.svc.props.pointer_tracking = True
        self.assertTrue(spin(lambda: bool(self.acts)))     # the timer runs
        for _ in range(int(mock_service.POINTER_LOOP_S * 1000 / mock_service.POINTER_TICK_MS)):
            self.svc._on_pointer_tick()                     # one loop, deterministically
        self.assertEqual({A.TOUCH, A.MOVE, A.SCROLL, A.TAP, A.CLICK, A.RELEASE, A.IDLE},
                         {a.action for a in self.acts})
        self.assertIn(2, {a.fingers for a in self.acts if a.action == A.TAP})
        self.assertEqual({"up", "down"},
                         {a.direction for a in self.acts if a.action == A.SCROLL})
        self.svc.props.pointer_tracking = False
        n = len(self.acts)
        drain(0.1)
        self.assertEqual(len(self.acts), n)

    def test_trackball_script(self) -> None:
        self.svc.simulate_pointer_kind(PointerKind.TRACKBALL)
        self.assertEqual(self.changes[-1], PointerKind.TRACKBALL)
        self.svc.props.pointer_tracking = True
        for _ in range(int(mock_service.POINTER_LOOP_S * 1000 / mock_service.POINTER_TICK_MS)):
            self.svc._on_pointer_tick()
        self.assertTrue({A.MOVE, A.SCROLL, A.CLICK, A.RELEASE, A.IDLE}
                        <= {a.action for a in self.acts})
        self.assertTrue(all(a.kind == PointerKind.TRACKBALL for a in self.acts))

    def test_env_selects_kind(self) -> None:
        os.environ["VIBEMAPP_MOCK_POINTER"] = "none"
        try:
            svc = MockKeyboardService(autostart=False)
        finally:
            del os.environ["VIBEMAPP_MOCK_POINTER"]
        self.assertIsNone(svc._mock_pointer)
        self.assertGreater(mock_service.POINTER_LOOP_S, 10)


if __name__ == "__main__":
    unittest.main()
