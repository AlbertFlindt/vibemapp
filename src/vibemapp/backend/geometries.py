"""Physical key geometry (key units) in Oryx key-index order, plus matrix maps.

Positions and matrix coordinates come from ZSA's QMK fork
(github.com/zsa/qmk_firmware, branch ``firmware25``, commit 93b2b9e):
``keyboards/zsa/voyager/keyboard.json`` (LAYOUT), ``moonlander/revb/keyboard.json``
(LAYOUT), ``ergodox_ez/info.json`` (LAYOUT_ergodox) and ``planck_ez/info.json``
(LAYOUT_planck_1x2uC).

Oryx's key order was checked against real layouts fetched from the Oryx
GraphQL API (Voyager "default", Moonlander "pbrEb", ErgoDox EZ "Wadz",
Planck EZ "default"/"yoxAo"):

* Split boards (Voyager, Moonlander, ErgoDox EZ) are **half-major**: every key
  of the left half (rows top to bottom, then the thumb cluster), then the
  right half in the same way. This differs from QMK's interleaved ``LAYOUT``
  macro for Voyager and Moonlander. It matches ``LAYOUT_ergodox``.
* Planck EZ is row-major with 47 keys (2u space bar at index 41).

Thumb clusters are rotated as a rigid group around a pivot. QMK's JSON
doesn't carry rotation, so the angles are approximations of the hardware.

Oryx key events carry the matrix ``(row, col)``. ``matrix_to_index`` maps
those to Oryx indices.
"""

from __future__ import annotations

import math

from ..models import Finger, KeyboardModel, KeyGeometry

# Entry: (row, col, x, y, w, h)
_Entry = tuple[int, int, float, float, float, float]


def _rows(rows: list[tuple[int, list[int]]], xs: list[float], ys: list[float],
          y0: float) -> list[_Entry]:
    out: list[_Entry] = []
    for r_index, (mrow, mcols) in enumerate(rows):
        for i, mcol in enumerate(mcols):
            out.append((mrow, mcol, xs[i], y0 + r_index + ys[i], 1.0, 1.0))
    return out


def _cluster(keys: list[tuple[int, int, float, float, float, float]],
             pivot: tuple[float, float], angle: float) -> list[tuple]:
    """Rotate keys given in pivot-local coords (top-left x, y, w, h) clockwise
    by ``angle`` degrees around ``pivot``. Returns (row, col, KeyGeometry-args)."""
    th = math.radians(angle)
    c, s = math.cos(th), math.sin(th)
    out = []
    for mrow, mcol, lx, ly, w, h in keys:
        cx, cy = lx + w / 2, ly + h / 2
        rx, ry = cx * c - cy * s, cx * s + cy * c
        out.append((mrow, mcol, round(pivot[0] + rx - w / 2, 4),
                    round(pivot[1] + ry - h / 2, 4), w, h, angle))
    return out


def _flat(entries: list[_Entry]) -> list[tuple]:
    return [(r, c, x, y, w, h, 0.0) for r, c, x, y, w, h in entries]


# --- Voyager (52 keys) ----------------------------------------------------------

def _voyager() -> list[tuple]:
    lx = [0, 1, 2, 3, 4, 5]
    ly = [0.5, 0.5, 0.25, 0, 0.25, 0.5]
    rx = [10, 11, 12, 13, 14, 15]
    ry = [0.5, 0.25, 0, 0.25, 0.5, 0.5]
    left = _rows([(0, [1, 2, 3, 4, 5, 6]), (1, [1, 2, 3, 4, 5, 6]),
                  (2, [1, 2, 3, 4, 5, 6])], lx, ly, 0)
    left += [(3, 1, 0, 3.5, 1, 1), (3, 2, 1, 3.5, 1, 1), (3, 3, 2, 3.25, 1, 1),
             (3, 4, 3, 3, 1, 1), (3, 5, 4, 3.25, 1, 1), (4, 4, 5, 3.5, 1, 1)]
    right = _rows([(6, [0, 1, 2, 3, 4, 5]), (7, [0, 1, 2, 3, 4, 5]),
                   (8, [0, 1, 2, 3, 4, 5])], rx, ry, 0)
    right += [(10, 2, 10, 3.5, 1, 1), (9, 1, 11, 3.25, 1, 1), (9, 2, 12, 3, 1, 1),
              (9, 3, 13, 3.25, 1, 1), (9, 4, 14, 3.5, 1, 1), (9, 5, 15, 3.5, 1, 1)]
    lthumb = _cluster([(5, 0, 0, 0, 1, 1), (5, 1, 1, 0, 1, 1)], (5.0, 4.6), 15)
    rthumb = _cluster([(11, 5, -2, 0, 1, 1), (11, 6, -1, 0, 1, 1)], (11.0, 4.6), -15)
    return _flat(left) + lthumb + _flat(right) + rthumb


