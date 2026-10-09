"""Hyprland focus source (IPC sockets).

* Event socket ``$XDG_RUNTIME_DIR/hypr/<sig>/.socket2.sock`` streams lines like
  ``activewindow>>kitty,nvim foo.py``. The title can contain commas, so only
  the first comma is split. ``activewindow>>,`` means nothing is focused.
* Command socket ``.socket.sock``: one request per connection. We only send
  read-only ``j/activewindow`` and ``j/clients``.
* Instance discovery: ``$HYPRLAND_INSTANCE_SIGNATURE`` first, then the newest
  live ``hypr/*/.socket2.sock`` (the env var is often missing in shells that
  weren't started by Hyprland).
* If the event socket closes (Hyprland restarted) we reconnect with
  exponential backoff and re-discover the instance.
"""

from __future__ import annotations

import glob
import json
import logging
import os
import socket

from gi.repository import GLib

from ...models import RunningApp
from .base import FocusWatcher

log = logging.getLogger(__name__)

EVENT_SOCKET = ".socket2.sock"
COMMAND_SOCKET = ".socket.sock"


def _socket_alive(path: str, timeout: float = 0.2) -> bool:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(path)
        return True
    except OSError:
        return False
    finally:
        s.close()


def find_hyprland_instance(runtime_dir: str, signature: str | None = None) -> str | None:
    """Return the instance directory containing the IPC sockets, or None."""
    roots = [os.path.join(runtime_dir, "hypr")]
    if signature:
        for root in roots:
            d = os.path.join(root, signature)
            if os.path.exists(os.path.join(d, EVENT_SOCKET)):
                return d
    candidates: list[tuple[float, str]] = []
    for root in roots:
        for sock in glob.glob(os.path.join(glob.escape(root), "*", EVENT_SOCKET)):
            d = os.path.dirname(sock)
            try:
                mtime = os.stat(d).st_mtime
            except OSError:
                continue
            candidates.append((mtime, d))
    for _mtime, d in sorted(candidates, reverse=True):
        # Probe the command socket: a probe on the event socket would show up
        # as a (short-lived) event subscriber.
        if _socket_alive(os.path.join(d, COMMAND_SOCKET)):
            return d
    return None


def parse_event_line(line: str) -> tuple[str, str] | None:
    """``"name>>data"`` -> (name, data)."""
    name, sep, data = line.partition(">>")
    if not sep:
        return None
    return name, data


def parse_activewindow(data: str) -> tuple[str, str]:
    """``activewindow`` payload ``class,title``. Split on the first comma only."""
    app_class, _sep, title = data.partition(",")
    return app_class, title


def parse_activewindow_json(text: str) -> tuple[str, str]:
    try:
        obj = json.loads(text)
    except ValueError:
        return "", ""
    if not isinstance(obj, dict):
        return "", ""
    return str(obj.get("class") or ""), str(obj.get("title") or "")


def normalize_address(addr: str) -> str | None:
    """Event payloads use bare hex (``55791bcb6d20``); JSON uses ``0x55791bcb6d20``."""
    addr = addr.strip().lower()
    if addr.startswith("0x"):
        addr = addr[2:]
    return addr or None


def parse_clients_json(text: str) -> list[RunningApp]:
    try:
        clients = json.loads(text)
    except ValueError:
        return []
    if not isinstance(clients, list):
        return []
    clients = [c for c in clients if isinstance(c, dict) and c.get("class")]
    clients.sort(key=lambda c: c.get("focusHistoryID", 1 << 30)
                 if isinstance(c.get("focusHistoryID"), int) and c.get("focusHistoryID", -1) >= 0
                 else 1 << 30)
    seen: dict[str, RunningApp] = {}
    for c in clients:
        cls = str(c["class"])
        if cls not in seen:
            seen[cls] = RunningApp(app_class=cls, title=str(c.get("title") or ""))
    return list(seen.values())


