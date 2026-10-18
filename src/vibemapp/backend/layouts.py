"""Layout comparison (SPEC: layout coach). Pure, no GTK, no I/O.

Scores the recorded typing as if the base layer's letters sat on a well-known
layout, using ``ergo.report`` on counts and pairs whose base-layer block keys
have moved. Nothing is written anywhere.

Slots: the 30 block keys in QWERTY order (``SLOT_CHARS``)::

    q w e r t y u i o p
    a s d f g h j k l ;
    z x c v b n m , . /

Finding the block on layer 0 (``find_block``): the 26 letters by the QWERTY
identity of their tap code (``KC_A``, ``DK_A``…); the ``;`` slot is the key right
of L on its row, the ``,`` ``.`` ``/`` slots the three keys right of M ("right of"
= centre within ``ROW_TOL`` key units vertically, next by x). The block must be
unambiguous (no letter twice, 30 distinct keys) and laid out like QWERTY (three
rows top to bottom, x increasing along each row of ten). Otherwise (a layer 0
that is not QWERTY already, a letter missing) there is no comparison.

Layouts (30-char strings in slot order; the standard ANSI definitions):

* QWERTY.
* Colemak: colemak.com (Shai Coleman, 2006): ``qwfpg jluy;`` / ``arstd hneio`` /
  ``zxcvb km,./``.
* Colemak-DH: colemakmods.github.io/mod-dh (ANSI, "matrix" angle-less variant):
  ``qwfpb jluy;`` / ``arstg mneio`` / ``zxcdv kh,./``.
* Workman: workmanlayout.org: ``qdrwb jfup;`` / ``ashtg yneoi`` / ``zxmcv kl,./``.
* Dvorak: Dvorak Simplified Keyboard (ANSI). Its block holds ``' , . ;`` as the
  four punctuation keys, the ``/`` goes off the block (to the ``[`` key). The
  ``'`` is taken as the key QWERTY has ``/`` on, so the string uses ``/`` for it:
  ``/,.pyfgcrl`` / ``aoeuidhtns`` / ``;qjkxbmwvz``.

Simulation: a layout is a permutation of the 30 slots. Presses and pairs on layer 0
of a block key move with their character: the key in slot k (QWERTY char c) goes
to the key in slot ``layout.index(c)``. Every other key and layer stays. Fixed
slots: a slot whose key is a non-ASCII letter (Æ on the ``;`` slot of a Danish
layout) keeps that key. A letter a layout wants on a fixed slot goes to the slot
where the layout puts ``;`` instead.
"""

from __future__ import annotations

from typing import Iterable, Mapping, Sequence

from ..models import (COACH_MIN_PRESSES, ErgoReport, Finger, KeyBinding, KeyGeometry,
                      LayoutComparison)
from . import ergo, keykinds

Key = tuple[int, int]
Pair = tuple[Key, Key]

SLOT_CHARS = "qwertyuiopasdfghjkl;zxcvbnm,./"
ROW_TOL = 0.5
_ROWS = (SLOT_CHARS[0:10], SLOT_CHARS[10:20], SLOT_CHARS[20:30])

LAYOUTS: tuple[tuple[str, str], ...] = (
    ("QWERTY", SLOT_CHARS),
    ("Colemak", "qwfpgjluy;arstdhneiozxcvbkm,./"),
    ("Colemak-DH", "qwfpbjluy;arstgmneiozxcdvkh,./"),
    ("Workman", "qdrwbjfup;ashtgyneoizxmcvkl,./"),
    ("Dvorak", "/,.pyfgcrlaoeuidhtns;qjkxbmwvz"),
)


def _centre(g: KeyGeometry) -> tuple[float, float]:
    return g.x + g.w / 2, g.y + g.h / 2


def _right_of(index: int, geometry: Sequence[KeyGeometry]) -> list[int]:
    """Keys on the same row to the right of ``index``, nearest first."""
    cx, cy = _centre(geometry[index])
    found = []
    for j, g in enumerate(geometry):
        x, y = _centre(g)
        if j != index and abs(y - cy) <= ROW_TOL and x > cx + 0.25:
            found.append((x, j))
    return [j for _x, j in sorted(found)]


