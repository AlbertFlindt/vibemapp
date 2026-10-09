"""Oryx raw-HID protocol and a hidraw connection.

Source of truth (fetched and read for this implementation):
  https://raw.githubusercontent.com/zsa/qmk_modules/13890cd7856175de20798689d15ba6a46bf0c5c7/oryx/oryx.h
  https://raw.githubusercontent.com/zsa/qmk_modules/13890cd7856175de20798689d15ba6a46bf0c5c7/oryx/oryx.c
  https://raw.githubusercontent.com/zsa/qmk_modules/13890cd7856175de20798689d15ba6a46bf0c5c7/oryx/config.h
That is the ``modules/zsa`` submodule pinned by zsa/qmk_firmware branch
``firmware25`` (commit 93b2b9ec3368f86c5eb5a2e3f934049f8daef885). In that
branch Oryx moved out of ``keyboards/zsa/common/oryx.[ch]``. ORYX_PROTOCOL_VERSION
is 0x05, which matches what a live Voyager reports.

Protocol facts (verified in the source, and live where noted):
* Reports are RAW_EPSIZE = 32 bytes. The raw-HID interface is Usage Page 0xFF60 /
  Usage 0x61 and has no report IDs, so a hidraw write is ``0x00`` (report
  number) followed by 32 payload bytes. Reads return the 32 bytes without a
  report-ID prefix. (Verified live.)
* Byte 0 is the command (host to kb) or event (kb to host). Params follow.
  Responses end with ORYX_STOP_BIT (-2 == 0xFE). Bytes after the stop byte
  are uninitialised firmware stack and must be ignored. (Seen live.)
* Commands: 0x00 GET_FW_VERSION, 0x01 PAIRING_INIT, 0x02 PAIRING_VALIDATE,
  0x03 DISCONNECT, 0x04 SET_LAYER, 0x05 RGB_CONTROL, 0x06 SET_RGB_LED,
  0x07 SET_STATUS_LED, 0x08 UPDATE_BRIGHTNESS, 0x09 SET_RGB_LED_ALL,
  0x0A STATUS_LED_CONTROL, 0x0B SET_AUTOMOUSE, 0x0C GET_AUTOMOUSE,
  0xFE GET_PROTOCOL_VERSION.
* Events: 0x00 FW_VERSION, 0x01 PAIRING_INPUT, 0x02 PAIRING_KEY_INPUT,
  0x03 PAIRING_FAILED, 0x04 PAIRING_SUCCESS, 0x05 LAYER, 0x06 KEYDOWN,
  0x07 KEYUP, 0x08 RGB_CONTROL, 0x09 TOGGLE_SMART_LAYER,
  0x0A TRIGGER_SMART_LAYER, 0x0B STATUS_LED_CONTROL, 0x0C AUTOMOUSE,
  0xFE PROTOCOL_VERSION, 0xFF ERROR (byte 1 = error code).
* GET_FW_VERSION works without pairing. It returns the USB SERIAL_NUMBER
  string, which Oryx builds set to ``"<layoutHashId>/<revisionHashId>"``
  (live: ``Bre9W/rbaBD4``).
* Handshake: on current firmware PAIRING_INIT marks the host paired straight
  away and replies PAIRING_SUCCESS followed by a LAYER event. Pairing is a RAM
  flag (``rawhid_state.paired``). Nothing is written to EEPROM.
  PAIRING_VALIDATE is a no-op kept for old Wally/Keymapp. Firmware from before
  the simplified handshake answers PAIRING_INIT with PAIRING_INPUT and wants
  keypresses. We treat that as "Oryx not available".
* SET_LAYER [0x04, on, layer] only works while paired. ``on != 0`` calls
  ``layer_move(layer)``, which turns off all other layers and turns on
  ``layer``. ``on == 0`` calls ``layer_off(layer)``. Both are RAM-only layer
  state. Returning to base means SET_LAYER(1, 0) (layer_move(0)).
* LAYER event [0x05, highest(layer_state | default_layer_state), 0xFE] is
  sent while paired whenever the highest layer changes.
* KEYDOWN/KEYUP [0x06/0x07, col, row, 0xFE] is sent while paired. Note the
  order: col first, then row.
* DISCONNECT (0x03) is in the enum but has no handler (it gets an ERROR
  "unknown command" reply). The keyboard keeps its current layer when the
  host goes away. ``paired`` is cleared only when a send to the host fails
  (ChibiOS).

Safety: this module refuses to encode anything except GET_FW_VERSION,
PAIRING_INIT, SET_LAYER and GET_PROTOCOL_VERSION. Nothing that touches LEDs,
EEPROM or the bootloader can be sent.
"""

