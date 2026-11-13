"""The keyboard's pointing device (ZSA Navigator): HID parsing and inference.

Pure logic, no GTK. Three parts:

* A small HID report-descriptor parser (``parse_descriptor``): input fields
  with their report id, bit offset, size, usage, logical range/signedness and
  the collections they sit in. Enough to find a touchpad's finger slots or a
  mouse's X/Y/buttons/wheel; not a full HID stack.
* ``classify(descriptor)``: ``TOUCHPAD`` (an application collection Digitizer
  0x0D / Touch Pad 0x05), ``MOUSE`` (Generic Desktop 0x01 / Mouse 0x02, and no
  keyboard collection: a shared interface would carry typed keys) or ``OTHER``.
* Interpreters that turn raw reports into ``PointerActivity`` changes:
  ``TrackpadInterpreter`` (Windows precision-touchpad style reports) and
  ``MouseInterpreter`` (relative mouse). Time is ``time.monotonic()`` seconds,
  passed in. ``feed(report, now)`` and ``poll(now)`` return the changes;
  ``next_deadline()`` says when ``poll`` may have something (IDLE), so the
  caller can schedule one GLib timeout.

Voyager + Navigator trackpad (verified on hardware, 2026-11): report id 1,
16 bytes with the id: two finger slots of 6 bytes (bit0 confidence, bit1 tip;
contact id; X u16 LE 0-2048; Y u16), scan time u16, then contact count (low
nibble) and buttons 1-3 (bits 4-6). The pad reports at ~125 Hz while touched,
sends one report with tip=0 when a finger lifts, then stays silent.

Nothing here records or stores what the device does, except
``KnownTrackballs``: the ids of keyboards whose trackball has moved.
"""

from __future__ import annotations

import json
import logging
import math
import os
import tempfile
from dataclasses import dataclass, field
from enum import Enum

from ..models import POINTER_FRAME_MS, POINTER_IDLE_MS, PointerAction, PointerActivity, PointerKind

log = logging.getLogger(__name__)

# Usage pages / usages.
PAGE_DESKTOP = 0x01
PAGE_BUTTON = 0x09
PAGE_CONSUMER = 0x0C
PAGE_DIGITIZER = 0x0D
DESKTOP_MOUSE = 0x02
DESKTOP_KEYBOARD = 0x06
DESKTOP_X = 0x30
DESKTOP_Y = 0x31
DESKTOP_WHEEL = 0x38
CONSUMER_AC_PAN = 0x238
DIGI_TOUCHPAD = 0x05
DIGI_FINGER = 0x22
DIGI_TIP = 0x42
DIGI_CONFIDENCE = 0x47
DIGI_CONTACT_ID = 0x51
DIGI_CONTACT_COUNT = 0x54


# --- report descriptor ------------------------------------------------------------

@dataclass(frozen=True)
class HidField:
    """One input value: ``report_id`` (0 = reports carry no id byte), its bit
    offset in the report *after* the id byte, and how to read it."""
    report_id: int
    bit_offset: int
    size: int
    usage_page: int
    usage: int
    logical_min: int
    logical_max: int
    physical_min: int
    physical_max: int
    unit: int
    unit_exponent: int
    relative: bool
    application: tuple[int, int]     # (page, usage) of the top-level collection
    collections: tuple[int, ...]     # instance ids of the enclosing collections
    collection_usages: tuple[tuple[int, int], ...]   # (page, usage) of each

    @property
    def signed(self) -> bool:
        return self.logical_min < 0

    def read(self, payload: bytes) -> int | None:
        """The value in ``payload`` (report without its id byte), or None if short."""
        end = self.bit_offset + self.size
        if end > len(payload) * 8 or self.size <= 0:
            return None
        first, last = self.bit_offset // 8, (end - 1) // 8
        raw = int.from_bytes(payload[first:last + 1], "little") >> (self.bit_offset % 8)
        raw &= (1 << self.size) - 1
        if self.signed and raw & (1 << (self.size - 1)):
            raw -= 1 << self.size
        return raw

    def units_per_mm(self) -> float | None:
        """Logical units per millimetre, from the physical range and unit."""
        system = self.unit & 0x0F
        length = (self.unit >> 4) & 0x0F
        span = self.physical_max - self.physical_min
        if length != 1 or span <= 0 or system not in (1, 3):
            return None
        exp = self.unit_exponent if self.unit_exponent < 8 else self.unit_exponent - 16
        mm = span * (10.0 ** exp) * (10.0 if system == 1 else 25.4)   # cm or inch
        return (self.logical_max - self.logical_min) / mm if mm > 0 else None


