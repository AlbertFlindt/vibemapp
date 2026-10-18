from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
import unittest

from vibemapp.backend.heatmap import HeatmapStore
from vibemapp.backend.oryx import OryxConnection
from vibemapp.backend.oryx_api import LayoutCache, LayoutRepository
from vibemapp.backend.zsa_service import ZsaKeyboardService
from vibemapp.models import FocusedWindow, HeatmapApp, HeatmapPeriod

from ._util import FakeFirmware, drain, spin
from .test_devices import RAW_DESC, make_node
from .test_oryx_api import SAMPLE
from .test_service import FakeWatcher


class Clock:
    def __init__(self, day: dt.date) -> None:
        self.day = day

    def __call__(self) -> dt.date:
        return self.day

    def advance(self, days: int) -> None:
        self.day += dt.timedelta(days=days)


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.clock = Clock(dt.date(2026, 10, 10))
        self.store = HeatmapStore(self.tmp.name, self.clock)
        self.store.set_layout("Bre9W")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_counting_layers_and_periods(self) -> None:
        s = self.store
        for _ in range(3):
            s.record(0, 5)
        s.record(1, 5)
        s.record(1, 7)
        self.assertEqual(s.counts(HeatmapPeriod.TODAY), {5: 4, 7: 1})
        self.assertEqual(s.counts(HeatmapPeriod.TODAY, layer=1), {5: 1, 7: 1})
        self.assertEqual(s.counts(HeatmapPeriod.ALL, layer=2), {})
        # day rollover: yesterday's presses leave TODAY but stay in WEEK/ALL
        self.clock.advance(1)
        s.record(0, 9)
        self.assertEqual(s.counts(HeatmapPeriod.TODAY), {9: 1})
        self.assertEqual(s.counts(HeatmapPeriod.WEEK), {5: 4, 7: 1, 9: 1})
        # WEEK = today + 6 previous days: 6 days later the first day is still in,
        # 7 days later it's out.
        self.clock.advance(5)
        self.assertEqual(s.counts(HeatmapPeriod.WEEK)[5], 4)
        self.clock.advance(1)
        self.assertEqual(s.counts(HeatmapPeriod.WEEK), {9: 1})
        self.assertEqual(s.counts(HeatmapPeriod.ALL), {5: 4, 7: 1, 9: 1})
        self.assertEqual(s.counts("week"), {9: 1})

    def test_persistence_roundtrip_and_privacy(self) -> None:
        self.store.record(0, 1)
        self.store.record(2, 3)
        self.assertTrue(self.store.dirty)
        self.assertTrue(self.store.flush())
        self.assertFalse(self.store.flush())          # nothing new
        path = os.path.join(self.tmp.name, "Bre9W.json")
        with open(path) as f:
            data = json.load(f)
        self.assertEqual(data, {"version": 2, "layout_id": "Bre9W",
                                "days": {"2026-10-10": {"0": {"1": 1}, "2": {"3": 1}}},
                                "app_days": {}, "app_names": {}, "pairs": {}, "app_pairs": {}})
        self.assertEqual([n for n in os.listdir(self.tmp.name) if n.endswith(".tmp")], [])
        other = HeatmapStore(self.tmp.name, self.clock)
        other.set_layout("Bre9W")
        self.assertEqual(other.counts(HeatmapPeriod.ALL), {1: 1, 3: 1})

    def test_layout_switch_flushes_and_isolates(self) -> None:
        self.store.record(0, 1)
        self.store.set_layout("other")
        self.assertEqual(self.store.counts(HeatmapPeriod.ALL), {})
        self.assertTrue(os.path.exists(os.path.join(self.tmp.name, "Bre9W.json")))
        self.store.set_layout(None)
        self.assertEqual(self.store.counts(HeatmapPeriod.ALL), {})
        self.store.record(0, 1)        # ignored without a layout
        self.assertFalse(self.store.dirty)

    def test_reset(self) -> None:
        self.store.record(0, 1)
        self.store.flush()
        self.store.reset()
        self.assertEqual(self.store.counts(HeatmapPeriod.ALL), {})
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "Bre9W.json")))

    def test_corrupt_and_bad_entries(self) -> None:
        path = os.path.join(self.tmp.name, "x.json")
        with open(path, "w") as f:
            json.dump({"days": {"2026-10-10": {"0": {"1": 2, "a": 3, "4": -1}},
                                "bogus": {"0": {"1": 1}}}}, f)
        s = HeatmapStore(self.tmp.name, self.clock)
        s.set_layout("x")
        self.assertEqual(s.counts(HeatmapPeriod.ALL), {1: 2})
        with open(path, "w") as f:
            f.write("{nope")
        s2 = HeatmapStore(self.tmp.name, self.clock)
        s2.set_layout("x")
        self.assertEqual(s2.counts(HeatmapPeriod.ALL), {})
        self.assertTrue(os.path.exists(path + ".corrupt"))


class AppStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.clock = Clock(dt.date(2026, 10, 10))
        self.store = HeatmapStore(self.tmp.name, self.clock)
        self.store.set_layout("Bre9W")
        self.path = os.path.join(self.tmp.name, "Bre9W.json")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def reload(self) -> HeatmapStore:
        other = HeatmapStore(self.tmp.name, self.clock)
        other.set_layout("Bre9W")
        return other

    def test_v1_file_loads_without_apps_then_upgrades(self) -> None:
        with open(self.path, "w") as f:
            json.dump({"version": 1, "layout_id": "Bre9W",
                       "days": {"2026-10-09": {"0": {"4": 5}}}}, f)
        s = self.reload()
        self.assertEqual(s.counts(HeatmapPeriod.ALL), {4: 5})
        self.assertEqual(s.counts(HeatmapPeriod.ALL, app=None), {4: 5})
        self.assertEqual(s.apps(HeatmapPeriod.ALL), [])
        s.record(0, 4, "firefox")
        s.flush()
        with open(self.path) as f:
            data = json.load(f)
        self.assertEqual(data["version"], 2)
        self.assertEqual(data["days"], {"2026-10-09": {"0": {"4": 5}},
                                        "2026-10-10": {"0": {"4": 1}}})
        self.assertEqual(data["app_days"], {"2026-10-10": {"firefox": {"0": {"4": 1}}}})

    def test_per_app_counts(self) -> None:
        s = self.store
        s.record(0, 1, "firefox")
        s.record(0, 1, "firefox")
        s.record(1, 2, "kitty")
        s.record(0, 3)                                   # no app: total only
        self.assertEqual(s.counts(HeatmapPeriod.ALL, app="firefox"), {1: 2})
        self.assertEqual(s.counts(HeatmapPeriod.ALL, layer=1, app="kitty"), {2: 1})
        self.assertEqual(s.counts(HeatmapPeriod.ALL, layer=0, app="kitty"), {})
        self.assertEqual(s.counts(HeatmapPeriod.ALL), {1: 2, 2: 1, 3: 1})
        self.assertEqual(s.counts(HeatmapPeriod.ALL, app="nope"), {})
        self.assertEqual(s.apps(HeatmapPeriod.ALL),
                         [HeatmapApp("firefox", "firefox", 2), HeatmapApp("kitty", "kitty", 1)])

    def test_apps_sorted_by_presses_then_name(self) -> None:
        for app, n in (("b", 2), ("a", 2), ("c", 5)):
            for _ in range(n):
                self.store.record(0, 1, app)
        self.assertEqual([a.key for a in self.store.apps(HeatmapPeriod.ALL)], ["c", "a", "b"])

    def test_steam_name_roundtrip(self) -> None:
        self.store.record(0, 1, "steam:1245620", "Elden Ring")
        self.store.record(0, 1, "firefox", "firefox")     # name == key: not stored
        self.store.flush()
        with open(self.path) as f:
            self.assertEqual(json.load(f)["app_names"], {"steam:1245620": "Elden Ring"})
        apps = self.reload().apps(HeatmapPeriod.ALL)
        self.assertEqual(apps, [HeatmapApp("steam:1245620", "Elden Ring", 1),
                                HeatmapApp("firefox", "firefox", 1)])

    def test_period_filtering(self) -> None:
        self.store.record(0, 1, "old")
        self.clock.advance(10)
        self.store.record(0, 2, "new")
        self.assertEqual([a.key for a in self.store.apps(HeatmapPeriod.TODAY)], ["new"])
        self.assertEqual([a.key for a in self.store.apps(HeatmapPeriod.WEEK)], ["new"])
        self.assertEqual(len(self.store.apps(HeatmapPeriod.ALL)), 2)
        self.assertEqual(self.store.counts(HeatmapPeriod.WEEK, app="old"), {})
        self.assertEqual(self.store.counts(HeatmapPeriod.ALL, app="old"), {1: 1})

    def test_garbage_app_entries_skipped(self) -> None:
        with open(self.path, "w") as f:
            json.dump({"days": {"2026-10-10": {"0": {"1": 3}}},
                       "app_days": {"2026-10-10": {"ok": {"0": {"1": 2, "x": 1, "2": -4}},
                                                   "bad": [1], "": {"0": {"1": 1}},
                                                   "worse": {"0": "no"}, "n": 7},
                                    "nodate": {"ok": {"0": {"1": 9}}}, "2026-10-11": 5,
                                    "future": 1},
                       "app_names": {"ok": "Okay", "bad": 5, "": "x", "z": ""},
                       "future_key": {"a": 1}}, f)
        s = self.reload()
        self.assertEqual(s.apps(HeatmapPeriod.ALL), [HeatmapApp("ok", "Okay", 2)])
        self.assertEqual(s.counts(HeatmapPeriod.ALL), {1: 3})
        for junk in ("x", ["a"], 5, None):
            with open(self.path, "w") as f:
                json.dump({"days": {}, "app_days": junk, "app_names": junk}, f)
            self.assertEqual(self.reload().apps(HeatmapPeriod.ALL), [])

    def test_app_key_sanitised(self) -> None:
        self.store.record(0, 1, "x" * 500)
        self.assertEqual(len(self.store.apps(HeatmapPeriod.ALL)[0].key), 200)

    def test_reset_clears_apps(self) -> None:
        self.store.record(0, 1, "firefox", "Fox")
        self.store.flush()
        self.store.reset()
        self.assertEqual(self.store.apps(HeatmapPeriod.ALL), [])
        self.assertEqual(self.store.counts(HeatmapPeriod.ALL, app="firefox"), {})
        self.assertEqual(self.store.to_json()["app_names"], {})


class PairStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.clock = Clock(dt.date(2026, 10, 10))
        self.store = HeatmapStore(self.tmp.name, self.clock)
        self.store.set_layout("Bre9W")
        self.path = os.path.join(self.tmp.name, "Bre9W.json")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def reload(self) -> HeatmapStore:
        other = HeatmapStore(self.tmp.name, self.clock)
        other.set_layout("Bre9W")
        return other

    def test_record_and_roundtrip(self) -> None:
        s = self.store
        s.record_pair((0, 1), (0, 2), "firefox")
        s.record_pair((0, 1), (0, 2), "firefox")
        s.record_pair((0, 1), (2, 3))                       # no app: total only
        s.record_pair((0, 1), (0, 1), "kitty")              # same key twice counts
        a, b, c = ((0, 1), (0, 2)), ((0, 1), (2, 3)), ((0, 1), (0, 1))
        self.assertEqual(s.pairs(HeatmapPeriod.ALL), {a: 2, b: 1, c: 1})
        self.assertEqual(s.pairs(HeatmapPeriod.ALL, app="firefox"), {a: 2})
        self.assertEqual(s.pairs(HeatmapPeriod.ALL, app="nope"), {})
        s.flush()
        with open(self.path) as f:
            data = json.load(f)
        self.assertEqual(data["pairs"], {"2026-10-10": {"0:1>0:1": 1, "0:1>0:2": 2, "0:1>2:3": 1}})
        self.assertEqual(data["app_pairs"]["2026-10-10"]["firefox"], {"0:1>0:2": 2})
        o = self.reload()
        self.assertEqual(o.pairs(HeatmapPeriod.ALL), {a: 2, b: 1, c: 1})
        self.assertEqual(o.pairs(HeatmapPeriod.TODAY, app="kitty"), {c: 1})
        self.assertEqual(o.layer_counts(HeatmapPeriod.ALL), {})   # pairs aren't presses

    def test_period_filter(self) -> None:
        self.store.record_pair((0, 1), (0, 2), "x")
        self.clock.advance(10)
        self.store.record_pair((0, 3), (0, 4), "x")
        self.assertEqual(self.store.pairs(HeatmapPeriod.WEEK), {((0, 3), (0, 4)): 1})
        self.assertEqual(len(self.store.pairs(HeatmapPeriod.ALL)), 2)
        self.assertEqual(self.store.pairs(HeatmapPeriod.WEEK, app="x"), {((0, 3), (0, 4)): 1})

    def test_v1_and_v2_without_pairs_load(self) -> None:
        with open(self.path, "w") as f:
            json.dump({"version": 2, "days": {"2026-10-10": {"0": {"1": 1}}}}, f)
        s = self.reload()
        self.assertEqual(s.pairs(HeatmapPeriod.ALL), {})
        self.assertEqual(s.layer_counts(HeatmapPeriod.ALL), {(0, 1): 1})

    def test_garbage_pairs_skipped(self) -> None:
        with open(self.path, "w") as f:
            json.dump({"days": {}, "pairs": {
                "2026-10-10": {"0:1>0:2": 3, "bad": 1, "0:1>0:x": 1, "-1:1>0:2": 1,
                               "0:1>0:3": -2, "0:1>0:4": "q", "0:1>0:5": "2"},
                "nodate": {"0:1>0:2": 9}, "2026-10-11": [1], "2026-10-12": 4},
                "app_pairs": {"2026-10-10": {"ok": {"1:1>1:2": 2, "zz": 1}, "bad": 5,
                                             "": {"0:1>0:2": 1}},
                              "nodate": {"ok": {"0:1>0:2": 1}}, "2026-10-11": 3}}, f)
        s = self.reload()
        self.assertEqual(s.pairs(HeatmapPeriod.ALL),
                         {((0, 1), (0, 2)): 3, ((0, 1), (0, 5)): 2})
        self.assertEqual(s.pairs(HeatmapPeriod.ALL, app="ok"), {((1, 1), (1, 2)): 2})
        for junk in ("x", ["a"], 5, None):
            with open(self.path, "w") as f:
                json.dump({"days": {}, "pairs": junk, "app_pairs": junk}, f)
            self.assertEqual(self.reload().pairs(HeatmapPeriod.ALL), {})

    def test_reset_clears_pairs(self) -> None:
        self.store.record_pair((0, 1), (0, 2), "x")
        self.store.flush()
        self.store.reset()
        self.assertEqual(self.store.pairs(HeatmapPeriod.ALL), {})
        self.assertEqual(self.store.pairs(HeatmapPeriod.ALL, app="x"), {})
        self.assertEqual(self.store.to_json()["pairs"], {})
        self.assertEqual(self.store.to_json()["app_pairs"], {})

    def test_negative_ignored_without_layout(self) -> None:
        self.store.record_pair((0, -1), (0, 2))
        self.assertFalse(self.store.dirty)
        self.store.set_layout(None)
        self.store.record_pair((0, 1), (0, 2))
        self.assertFalse(self.store.dirty)


class ServiceHeatmapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        make_node(root, 10, "5-2.2", 1, 0x3297, 0x1977, RAW_DESC)
        self.fw = FakeFirmware()
        self.clock = Clock(dt.date(2026, 10, 10))
        self.hdir = os.path.join(root, "heatmap")
        self.changes = 0
        self.watcher = FakeWatcher()
        self.now = 100.0
        self.svc = self._make()

    def _make(self) -> ZsaKeyboardService:
        root = self.tmp.name
        svc = ZsaKeyboardService(
            sysfs_root=os.path.join(root, "sys"), dev_root=os.path.join(root, "dev"),
            rules_path=os.path.join(root, "rules.json"), focus_watcher=self.watcher,
            repository=LayoutRepository(LayoutCache(os.path.join(root, "c")),
                                        fetcher=lambda *a: SAMPLE),
            connection_factory=lambda p, e, c: OryxConnection(p, e, c, opener=self.fw.opener),
            poll_interval_ms=0, heatmap_dir=self.hdir, clock=self.clock,
            monotonic=lambda: self.now)
        svc.connect("key-counts-changed", self._on_changed)
        return svc

    def _on_changed(self, *_a) -> None:
        self.changes += 1

    def tearDown(self) -> None:
        self.svc.shutdown()
        drain(0.05)
        self.fw.unplug()
        self.tmp.cleanup()

    def press(self, row: int, col: int, n: int = 1) -> None:
        for _ in range(n):
            self.fw.send(0x06, col, row, 0xFE)
            self.fw.send(0x07, col, row, 0xFE)

    def test_opt_in_counting_layers_throttle_and_persist(self) -> None:
        self.assertFalse(self.svc.props.heatmap_recording)
        self.assertTrue(spin(lambda: self.svc.get_connected_device() is not None))
        self.assertEqual(self.svc.get_key_counts(HeatmapPeriod.ALL), {})
        self.press(5, 0)                                  # not recording: ignored
        drain(0.1)
        self.assertEqual(self.svc.get_key_counts(HeatmapPeriod.ALL), {})

        self.svc.props.heatmap_recording = True
        before = self.changes
        self.press(5, 0, 3)                               # index 24 on layer 0
        self.assertTrue(spin(lambda: self.svc.get_key_counts(HeatmapPeriod.TODAY) == {24: 3}))
        self.fw.send(0x05, 2, 0xFE)                       # keyboard now on layer 2
        self.assertTrue(spin(lambda: self.svc.get_active_layer() == 2))
        self.press(0, 1, 2)                               # index 0 on layer 2
        self.assertTrue(spin(lambda: self.svc.get_key_counts(HeatmapPeriod.ALL) == {24: 3, 0: 2}))
        self.assertEqual(self.svc.get_key_counts(HeatmapPeriod.ALL, layer=2), {0: 2})
        self.assertEqual(self.svc.get_key_counts(HeatmapPeriod.WEEK, layer=0), {24: 3})
        # Throttle: 5 presses within ~1 s produced at most 2 notifications.
        drain(0.1)
        self.assertLessEqual(self.changes - before, 2)
        self.assertTrue(spin(lambda: self.changes - before >= 1, timeout=1.5))
        # Persist on shutdown, reload in a new service.
        self.svc.shutdown()
        self.assertTrue(os.path.exists(os.path.join(self.hdir, "Bre9W.json")))
        self.fw.unplug()
        self.fw = FakeFirmware()
        self.svc = self._make()
        self.assertTrue(spin(lambda: self.svc.get_connected_device() is not None))
        self.assertEqual(self.svc.get_key_counts(HeatmapPeriod.ALL), {24: 3, 0: 2})
        before = self.changes
        self.svc.reset_key_counts()
        self.assertEqual(self.changes - before, 1)
        self.assertEqual(self.svc.get_key_counts(HeatmapPeriod.ALL), {})

    def test_records_focused_app(self) -> None:
        self.svc.props.heatmap_recording = True
        self.assertTrue(spin(lambda: self.svc.get_connected_device() is not None))
        self.svc._focused_window = None                   # nothing focused: total only
        self.press(5, 0)
        self.assertTrue(spin(lambda: self.svc.get_key_counts(HeatmapPeriod.ALL) == {24: 1}))
        self.assertEqual(self.svc.list_heatmap_apps(HeatmapPeriod.ALL), [])
        self.svc._update_focus("firefox", "a page")
        self.press(5, 0, 2)
        self.assertTrue(spin(lambda: self.svc.get_key_counts(HeatmapPeriod.ALL) == {24: 3}))
        self.svc._focused_window = FocusedWindow("steam_app_1245620", "ER", 1, "1245620",
                                                 "Elden Ring")
        self.press(0, 1)
        self.assertTrue(spin(lambda: self.svc.get_key_counts(HeatmapPeriod.ALL) == {24: 3, 0: 1}))
        self.svc._focused_window = FocusedWindow("steam_app_9", "G", 1, "9")   # name unknown
        self.press(0, 1)
        self.assertTrue(spin(lambda: self.svc.get_key_counts(HeatmapPeriod.ALL) == {24: 3, 0: 2}))
        self.assertEqual(self.svc.get_key_counts(HeatmapPeriod.ALL, app="firefox"), {24: 2})
        self.assertEqual(self.svc.get_key_counts(HeatmapPeriod.ALL, app="steam:1245620"), {0: 1})
        self.assertEqual(self.svc.list_heatmap_apps(HeatmapPeriod.ALL),
                         [HeatmapApp("firefox", "firefox", 2),
                          HeatmapApp("steam:1245620", "Elden Ring", 1),
                          HeatmapApp("steam:9", "steam:9", 1)])
        self.svc.reset_key_counts()
        self.assertEqual(self.svc.list_heatmap_apps(HeatmapPeriod.ALL), [])

    def pair_press(self, row: int, col: int, at: float) -> None:
        self.now = at
        self.press(row, col)
        drain(0.05)

    def pairs(self):
        return self.svc._heatmap.pairs(HeatmapPeriod.ALL)

    def start_pairs(self) -> None:
        self.svc.props.heatmap_recording = True
        self.svc.props.pair_recording = True
        self.assertTrue(spin(lambda: self.svc.get_connected_device() is not None))
        drain(0.05)

    A, B, C = (0, 24), (0, 0), (0, 25)        # (5,0) -> 24, (0,1) -> 0 on this board

    def test_pair_window_and_idle_gap(self) -> None:
        self.assertFalse(self.svc.props.pair_recording)
        self.start_pairs()
        self.pair_press(5, 0, 10.0)
        self.assertEqual(self.pairs(), {})                 # no previous key yet
        self.pair_press(0, 1, 10.5)                        # within the window
        self.assertEqual(self.pairs(), {(self.A, self.B): 1})
        self.pair_press(0, 1, 11.6)                        # 1.1 s later: idle gap
        self.assertEqual(self.pairs(), {(self.A, self.B): 1})
        self.pair_press(0, 1, 12.0)                        # same key twice counts
        self.assertEqual(self.pairs()[(self.B, self.B)], 1)
        self.pair_press(0, 1, 13.0)                        # exactly 1.0 s: outside
        self.assertEqual(sum(self.pairs().values()), 2)
        self.assertEqual(self.svc.PAIR_WINDOW_S, 1.0)

    def test_pairs_use_active_layer_and_app(self) -> None:
        self.start_pairs()
        self.watcher.focus("firefox", "page")
        drain(0.05)
        self.pair_press(5, 0, 10.0)
        self.fw.send(0x05, 2, 0xFE)
        self.assertTrue(spin(lambda: self.svc.get_active_layer() == 2))
        self.pair_press(0, 1, 10.4)
        pair = (self.A, (2, 0))
        self.assertEqual(self.pairs(), {pair: 1})
        self.assertEqual(self.svc._heatmap.pairs(HeatmapPeriod.ALL, app="firefox"), {pair: 1})

    def test_pairs_need_both_opt_ins(self) -> None:
        self.svc.props.heatmap_recording = True            # pair_recording stays off
        self.assertTrue(spin(lambda: self.svc.get_connected_device() is not None))
        self.pair_press(5, 0, 10.0)
        self.pair_press(0, 1, 10.2)
        self.assertEqual(self.pairs(), {})
        self.svc.props.heatmap_recording = False
        self.svc.props.pair_recording = True
        self.pair_press(5, 0, 11.0)
        self.pair_press(0, 1, 11.2)
        self.assertEqual(self.pairs(), {})

    def test_previous_key_cleared_by_focus_property_and_layout(self) -> None:
        self.start_pairs()
        self.pair_press(5, 0, 10.0)
        self.watcher.focus("firefox", "page")
        drain(0.05)
        self.pair_press(0, 1, 10.2)                        # focus changed in between
        self.assertEqual(self.pairs(), {})
        self.svc.props.pair_recording = False
        self.svc.props.pair_recording = True
        self.pair_press(5, 0, 20.0)
        self.svc.props.pair_recording = False
        self.svc.props.pair_recording = True
        self.pair_press(0, 1, 20.2)
        self.assertEqual(self.pairs(), {})
        self.pair_press(5, 0, 30.0)
        self.svc.props.heatmap_recording = False
        self.svc.props.heatmap_recording = True
        self.pair_press(0, 1, 30.2)
        self.assertEqual(self.pairs(), {})
        self.pair_press(5, 0, 40.0)
        self.svc._set_heatmap_layout("other")
        self.svc._set_heatmap_layout("Bre9W")
        self.pair_press(0, 1, 40.2)
        self.assertEqual(self.pairs(), {})
        self.pair_press(0, 1, 40.5)                        # still works afterwards
        self.assertEqual(sum(self.pairs().values()), 1)

    def test_reset_clears_pairs_and_report(self) -> None:
        self.start_pairs()
        self.pair_press(5, 0, 10.0)
        self.pair_press(0, 1, 10.2)
        self.assertTrue(spin(lambda: self.svc.get_layout() is not None))
        r = self.svc.get_ergo_report(HeatmapPeriod.ALL)
        self.assertEqual((r.presses, r.pairs), (2, 1))
        self.assertEqual(self.svc.get_ergo_report(HeatmapPeriod.ALL, app="nope").presses, 0)
        self.svc._flush_heatmap()
        with open(os.path.join(self.hdir, "Bre9W.json")) as f:     # nothing longer than a pair
            self.assertEqual(json.load(f)["pairs"], {"2026-10-10": {"0:24>0:0": 1}})
        self.svc.reset_key_counts()
        self.assertEqual(self.pairs(), {})
        r = self.svc.get_ergo_report(HeatmapPeriod.ALL)
        self.assertEqual((r.presses, r.pairs, r.score), (0, 0, 100.0))

    def test_no_report_without_layout(self) -> None:
        self.assertIsNone(self.svc.get_ergo_report(HeatmapPeriod.ALL))

    def test_throttle_interval(self) -> None:
        self.svc.HEATMAP_THROTTLE_MS = 300
        self.svc.props.heatmap_recording = True
        self.assertTrue(spin(lambda: self.svc.get_connected_device() is not None))
        drain(0.35)
        start = self.changes
        import time
        t0 = time.monotonic()
        while time.monotonic() - t0 < 0.9:               # continuous typing for 0.9 s
            self.press(5, 0)
            drain(0.03)
        drain(0.4)
        n = self.changes - start
        self.assertGreaterEqual(n, 2)
        self.assertLessEqual(n, 5)                       # ~0.9 s / 0.3 s + leading edge


if __name__ == "__main__":
    unittest.main()
