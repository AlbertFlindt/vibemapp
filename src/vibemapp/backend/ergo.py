"""Ergonomic score of recorded typing (SPEC: ergo score). Pure, no GTK.

The inputs are per-key press counts and per-pair counts (a pair is "key B
pressed within about a second after key A"), both keyed by
``(layer, Oryx key index)``, plus the board's geometry, finger map and home
keys (``backend.geometries``). Presses on keys without a finger are ignored.
The score starts at 100 and every metric below takes a weighted deduction
(``WEIGHTS``); the result is clamped to 0..100 and rounded to one decimal.

Metrics, with P the presses and N the pairs:

* ``finger_load[f]``: presses of finger f / P.
* ``left_share``: presses of the left hand / P.
* ``finger_overload`` penalty: Σ over fingers of max(0, load - capacity),
  times ``WEIGHTS["finger_overload"]``. A finger is overloaded when it takes
  more than its comfortable share of all presses.
* ``hand_imbalance`` penalty: |left_share - 0.5| * 2 (0 balanced, 1 one hand
  only), times ``WEIGHTS["hand_imbalance"]``.
* ``same_finger_rate``: pairs where both keys use the same finger but are
  different key indexes / N (the same key twice, or the same index on another
  layer, is not a same-finger pair). Penalty ``WEIGHTS["same_finger"]`` times
  the rate. ``worst_same_finger`` lists the five most frequent such pairs.
* ``reach_cost``: mean over presses of (distance in key units from the key
  centre to its finger's home key centre) / finger strength. Home keys cost
  0, a weak finger stretching costs more. Penalty ``WEIGHTS["reach"]`` times it.
* ``layer_switch_rate``: pairs whose two keys are on different layers / N.
  Penalty ``WEIGHTS["layer_switch"]`` times the rate.

With no presses all shares are 0 and the score is 100. With no pairs the
pair-based rates are 0.
"""

from __future__ import annotations

import math
from typing import Mapping, Sequence

from ..models import ErgoReport, Finger, KeyGeometry, KeyPairCount

# Finger kind -> value, where the kind is the part after "l-"/"r-" of Finger.value.
WEIGHTS: dict[str, object] = {
    # How strong a finger is: the reach distance is divided by it, so weak fingers pay more.
    "finger_strength": {"pinky": 0.5, "ring": 0.7, "middle": 1.0, "index": 1.0, "thumb": 0.9},
    # The maximum comfortable share of all presses one finger should take.
    "finger_capacity": {"pinky": 0.08, "ring": 0.11, "middle": 0.16, "index": 0.20,
                        "thumb": 0.20},
    # Penalty points per unit of: summed share above capacity (0..1),
    "finger_overload": 100.0,
    # hand imbalance (0 balanced .. 1 one hand),
    "hand_imbalance": 50.0,
    # same-finger pair rate (0..1),
    "same_finger": 300.0,
    # mean strength-weighted reach in key units,
    "reach": 15.0,
    # and layer-switching pair rate (0..1).
    "layer_switch": 20.0,
}

PENALTY_KEYS = ("finger_overload", "hand_imbalance", "same_finger", "reach", "layer_switch")

Key = tuple[int, int]
Pair = tuple[Key, Key]


def _kind(finger: Finger) -> str:
    return finger.value.split("-", 1)[1]


def _centre(g: KeyGeometry) -> tuple[float, float]:
    return g.x + g.w / 2, g.y + g.h / 2


def key_reach(index: int, geometry: Sequence[KeyGeometry], fingers: Sequence[Finger],
              home_keys: Mapping[Finger, int]) -> float:
    """Strength-weighted distance (key units) from key ``index`` to its finger's
    home key. 0 for home keys and for keys with no finger or home."""
    if not 0 <= index < len(fingers) or index >= len(geometry):
        return 0.0
    finger = fingers[index]
    home = home_keys.get(finger)
    if home is None or not 0 <= home < len(geometry):
        return 0.0
    kx, ky = _centre(geometry[index])
    hx, hy = _centre(geometry[home])
    strength = WEIGHTS["finger_strength"][_kind(finger)]       # type: ignore[index]
    return math.hypot(kx - hx, ky - hy) / strength


def report(counts: Mapping[Key, int], pairs: Mapping[Pair, int],
           geometry: Sequence[KeyGeometry], fingers: Sequence[Finger],
           home_keys: Mapping[Finger, int]) -> ErgoReport:
    def finger_of(index: int) -> Finger | None:
        return fingers[index] if 0 <= index < len(fingers) else None

    load_counts: dict[Finger, int] = {f: 0 for f in Finger}
    presses = 0
    reach_sum = 0.0
    for (_layer, index), n in counts.items():
        f = finger_of(index)
        if f is None or n <= 0:
            continue
        presses += n
        load_counts[f] += n
        reach_sum += n * key_reach(index, geometry, fingers, home_keys)
    finger_load = {f: (n / presses if presses else 0.0) for f, n in load_counts.items()}
    left_share = (sum(n for f, n in load_counts.items() if f.is_left) / presses
                  if presses else 0.0)
    reach_cost = reach_sum / presses if presses else 0.0

    capacity = WEIGHTS["finger_capacity"]
    overload = sum(max(0.0, load - capacity[_kind(f)])        # type: ignore[index]
                   for f, load in finger_load.items())
    imbalance = abs(left_share - 0.5) * 2 if presses else 0.0

    total_pairs = 0
    same = 0
    switches = 0
    same_pairs: list[KeyPairCount] = []
    for (a, b), n in pairs.items():
        if n <= 0:
            continue
        total_pairs += n
        if a[0] != b[0]:
            switches += n
        fa, fb = finger_of(a[1]), finger_of(b[1])
        if fa is not None and fa == fb and a[1] != b[1]:
            same += n
            same_pairs.append(KeyPairCount(a, b, n, fa))
    same_rate = same / total_pairs if total_pairs else 0.0
    switch_rate = switches / total_pairs if total_pairs else 0.0
    same_pairs.sort(key=lambda p: (-p.count, p.first, p.second))

    penalties = {
        "finger_overload": WEIGHTS["finger_overload"] * overload,      # type: ignore[operator]
        "hand_imbalance": WEIGHTS["hand_imbalance"] * imbalance,       # type: ignore[operator]
        "same_finger": WEIGHTS["same_finger"] * same_rate,             # type: ignore[operator]
        "reach": WEIGHTS["reach"] * reach_cost,                        # type: ignore[operator]
        "layer_switch": WEIGHTS["layer_switch"] * switch_rate,         # type: ignore[operator]
    }
    score = round(max(0.0, min(100.0, 100.0 - sum(penalties.values()))), 1)
    return ErgoReport(
        presses=presses, pairs=total_pairs, finger_load=finger_load, left_share=left_share,
        same_finger_rate=same_rate, worst_same_finger=tuple(same_pairs[:5]),
        reach_cost=reach_cost, layer_switch_rate=switch_rate, score=score,
        penalties=penalties)


__all__ = ["WEIGHTS", "PENALTY_KEYS", "key_reach", "report"]