@dataclass
class HidDescriptor:
    fields: list[HidField] = field(default_factory=list)
    applications: list[tuple[int, int]] = field(default_factory=list)
    uses_report_ids: bool = False

    def find(self, page: int, usage: int, report_id: int | None = None) -> list[HidField]:
        return [f for f in self.fields if f.usage_page == page and f.usage == usage
                and (report_id is None or f.report_id == report_id)]

    def payload(self, report: bytes, report_id: int) -> bytes | None:
        """Strip the id byte; None if ``report`` isn't that report."""
        if not self.uses_report_ids:
            return report
        if not report or report[0] != report_id:
            return None
        return report[1:]


def _signed(value: int, size: int) -> int:
    bits = size * 8
    if bits and value & (1 << (bits - 1)):
        return value - (1 << bits)
    return value


@dataclass
class _Globals:
    usage_page: int = 0
    logical_min: int = 0
    logical_max: int = 0
    physical_min: int = 0
    physical_max: int = 0
    unit_exponent: int = 0
    unit: int = 0
    report_size: int = 0
    report_id: int = 0
    report_count: int = 0
    lmin_signed: bool = False

    def copy(self) -> "_Globals":
        return _Globals(**self.__dict__)


def parse_descriptor(desc: bytes) -> HidDescriptor:
    """Parse ``desc`` into input fields. Tolerates truncation (stops there)."""
    out = HidDescriptor()
    g = _Globals()
    stack: list[_Globals] = []
    usages: list[int] = []              # full (page << 16 | usage)
    usage_min: int | None = None
    usage_max: int | None = None
    coll: list[tuple[int, tuple[int, int]]] = []   # (instance id, (page, usage))
    application = (0, 0)
    next_coll = 0
    offsets: dict[int, int] = {}
    lmax_raw = (0, 0)                   # (value, size) to reinterpret after a signed min
    i, n = 0, len(desc)
    while i < n:
        prefix = desc[i]
        if prefix == 0xFE:                               # long item
            if i + 1 >= n:
                break
            i += 3 + desc[i + 1]
            continue
        size = (0, 1, 2, 4)[prefix & 0x03]
        typ = (prefix >> 2) & 0x03
        tag = prefix >> 4
        data = desc[i + 1:i + 1 + size]
        if len(data) < size:
            break
        value = int.from_bytes(data, "little") if size else 0
        i += 1 + size

        if typ == 1:                                     # global
            if tag == 0x0:
                g.usage_page = value
            elif tag == 0x1:
                g.logical_min = _signed(value, size)
                g.lmin_signed = g.logical_min < 0
                v, s = lmax_raw
                g.logical_max = _signed(v, s) if g.lmin_signed else v
            elif tag == 0x2:
                lmax_raw = (value, size)
                g.logical_max = _signed(value, size) if g.lmin_signed else value
            elif tag == 0x3:
                g.physical_min = _signed(value, size)
            elif tag == 0x4:
                g.physical_max = _signed(value, size) if g.physical_min < 0 else value
            elif tag == 0x5:
                g.unit_exponent = value & 0x0F
            elif tag == 0x6:
                g.unit = value
            elif tag == 0x7:
                g.report_size = value
            elif tag == 0x8:
                g.report_id = value
                out.uses_report_ids = True
            elif tag == 0x9:
                g.report_count = value
            elif tag == 0xA:
                stack.append(g.copy())
            elif tag == 0xB and stack:
                g = stack.pop()
        elif typ == 2:                                   # local
            full = value if size == 4 else (g.usage_page << 16) | value
            if tag == 0x0:
                usages.append(full)
            elif tag == 0x1:
                usage_min = full
            elif tag == 0x2:
                usage_max = full
        elif typ == 0:                                   # main
            if tag == 0xA:                               # collection
                full = usages[0] if usages else (g.usage_page << 16)
                pu = ((full >> 16) & 0xFFFF, full & 0xFFFF)
                if not coll:
                    application = pu
                    if value == 0x01:
                        out.applications.append(pu)
                coll.append((next_coll, pu))
                next_coll += 1
            elif tag == 0xC:                             # end collection
                if coll:
                    coll.pop()
            elif tag == 0x8:                             # input
                _add_input(out, g, value, usages, usage_min, usage_max, coll,
                           application, offsets)
            usages, usage_min, usage_max = [], None, None
    return out