def find_block(bindings: Iterable[KeyBinding],
               geometry: Sequence[KeyGeometry]) -> list[int] | None:
    """Key index of each of the 30 slots (``SLOT_CHARS`` order), or None when
    the layer's letter block isn't recognisable."""
    by_char: dict[str, int] = {}
    for b in bindings:
        ch = keykinds.qwerty_letter(b)
        if ch is None or not 0 <= b.index < len(geometry):
            continue
        if ch in by_char:
            return None                      # a letter twice: ambiguous
        by_char[ch] = b.index
    if any(c not in by_char for c in "abcdefghijklmnopqrstuvwxyz"):
        return None
    semi = _right_of(by_char["l"], geometry)[:1]
    rest = _right_of(by_char["m"], geometry)[:3]
    if len(semi) < 1 or len(rest) < 3:
        return None
    punct = {";": semi[0], ",": rest[0], ".": rest[1], "/": rest[2]}
    slots = [by_char.get(c, punct.get(c, -1)) for c in SLOT_CHARS]
    if -1 in slots or len(set(slots)) != 30:
        return None
    # QWERTY-shaped: rows top to bottom, x increasing along each row.
    prev_y = None
    for r in range(3):
        keys = slots[r * 10:(r + 1) * 10]
        xs = [_centre(geometry[k])[0] for k in keys]
        if any(b <= a for a, b in zip(xs, xs[1:])):
            return None
        y = sum(_centre(geometry[k])[1] for k in keys) / 10
        if prev_y is not None and y <= prev_y:
            return None
        prev_y = y
    return slots


def _fixed_slots(bindings: Mapping[int, KeyBinding], slots: Sequence[int]) -> set[int]:
    fixed = set()
    for k, idx in enumerate(slots):
        b = bindings.get(idx)
        if b is None:
            continue
        ch = keykinds.letter_char(b)
        if ch is not None and not ch.isascii():
            fixed.add(k)
    return fixed


def slot_map(layout: str, slots: Sequence[int], fixed: set[int]) -> dict[int, int]:
    """Old key index -> new key index for the keys that move under ``layout``."""
    semi_slot = layout.index(";")
    moves: dict[int, int] = {}
    for k, ch in enumerate(SLOT_CHARS):
        if k in fixed:
            continue
        t = layout.index(ch)
        if t in fixed:
            t = semi_slot
        if t != k:
            moves[slots[k]] = slots[t]
    return moves


def _apply(counts: Mapping[Key, int], pairs: Mapping[Pair, int],
           moves: Mapping[int, int]) -> tuple[dict[Key, int], dict[Pair, int]]:
    def m(k: Key) -> Key:
        return (0, moves[k[1]]) if k[0] == 0 and k[1] in moves else k

    c: dict[Key, int] = {}
    for k, n in counts.items():
        c[m(k)] = c.get(m(k), 0) + n
    p: dict[Pair, int] = {}
    for (a, b), n in pairs.items():
        key = (m(a), m(b))
        p[key] = p.get(key, 0) + n
    return c, p


def compare(bindings: Sequence[KeyBinding], counts: Mapping[Key, int],
            pairs: Mapping[Pair, int], geometry: Sequence[KeyGeometry],
            fingers: Sequence[Finger], home_keys: Mapping[Finger, int]
            ) -> list[LayoutComparison]:
    """``bindings``: layer 0 in Oryx key indices. Best score first, ties in the
    order of ``LAYOUTS``; [] below ``COACH_MIN_PRESSES`` presses or when the
    letter block isn't recognisable."""
    base = ergo.report(counts, pairs, geometry, fingers, home_keys)
    if base.presses < COACH_MIN_PRESSES:
        return []
    slots = find_block(bindings, geometry)
    if slots is None:
        return []
    fixed = _fixed_slots({b.index: b for b in bindings}, slots)
    out: list[tuple[float, int, LayoutComparison]] = []
    for order, (name, layout) in enumerate(LAYOUTS):
        moves = slot_map(layout, slots, fixed)
        if moves:
            c, p = _apply(counts, pairs, moves)
            rep: ErgoReport = ergo.report(c, p, geometry, fingers, home_keys)
        else:
            rep = base
        out.append((-rep.score, order, LayoutComparison(
            name, rep, round(rep.score - base.score, 1), current=(order == 0))))
    out.sort(key=lambda e: e[:2])
    return [e[2] for e in out]


__all__ = ["SLOT_CHARS", "LAYOUTS", "find_block", "slot_map", "compare"]