# --- Moonlander (72 keys) -------------------------------------------------------

def _moonlander() -> list[tuple]:
    lx = [0, 1, 2, 3, 4, 5, 6]
    ly = [0.375, 0.375, 0.125, 0, 0.125, 0.25, 0.25]
    rx = [10, 11, 12, 13, 14, 15, 16]
    ry = [0.25, 0.25, 0.125, 0, 0.125, 0.375, 0.375]
    left = _rows([(0, list(range(7))), (1, list(range(7))), (2, list(range(7))),
                  (3, list(range(6))), (4, list(range(5)))], lx, ly, 0)
    right = _rows([(6, list(range(7))), (7, list(range(7))), (8, list(range(7)))], rx, ry, 0)
    right += [(9, c, rx[c], 3 + ry[c], 1, 1) for c in range(1, 7)]
    # Row 4 of the right half: red thumb key first (QMK/Oryx order), then 5 keys.
    rrow4 = [(10, c, rx[c], 4 + ry[c], 1, 1) for c in range(2, 7)]
    lthumb = _cluster([(5, 3, 0, 0, 2, 1),                      # red key
                       (5, 0, 0, 1, 1, 1.5), (5, 1, 1, 1, 1, 1.5), (5, 2, 2, 1, 1, 1.5)],
                      (5.4, 4.7), 25)
    rred = _cluster([(11, 3, -2, 0, 2, 1)], (11.6, 4.7), -25)
    rthumb = _cluster([(11, 4, -3, 1, 1, 1.5), (11, 5, -2, 1, 1, 1.5),
                       (11, 6, -1, 1, 1, 1.5)], (11.6, 4.7), -25)
    return _flat(left) + lthumb + _flat(right) + rred + _flat(rrow4) + rthumb


# --- ErgoDox EZ (76 keys, LAYOUT_ergodox order) ----------------------------------

def _ergodox() -> list[tuple]:
    left: list[_Entry] = [
        (0, 0, 0, 0.375, 1.5, 1), (1, 0, 1.5, 0.375, 1, 1), (2, 0, 2.5, 0.125, 1, 1),
        (3, 0, 3.5, 0, 1, 1), (4, 0, 4.5, 0.125, 1, 1), (5, 0, 5.5, 0.25, 1, 1),
        (6, 0, 6.5, 0.25, 1, 1),
        (0, 1, 0, 1.375, 1.5, 1), (1, 1, 1.5, 1.375, 1, 1), (2, 1, 2.5, 1.125, 1, 1),
        (3, 1, 3.5, 1, 1, 1), (4, 1, 4.5, 1.125, 1, 1), (5, 1, 5.5, 1.25, 1, 1),
        (6, 1, 6.5, 1.25, 1, 1.5),
        (0, 2, 0, 2.375, 1.5, 1), (1, 2, 1.5, 2.375, 1, 1), (2, 2, 2.5, 2.125, 1, 1),
        (3, 2, 3.5, 2, 1, 1), (4, 2, 4.5, 2.125, 1, 1), (5, 2, 5.5, 2.25, 1, 1),
        (0, 3, 0, 3.375, 1.5, 1), (1, 3, 1.5, 3.375, 1, 1), (2, 3, 2.5, 3.125, 1, 1),
        (3, 3, 3.5, 3, 1, 1), (4, 3, 4.5, 3.125, 1, 1), (5, 3, 5.5, 3.25, 1, 1),
        (6, 3, 6.5, 2.75, 1, 1.5),
        (0, 4, 0.5, 4.375, 1, 1), (1, 4, 1.5, 4.375, 1, 1), (2, 4, 2.5, 4.125, 1, 1),
        (3, 4, 3.5, 4, 1, 1), (4, 4, 4.5, 4.125, 1, 1),
    ]
    lthumb = _cluster([(5, 5, 1, 0, 1, 1), (6, 5, 2, 0, 1, 1), (4, 5, 2, 1, 1, 1),
                       (3, 5, 0, 1, 1, 2), (2, 5, 1, 1, 1, 2), (1, 5, 2, 2, 1, 1)],
                      (5.5, 4.9), 25)
    right: list[_Entry] = [
        (7, 0, 9.5, 0.25, 1, 1), (8, 0, 10.5, 0.25, 1, 1), (9, 0, 11.5, 0.125, 1, 1),
        (10, 0, 12.5, 0, 1, 1), (11, 0, 13.5, 0.125, 1, 1), (12, 0, 14.5, 0.375, 1, 1),
        (13, 0, 15.5, 0.375, 1.5, 1),
        (7, 1, 9.5, 1.25, 1, 1.5), (8, 1, 10.5, 1.25, 1, 1), (9, 1, 11.5, 1.125, 1, 1),
        (10, 1, 12.5, 1, 1, 1), (11, 1, 13.5, 1.125, 1, 1), (12, 1, 14.5, 1.375, 1, 1),
        (13, 1, 15.5, 1.375, 1.5, 1),
        (8, 2, 10.5, 2.25, 1, 1), (9, 2, 11.5, 2.125, 1, 1), (10, 2, 12.5, 2, 1, 1),
        (11, 2, 13.5, 2.125, 1, 1), (12, 2, 14.5, 2.375, 1, 1), (13, 2, 15.5, 2.375, 1.5, 1),
        (7, 3, 9.5, 2.75, 1, 1.5), (8, 3, 10.5, 3.25, 1, 1), (9, 3, 11.5, 3.125, 1, 1),
        (10, 3, 12.5, 3, 1, 1), (11, 3, 13.5, 3.125, 1, 1), (12, 3, 14.5, 3.375, 1, 1),
        (13, 3, 15.5, 3.375, 1.5, 1),
        (9, 4, 11.5, 4.125, 1, 1), (10, 4, 12.5, 4, 1, 1), (11, 4, 13.5, 4.125, 1, 1),
        (12, 4, 14.5, 4.375, 1, 1), (13, 4, 15.5, 4.375, 1, 1),
    ]
    rthumb = _cluster([(7, 5, -3, 0, 1, 1), (8, 5, -2, 0, 1, 1), (9, 5, -3, 1, 1, 1),
                       (12, 5, -3, 2, 1, 1), (11, 5, -2, 1, 1, 2), (10, 5, -1, 1, 1, 2)],
                      (11.5, 4.9), -25)
    return _flat(left) + lthumb + _flat(right) + rthumb