def _add_input(out: HidDescriptor, g: _Globals, flags: int, usages: list[int],
               usage_min: int | None, usage_max: int | None,
               coll: list[tuple[int, tuple[int, int]]], application: tuple[int, int],
               offsets: dict[int, int]) -> None:
    rid = g.report_id
    offset = offsets.get(rid, 0)
    total = g.report_size * g.report_count
    offsets[rid] = offset + total
    constant = bool(flags & 0x01)
    variable = bool(flags & 0x02)
    if constant or not variable:
        # Padding, or an array (key codes / consumer lists): not needed here.
        return
    seq = list(usages)
    if usage_min is not None and usage_max is not None and usage_max >= usage_min:
        seq += list(range(usage_min, usage_max + 1))[:max(0, g.report_count)]
    if not seq:
        return
    ids = tuple(c[0] for c in coll)
    cus = tuple(c[1] for c in coll)
    for k in range(g.report_count):
        full = seq[min(k, len(seq) - 1)]
        out.fields.append(HidField(
            report_id=rid, bit_offset=offset + k * g.report_size, size=g.report_size,
            usage_page=(full >> 16) & 0xFFFF, usage=full & 0xFFFF,
            logical_min=g.logical_min, logical_max=g.logical_max,
            physical_min=g.physical_min, physical_max=g.physical_max,
            unit=g.unit, unit_exponent=g.unit_exponent, relative=bool(flags & 0x04),
            application=application, collections=ids, collection_usages=cus))


class InterfaceClass(str, Enum):
    TOUCHPAD = "touchpad"
    MOUSE = "mouse"
    OTHER = "other"


def classify(desc: bytes) -> InterfaceClass:
    apps = parse_descriptor(desc).applications
    if (PAGE_DIGITIZER, DIGI_TOUCHPAD) in apps:
        return InterfaceClass.TOUCHPAD
    # A shared interface with a keyboard collection carries typed keys: never read it.
    if (PAGE_DESKTOP, DESKTOP_KEYBOARD) in apps:
        return InterfaceClass.OTHER
    if (PAGE_DESKTOP, DESKTOP_MOUSE) in apps:
        return InterfaceClass.MOUSE
    return InterfaceClass.OTHER


# --- shared emitting state --------------------------------------------------------

IDLE_S = POINTER_IDLE_MS / 1000.0
FRAME_S = POINTER_FRAME_MS / 1000.0
_LIVE = (PointerAction.TOUCH, PointerAction.MOVE, PointerAction.SCROLL)
_MOVING = (PointerAction.MOVE, PointerAction.SCROLL)


def _direction(dx: float, dy: float) -> str:
    if abs(dx) > abs(dy):
        return "right" if dx > 0 else "left"
    return "down" if dy > 0 else "up"


