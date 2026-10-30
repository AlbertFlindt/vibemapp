from __future__ import annotations

import time
import unittest

from gi.repository import GLib

from vibemapp.backend import geometries
from vibemapp.mock_service import (MOCK_TO_ORYX, MockKeyboardService, voyager_layout,
                                    voyager_geometry)
from vibemapp.models import AppRule, Finger, HeatmapPeriod, KeyboardModel, LayoutSync, RuleMatch
from vibemapp.service import KeyboardService


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
                     "rules-changed", "error", "focus-source-changed",
                     "key-counts-changed"):
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

    def test_layout_sync(self) -> None:
        self.assertEqual(self.svc.get_layout_sync(), LayoutSync.UNKNOWN)
        self.connect_and_load()
        self.assertEqual(self.svc.get_layout_sync(), LayoutSync.FLASHED)
        self.svc.simulate_unplug()
        self.assertEqual(self.svc.get_layout_sync(), LayoutSync.UNKNOWN)

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
        self.svc.simulate_focus("io.github.albertflindt.Vibemapp", "Vibemapp")
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

    def test_heatmap(self) -> None:
        self.assertEqual(self.svc.get_key_counts(HeatmapPeriod.ALL), {})
        self.connect_and_load()
        self.svc.simulate_key(3, True)
        self.assertEqual(self.svc.get_key_counts(HeatmapPeriod.ALL), {}, "opt-in")

        self.svc.props.heatmap_recording = True
        for _ in range(3):
            self.svc.simulate_key(3, True)
            self.svc.simulate_key(3, False)      # releases don't count
        self.svc.request_layer(2)
        self.assertTrue(spin(lambda: self.svc.get_active_layer() == 2))
        self.svc.simulate_key(7, True)
        for period in HeatmapPeriod:
            self.assertEqual(self.svc.get_key_counts(period), {3: 3, 7: 1})
        self.assertEqual(self.svc.get_key_counts(HeatmapPeriod.TODAY, 2), {7: 1})
        self.assertEqual(self.svc.get_key_counts(HeatmapPeriod.TODAY, 0), {3: 3})
        self.assertTrue(spin(lambda: "key-counts-changed" in self.rec.names()))

        self.svc.reset_key_counts()
        self.assertEqual(self.svc.get_key_counts(HeatmapPeriod.ALL), {})

    def test_heatmap_apps(self) -> None:
        self.connect_and_load()
        self.svc.props.heatmap_recording = True
        self.svc.simulate_focus("firefox", "page")
        self.svc.simulate_key(3, True)
        self.svc.simulate_focus("steam_app_1245620", "ELDEN RING")
        self.svc.simulate_key(4, True)
        self.svc.simulate_key(4, True)
        apps = self.svc.list_heatmap_apps(HeatmapPeriod.ALL)
        self.assertEqual([(a.key, a.name, a.presses) for a in apps],
                         [("steam:1245620", "ELDEN RING", 2), ("firefox", "firefox", 1)])
        self.assertEqual(self.svc.get_key_counts(HeatmapPeriod.ALL, app="firefox"), {3: 1})
        self.assertEqual(self.svc.get_key_counts(HeatmapPeriod.ALL), {3: 1, 4: 2})
        self.svc.reset_key_counts()
        self.assertEqual(self.svc.list_heatmap_apps(HeatmapPeriod.ALL), [])

    def test_heatmap_seed_apps(self) -> None:
        self.svc._seed_heatmap = True
        self.connect_and_load()
        for period in HeatmapPeriod:
            apps = self.svc.list_heatmap_apps(period)
            self.assertGreaterEqual(len(apps), 3)
            total: dict[int, int] = {}
            for a in apps:
                for k, n in self.svc.get_key_counts(period, app=a.key).items():
                    total[k] = total.get(k, 0) + n
            self.assertEqual(total, self.svc.get_key_counts(period))
            self.assertEqual(sum(a.presses for a in apps), sum(total.values()))
        names = {a.key: a.name for a in self.svc.list_heatmap_apps(HeatmapPeriod.ALL)}
        self.assertEqual(names["steam:1245620"], "ELDEN RING")

    def test_pairs_and_ergo_report(self) -> None:
        now = [10.0]
        self.svc._monotonic = lambda: now[0]
        self.assertIsNone(self.svc.get_ergo_report(HeatmapPeriod.ALL))
        self.connect_and_load()
        self.svc.props.heatmap_recording = True
        self.svc.simulate_focus("firefox", "page")

        def press(index: int, at: float) -> None:
            now[0] = at
            self.svc.simulate_key(index, True)
            self.svc.simulate_key(index, False)

        press(1, 10.0)
        press(2, 10.4)                                    # pair_recording is off
        self.assertEqual(self.svc.get_ergo_report(HeatmapPeriod.ALL).pairs, 0)
        self.svc.props.pair_recording = True
        press(1, 20.0)
        press(2, 20.4)                                    # pair
        press(3, 21.6)                                    # idle gap
        self.svc.simulate_focus("kitty", "shell")
        press(4, 21.7)                                    # focus changed: no pair
        press(4, 21.9)                                    # same key twice: pair
        r = self.svc.get_ergo_report(HeatmapPeriod.ALL)
        self.assertEqual((r.presses, r.pairs), (7, 2))
        self.assertEqual(self.svc.get_ergo_report(HeatmapPeriod.ALL, "firefox").pairs, 1)
        self.assertEqual(self.svc.get_ergo_report(HeatmapPeriod.ALL, "kitty").pairs, 1)
        self.assertEqual(self.svc.get_ergo_report(HeatmapPeriod.ALL, "nope").presses, 0)
        self.svc.reset_key_counts()
        r = self.svc.get_ergo_report(HeatmapPeriod.ALL)
        self.assertEqual((r.presses, r.pairs, r.score), (0, 0, 100.0))

    def test_worst_same_finger_uses_mock_indices(self) -> None:
        now = [10.0]
        self.svc._monotonic = lambda: now[0]
        self.connect_and_load()
        self.svc.props.heatmap_recording = True
        self.svc.props.pair_recording = True
        fingers = geometries.FINGERS[KeyboardModel.VOYAGER]
        by_finger: dict = {}
        for mock_i, oryx_i in enumerate(MOCK_TO_ORYX):
            by_finger.setdefault(fingers[oryx_i], []).append(mock_i)
        a, b = next(v for v in by_finger.values() if len(v) >= 2)[:2]
        for t in (20.0, 30.0, 40.0):
            for index in (a, b):
                now[0] = t + (0.3 if index == b else 0.0)
                self.svc.simulate_key(index, True)
                self.svc.simulate_key(index, False)
        worst = self.svc.get_ergo_report(HeatmapPeriod.ALL).worst_same_finger
        self.assertTrue(worst)
        self.assertEqual({worst[0].first[1], worst[0].second[1]}, {a, b})
        self.assertEqual(worst[0].finger, fingers[MOCK_TO_ORYX[a]])

    def test_mock_has_tap_codes_and_danish_ae(self) -> None:
        base = voyager_layout().layers[0].keys
        codes = {b.tap_label: b.tap_code for b in base if b.tap_label}
        self.assertEqual((codes["A"], codes["Æ"], codes["Ø"]), ("KC_A", "DK_AE", "DK_OSTR"))
        self.assertEqual((codes["Shift"], codes["⌫"]), ("KC_LSHIFT", "KC_BSPACE"))
        self.assertTrue(all(b.tap_code is None for b in base if b.transparent))

    def test_layout_comparisons(self) -> None:
        self.assertEqual(self.svc.get_layout_comparisons(HeatmapPeriod.WEEK), [])    # no layout
        self.svc._seed_heatmap = True
        self.connect_and_load()
        res = self.svc.get_layout_comparisons(HeatmapPeriod.WEEK)
        self.assertEqual({r.name for r in res},
                         {"QWERTY", "Colemak", "Colemak-DH", "Workman", "Dvorak"})
        report = self.svc.get_ergo_report(HeatmapPeriod.WEEK)
        cur = next(r for r in res if r.current)
        self.assertEqual((cur.name, cur.score_delta, cur.report.score), ("QWERTY", 0.0, report.score))
        scores = [r.report.score for r in res]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertEqual(self.svc.get_layout_comparisons(HeatmapPeriod.WEEK, "nope"), [])
        self.assertTrue(self.svc.get_layout_comparisons(HeatmapPeriod.WEEK, "firefox"))

    def test_mock_to_oryx_permutation(self) -> None:
        self.assertEqual(sorted(MOCK_TO_ORYX), list(range(52)))
        mock = voyager_geometry()
        oryx = geometries.geometry_for(KeyboardModel.VOYAGER)
        mock_right = min(mock[i].x for i in range(48) if (i % 12) >= 6)
        for i, j in enumerate(MOCK_TO_ORYX):          # same neighbourhood within its half
            right = j >= 26
            self.assertLess(abs((mock[i].x - (mock_right if right else 0))
                                - (oryx[j].x - (10 if right else 0))), 1.2, i)
            self.assertLess(abs(mock[i].y - oryx[j].y), 0.8, i)
        fingers = geometries.FINGERS[KeyboardModel.VOYAGER]
        homes = geometries.HOME_KEYS[KeyboardModel.VOYAGER]
        base = {b.tap_label: b.index for b in voyager_layout().layers[0].keys if b.tap_label}
        for label, finger in (("A", Finger.L_PINKY), ("S", Finger.L_RING),
                              ("D", Finger.L_MIDDLE), ("F", Finger.L_INDEX),
                              ("J", Finger.R_INDEX), ("K", Finger.R_MIDDLE),
                              ("L", Finger.R_RING), ("Æ", Finger.R_PINKY)):
            self.assertEqual(MOCK_TO_ORYX[base[label]], homes[finger], label)
        keys = voyager_layout().layers[0].keys
        for i in (48, 49, 50, 51):                    # thumb cluster: Space, Esc, Enter, ⌫
            self.assertIn(fingers[MOCK_TO_ORYX[i]], (Finger.L_THUMB, Finger.R_THUMB),
                          keys[i].tap_label)
        self.assertEqual(keys[48].tap_label, "Space")
        self.assertEqual(fingers[MOCK_TO_ORYX[48]], Finger.L_THUMB)
        self.assertEqual(fingers[MOCK_TO_ORYX[51]], Finger.R_THUMB)
        self.assertEqual(fingers[MOCK_TO_ORYX[base["E"]]], Finger.L_MIDDLE)
        self.assertEqual(fingers[MOCK_TO_ORYX[base["O"]]], Finger.R_RING)

    def test_seeded_pairs_are_consistent(self) -> None:
        self.svc._seed_heatmap = True
        self.connect_and_load()
        for period in HeatmapPeriod:
            total = self.svc.get_ergo_report(period)
            self.assertEqual(total.presses, sum(self.svc.get_key_counts(period).values()))
            self.assertGreater(total.pairs, 0)
            self.assertLess(total.same_finger_rate, 0.08)       # English bigrams on QWERTY: 4-7%
            self.assertGreater(total.same_finger_rate, 0.01)
            self.assertGreater(total.layer_switch_rate, 0.0)
            self.assertLessEqual(len(total.worst_same_finger), 5)
            apps = self.svc.list_heatmap_apps(period)
            self.assertEqual(sum(self.svc.get_ergo_report(period, a.key).pairs for a in apps),
                             total.pairs)
            self.assertEqual(sum(self.svc.get_ergo_report(period, a.key).presses for a in apps),
                             total.presses)
        a = self.svc.get_ergo_report(HeatmapPeriod.ALL, "steam:1245620")
        b = self.svc.get_ergo_report(HeatmapPeriod.ALL, "firefox")
        self.assertNotEqual(a.finger_load, b.finger_load)
        self.assertEqual(self.svc.get_ergo_report(HeatmapPeriod.ALL),
                         self.svc.get_ergo_report(HeatmapPeriod.ALL))     # deterministic

    def test_heatmap_periods(self) -> None:
        import datetime as dt
        self.connect_and_load()
        today = dt.date.today()
        lid = self.svc.get_layout().id
        self.svc._counts[lid] = {
            (today.isoformat(), 0, 1): 1,
            ((today - dt.timedelta(days=6)).isoformat(), 0, 2): 2,
            ((today - dt.timedelta(days=7)).isoformat(), 0, 3): 4,
        }
        self.assertEqual(self.svc.get_key_counts(HeatmapPeriod.TODAY), {1: 1})
        self.assertEqual(self.svc.get_key_counts(HeatmapPeriod.WEEK), {1: 1, 2: 2})
        self.assertEqual(self.svc.get_key_counts(HeatmapPeriod.ALL), {1: 1, 2: 2, 3: 4})

    def test_steam(self) -> None:
        games = self.svc.list_steam_games()
        self.assertEqual([g.name for g in games], sorted((g.name for g in games), key=str.casefold))
        self.connect_and_load()
        self.svc.set_rules([AppRule("1245620", "ELDEN RING", 1, match=RuleMatch.STEAM_GAME),
                            AppRule("", "All Steam Games", 3, match=RuleMatch.STEAM_GAME)])
        self.svc.set_fallback_layer(0)
        self.svc.props.smart_layers_enabled = True

        self.svc.simulate_focus("steam_app_1245620", "ELDEN RING™")
        window = self.svc.get_focused_window()
        self.assertEqual((window.steam_app_id, window.steam_game_name), ("1245620", "ELDEN RING"))
        self.assertEqual(self.svc.get_active_layer(), 1)

        self.svc.simulate_focus("dota2", "Dota 2")          # native game, via environment
        self.assertEqual(self.svc.get_focused_window().steam_app_id, "570")
        self.assertEqual(self.svc.get_active_layer(), 3)

        self.svc.simulate_focus("steam", "Steam")           # the client is not a game
        self.assertIsNone(self.svc.get_focused_window().steam_app_id)
        self.assertEqual(self.svc.get_active_layer(), 0)

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