from __future__ import annotations

import errno
import logging
import os
import queue
import select
import threading
import time
from dataclasses import dataclass
from enum import IntEnum
from typing import Callable

log = logging.getLogger(__name__)

REPORT_SIZE = 32
REPORT_ID = 0x00
STOP_BYTE = 0xFE
PROTOCOL_VERSION = 0x05


class Cmd(IntEnum):
    GET_FW_VERSION = 0x00
    PAIRING_INIT = 0x01
    PAIRING_VALIDATE = 0x02
    DISCONNECT = 0x03
    SET_LAYER = 0x04
    RGB_CONTROL = 0x05
    SET_RGB_LED = 0x06
    SET_STATUS_LED = 0x07
    UPDATE_BRIGHTNESS = 0x08
    SET_RGB_LED_ALL = 0x09
    STATUS_LED_CONTROL = 0x0A
    SET_AUTOMOUSE = 0x0B
    GET_AUTOMOUSE = 0x0C
    GET_PROTOCOL_VERSION = 0xFE


class Evt(IntEnum):
    FW_VERSION = 0x00
    PAIRING_INPUT = 0x01
    PAIRING_KEY_INPUT = 0x02
    PAIRING_FAILED = 0x03
    PAIRING_SUCCESS = 0x04
    LAYER = 0x05
    KEYDOWN = 0x06
    KEYUP = 0x07
    RGB_CONTROL = 0x08
    TOGGLE_SMART_LAYER = 0x09
    TRIGGER_SMART_LAYER = 0x0A
    STATUS_LED_CONTROL = 0x0B
    AUTOMOUSE = 0x0C
    PROTOCOL_VERSION = 0xFE
    ERROR = 0xFF


class Err(IntEnum):
    PAIRING_INIT_FAILED = 0x00
    PAIRING_INPUT_FAILED = 0x01
    PAIRING_KEY_INPUT_FAILED = 0x02
    PAIRING_FAILED = 0x03
    RGB_MATRIX_NOT_ENABLED = 0x04
    STATUS_LED_OUT_OF_RANGE = 0x05
    UNKNOWN_COMMAND = 0xFF


# Only these may ever be sent (see module docstring).
ALLOWED_COMMANDS = frozenset({Cmd.GET_FW_VERSION, Cmd.PAIRING_INIT, Cmd.SET_LAYER,
                              Cmd.GET_PROTOCOL_VERSION})

MAX_LAYER = 31  # layer_state_t is 32 bits on ZSA boards


def encode_command(cmd: int, params: bytes | list[int] = b"") -> bytes:
    """Build a hidraw write buffer: report id 0 + 32 payload bytes."""
    if cmd not in ALLOWED_COMMANDS:
        raise ValueError(f"Oryx command 0x{cmd:02x} is not allowed")
    params = bytes(params)
    if len(params) > REPORT_SIZE - 1:
        raise ValueError("too many params")
    payload = bytes([cmd]) + params
    return bytes([REPORT_ID]) + payload + bytes(REPORT_SIZE - len(payload))


def encode_set_layer(layer: int, on: bool = True) -> bytes:
    """SET_LAYER: ``on`` → layer_move(layer); ``not on`` → layer_off(layer)."""
    if not 0 <= layer <= MAX_LAYER:
        raise ValueError(f"layer {layer} out of range")
    return encode_command(Cmd.SET_LAYER, [1 if on else 0, layer])


@dataclass(frozen=True)
class OryxEvent:
    code: int
    layer: int | None = None
    row: int | None = None
    col: int | None = None
    text: str | None = None          # FW_VERSION payload
    value: int | None = None         # PROTOCOL_VERSION / ERROR code / flags

    @property
    def name(self) -> str:
        try:
            return Evt(self.code).name
        except ValueError:
            return f"UNKNOWN_0x{self.code:02x}"