class _Emitter:
    """The last state sent. Action changes go out at once; while the state is
    TOUCH/MOVE/SCROLL, fresh contacts and accumulated motion are re-sent at
    most every ``POINTER_FRAME_MS`` (``frame``), with a deadline for the last
    update so it isn't lost when reports stop."""

    def __init__(self, kind: PointerKind) -> None:
        self.kind = kind
        self.buttons: frozenset[int] = frozenset()
        self.state: tuple[PointerAction, int, str] = (PointerAction.IDLE, 0, "")
        self.contacts: tuple[tuple[float, float], ...] = ()
        self._sent_contacts: tuple[tuple[float, float], ...] = ()
        self._acc = [0.0, 0.0]
        self._last_sent = -math.inf
        self._dirty = False

    def update(self, contacts: tuple[tuple[float, float], ...], dx: float, dy: float) -> None:
        self.contacts = contacts
        self._acc[0] += dx
        self._acc[1] += dy
        self._dirty = contacts != self._sent_contacts or self._acc != [0.0, 0.0]

    def clear_motion(self) -> None:
        self._acc = [0.0, 0.0]

    def _send(self, out: list[PointerActivity], now: float) -> None:
        action, fingers, direction = self.state
        out.append(PointerActivity(self.kind, action, fingers=fingers, buttons=self.buttons,
                                   direction=direction, contacts=self.contacts,
                                   motion=(self._acc[0], self._acc[1])))
        self._acc = [0.0, 0.0]
        self._sent_contacts = self.contacts
        self._last_sent = now
        self._dirty = False

    def motion(self, out: list[PointerActivity], now: float, action: PointerAction,
               fingers: int, direction: str = "") -> None:
        state = (action, fingers, direction)
        if state != self.state:
            self.state = state
            self._send(out, now)

    def frame(self, out: list[PointerActivity], now: float) -> None:
        if self._dirty and self.state[0] in _LIVE and now - self._last_sent >= FRAME_S:
            self._send(out, now)

    def frame_deadline(self) -> float | None:
        if self._dirty and self.state[0] in _LIVE:
            return self._last_sent + FRAME_S
        return None

    def set_buttons(self, out: list[PointerActivity], buttons: frozenset[int],
                    fingers: int) -> None:
        if buttons == self.buttons:
            return
        for b in sorted(buttons - self.buttons):
            self.buttons |= {b}
            out.append(PointerActivity(self.kind, PointerAction.CLICK, fingers=fingers,
                                       button=b, buttons=self.buttons, contacts=self.contacts))
        for b in sorted(self.buttons - buttons):
            self.buttons -= {b}
            out.append(PointerActivity(self.kind, PointerAction.RELEASE, fingers=fingers,
                                       button=b, buttons=self.buttons, contacts=self.contacts))


def _min_deadline(*values: float | None) -> float | None:
    found = [v for v in values if v is not None]
    return min(found) if found else None


# --- trackpad ---------------------------------------------------------------------

@dataclass(frozen=True)
class _Slot:
    tip: HidField | None
    contact_id: HidField | None
    x: HidField
    y: HidField