class HyprlandFocusWatcher(FocusWatcher):
    __gtype_name__ = "KmlHyprlandFocusWatcher"

    MIN_BACKOFF_MS = 250
    MAX_BACKOFF_MS = 10_000

    def __init__(self, runtime_dir: str | None = None, signature: str | None = None) -> None:
        super().__init__()
        self._runtime_dir = (runtime_dir or os.environ.get("XDG_RUNTIME_DIR")
                             or f"/run/user/{os.getuid()}")
        self._signature = signature if signature is not None else \
            os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
        self._instance_dir: str | None = None
        self._sock: socket.socket | None = None
        self._watch_id = 0
        self._retry_id = 0
        self._requery_id = 0
        self._backoff_ms = self.MIN_BACKOFF_MS
        self._buffer = b""
        self._current: tuple[str, str] | None = None
        self._active_address: str | None = None
        self._available = False
        self._started = False

    # -- public --

    def available(self) -> bool:
        return self._available

    def current(self) -> tuple[str, str] | None:
        return self._current

    @property
    def instance_dir(self) -> str | None:
        return self._instance_dir

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._connect()

    def stop(self) -> None:
        self._started = False
        for attr in ("_retry_id", "_requery_id"):
            sid = getattr(self, attr)
            if sid:
                GLib.source_remove(sid)
                setattr(self, attr, 0)
        self._drop_socket()
        self._set_available(False)

    def query(self, command: str, timeout: float = 0.5) -> str | None:
        """Send a read-only command (``j/...``) to the command socket."""
        if not command.startswith("j/"):
            raise ValueError("only read-only j/ queries are allowed")
        d = self._instance_dir or find_hyprland_instance(self._runtime_dir, self._signature)
        if d is None:
            return None
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            s.connect(os.path.join(d, COMMAND_SOCKET))
            s.sendall(command.encode())
            chunks = []
            while True:
                chunk = s.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
            return b"".join(chunks).decode("utf-8", errors="replace")
        except OSError as exc:
            log.debug("hyprland query %s failed: %s", command, exc)
            return None
        finally:
            s.close()

    def list_running_apps(self) -> list[RunningApp]:
        text = self.query("j/clients")
        return parse_clients_json(text) if text else []

    # -- connection --

    def _connect(self) -> None:
        self._retry_id = 0
        if not self._started:
            return
        d = find_hyprland_instance(self._runtime_dir, self._signature)
        if d is None:
            self._schedule_retry()
            return
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.settimeout(0.5)
            s.connect(os.path.join(d, EVENT_SOCKET))
            s.setblocking(False)
        except OSError as exc:
            s.close()
            log.debug("hyprland connect failed: %s", exc)
            # A stale env signature shouldn't block discovery forever.
            self._signature = None
            self._schedule_retry()
            return
        self._instance_dir = d
        self._sock = s
        self._buffer = b""
        self._backoff_ms = self.MIN_BACKOFF_MS
        self._watch_id = GLib.unix_fd_add_full(
            GLib.PRIORITY_DEFAULT, s.fileno(),
            GLib.IOCondition.IN | GLib.IOCondition.HUP | GLib.IOCondition.ERR,
            self._on_io)
        self._set_available(True)
        self._refresh_active()

    def _schedule_retry(self) -> None:
        if not self._started or self._retry_id:
            return
        delay = self._backoff_ms
        self._backoff_ms = min(self._backoff_ms * 2, self.MAX_BACKOFF_MS)
        self._retry_id = GLib.timeout_add(delay, self._on_retry)

    def _on_retry(self) -> bool:
        self._retry_id = 0
        self._connect()
        return GLib.SOURCE_REMOVE

    def _drop_socket(self) -> None:
        if self._watch_id:
            GLib.source_remove(self._watch_id)
            self._watch_id = 0
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def _lost(self) -> None:
        log.info("Hyprland event socket closed; reconnecting")
        self._watch_id = 0      # the source is being removed by returning False
        if self._sock is not None:
            self._sock.close()
            self._sock = None
        self._instance_dir = None
        self._set_available(False)
        self._schedule_retry()

    def _set_available(self, value: bool) -> None:
        if value != self._available:
            self._available = value
            self.emit("availability-changed", value)

    def _on_io(self, _fd: int, condition: GLib.IOCondition) -> bool:
        if self._sock is None:
            return GLib.SOURCE_REMOVE
        if condition & GLib.IOCondition.IN:
            try:
                data = self._sock.recv(65536)
            except BlockingIOError:
                return GLib.SOURCE_CONTINUE
            except OSError:
                data = b""
            if data:
                self._feed(data)
                return GLib.SOURCE_CONTINUE
            self._lost()
            return GLib.SOURCE_REMOVE
        if condition & (GLib.IOCondition.HUP | GLib.IOCondition.ERR):
            self._lost()
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    # -- events --

    def _feed(self, data: bytes) -> None:
        self._buffer += data
        *lines, self._buffer = self._buffer.split(b"\n")
        for raw in lines:
            self.handle_line(raw.decode("utf-8", errors="replace"))

    def handle_line(self, line: str) -> None:
        parsed = parse_event_line(line)
        if parsed is None:
            return
        name, data = parsed
        if name == "activewindow":
            self._set_focus(*parse_activewindow(data))
        elif name == "activewindowv2":
            self._active_address = normalize_address(data.strip().strip(","))
        elif name == "windowtitlev2":
            addr, _sep, title = data.partition(",")
            if self._current and self._active_address and \
                    normalize_address(addr) == self._active_address:
                self._set_focus(self._current[0], title)
        elif name == "closewindow":
            if self._active_address and normalize_address(data) == self._active_address:
                # Hyprland normally follows up with activewindow; re-query to be safe.
                self._schedule_requery()

    def _schedule_requery(self) -> None:
        if self._requery_id:
            return

        def run() -> bool:
            self._requery_id = 0
            self._refresh_active()
            return GLib.SOURCE_REMOVE
        self._requery_id = GLib.timeout_add(150, run)

    def _refresh_active(self) -> None:
        text = self.query("j/activewindow")
        if text is None:
            return
        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                self._active_address = normalize_address(str(obj.get("address") or ""))
        except ValueError:
            pass
        self._set_focus(*parse_activewindow_json(text))

    def _set_focus(self, app_class: str, title: str) -> None:
        value = (app_class, title)
        if value == self._current:
            return
        self._current = value
        self.emit("focus-changed", app_class, title)
