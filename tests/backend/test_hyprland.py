from __future__ import annotations

import json
import os
import socket
import tempfile
import threading
import unittest

from vibemapp.backend.focus import NullFocusWatcher, create_focus_watcher
from vibemapp.backend.focus.hyprland import (HyprlandFocusWatcher,
                                                    find_hyprland_instance,
                                                    parse_activewindow,
                                                    parse_activewindow_json,
                                                    parse_clients_json, parse_event_line)

from ._util import drain, spin

CLIENTS = [
    {"address": "0x1", "class": "firefox", "title": "Tab, with, commas", "focusHistoryID": 1},
    {"address": "0x2", "class": "kitty", "title": "nvim", "focusHistoryID": 0},
    {"address": "0x3", "class": "kitty", "title": "htop", "focusHistoryID": 2},
    {"address": "0x4", "class": "", "title": "ghost", "focusHistoryID": 3},
]


class FakeHyprland:
    """Serves .socket.sock (j/ queries) and .socket2.sock (event stream)."""

    def __init__(self, runtime: str, sig: str = "abc_123_456",
                 active: dict | None = None) -> None:
        self.dir = os.path.join(runtime, "hypr", sig)
        os.makedirs(self.dir, exist_ok=True)
        self.active = active if active is not None else {
            "address": "0x2", "class": "kitty", "title": "nvim, a, b"}
        self.queries: list[str] = []
        self.event_clients: list[socket.socket] = []
        self._cmd = self._listen(".socket.sock")
        self._evt = self._listen(".socket2.sock")
        self._stop = False
        threading.Thread(target=self._serve_cmd, daemon=True).start()
        threading.Thread(target=self._serve_evt, daemon=True).start()

    def _listen(self, name: str) -> socket.socket:
        path = os.path.join(self.dir, name)
        if os.path.exists(path):
            os.unlink(path)
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(path)
        s.listen(8)
        return s

    def _serve_cmd(self) -> None:
        while not self._stop:
            try:
                c, _ = self._cmd.accept()
            except OSError:
                return
            with c:
                try:
                    q = c.recv(1024).decode()
                    if not q:          # liveness probe: connect + close
                        continue
                    self.queries.append(q)
                    if q == "j/activewindow":
                        c.sendall(json.dumps(self.active).encode())
                    elif q == "j/clients":
                        c.sendall(json.dumps(CLIENTS).encode())
                    else:
                        c.sendall(b"unknown request")
                except OSError:
                    continue

    def _serve_evt(self) -> None:
        while not self._stop:
            try:
                c, _ = self._evt.accept()
            except OSError:
                return
            self.event_clients.append(c)

    def emit(self, *lines: str, split_at: int | None = None) -> None:
        data = "".join(l + "\n" for l in lines).encode()
        for c in list(self.event_clients):
            if split_at is None:
                c.sendall(data)
            else:
                c.sendall(data[:split_at])
                c.sendall(data[split_at:])

    def shutdown(self) -> None:
        self._stop = True
        for s in (self._cmd, self._evt, *self.event_clients):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            s.close()
        for name in (".socket.sock", ".socket2.sock"):
            try:
                os.unlink(os.path.join(self.dir, name))
            except OSError:
                pass


class ParseTests(unittest.TestCase):
    def test_event_line(self) -> None:
        self.assertEqual(parse_event_line("activewindow>>kitty,a>>b"), ("activewindow", "kitty,a>>b"))
        self.assertIsNone(parse_event_line("garbage"))

    def test_activewindow_first_comma(self) -> None:
        self.assertEqual(parse_activewindow("firefox,Hello, world, again"),
                         ("firefox", "Hello, world, again"))
        self.assertEqual(parse_activewindow(","), ("", ""))
        self.assertEqual(parse_activewindow(""), ("", ""))

    def test_json(self) -> None:
        self.assertEqual(parse_activewindow_json("{}"), ("", ""))
        self.assertEqual(parse_activewindow_json('{"class":"a","title":"b"}'), ("a", "b"))
        self.assertEqual(parse_activewindow_json("nope"), ("", ""))
        apps = parse_clients_json(json.dumps(CLIENTS))
        self.assertEqual([(a.app_class, a.title) for a in apps],
                         [("kitty", "nvim"), ("firefox", "Tab, with, commas")])


class WatcherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.runtime = self.tmp.name
        self.server = FakeHyprland(self.runtime)
        self.watcher: HyprlandFocusWatcher | None = None
        self.focus: list[tuple[str, str]] = []
        self.avail: list[bool] = []

    def tearDown(self) -> None:
        if self.watcher is not None:
            self.watcher.stop()
        self.server.shutdown()
        drain(0.02)
        self.tmp.cleanup()

    def _start(self, signature: str | None = None) -> HyprlandFocusWatcher:
        w = HyprlandFocusWatcher(runtime_dir=self.runtime, signature=signature)
        w.connect("focus-changed", lambda _w, c, t: self.focus.append((c, t)))
        w.connect("availability-changed", lambda _w, a: self.avail.append(a))
        w.start()
        self.watcher = w
        return w

    def test_discovery_without_env(self) -> None:
        self.assertEqual(find_hyprland_instance(self.runtime, None), self.server.dir)
        self.assertEqual(find_hyprland_instance(self.runtime, "stale-sig"), self.server.dir)
        self.assertIsNone(find_hyprland_instance(os.path.join(self.runtime, "nope")))

    def test_initial_state_and_events(self) -> None:
        w = self._start()
        self.assertTrue(w.available())
        self.assertEqual(self.focus, [("kitty", "nvim, a, b")])
        self.assertTrue(spin(lambda: len(self.server.event_clients) == 1))
        self.server.emit("workspace>>2", "activewindow>>firefox,YouTube, the, video",
                         "activewindowv2>>1", split_at=30)
        self.assertTrue(spin(lambda: len(self.focus) == 2))
        self.assertEqual(self.focus[-1], ("firefox", "YouTube, the, video"))
        # duplicate is dropped, title change on the active window is forwarded
        self.server.emit("activewindow>>firefox,YouTube, the, video",
                         "windowtitlev2>>1,New title", "windowtitlev2>>99,Other window")
        self.assertTrue(spin(lambda: len(self.focus) == 3))
        drain(0.05)
        self.assertEqual(self.focus[-1], ("firefox", "New title"))
        self.assertEqual(len(self.focus), 3)
        self.server.emit("activewindow>>,")
        self.assertTrue(spin(lambda: self.focus[-1] == ("", "")))
        apps = w.list_running_apps()
        self.assertEqual(apps[0].app_class, "kitty")
        self.assertTrue(all(q.startswith("j/") for q in self.server.queries))

    def test_reconnect_after_restart(self) -> None:
        w = self._start(signature="abc_123_456")
        self.assertTrue(spin(lambda: len(self.server.event_clients) == 1))
        self.server.shutdown()
        self.assertTrue(spin(lambda: self.avail == [True, False]))
        self.assertFalse(w.available())
        # Hyprland comes back under a new signature with a different window.
        self.server = FakeHyprland(self.runtime, sig="new_999_1",
                                   active={"address": "0x9", "class": "gimp", "title": "img"})
        self.assertTrue(spin(lambda: w.available(), timeout=3.0))
        self.assertTrue(spin(lambda: self.focus[-1] == ("gimp", "img")))
        self.assertTrue(spin(lambda: len(self.server.event_clients) == 1))
        self.server.emit("activewindow>>kitty,shell")
        self.assertTrue(spin(lambda: self.focus[-1] == ("kitty", "shell")))

    def test_query_refuses_dispatch(self) -> None:
        w = HyprlandFocusWatcher(runtime_dir=self.runtime)
        with self.assertRaises(ValueError):
            w.query("dispatch exec rm -rf ~")

    def test_factory(self) -> None:
        self.assertIsInstance(create_focus_watcher(self.runtime, environ={}), HyprlandFocusWatcher)
        empty = os.path.join(self.runtime, "empty")
        os.makedirs(empty)
        self.assertIsInstance(create_focus_watcher(empty, environ={}), NullFocusWatcher)


if __name__ == "__main__":
    unittest.main()


class PidTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.server = FakeHyprland(self.tmp.name, active={
            "address": "0x2", "class": "kitty", "title": "nvim", "pid": 1234})
        self.watcher = HyprlandFocusWatcher(runtime_dir=self.tmp.name)
        self.seen: list[tuple] = []
        self.watcher.connect("focus-changed", lambda w, c, t: self.seen.append(
            (c, t, w.current_pid())))
        self.watcher.start()

    def tearDown(self) -> None:
        self.watcher.stop()
        self.server.shutdown()
        drain(0.02)
        self.tmp.cleanup()

    def test_pid_resolved_before_emit(self) -> None:
        self.assertEqual(self.seen, [("kitty", "nvim", 1234)])
        self.assertTrue(spin(lambda: len(self.server.event_clients) == 1))
        self.server.active = {"address": "0x9", "class": "steam_app_1", "title": "Game",
                              "pid": 777}
        self.server.emit("activewindow>>steam_app_1,Game", "activewindowv2>>9")
        self.assertTrue(spin(lambda: self.seen[-1] == ("steam_app_1", "Game", 777)))
        # Hyprland already moved on (class mismatch): pid unknown rather than wrong.
        self.server.emit("activewindow>>firefox,Docs")
        self.assertTrue(spin(lambda: self.seen[-1] == ("firefox", "Docs", None)))
        # title change on the active window keeps the pid
        self.server.active = {"address": "0x9", "class": "steam_app_1", "title": "x", "pid": 777}
        self.server.emit("activewindow>>steam_app_1,Game")
        self.assertTrue(spin(lambda: self.seen[-1] == ("steam_app_1", "Game", 777)))
        self.server.emit("windowtitlev2>>9,Menu")
        self.assertTrue(spin(lambda: self.seen[-1] == ("steam_app_1", "Menu", 777)))