class TrackpadInterpreter:
    """Precision-touchpad reports -> ``PointerActivity`` changes.

    * TOUCH when fingers go down, or the finger count changes before (or
      without) motion; motion starts again from the new position.
    * MOVE (1 finger, or 3+) / SCROLL (2 fingers, ``direction`` of the finger
      motion, dominant axis) once the fingers' mean position moves
      ``MOVE_MM``; SCROLL again when the direction changes.
    * While TOUCH/MOVE/SCROLL, the same activity again with fresh
      ``contacts``/``motion`` at most every ``POINTER_FRAME_MS``.
    * IDLE (fingers still down) after ``POINTER_IDLE_MS`` without motion.
    * On all fingers up: TAP (``fingers`` = most fingers during it,
      ``contacts`` where they were) if the touch lasted at most
      ``TAP_MAX_S``, never moved and pressed no button; then IDLE, fingers 0.
    * CLICK/RELEASE from the button bits, whenever they change.

    ``contacts`` are (x, y) in 0..1 of the logical range, sorted by contact
    id; ``motion`` is the mean position's change in the same units.
    """

    TAP_MAX_S = 0.18
    MOVE_MM = 1.5          # libinput's tap motion threshold is 1.3 mm
    JITTER_MM = 0.25       # smaller steps don't count as motion (for IDLE)
    STALE_S = 0.5          # no report this long while touched: assume lifted

    def __init__(self, descriptor: bytes) -> None:
        d = parse_descriptor(descriptor)
        self._desc = d
        pad = (PAGE_DIGITIZER, DIGI_TOUCHPAD)
        tips = [f for f in d.find(PAGE_DIGITIZER, DIGI_TIP) if f.application == pad]
        self.report_id = tips[0].report_id if tips else 0
        rid = self.report_id
        groups: dict[int, dict[tuple[int, int], HidField]] = {}
        for f in d.fields:
            if f.report_id != rid or f.application != pad:
                continue
            finger = next((c for c, cu in zip(f.collections, f.collection_usages)
                           if cu == (PAGE_DIGITIZER, DIGI_FINGER)), None)
            if finger is not None:
                groups.setdefault(finger, {}).setdefault((f.usage_page, f.usage), f)
        self._slots: list[_Slot] = []
        for g in groups.values():
            x, y = g.get((PAGE_DESKTOP, DESKTOP_X)), g.get((PAGE_DESKTOP, DESKTOP_Y))
            if x is not None and y is not None:
                self._slots.append(_Slot(g.get((PAGE_DIGITIZER, DIGI_TIP)),
                                         g.get((PAGE_DIGITIZER, DIGI_CONTACT_ID)), x, y))
        count = [f for f in d.find(PAGE_DIGITIZER, DIGI_CONTACT_COUNT, rid)
                 if f.application == pad]
        self._count = count[0] if count else None
        self._buttons = [f for f in d.fields if f.usage_page == PAGE_BUTTON
                         and f.report_id == rid and f.application == pad]
        self._x0 = self._y0 = 0.0
        self._xspan = self._yspan = 1.0
        upm: float | None = None
        if self._slots:
            sx, sy = self._slots[0].x, self._slots[0].y
            self._x0, self._y0 = float(sx.logical_min), float(sy.logical_min)
            self._xspan = float(max(1, sx.logical_max - sx.logical_min))
            self._yspan = float(max(1, sy.logical_max - sy.logical_min))
            upm = sx.units_per_mm() or self._xspan / 60.0    # else assume a ~60 mm pad
        upm = upm or 1.0
        self.move_threshold = self.MOVE_MM * upm
        self.jitter = self.JITTER_MM * upm
        self._em = _Emitter(PointerKind.TRACKPAD)
        self._reset_touch()
        self._last_report = 0.0
        self._pending: dict[int, tuple[bool, int, int]] = {}
        self._pending_need = 0
        self._pending_buttons: frozenset[int] = frozenset()

    @property
    def valid(self) -> bool:
        return bool(self._slots)

    def _reset_touch(self) -> None:
        self._contacts: dict[int, tuple[int, int]] = {}
        self._centre: tuple[float, float] | None = None
        self._down_at: float | None = None
        self._max_fingers = 0
        self._tap_contacts: tuple[tuple[float, float], ...] = ()
        self._moved = False
        self._clicked = False
        self._anchor: tuple[float, float] | None = None
        self._last_motion = 0.0

    def _norm(self, down: dict[int, tuple[int, int]]) -> tuple[tuple[float, float], ...]:
        return tuple((min(1.0, max(0.0, (x - self._x0) / self._xspan)),
                      min(1.0, max(0.0, (y - self._y0) / self._yspan)))
                     for _cid, (x, y) in sorted(down.items()))

    # -- frames --

    def _frame(self, report: bytes) -> tuple[dict[int, tuple[int, int]], frozenset[int]] | None:
        """Contacts down and buttons of a complete frame, or None (no frame yet).

        Hybrid mode: a frame with more contacts than slots spans several
        reports; the first has the contact count, the others count 0.
        """
        payload = self._desc.payload(report, self.report_id)
        if payload is None:
            return None
        count = self._count.read(payload) if self._count is not None else None
        if count is None or count > 0:
            self._pending = {}
            self._pending_need = count if count is not None else len(self._slots)
            self._pending_buttons = frozenset(
                f.usage for f in self._buttons if f.read(payload))
        elif self._pending_need <= len(self._pending):
            return None          # stray continuation
        for k, slot in enumerate(self._slots):
            if len(self._pending) >= self._pending_need:
                break
            tip = slot.tip.read(payload) if slot.tip is not None else 1
            cid = slot.contact_id.read(payload) if slot.contact_id is not None else k
            x, y = slot.x.read(payload), slot.y.read(payload)
            if tip is None or cid is None or x is None or y is None:
                return None
            self._pending[cid] = (bool(tip), x, y)
        if len(self._pending) < self._pending_need:
            return None
        down = {cid: (x, y) for cid, (tip, x, y) in self._pending.items() if tip}
        return down, self._pending_buttons

    # -- public --

    def feed(self, report: bytes, now: float) -> list[PointerActivity]:
        out = self.poll(now)
        frame = self._frame(report)
        if frame is None:
            return out
        self._last_report = now
        down, buttons = frame
        fingers = len(down)
        if buttons - self._em.buttons:
            self._clicked = True

        if not down:
            self._em.set_buttons(out, buttons, 0)
            if self._contacts:
                self._lift(out, now)
            return out
        if not self._contacts:                       # touch down
            self._down_at = now
            self._max_fingers = 0
            self._moved = False
            self._clicked = bool(buttons)
        contacts = self._norm(down)
        if fingers >= self._max_fingers:
            self._max_fingers = fingers
            self._tap_contacts = contacts
        centre = (sum(p[0] for p in down.values()) / fingers,
                  sum(p[1] for p in down.values()) / fingers)
        if set(down) != set(self._contacts):
            # Fingers changed: the mean jumps, so motion starts over from here.
            self._contacts = down
            self._centre = self._anchor = centre
            self._last_motion = now
            self._em.update(contacts, 0.0, 0.0)
            self._em.set_buttons(out, buttons, fingers)
            self._em.motion(out, now, PointerAction.TOUCH, fingers)
            self._em.frame(out, now)
            return out
        prev = self._centre or centre
        self._contacts = down
        self._centre = centre
        self._em.update(contacts, (centre[0] - prev[0]) / self._xspan,
                        (centre[1] - prev[1]) / self._yspan)
        self._em.set_buttons(out, buttons, fingers)
        assert self._anchor is not None
        dx, dy = centre[0] - self._anchor[0], centre[1] - self._anchor[1]
        dist = math.hypot(dx, dy)
        action = self._em.state[0]
        moving = action in _MOVING
        if dist >= (self.jitter if moving else self.move_threshold):
            self._moved = True
            self._anchor = centre
            self._last_motion = now
            if fingers == 2:
                direction = _direction(dx, dy)
                if action == PointerAction.SCROLL and direction != self._em.state[2]:
                    # Hysteresis: a diagonal step keeps the current direction.
                    major, minor = max(abs(dx), abs(dy)), min(abs(dx), abs(dy))
                    if major < 2 * minor:
                        direction = self._em.state[2]
                self._em.motion(out, now, PointerAction.SCROLL, 2, direction)
            else:
                self._em.motion(out, now, PointerAction.MOVE, fingers)
        self._em.frame(out, now)
        return out

    def _lift(self, out: list[PointerActivity], now: float) -> None:
        tap = (self._down_at is not None and not self._moved and not self._clicked
               and now - self._down_at <= self.TAP_MAX_S)
        fingers, where = self._max_fingers, self._tap_contacts
        self._reset_touch()
        if tap:
            self._em.state = (PointerAction.TAP, fingers, "")
            out.append(PointerActivity(PointerKind.TRACKPAD, PointerAction.TAP,
                                       fingers=fingers, buttons=self._em.buttons,
                                       contacts=where))
        self._em.update((), 0.0, 0.0)
        self._em.clear_motion()
        self._em.motion(out, now, PointerAction.IDLE, 0)

    def poll(self, now: float) -> list[PointerActivity]:
        out: list[PointerActivity] = []
        if self._contacts and now - self._last_report >= self.STALE_S:
            self._em.set_buttons(out, frozenset(), 0)
            self._down_at = None             # a missed lift is never a tap
            self._lift(out, now)
            return out
        self._em.frame(out, now)
        if (self._contacts and self._em.state[0] in _MOVING
                and now - self._last_motion >= IDLE_S):
            self._em.motion(out, now, PointerAction.IDLE, len(self._contacts))
        return out

    def next_deadline(self) -> float | None:
        if not self._contacts:
            return None
        idle = self._last_motion + IDLE_S if self._em.state[0] in _MOVING else None
        return _min_deadline(self._last_report + self.STALE_S, idle,
                             self._em.frame_deadline())

    def reset(self) -> None:
        """Forget the current touch (tracking stopped); emits nothing."""
        self._reset_touch()
        self._em = _Emitter(PointerKind.TRACKPAD)
        self._pending = {}
        self._pending_need = 0


