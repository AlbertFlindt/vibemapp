from __future__ import annotations

import errno
import os
import tempfile
import threading
import unittest

from vibemapp.appid import DEVEL_APP_ID
from vibemapp.backend.focus.base import FocusWatcher
from vibemapp.backend.oryx import OryxConnection, OryxError
from vibemapp.backend.oryx_api import LayoutCache, LayoutRepository
from vibemapp.backend.smart_layers import APP_ID
from vibemapp.backend.zsa_service import ZsaKeyboardService
from vibemapp.models import AppRule, RunningApp

from ._util import FakeFirmware, drain, spin
from .test_devices import KBD_DESC, RAW_DESC, make_node
from .test_oryx_api import SAMPLE


class FakeWatcher(FocusWatcher):
    __gtype_name__ = "KmlTestFakeWatcher"

    def __init__(self) -> None:
        super().__init__()
        self._current: tuple[str, str] | None = ("kitty", "shell")

    def available(self) -> bool:
        return True

    def current(self):
        return self._current

    def list_running_apps(self):
        return [RunningApp("kitty", "shell")]

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def focus(self, cls: str, title: str) -> None:
        self._current = (cls, title)
        self.emit("focus-changed", cls, title)


class ServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        make_node(root, 9, "5-2.2", 0, 0x3297, 0x1977, KBD_DESC)
        make_node(root, 10, "5-2.2", 1, 0x3297, 0x1977, RAW_DESC)
        self.root = root
        self.fw = FakeFirmware(layer=0)
        self.watcher = FakeWatcher()
        self.main = threading.current_thread()
        self.signals: list[tuple] = []
        self.off_main: list[str] = []

        def factory(path, on_event, on_closed):
            return OryxConnection(path, on_event, on_closed, opener=self.fw.opener)

        repo = LayoutRepository(LayoutCache(os.path.join(root, "cache")),
                                fetcher=lambda lid, rev, geo: SAMPLE)
        self.svc = ZsaKeyboardService(
            sysfs_root=os.path.join(root, "sys"), dev_root=os.path.join(root, "dev"),
            rules_path=os.path.join(root, "config", "smart-layers.json"),
            focus_watcher=self.watcher, repository=repo, connection_factory=factory,
            poll_interval_ms=0, heatmap_dir=os.path.join(self.tmp.name, "hm"))
        for name in ("devices-changed", "connected", "disconnected", "layout-loaded",
                     "layer-changed", "key-event", "focus-changed", "rule-applied",
                     "rules-changed", "error"):
            self.svc.connect(name, self._recorder(name))

    def _recorder(self, name: str):
        def record(_svc, *args):
            if threading.current_thread() is not self.main:
                self.off_main.append(name)
            self.signals.append((name, *args))
        return record

    def names(self) -> list[str]:
        return [s[0] for s in self.signals]

    def tearDown(self) -> None:
        self.svc.shutdown()
        drain(0.05)
        self.fw.unplug()
        self.tmp.cleanup()

    def wait_connected(self) -> None:
        self.assertTrue(spin(lambda: "connected" in self.names()), self.signals)
        self.assertTrue(spin(lambda: "layout-loaded" in self.names()), self.signals)

    def test_auto_connect_layout_and_events(self) -> None:
        self.wait_connected()
        dev = self.svc.get_connected_device()
        self.assertEqual((dev.layout_id, dev.revision_id), ("Bre9W", "rbaBD4"))
        self.assertTrue(dev.oryx_available)
        self.assertEqual(self.svc.get_layout().title, "Flindt 1.1")
        self.assertEqual(self.svc.list_devices()[0], dev)
        # key events are mapped from (row, col) to Oryx index
        self.fw.send(0x06, 0, 5, 0xFE)        # col 0, row 5 -> left thumb, index 24
        self.fw.send(0x07, 0, 5, 0xFE)
        self.assertTrue(spin(lambda: ("key-event", 24, False) in self.signals))
        self.assertIn(("key-event", 24, True), self.signals)
        self.fw.send(0x05, 2, 0xFE)
        self.assertTrue(spin(lambda: self.svc.get_active_layer() == 2))
        self.assertIn(("layer-changed", 2), self.signals)
        self.assertEqual(self.off_main, [])

        done = []
        self.svc.load_layout_async(dev, None, lambda l, e: done.append((l, e)))
        self.assertTrue(spin(lambda: bool(done)))
        self.assertEqual(done[0][0].id, "Bre9W")

    def test_smart_layers_flow(self) -> None:
        self.wait_connected()
        self.svc.set_rules([AppRule("firefox", "Firefox", 1)])
        self.svc.set_fallback_layer(0)
        self.assertEqual(self.svc.get_fallback_layer(), 0)
        self.svc.props.smart_layers_enabled = True     # evaluates kitty -> fallback 0
        self.assertTrue(spin(lambda: ("rule-applied", "", 0) in self.signals))
        self.watcher.focus("firefox", "page")
        self.assertTrue(spin(lambda: ("rule-applied", "firefox", 1) in self.signals))
        self.assertTrue(spin(lambda: self.svc.get_active_layer() == 1))
        # Our own window: no focus-changed, no layer change
        n = len(self.signals)
        self.watcher.focus(APP_ID, "Vibemapp")
        drain(0.1)
        self.watcher.focus(DEVEL_APP_ID, "Vibemapp (Development)")
        drain(0.1)
        self.assertEqual(self.signals[n:], [])
        self.assertEqual(self.svc.get_focused_app(), ("firefox", "page"))
        # Shutdown returns the keyboard to base since Smart Layers moved it.
        self.svc.shutdown()
        self.assertTrue(spin(lambda: self.fw.set_layer_commands()[-1:] == [(1, 0)]))
        self.assertEqual(self.fw.set_layer_commands(), [(1, 0), (1, 1), (1, 0)])
        self.assertEqual(self.off_main, [])

    def test_request_layer_validation_and_unplug(self) -> None:
        self.wait_connected()
        self.svc.request_layer(7)
        self.assertIn("error", self.names())
        self.svc.request_layer(1)
        self.assertTrue(spin(lambda: self.svc.get_active_layer() == 1))
        self.fw.unplug()
        self.assertTrue(spin(lambda: "disconnected" in self.names()))
        self.assertIsNone(self.svc.get_connected_device())
        self.assertEqual(self.svc.get_rules(), [])

    def test_manual_disconnect(self) -> None:
        self.wait_connected()
        self.svc.disconnect_device()
        self.assertEqual(self.names()[-1], "disconnected")
        self.assertEqual(self.svc.list_running_apps(), [RunningApp("kitty", "shell")])
        self.assertTrue(self.svc.focus_source_available())


