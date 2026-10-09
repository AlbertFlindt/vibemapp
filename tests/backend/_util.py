"""Shared test helpers: GLib loop spinning and a fake Oryx firmware."""

from __future__ import annotations

import os
import socket
import threading
import time
from typing import Callable

from gi.repository import GLib


def spin(until: Callable[[], bool], timeout: float = 2.0) -> bool:
    """Iterate the default main context until ``until()`` or timeout."""
    ctx = GLib.MainContext.default()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        while ctx.pending():
            ctx.iteration(False)
        if until():
            return True
        ctx.iteration(False)
        time.sleep(0.005)
    while ctx.pending():
        ctx.iteration(False)
    return until()


def drain(duration: float = 0.05) -> None:
    spin(lambda: False, duration)


class FakeFirmware:
    """Speaks the Oryx protocol on one end of a SOCK_SEQPACKET socketpair.

    ``opener`` returns the host-side fd (as if it were /dev/hidrawN).
    Records every packet the host writes (33 bytes, report id first).
    """

    def __init__(self, serial: str = "Bre9W/rbaBD4", layer: int = 0,
                 legacy: bool = False, protocol: int | None = 5) -> None:
        self.serial = serial
        self.layer = layer
        self.legacy = legacy
        self.protocol = protocol
        self.paired = False
        self.received: list[bytes] = []
        self.host, self.kb = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        self._stop = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def opener(self, _path: str) -> int:
        return os.dup(self.host.fileno())

    def send(self, *payload: int) -> None:
        data = bytes(payload) + bytes(32 - len(payload))
        self.kb.send(data)

    def unplug(self) -> None:
        self._stop = True
        try:
            self.kb.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.kb.close()
        self.host.close()

    def set_layer_commands(self) -> list[tuple[int, int]]:
        return [(p[2], p[3]) for p in self.received if p[1] == 0x04]

    def _run(self) -> None:
        while not self._stop:
            try:
                pkt = self.kb.recv(64)
            except OSError:
                return
            if not pkt:
                return
            self.received.append(pkt)
            assert len(pkt) == 33 and pkt[0] == 0, pkt
            cmd = pkt[1]
            if cmd == 0x00:
                s = self.serial.encode()
                # Real firmware leaves stack garbage after the stop byte.
                self.send(0x00, *s, 0xFE, 0x00, 0xB7, 0xEE)
            elif cmd == 0xFE:
                if self.protocol is None:
                    self.send(0xFF, 0xFF)
                else:
                    self.send(0xFE, self.protocol, 0xFE)
            elif cmd == 0x01:
                if self.legacy:
                    self.send(0x01, 0xFE)
                else:
                    self.paired = True
                    self.send(0x04, 0xFE)
                    self.send(0x05, self.layer, 0xFE)
            elif cmd == 0x04 and self.paired:
                on, layer = pkt[2], pkt[3]
                new = layer if on else 0
                if new != self.layer:
                    self.layer = new
                    self.send(0x05, self.layer, 0xFE)
            else:
                self.send(0xFF, 0xFF)