# --- mouse / trackball ------------------------------------------------------------

class MouseInterpreter:
    """Relative mouse reports -> ``PointerActivity`` changes (kind TRACKBALL).

    MOVE when X/Y move (``motion`` = counts), SCROLL for wheel/AC Pan
    (``motion`` = (pan, wheel), positive = right/up; ``direction`` from the
    larger), re-sent when the direction changes and, while rolling, with fresh
    motion at most every ``POINTER_FRAME_MS``. IDLE ``POINTER_IDLE_MS`` after
    the last motion (reports stop when nothing moves, so this needs ``poll``).
    CLICK/RELEASE from the buttons (HID button numbers: 1 left, 2 right,
    3 middle). ``last_moved`` says whether the last report had X/Y motion.
    """

    def __init__(self, descriptor: bytes) -> None:
        d = parse_descriptor(descriptor)
        self._desc = d
        mouse = (PAGE_DESKTOP, DESKTOP_MOUSE)
        xs = [f for f in d.find(PAGE_DESKTOP, DESKTOP_X) if f.application == mouse]
        self.report_id = xs[0].report_id if xs else 0
        rid = self.report_id

        def one(page: int, usage: int) -> HidField | None:
            found = [f for f in d.find(page, usage, rid) if f.application == mouse]
            return found[0] if found else None
        self._x = one(PAGE_DESKTOP, DESKTOP_X)
        self._y = one(PAGE_DESKTOP, DESKTOP_Y)
        self._wheel = one(PAGE_DESKTOP, DESKTOP_WHEEL)
        self._pan = one(PAGE_CONSUMER, CONSUMER_AC_PAN)
        self._buttons = [f for f in d.fields if f.usage_page == PAGE_BUTTON
                         and f.report_id == rid and f.application == mouse]
        self._em = _Emitter(PointerKind.TRACKBALL)
        self._last_motion = 0.0
        self.last_moved = False

    @property
    def valid(self) -> bool:
        return self._x is not None and self._y is not None

    def feed(self, report: bytes, now: float) -> list[PointerActivity]:
        out = self.poll(now)
        self.last_moved = False
        payload = self._desc.payload(report, self.report_id)
        if payload is None or not self.valid:
            return out

        def val(f: HidField | None) -> int:
            v = f.read(payload) if f is not None else None
            return v or 0
        buttons = frozenset(f.usage for f in self._buttons if f.read(payload))
        self._em.set_buttons(out, buttons, 0)
        dx, dy, wheel, pan = val(self._x), val(self._y), val(self._wheel), val(self._pan)
        if wheel or pan:
            if abs(wheel) >= abs(pan):
                direction = "up" if wheel > 0 else "down"
            else:
                direction = "right" if pan > 0 else "left"
            state, mx, my = (PointerAction.SCROLL, 0, direction), float(pan), float(wheel)
        elif dx or dy:
            self.last_moved = True
            state, mx, my = (PointerAction.MOVE, 0, ""), float(dx), float(dy)
        else:
            self._em.frame(out, now)
            return out
        self._last_motion = now
        if state != self._em.state:
            self._em.clear_motion()          # motion of another kind isn't carried over
        self._em.update((), mx, my)
        self._em.motion(out, now, *state)
        self._em.frame(out, now)
        return out

    def poll(self, now: float) -> list[PointerActivity]:
        out: list[PointerActivity] = []
        self._em.frame(out, now)
        if self._em.state[0] in _MOVING and now - self._last_motion >= IDLE_S:
            self._em.clear_motion()
            self._em.motion(out, now, PointerAction.IDLE, 0)
        return out

    def next_deadline(self) -> float | None:
        idle = self._last_motion + IDLE_S if self._em.state[0] in _MOVING else None
        return _min_deadline(idle, self._em.frame_deadline())

    def reset(self) -> None:
        self._em = _Emitter(PointerKind.TRACKBALL)
        self.last_moved = False