if __name__ == "__main__":
    unittest.main()


class FailingConnection:
    """Connection whose open() always fails with a given errno."""

    attempts = 0

    def __init__(self, err_no: int) -> None:
        self.err_no = err_no

    def open(self) -> None:
        FailingConnection.attempts += 1
        raise OryxError(os.strerror(self.err_no), self.err_no)

    def handshake(self):
        raise AssertionError("unreachable")

    def close(self) -> None:
        pass


class FailureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        make_node(root, 10, "5-2.2", 1, 0x3297, 0x1977, RAW_DESC)
        self.root = root
        self.watcher = FakeWatcher()
        self.signals: list[tuple] = []
        FailingConnection.attempts = 0

    def tearDown(self) -> None:
        self.svc.shutdown()
        drain(0.05)
        self.tmp.cleanup()

    def make(self, err_no: int) -> ZsaKeyboardService:
        self.svc = ZsaKeyboardService(
            sysfs_root=os.path.join(self.root, "sys"), dev_root=os.path.join(self.root, "dev"),
            rules_path=os.path.join(self.root, "rules.json"),
            focus_watcher=self.watcher,
            repository=LayoutRepository(LayoutCache(os.path.join(self.root, "c")),
                                        fetcher=lambda *a: SAMPLE),
            connection_factory=lambda *a: FailingConnection(err_no),
            poll_interval_ms=0, autostart=False,
            heatmap_dir=os.path.join(self.tmp.name, "hm"))
        for name in ("devices-changed", "error", "focus-source-changed", "connected"):
            self.svc.connect(name, lambda _s, *a, n=name: self.signals.append((n, *a)))
        return self.svc

    def test_focus_source_changed_relayed_on_main_loop(self) -> None:
        svc = self.make(errno.EIO)
        main = threading.current_thread()
        seen = []
        svc.connect("focus-source-changed",
                    lambda _s, a: seen.append((a, threading.current_thread() is main)))
        self.watcher.emit("availability-changed", False)
        self.watcher.emit("availability-changed", True)
        self.assertEqual(seen, [(False, True), (True, True)])

    def test_eacces_marks_access_denied_without_retry_loop(self) -> None:
        svc = self.make(errno.EACCES)
        svc.start()
        self.assertTrue(spin(lambda: any(s[0] == "error" for s in self.signals)))
        self.assertTrue(spin(lambda: svc.list_devices()[0].access_denied))
        dev = svc.list_devices()[0]
        self.assertFalse(dev.oryx_available)
        self.assertIn("udev", [s for s in self.signals if s[0] == "error"][0][1])
        self.assertIn("devices-changed", [s[0] for s in self.signals])
        drain(0.2)
        self.assertEqual(FailingConnection.attempts, 1)
        self.assertEqual(svc._retry_id, 0)

    def test_backoff_grows_caps_and_resets_on_hotplug(self) -> None:
        svc = self.make(errno.EIO)
        self.assertEqual([svc.backoff_delay_ms(n) for n in range(1, 8)],
                         [1000, 2000, 4000, 8000, 16000, 30000, 30000])
        svc.BACKOFF_BASE_MS = 40
        svc.BACKOFF_MAX_MS = 80
        svc.start()
        self.assertTrue(spin(lambda: FailingConnection.attempts >= 1))
        drain(0.02)
        self.assertEqual(svc._failures, 1)
        self.assertNotEqual(svc._retry_id, 0)
        # Attempts are spaced by the backoff, not by every poll/idle.
        svc._maybe_auto_connect()
        self.assertEqual(FailingConnection.attempts, 1)
        self.assertTrue(spin(lambda: FailingConnection.attempts >= 3, timeout=2.0))
        self.assertGreaterEqual(svc._failures, 3)
        self.assertEqual(svc.backoff_delay_ms(svc._failures), 80)   # capped
        # Hotplug resets the counter and retries immediately.
        before = FailingConnection.attempts
        svc._on_devices_changed(svc._monitor)
        self.assertTrue(spin(lambda: FailingConnection.attempts == before + 1, timeout=0.5))
        drain(0.02)
        self.assertEqual(svc._failures, 1)


class AutoConnectPropertyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        make_node(root, 10, "5-2.2", 1, 0x3297, 0x1977, RAW_DESC)
        self.fw = FakeFirmware()
        self.signals: list[str] = []
        self.svc = ZsaKeyboardService(
            sysfs_root=os.path.join(root, "sys"), dev_root=os.path.join(root, "dev"),
            rules_path=os.path.join(root, "rules.json"), focus_watcher=FakeWatcher(),
            repository=LayoutRepository(LayoutCache(os.path.join(root, "c")),
                                        fetcher=lambda *a: SAMPLE),
            connection_factory=lambda p, e, c: OryxConnection(p, e, c, opener=self.fw.opener),
            poll_interval_ms=0, auto_connect=False,
            heatmap_dir=os.path.join(self.tmp.name, "hm"))
        for name in ("connected", "disconnected"):
            self.svc.connect(name, lambda *_a, n=name: self.signals.append(n))

    def tearDown(self) -> None:
        self.svc.shutdown()
        drain(0.05)
        self.fw.unplug()
        self.tmp.cleanup()

    def test_kwarg_alias_and_live_toggle(self) -> None:
        self.assertFalse(self.svc.props.auto_connect)     # kwarg sets the property
        drain(0.2)
        self.assertEqual(self.signals, [])
        self.svc.props.auto_connect = True                 # device present, nothing connected
        self.assertTrue(spin(lambda: self.signals == ["connected"]))
        # Turning it off keeps the live connection; hotplug no longer reconnects.
        self.svc.props.auto_connect = False
        self.svc.disconnect_device()
        self.svc._on_devices_changed(self.svc._monitor)
        drain(0.2)
        self.assertEqual(self.signals, ["connected", "disconnected"])
        self.assertIsNone(self.svc.get_connected_device())
        # A manual connect still works with auto-connect off.
        self.svc.connect_device(self.svc.list_devices()[0].id)
        self.assertTrue(spin(lambda: self.signals[-1] == "connected"))


