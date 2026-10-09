from __future__ import annotations

import time
import unittest

from gi.repository import GLib

from keymappforlinux.mock_service import MockKeyboardService, voyager_geometry
from keymappforlinux.models import AppRule, KeyboardModel
from keymappforlinux.service import KeyboardService


def spin(condition, timeout: float = 2.0) -> bool:
    """Iterate the default main context until ``condition()`` or timeout."""
    ctx = GLib.MainContext.default()
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            return False
        ctx.iteration(False)
        time.sleep(0.001)
    return True


class Recorder:
    def __init__(self, service: KeyboardService) -> None:
        self.events: list[tuple] = []
        for name in ("devices-changed", "connected", "disconnected", "layout-loaded",
                     "layer-changed", "key-event", "focus-changed", "rule-applied",
                     "rules-changed", "error", "focus-source-changed"):
            service.connect(name, lambda _s, *args, n=name: self.events.append((n, *args)))

    def names(self) -> list[str]:
        return [e[0] for e in self.events]

    def last(self, name: str) -> tuple | None:
        return next((e for e in reversed(self.events) if e[0] == name), None)


class MockServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.svc = MockKeyboardService(speed=50, autostart=False)
        self.rec = Recorder(self.svc)

    def tearDown(self) -> None:
        self.svc.shutdown()

    def connect_and_load(self) -> None:
        self.svc.simulate_plug()
        device = self.svc.list_devices()[0]
        self.svc.connect_device(device.id)
        self.assertTrue(spin(lambda: self.svc.get_connected_device() is not None))
        result: list = []
        self.svc.load_layout_async(self.svc.get_connected_device(), None,
                                   lambda layout, err: result.append((layout, err)))
        self.assertTrue(spin(lambda: bool(result)))
        self.assertIsNotNone(result[0][0])

    def test_is_a_keyboard_service(self) -> None:
        self.assertIsInstance(self.svc, KeyboardService)

    def test_geometry(self) -> None:
        geometry = voyager_geometry()
        self.assertEqual(len(geometry), 52)
        self.assertEqual([g.index for g in geometry], list(range(52)))
        self.assertEqual(sum(1 for g in geometry if g.rotation), 4)

    def test_hotplug_and_connect(self) -> None:
        self.assertEqual(self.svc.list_devices(), [])
        self.connect_and_load()
        device = self.svc.get_connected_device()
        self.assertEqual(device.model, KeyboardModel.VOYAGER)
        self.assertIn("devices-changed", self.rec.names())
        self.assertIn("connected", self.rec.names())
        self.assertIn("layout-loaded", self.rec.names())
        layout = self.svc.get_layout()
        self.assertEqual(len(layout.layers), 4)
        for layer in layout.layers:
            self.assertEqual(len(layer.keys), len(layout.geometry))

        self.svc.simulate_unplug()
        self.assertIsNone(self.svc.get_connected_device())
        self.assertIsNone(self.svc.get_layout())
        self.assertIn("disconnected", self.rec.names())
        self.assertEqual(self.svc.get_rules(), [])

    def test_request_layer(self) -> None:
        self.connect_and_load()
        self.svc.request_layer(3)
        self.assertTrue(spin(lambda: self.svc.get_active_layer() == 3))
        self.assertEqual(self.rec.last("layer-changed"), ("layer-changed", 3))
        self.svc.request_layer(42)
        self.assertEqual(self.rec.last("error")[0], "error")

    def test_rules_round_trip(self) -> None:
        self.connect_and_load()
        rules = [AppRule("kitty", "kitty", 1)]
        self.svc.set_rules(rules)
        self.assertEqual(self.svc.get_rules(), rules)
        self.assertIn("rules-changed", self.rec.names())
        self.svc.set_fallback_layer(None)
        self.assertIsNone(self.svc.get_fallback_layer())

    def test_rules_need_a_keyboard(self) -> None:
        self.svc.set_rules([AppRule("kitty", "kitty", 1)])
        self.assertEqual(self.rec.last("error")[0], "error")
        self.assertEqual(self.svc.get_rules(), [])

    def test_smart_layers(self) -> None:
        self.connect_and_load()
        self.svc.set_rules([AppRule("firefox", "Firefox", 2, title_pattern="YouTube"),
                            AppRule("kitty", "kitty", 1)])
        self.svc.set_fallback_layer(0)

        # Disabled: focus is reported, the layer is left alone.
        self.svc.props.smart_layers_enabled = False
        self.svc.simulate_focus("kitty", "fish")
        self.assertEqual(self.rec.last("focus-changed"), ("focus-changed", "kitty", "fish"))
        self.assertEqual(self.svc.get_active_layer(), 0)

        # Enabling re-evaluates the current focus.
        self.svc.props.smart_layers_enabled = True
        self.assertEqual(self.svc.get_active_layer(), 1)
        self.assertEqual(self.rec.last("rule-applied"), ("rule-applied", "kitty", 1))

        self.svc.simulate_focus("firefox", "Lo-fi — YouTube")
        self.assertEqual(self.svc.get_active_layer(), 2)
        self.svc.simulate_focus("firefox", "Docs")
        self.assertEqual(self.svc.get_active_layer(), 0)
        self.assertEqual(self.rec.last("rule-applied"), ("rule-applied", "", 0))

        # Our own window is ignored.
        self.svc.simulate_focus("io.github.albertflindt.KeyMappForLinux", "KeyMapp")
        self.assertEqual(self.svc.get_active_layer(), 0)

        # "Don't change" fallback.
        self.svc.simulate_focus("kitty", "fish")
        self.svc.set_fallback_layer(None)
        self.svc.simulate_focus("blender", "scene")
        self.assertEqual(self.svc.get_active_layer(), 1)

    def test_key_events(self) -> None:
        self.connect_and_load()
        self.svc.simulate_key(5, True)
        self.svc.simulate_key(5, False)
        self.assertEqual([e for e in self.rec.events if e[0] == "key-event"],
                         [("key-event", 5, True), ("key-event", 5, False)])

    def test_auto_connect_property(self) -> None:
        self.svc.props.auto_connect = False
        self.svc.simulate_plug()
        spin(lambda: False, timeout=0.1)
        self.assertIsNone(self.svc.get_connected_device())
        self.svc.props.auto_connect = True
        self.assertTrue(spin(lambda: self.svc.get_connected_device() is not None))

    def test_focus_source_changes(self) -> None:
        self.svc.simulate_focus_source(False)
        self.assertFalse(self.svc.focus_source_available())
        self.assertIsNone(self.svc.get_focused_app())
        self.assertEqual(self.rec.last("focus-source-changed"), ("focus-source-changed", False))
        self.svc.simulate_focus_source(True)
        self.assertTrue(self.svc.focus_source_available())

    def test_access_denied_device(self) -> None:
        self.svc._denied = True
        self.svc.simulate_plug()
        self.assertTrue(self.svc.list_devices()[0].access_denied)

    def test_running_apps(self) -> None:
        apps = self.svc.list_running_apps()
        self.assertTrue(any(a.app_class == "firefox" for a in apps))
        self.assertTrue(self.svc.focus_source_available())


if __name__ == "__main__":
    unittest.main()
