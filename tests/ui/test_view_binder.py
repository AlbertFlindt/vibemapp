from __future__ import annotations

import unittest

from vibemapp.mock_service import MockKeyboardService
from vibemapp.ui.view_binder import KeyboardViewBinder

from .test_mock_service import spin


class FakeView:
    def __init__(self) -> None:
        self.pressed: set[int] = set()
        self.clears = 0

    def set_key_pressed(self, index: int, pressed: bool) -> None:
        (self.pressed.add if pressed else self.pressed.discard)(index)

    def clear_pressed(self) -> None:
        self.clears += 1
        self.pressed.clear()


class BinderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.svc = MockKeyboardService(speed=50, autostart=False)
        self.svc.simulate_plug()
        self.assertTrue(spin(lambda: self.svc.get_connected_device() is not None))
        self.svc.load_layout_async(self.svc.get_connected_device(), None, lambda *_a: None)
        self.assertTrue(spin(lambda: self.svc.get_layout() is not None))
        self.layers: list[int] = []

    def tearDown(self) -> None:
        self.svc.shutdown()

    def test_forwards_keys_and_layers_and_clears_on_disconnect(self) -> None:
        view = FakeView()
        binder = KeyboardViewBinder(self.svc, view, on_layer_changed=self.layers.append)
        self.svc.simulate_key(3, True)
        self.svc.simulate_key(7, True)
        self.svc.simulate_key(3, False)
        self.assertEqual(view.pressed, {7})
        self.svc.simulate_layer(2)
        self.assertEqual(self.layers, [2])
        self.assertEqual(view.pressed, {7})          # the Layout page keeps them
        self.svc.simulate_unplug()
        self.assertEqual(view.pressed, set())
        binder.disconnect()
        self.svc.simulate_plug()
        self.assertTrue(spin(lambda: self.svc.get_connected_device() is not None))
        self.svc.simulate_layer(1)
        self.assertEqual(self.layers, [2])           # disconnected binder is silent

    def test_gate(self) -> None:
        view = FakeView()
        KeyboardViewBinder(self.svc, view, active=lambda: False)
        self.svc.simulate_key(3, True)
        self.assertEqual(view.pressed, set())

    def test_reset_on_layer_change_keeps_held_keys(self) -> None:
        view = FakeView()
        KeyboardViewBinder(self.svc, view, reset_on_layer_change=True,
                           on_layer_changed=self.layers.append)
        self.svc.simulate_key(48, True)      # e.g. the MO key, still held
        self.svc.simulate_key(5, True)
        self.svc.simulate_key(5, False)
        view.pressed.add(99)                  # stale state from a missed release
        self.svc.simulate_layer(1)
        self.assertEqual(self.layers, [1])
        self.assertEqual(view.pressed, {48})
        self.assertGreaterEqual(view.clears, 1)

    def test_set_views_syncs_new_views(self) -> None:
        old, new_left, new_right = FakeView(), FakeView(), FakeView()
        binder = KeyboardViewBinder(self.svc, old)
        self.svc.simulate_key(10, True)
        binder.set_views(new_left, new_right)
        self.assertEqual((new_left.pressed, new_right.pressed), ({10}, {10}))
        self.svc.simulate_key(11, True)
        self.assertEqual(old.pressed, {10})
        self.assertEqual(new_right.pressed, {10, 11})


class MockPressedKeysTests(unittest.TestCase):
    def test_mock_tracks_pressed_keys_and_monitor(self) -> None:
        svc = MockKeyboardService(speed=50, autostart=False)
        try:
            self.assertEqual(svc.get_pressed_keys(), set())
            svc.simulate_plug()
            self.assertTrue(spin(lambda: svc.get_connected_device() is not None))
            svc.simulate_key(4, True)
            self.assertEqual(svc.get_pressed_keys(), {4})
            svc.get_pressed_keys().add(9)          # a copy
            self.assertEqual(svc.get_pressed_keys(), {4})
            svc.simulate_unplug()
            self.assertEqual(svc.get_pressed_keys(), set())
            seen: list[str] = []
            svc.connect("focused-monitor-changed", lambda _s, name: seen.append(name))
            svc.simulate_monitor("DP-2")
            self.assertEqual((seen, svc.get_focused_monitor()), (["DP-2"], "DP-2"))
        finally:
            svc.shutdown()


if __name__ == "__main__":
    unittest.main()