class SteamFocusTests(unittest.TestCase):
    def setUp(self) -> None:
        from vibemapp.backend.steam import SteamLibrary  # noqa: F401
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        make_node(root, 10, "5-2.2", 1, 0x3297, 0x1977, RAW_DESC)
        steam = os.path.join(root, "Steam", "steamapps")
        os.makedirs(steam)
        with open(os.path.join(steam, "appmanifest_2694490.acf"), "w") as f:
            f.write('"AppState" { "appid" "2694490" "name" "Path of Exile 2" }')
        proc = os.path.join(root, "proc", "4242")
        os.makedirs(proc)
        with open(os.path.join(proc, "environ"), "wb") as f:
            f.write(b"SteamAppId=2694490\0")
        self.fw = FakeFirmware()
        self.watcher = PidWatcher()
        self.svc = ZsaKeyboardService(
            sysfs_root=os.path.join(root, "sys"), dev_root=os.path.join(root, "dev"),
            rules_path=os.path.join(root, "rules.json"), focus_watcher=self.watcher,
            repository=LayoutRepository(LayoutCache(os.path.join(root, "c")),
                                        fetcher=lambda *a: SAMPLE),
            connection_factory=lambda p, e, c: OryxConnection(p, e, c, opener=self.fw.opener),
            poll_interval_ms=0, heatmap_dir=os.path.join(root, "hm"),
            steam_roots=[os.path.join(root, "Steam")], proc_root=os.path.join(root, "proc"))
        self.seen: list = []
        self.svc.connect("focus-changed", lambda s, c, t: self.seen.append(s.get_focused_window()))
        self.applied: list = []
        self.svc.connect("rule-applied", lambda _s, c, l: self.applied.append((c, l)))

    def tearDown(self) -> None:
        self.svc.shutdown()
        drain(0.05)
        self.fw.unplug()
        self.tmp.cleanup()

    def test_focused_window_and_steam_rule(self) -> None:
        from vibemapp.models import FocusedWindow, RuleMatch, SteamGame
        self.assertEqual(self.svc.list_steam_games(), [SteamGame("2694490", "Path of Exile 2")])
        self.assertTrue(spin(lambda: self.svc.get_connected_device() is not None))
        self.svc.set_rules([AppRule("", "Any Steam game", 1, match=RuleMatch.STEAM_GAME)])
        self.svc.set_fallback_layer(0)
        self.svc.props.smart_layers_enabled = True
        self.watcher.focus_pid("PathOfExile", "PoE2", 4242)       # native game via environ
        self.assertEqual(self.seen[-1], FocusedWindow("PathOfExile", "PoE2", 4242,
                                                      "2694490", "Path of Exile 2"))
        self.assertTrue(spin(lambda: ("", 1) in self.applied))
        self.watcher.focus_pid("steam", "Steam", 4242)            # client is never a game
        self.assertIsNone(self.seen[-1].steam_app_id)
        self.assertTrue(spin(lambda: self.applied[-1] == ("", 0)))


class PidWatcher(FakeWatcher):
    __gtype_name__ = "VmTestPidWatcher"

    def __init__(self) -> None:
        super().__init__()
        self._pid: int | None = None

    def current_pid(self):
        return self._pid

    def focus_pid(self, cls: str, title: str, pid: int | None) -> None:
        self._pid = pid
        self.focus(cls, title)