# --- reference hardware -----------------------------------------------------------

# The Voyager + Navigator trackpad interface's report descriptor (hidraw dump,
# 2026-11). Used by the mock and the tests.
VOYAGER_TOUCHPAD_DESCRIPTOR = bytes.fromhex(
    "050d0905a10185010922a100a4150025010947094295027501810275019506810195017503250509518102"
    "750195058101050115002600087510550e6511093035004626029501810226000846260209318102b4c005"
    "0d0922a100a4150025010947094295027501810275019506810195017503250509518102750195058101050115"
    "002600087510550e6511093035004626029501810226000846260209318102b4c0a4550c660110050d0956341447"
    "ffff000027ffff00007510950181020954250595017504810205090901090209032501750195038102750195018101"
    "050d8502095575049501250fb1020959750395012507b1020957750195012501b10206ff00850309c526ff0075"
    "08960001b102b4c0050d090ea101a485040922a10209521500250a95017508b102c00922a1008505095709582501"
    "95027501b1029506b103b4c0c005010902a10185060901a100050919012903150025019503750181029501750581"
    "010501093009311581257f950275088106c0c0")
# Its mouse interface (mouse keys; a trackball would report here too).
VOYAGER_MOUSE_DESCRIPTOR = bytes.fromhex(
    "05010902a1010901a100050919012908150025019508750181020501093009311581257f95027508810609381581"
    "257f950175088106050c0a38021581257f950175088106c0c0")