def _until_stop(data: bytes) -> bytes:
    end = len(data)
    for i, b in enumerate(data):
        if b in (STOP_BYTE, 0x00):
            end = i
            break
    return data[:end]


def decode_report(data: bytes) -> OryxEvent | None:
    """Decode a 32-byte input report (no report-id prefix)."""
    if not data:
        return None
    code = data[0]
    arg = data[1:]

    def at(i: int) -> int | None:
        return arg[i] if len(arg) > i else None

    if code == Evt.FW_VERSION:
        text = _until_stop(arg).decode("ascii", errors="replace")
        return OryxEvent(code, text=text)
    if code == Evt.LAYER:
        return OryxEvent(code, layer=at(0))
    if code in (Evt.KEYDOWN, Evt.KEYUP):
        return OryxEvent(code, col=at(0), row=at(1))
    if code in (Evt.PROTOCOL_VERSION, Evt.ERROR, Evt.RGB_CONTROL,
                Evt.STATUS_LED_CONTROL):
        return OryxEvent(code, value=at(0))
    return OryxEvent(code)


# --- connection ---------------------------------------------------------------

class OryxError(Exception):
    def __init__(self, message: str, errno: int | None = None) -> None:
        super().__init__(message)
        self.errno = errno


@dataclass(frozen=True)
class HandshakeResult:
    firmware_version: str | None
    protocol_version: int | None
    paired: bool
    layer: int | None
    legacy_pairing: bool = False


Dispatch = Callable[..., object]


def glib_dispatch(fn: Callable[..., object], *args: object) -> None:
    from gi.repository import GLib

    def run() -> bool:
        fn(*args)
        return GLib.SOURCE_REMOVE
    GLib.idle_add(run)


