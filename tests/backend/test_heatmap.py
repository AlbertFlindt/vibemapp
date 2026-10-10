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
from vibemapp.models import HeatmapPeriod

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
        self.assertEqual(data, {"version": 1, "layout_id": "Bre9W",
                                "days": {"2026-10-10": {"0": {"1": 1}, "2": {"3": 1}}}})
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


class ServiceHeatmapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        make_node(root, 10, "5-2.2", 1, 0x3297, 0x1977, RAW_DESC)
        self.fw = FakeFirmware()
        self.clock = Clock(dt.date(2026, 10, 10))
        self.hdir = os.path.join(root, "heatmap")
        self.changes = 0
        self.svc = self._make()

    def _make(self) -> ZsaKeyboardService:
        root = self.tmp.name
        svc = ZsaKeyboardService(
            sysfs_root=os.path.join(root, "sys"), dev_root=os.path.join(root, "dev"),
            rules_path=os.path.join(root, "rules.json"), focus_watcher=FakeWatcher(),
            repository=LayoutRepository(LayoutCache(os.path.join(root, "c")),
                                        fetcher=lambda *a: SAMPLE),
            connection_factory=lambda p, e, c: OryxConnection(p, e, c, opener=self.fw.opener),
            poll_interval_ms=0, heatmap_dir=self.hdir, clock=self.clock)
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