# --- Planck EZ (47 keys, LAYOUT_planck_1x2uC) ----------------------------------------

def _planck() -> list[tuple]:
    out: list[_Entry] = []
    for r in range(3):
        for c in range(12):
            mrow, mcol = (r, c) if c < 6 else (r + 4, c - 6)
            out.append((mrow, mcol, c, r, 1, 1))
    out += [(3, 0, 0, 3, 1, 1), (3, 1, 1, 3, 1, 1), (3, 2, 2, 3, 1, 1),
            (7, 3, 3, 3, 1, 1), (7, 4, 4, 3, 1, 1), (7, 5, 5, 3, 2, 1),
            (7, 0, 7, 3, 1, 1), (7, 1, 8, 3, 1, 1), (7, 2, 9, 3, 1, 1),
            (3, 3, 10, 3, 1, 1), (3, 4, 11, 3, 1, 1)]
    return _flat(out)


_RAW: dict[KeyboardModel, list[tuple]] = {
    KeyboardModel.VOYAGER: _voyager(),
    KeyboardModel.MOONLANDER: _moonlander(),
    KeyboardModel.ERGODOX_EZ: _ergodox(),
    KeyboardModel.PLANCK_EZ: _planck(),
}

KEY_COUNTS: dict[KeyboardModel, int] = {
    KeyboardModel.VOYAGER: 52,
    KeyboardModel.MOONLANDER: 72,
    KeyboardModel.ERGODOX_EZ: 76,
    KeyboardModel.PLANCK_EZ: 47,
}

GEOMETRIES: dict[KeyboardModel, tuple[KeyGeometry, ...]] = {
    model: tuple(KeyGeometry(index=i, x=float(x), y=float(y), w=float(w), h=float(h),
                             rotation=float(rot))
                 for i, (_r, _c, x, y, w, h, rot) in enumerate(entries))
    for model, entries in _RAW.items()
}

MATRIX_TO_INDEX: dict[KeyboardModel, dict[tuple[int, int], int]] = {
    model: {(r, c): i for i, (r, c, *_rest) in enumerate(entries)}
    for model, entries in _RAW.items()
}