class OryxConnection:
    """Owns one hidraw fd. A worker thread reads reports and does all writes.

    ``open()`` and ``handshake()`` block and should run off the main thread.
    After the handshake, events go to ``on_event`` through ``dispatch``
    (by default ``GLib.idle_add``, so callbacks run on the main loop).
    ``on_closed(reason)`` fires once if the device goes away or ``close()``
    is called (reason None for a deliberate close).
    """

    def __init__(self, path: str,
                 on_event: Callable[[OryxEvent], None] | None = None,
                 on_closed: Callable[[str | None], None] | None = None,
                 dispatch: Dispatch = glib_dispatch,
                 opener: Callable[[str], int] | None = None) -> None:
        self.path = path
        self._opener = opener or (lambda p: os.open(p, os.O_RDWR | os.O_CLOEXEC))
        self._on_event = on_event
        self._on_closed = on_closed
        self._dispatch = dispatch
        self._fd = -1
        self._wake_r = -1
        self._wake_w = -1
        self._thread: threading.Thread | None = None
        self._writes: queue.SimpleQueue[bytes] = queue.SimpleQueue()
        self._sync: queue.SimpleQueue[OryxEvent] = queue.SimpleQueue()
        self._live = False           # dispatch events (after handshake)
        self._closing = False
        self._closed_reported = False
        self._lock = threading.Lock()

    # -- lifecycle --

    @property
    def is_open(self) -> bool:
        return self._fd >= 0 and not self._closing

    def open(self) -> None:
        try:
            self._fd = self._opener(self.path)
        except OSError as exc:
            raise OryxError(exc.strerror or str(exc), exc.errno) from exc
        self._wake_r, self._wake_w = os.pipe2(os.O_NONBLOCK | os.O_CLOEXEC)
        self._thread = threading.Thread(target=self._run, name="oryx-hid", daemon=True)
        self._thread.start()

    def close(self) -> None:
        with self._lock:
            if self._closing:
                return
            self._closing = True
        self._wake()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=1.0)
        self._cleanup()
        self._report_closed(None)

    def _cleanup(self) -> None:
        for attr in ("_fd", "_wake_r", "_wake_w"):
            fd = getattr(self, attr)
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass
                setattr(self, attr, -1)

    def _report_closed(self, reason: str | None) -> None:
        with self._lock:
            if self._closed_reported:
                return
            self._closed_reported = True
        if self._on_closed is not None:
            self._dispatch(self._on_closed, reason)

    # -- writes --

    def send(self, packet: bytes) -> None:
        """Queue a pre-encoded packet (from ``encode_*``). Non-blocking."""
        if len(packet) != REPORT_SIZE + 1 or packet[0] != REPORT_ID or packet[1] not in ALLOWED_COMMANDS:
            raise ValueError("refusing to send malformed or disallowed packet")
        if not self.is_open:
            raise OryxError("connection is closed")
        self._writes.put(packet)
        self._wake()

    def set_layer(self, layer: int) -> None:
        """Make ``layer`` the only active layer (``layer_move``)."""
        self.send(encode_set_layer(layer, True))

    def _wake(self) -> None:
        if self._wake_w >= 0:
            try:
                os.write(self._wake_w, b"\x01")
            except OSError:
                pass

    # -- handshake --

    def handshake(self, timeout: float = 1.0) -> HandshakeResult:
        """GET_FW_VERSION, GET_PROTOCOL_VERSION, PAIRING_INIT. Blocking."""
        fw: str | None = None
        proto: int | None = None
        layer: int | None = None
        paired = False
        legacy = False

        self.send(encode_command(Cmd.GET_FW_VERSION))
        ev = self._wait_for({Evt.FW_VERSION}, timeout)
        if ev is not None:
            fw = ev.text

        self.send(encode_command(Cmd.GET_PROTOCOL_VERSION))
        ev = self._wait_for({Evt.PROTOCOL_VERSION, Evt.ERROR}, timeout / 2)
        if ev is not None and ev.code == Evt.PROTOCOL_VERSION:
            proto = ev.value

        self.send(encode_command(Cmd.PAIRING_INIT))
        ev = self._wait_for({Evt.PAIRING_SUCCESS, Evt.PAIRING_INPUT, Evt.PAIRING_FAILED,
                             Evt.ERROR}, timeout)
        if ev is not None and ev.code == Evt.PAIRING_SUCCESS:
            paired = True
            lev = self._wait_for({Evt.LAYER}, 0.3)
            if lev is not None:
                layer = lev.layer
        elif ev is not None and ev.code == Evt.PAIRING_INPUT:
            legacy = True

        # Flush anything else that was queued during the handshake, keeping
        # the newest layer.
        while True:
            try:
                extra = self._sync.get_nowait()
            except queue.Empty:
                break
            if extra.code == Evt.LAYER:
                layer = extra.layer
        self._live = True
        return HandshakeResult(fw, proto, paired, layer, legacy)

    def _wait_for(self, codes: set[int], timeout: float) -> OryxEvent | None:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                ev = self._sync.get(timeout=remaining)
            except queue.Empty:
                return None
            if ev.code in codes:
                return ev

    # -- worker --

    def _run(self) -> None:
        reason: str | None = None
        fd, wake = self._fd, self._wake_r
        try:
            while not self._closing:
                try:
                    readable, _, _ = select.select([fd, wake], [], [], 1.0)
                except InterruptedError:
                    continue
                if wake in readable:
                    try:
                        os.read(wake, 512)
                    except BlockingIOError:
                        pass
                self._flush_writes(fd)
                if fd in readable:
                    data = os.read(fd, 64)
                    if not data:
                        reason = "end of file"
                        break
                    ev = decode_report(data)
                    if ev is None:
                        continue
                    if self._live:
                        if self._on_event is not None:
                            self._dispatch(self._on_event, ev)
                    else:
                        self._sync.put(ev)
        except OSError as exc:
            if not self._closing:
                reason = exc.strerror or errno.errorcode.get(exc.errno or 0, str(exc))
        else:
            # Deliberate close: send what's still queued (e.g. a final
            # "return to layer 0") before the fd goes away.
            try:
                self._flush_writes(fd)
            except OSError:
                pass
        if not self._closing:
            with self._lock:
                self._closing = True
            self._cleanup()
            self._report_closed(reason or "device closed")

    def _flush_writes(self, fd: int) -> None:
        while True:
            try:
                packet = self._writes.get_nowait()
            except queue.Empty:
                return
            os.write(fd, packet)


__all__ = [
    "REPORT_SIZE", "STOP_BYTE", "Cmd", "Evt", "Err", "ALLOWED_COMMANDS",
    "encode_command", "encode_set_layer", "decode_report", "OryxEvent",
    "OryxConnection", "OryxError", "HandshakeResult", "glib_dispatch",
]