def voyager_trackpad_report(contacts: list[tuple[int, float, float, bool]],
                            buttons: frozenset[int] = frozenset(), scan: int = 0) -> bytes:
    """A Voyager trackpad report: up to two ``(contact id, x, y, tip)`` with
    x/y in 0..1, buttons 1-3. Lifted fingers are sent once with tip False."""
    out = bytearray([1])
    for k in range(2):
        if k < len(contacts):
            cid, x, y, tip = contacts[k]
            out += bytes([0x01 | (0x02 if tip else 0), cid & 0x07])
            out += int(round(min(1.0, max(0.0, x)) * 2048)).to_bytes(2, "little")
            out += int(round(min(1.0, max(0.0, y)) * 2048)).to_bytes(2, "little")
        else:
            out += bytes(6)
    out += (scan & 0xFFFF).to_bytes(2, "little")
    bits = sum(1 << (b - 1) for b in buttons if 1 <= b <= 3)
    out.append((len(contacts) & 0x0F) | (bits << 4))
    return bytes(out)


# --- trackball detection ----------------------------------------------------------

class TrackballDetector:
    """Tells a trackball from mouse keys on the shared mouse interface.

    Mouse keys only move the pointer while an Oryx key is held, so motion
    counts when no key is held, no key event came within ``KEY_GRACE_S`` (the
    two arrive on different interfaces, in either order), and motion keeps
    coming for ``CONFIRM_S`` without a key going down meanwhile.
    """

    KEY_GRACE_S = 0.2
    CONFIRM_S = 0.15
    GAP_S = 1.0            # motion this far apart starts a new candidate

    def __init__(self) -> None:
        self._keys_held = False
        self._last_key = -math.inf
        self._since: float | None = None
        self._last_motion = -math.inf

    def key_event(self, any_held: bool, now: float) -> None:
        self._keys_held = any_held
        self._last_key = now
        self._since = None

    def motion(self, now: float) -> bool:
        """A report with X/Y motion arrived. True once it's a trackball."""
        if self._keys_held or now - self._last_key < self.KEY_GRACE_S:
            self._since = None
            return False
        if self._since is None or now - self._last_motion > self.GAP_S:
            self._since = now
        self._last_motion = now
        return now - self._since >= self.CONFIRM_S


def default_state_path() -> str:
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, "vibemapp", "pointer.json")


class KnownTrackballs:
    """Keyboards (by stable key) whose trackball has moved, in a small JSON
    file, so the trackball is known on the next start."""

    def __init__(self, path: str | None = None) -> None:
        self.path = path or default_state_path()
        self._keys: set[str] | None = None

    def _load(self) -> set[str]:
        if self._keys is None:
            self._keys = set()
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                keys = data.get("trackballs") if isinstance(data, dict) else None
                if isinstance(keys, list):
                    self._keys = {k for k in keys if isinstance(k, str)}
            except FileNotFoundError:
                pass
            except (OSError, ValueError) as exc:
                log.warning("Can't read %s: %s", self.path, exc)
        return self._keys

    def __contains__(self, key: str) -> bool:
        return key in self._load()

    def add(self, key: str) -> None:
        keys = self._load()
        if key in keys:
            return
        keys.add(key)
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self.path), suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"version": 1, "trackballs": sorted(keys)}, f, indent=2)
            os.replace(tmp, self.path)
        except OSError as exc:
            log.warning("Can't save %s: %s", self.path, exc)


__all__ = [
    "HidField", "HidDescriptor", "parse_descriptor", "InterfaceClass", "classify",
    "TrackpadInterpreter", "MouseInterpreter", "TrackballDetector", "KnownTrackballs",
    "default_state_path", "VOYAGER_TOUCHPAD_DESCRIPTOR", "VOYAGER_MOUSE_DESCRIPTOR",
    "voyager_trackpad_report",
]