# --- Finger map ----------------------------------------------------------------
#
# Which finger presses each key, for the ergo score. Built from the geometry:
#
# * Thumbs are the rotated clusters (Voyager, Moonlander incl. the red keys,
#   ErgoDox: every entry with a rotation) and, on the Planck, the bottom-row
#   keys with x from 3 to 8 (the 2u space at x=5 is a left thumb).
# * The hand is the half the key belongs to: Oryx index below half the key
#   count is left on split boards; on the Planck the key's left edge x < 6.
# * Other keys get a finger from their column counted from the outer edge
#   ``c`` (left: x, right: mirrored). Voyager/Planck: pinky, pinky, ring,
#   middle, index, index (Moonlander adds a third index column). ErgoDox:
#   c < 2 pinky, < 3 ring, < 4 middle, else index.
# * Home row: the 3rd row of the main block (Planck: the 2nd). Home keys are
#   the home-row keys at the "A S D F" columns: pinky c=1, ring 2, middle 3,
#   index 4 (ErgoDox +0.5). The thumb home is the thumb key nearest the index
#   home key.

_COLUMN_FINGERS = ("pinky", "pinky", "ring", "middle", "index", "index", "index")
_PLANCK_THUMB_X = (3.0, 8.0)


def _build_fingers(model: KeyboardModel) -> tuple[tuple[Finger, ...], dict[Finger, int]]:
    entries = _RAW[model]
    n = len(entries)
    fingers: list[Finger] = []
    cols: list[float] = []       # column from the outer edge
    left_of: list[bool] = []
    for i, (_r, _c, x, y, w, _h, rot) in enumerate(entries):
        if model == KeyboardModel.PLANCK_EZ:
            left = x < 6
            thumb = y == 3 and _PLANCK_THUMB_X[0] <= x <= _PLANCK_THUMB_X[1]
            c = x if left else 11 - x
        else:
            left = i < n // 2
            thumb = rot != 0
            if model == KeyboardModel.ERGODOX_EZ:
                c = x if left else 16 - x
            else:
                last = 15 if model == KeyboardModel.VOYAGER else 16
                c = x if left else last - x
        left_of.append(left)
        cols.append(c)
        if thumb:
            fingers.append(Finger.L_THUMB if left else Finger.R_THUMB)
            continue
        if model == KeyboardModel.ERGODOX_EZ:
            name = "pinky" if c < 2 else "ring" if c < 3 else "middle" if c < 4 else "index"
        else:
            name = _COLUMN_FINGERS[int(round(c))] if model != KeyboardModel.PLANCK_EZ \
                else _COLUMN_FINGERS[min(int(round(c)), 5)]
        fingers.append(Finger(f"{'l' if left else 'r'}-{name}"))

    def home_row(i: int) -> bool:
        r, c, _x, y, *_ = entries[i]
        if model == KeyboardModel.PLANCK_EZ:
            return y == 1
        if model == KeyboardModel.ERGODOX_EZ:
            return c == 2
        return r in (2, 8)

    offset = 0.5 if model == KeyboardModel.ERGODOX_EZ else 0.0
    home_c = {"pinky": 1 + offset, "ring": 2 + offset, "middle": 3 + offset,
              "index": 4 + offset}
    homes: dict[Finger, int] = {}
    for i, f in enumerate(fingers):
        name = f.value.split("-")[1]
        if name != "thumb" and home_row(i) and cols[i] == home_c[name]:
            homes[f] = i

    def centre(i: int) -> tuple[float, float]:
        _r, _c, x, y, w, h, _rot = entries[i]
        return x + w / 2, y + h / 2

    for thumb, index in ((Finger.L_THUMB, Finger.L_INDEX), (Finger.R_THUMB, Finger.R_INDEX)):
        ix, iy = centre(homes[index])
        homes[thumb] = min((i for i, f in enumerate(fingers) if f == thumb),
                           key=lambda i: (math.hypot(centre(i)[0] - ix, centre(i)[1] - iy), i))
    return tuple(fingers), homes


FINGERS: dict[KeyboardModel, tuple[Finger, ...]] = {}
HOME_KEYS: dict[KeyboardModel, dict[Finger, int]] = {}
for _model in _RAW:
    FINGERS[_model], HOME_KEYS[_model] = _build_fingers(_model)


def geometry_for(model: KeyboardModel) -> tuple[KeyGeometry, ...]:
    return GEOMETRIES.get(model, ())


def matrix_to_index(model: KeyboardModel, row: int, col: int) -> int | None:
    """Oryx key index for a matrix position reported in KEYDOWN/KEYUP events."""
    return MATRIX_TO_INDEX.get(model, {}).get((row, col))


__all__ = ["FINGERS", "HOME_KEYS", "GEOMETRIES", "MATRIX_TO_INDEX", "KEY_COUNTS", "geometry_for", "matrix_to_index"]
