"""Layout coach (SPEC: layout coach). Pure, no GTK, no I/O, no service access:
it only turns recorded counts into suggestions and never writes to the keyboard.

The inputs are those of ``ergo.report`` (counts and pairs keyed by
``(layer, Oryx key index)``, geometry, finger map, home keys). ``hints`` returns
at most ``THRESHOLDS["max_hints"]`` rule-based ``CoachHint`` objects, each
carrying the numbers behind it (see ``CoachHintKind`` in models.py).

Gates: no hints below ``COACH_MIN_PRESSES`` presses (on keys with a finger);
SAME_FINGER also needs ``COACH_MIN_PAIRS`` pairs.

Rules (all limits in ``THRESHOLDS``):

* SWAP: two keys on one layer trade places. Only keys of kind ``OTHER`` (see
  ``keykinds``: no letters, modifiers, layer keys or keys with a hold action)
  swap, and the lighter partner may also be a blank or transparent key (a "move
  here"). Letters are compared as whole layouts in ``layouts`` instead. The
  candidate heavy keys (the most pressed ones) are tried against every lighter
  partner of their layer; a cheap
  count-only score picks the best ``swap_rescore`` candidates, and those are
  rescored with ``ergo.report`` after swapping the two keys' counts and
  remapping the pairs. ``score_delta`` = score after - score before, each
  from ``ergo.report``. Each delta is against the current layout (not
  cumulative), the chosen swaps share no key, best gain first.
* HAND_IMBALANCE: the busier hand does more than ``imbalance_share``.
* SAME_FINGER: the most frequent same-finger pairs (a pair and its reverse
  count as one; the more frequent direction gives the key order). At least one
  key must not be a letter: letter-letter pairs are left to the layout comparison.
* FAR_KEY: a key with a high share, enough presses and a long reach; never a
  letter.
* UNUSED_PRIME_KEY: a home-row or thumb key that is barely used on a layer
  that takes a real share of all presses; never a letter (modifiers, layer, hold,
  blank and other keys qualify).

Order: SWAP, HAND_IMBALANCE, SAME_FINGER, FAR_KEY, UNUSED_PRIME_KEY (concrete
fixes with a measured gain first), inside a kind the strongest first, ties by
key index. The list is cut to ``max_hints``.

``layers`` (layer index -> its ``KeyBinding`` list, Oryx key indices) says what
each key is. Without it every key counts as ``OTHER`` (no letter protection).
"""

from __future__ import annotations

from typing import Iterable, Mapping, Sequence

from ..models import (COACH_MIN_PAIRS, COACH_MIN_PRESSES, CoachHint, CoachHintKind,
                      Finger, KeyBinding, KeyGeometry)
from . import ergo, geometries
from .keykinds import KeyKind, classify

THRESHOLDS: dict[str, float] = {
    # Total hints returned.
    "max_hints": 8,
    # FAR_KEY: a key must take at least this share of all presses, at least this
    # many presses, and sit at least this far from home (key units divided by
    # finger strength: 2.0 is one key away for a pinky, two for a middle finger).
    "far_min_share": 0.015,
    "far_min_presses": 300,
    "far_min_reach": 1.8,
    "far_max": 2,
    # UNUSED_PRIME_KEY: the layer takes at least this share of all presses and
    # the key at most this share (0.2%); at most this many hints.
    "prime_layer_min_share": 0.05,
    "prime_max_share": 0.002,
    "prime_max": 2,
    # HAND_IMBALANCE: the busier hand's share of presses (0.5 is balanced).
    "imbalance_share": 0.62,
    # SAME_FINGER: a pair needs this many occurrences and this share of all pairs.
    "same_min_count": 40,
    "same_min_rate": 0.003,
    "same_max": 2,
    # SWAP: the heavy key takes at least this share of all presses; at most
    # swap_heavy heavy keys are tried; swap_rescore candidates are rescored in
    # full; a swap must gain at least this many score points; at most swap_max.
    "swap_min_share": 0.01,
    "swap_heavy": 10,
    "swap_rescore": 12,
    "swap_min_gain": 1.0,
    "swap_max": 3,
}

Key = tuple[int, int]
Pair = tuple[Key, Key]

_KIND_ORDER = {CoachHintKind.SWAP: 0, CoachHintKind.HAND_IMBALANCE: 1,
               CoachHintKind.SAME_FINGER: 2, CoachHintKind.FAR_KEY: 3,
               CoachHintKind.UNUSED_PRIME_KEY: 4}


def hints(counts: Mapping[Key, int], pairs: Mapping[Pair, int],
          geometry: Sequence[KeyGeometry], fingers: Sequence[Finger],
          home_keys: Mapping[Finger, int],
          thresholds: Mapping[str, float] | None = None,
          layers: Mapping[int, Iterable[KeyBinding]] | None = None) -> list[CoachHint]:
    t = {**THRESHOLDS, **(thresholds or {})}
    kinds: dict[int, dict[int, KeyKind]] = (
        {} if layers is None else {l: {b.index: classify(b) for b in keys}
                                   for l, keys in layers.items()})

    def kind_of(layer: int, index: int) -> KeyKind:
        if layers is None:
            return KeyKind.OTHER
        return kinds.get(layer, {}).get(index, KeyKind.BLANK)
    base = ergo.report(counts, pairs, geometry, fingers, home_keys)
    presses = base.presses
    if presses < COACH_MIN_PRESSES:
        return []

    def has_finger(index: int) -> bool:
        return 0 <= index < len(fingers)

    def reach_of(index: int) -> float:
        return ergo.key_reach(index, geometry, fingers, home_keys)

    # Counts on keys with a finger only.
    cnt = {k: n for k, n in counts.items() if n > 0 and has_finger(k[1])}
    layer_presses: dict[int, int] = {}
    for (layer, _i), n in cnt.items():
        layer_presses[layer] = layer_presses.get(layer, 0) + n

    swaps = _swaps(cnt, pairs, geometry, fingers, home_keys, base.score, t, kind_of)
    out: list[tuple[tuple, CoachHint]] = []
    for rank, h in enumerate(swaps):
        out.append(((_KIND_ORDER[h.kind], rank), h))

    left = base.left_share
    busier = max(left, 1 - left)
    if busier >= t["imbalance_share"]:
        out.append(((_KIND_ORDER[CoachHintKind.HAND_IMBALANCE], 0), CoachHint(
            CoachHintKind.HAND_IMBALANCE, (),
            {"left_share": left, "right_share": 1 - left})))

    if base.pairs >= COACH_MIN_PAIRS:
        # A pair and its reverse (E then D, D then E) count as one.
        merged: dict[tuple[Key, Key], list] = {}
        for (a, b), n in pairs.items():
            fa = fingers[a[1]] if has_finger(a[1]) else None
            if n <= 0 or fa is None or fa != (fingers[b[1]] if has_finger(b[1]) else None) \
                    or a[1] == b[1]:
                continue
            if kind_of(*a) == KeyKind.LETTER and kind_of(*b) == KeyKind.LETTER:
                continue                    # letter-letter: see the layout comparison
            m = merged.setdefault((min(a, b), max(a, b)), [0, a, b, 0, fa])
            m[0] += n
            if n > m[3]:
                m[1], m[2], m[3] = a, b, n
        same = sorted((-m[0], m[1], m[2], m[4]) for m in merged.values()
                      if m[0] >= t["same_min_count"] and m[0] / base.pairs >= t["same_min_rate"])
        for rank, (nc, first, second, finger) in enumerate(same[:int(t["same_max"])]):
            out.append(((_KIND_ORDER[CoachHintKind.SAME_FINGER], rank), CoachHint(
                CoachHintKind.SAME_FINGER, (first, second),
                {"count": float(-nc), "rate": -nc / base.pairs}, finger)))

    far = []
    for key, n in cnt.items():
        if kind_of(*key) == KeyKind.LETTER:
            continue
        reach = reach_of(key[1])
        share = n / presses
        if (n >= t["far_min_presses"] and share >= t["far_min_share"]
                and reach >= t["far_min_reach"]):
            far.append((-(share * reach), key, n, share, reach))
    far.sort()
    for rank, (_s, key, n, share, reach) in enumerate(far[:int(t["far_max"])]):
        out.append(((_KIND_ORDER[CoachHintKind.FAR_KEY], rank), CoachHint(
            CoachHintKind.FAR_KEY, (key,),
            {"presses": float(n), "share": share, "reach": reach},
            fingers[key[1]])))

    prime = geometries.prime_keys(geometry, fingers, home_keys)
    unused = []
    for layer, lp in layer_presses.items():
        if lp / presses < t["prime_layer_min_share"]:
            continue
        for index in sorted(prime):
            if kind_of(layer, index) == KeyKind.LETTER:
                continue
            n = cnt.get((layer, index), 0)
            if n / presses <= t["prime_max_share"]:
                unused.append((-lp, n, layer, index))
    unused.sort()
    for rank, (_lp, n, layer, index) in enumerate(unused[:int(t["prime_max"])]):
        thumb = fingers[index] in (Finger.L_THUMB, Finger.R_THUMB)
        out.append(((_KIND_ORDER[CoachHintKind.UNUSED_PRIME_KEY], rank), CoachHint(
            CoachHintKind.UNUSED_PRIME_KEY, ((layer, index),),
            {"presses": float(n), "share": n / presses, "thumb": 1.0 if thumb else 0.0},
            fingers[index])))

    out.sort(key=lambda e: e[0])
    return [h for _o, h in out[:int(t["max_hints"])]]


def _swaps(cnt: Mapping[Key, int], pairs: Mapping[Pair, int],
           geometry: Sequence[KeyGeometry], fingers: Sequence[Finger],
           home_keys: Mapping[Finger, int], before: float,
           t: Mapping[str, float], kind_of) -> list[CoachHint]:
    presses = sum(cnt.values())
    reach = [ergo.key_reach(i, geometry, fingers, home_keys) for i in range(len(fingers))]
    kind = [ergo._kind(f) for f in fingers]            # noqa: SLF001
    cap = ergo.WEIGHTS["finger_capacity"]
    w_over, w_imb, w_reach = (ergo.WEIGHTS["finger_overload"], ergo.WEIGHTS["hand_imbalance"],
                              ergo.WEIGHTS["reach"])
    load: dict[Finger, int] = {f: 0 for f in Finger}
    reach_sum = 0.0
    for (_l, i), n in cnt.items():
        load[fingers[i]] += n
        reach_sum += n * reach[i]
    left_n = sum(n for f, n in load.items() if f.is_left)

    def count_penalty(load_: Mapping[Finger, int], left: int, rsum: float) -> float:
        over = sum(max(0.0, n / presses - cap[ergo._kind(f)]) for f, n in load_.items())  # noqa: SLF001
        return (w_over * over + w_imb * abs(left / presses - 0.5) * 2
                + w_reach * rsum / presses)

    base_pen = count_penalty(load, left_n, reach_sum)
    heavy = sorted((k for k, n in cnt.items() if n / presses >= t["swap_min_share"]
                    and kind_of(*k) == KeyKind.OTHER),
                   key=lambda k: (-cnt[k], k))[:int(t["swap_heavy"])]
    cands: list[tuple[float, Key, Key]] = []
    for hk in heavy:
        layer, hi = hk
        ch, fh = cnt[hk], fingers[hi]
        for ji in range(len(fingers)):
            if ji == hi or kind_of(layer, ji) not in (KeyKind.OTHER, KeyKind.BLANK):
                continue
            cj = cnt.get((layer, ji), 0)
            if cj >= ch:
                continue
            fj = fingers[ji]
            d = cj - ch                       # change of the heavy key's finger
            new = dict(load)
            new[fh] += d
            new[fj] -= d
            left = left_n + (d if fh.is_left else 0) - (d if fj.is_left else 0)
            rsum = reach_sum + d * reach[hi] - d * reach[ji]
            gain = base_pen - count_penalty(new, left, rsum)
            if gain > 0:
                cands.append((-gain, hk, (layer, ji)))
    cands.sort()

    scored: list[tuple[float, Key, Key, float]] = []
    for _g, a, b in cands[:int(t["swap_rescore"])]:
        after = ergo.report(_swap_counts(cnt, a, b), _swap_pairs(pairs, a, b),
                            geometry, fingers, home_keys).score
        delta = round(after - before, 1)
        if delta >= t["swap_min_gain"]:
            scored.append((-delta, a, b, after))
    scored.sort()

    out: list[CoachHint] = []
    used: set[Key] = set()
    for nd, a, b, after in scored:
        if a in used or b in used:
            continue
        used.update((a, b))
        out.append(CoachHint(CoachHintKind.SWAP, (a, b),
                             {"score_before": before, "score_after": after},
                             score_delta=-nd))
        if len(out) >= t["swap_max"]:
            break
    return out


def _swap_counts(cnt: Mapping[Key, int], a: Key, b: Key) -> dict[Key, int]:
    out = dict(cnt)
    out.pop(a, None)
    out.pop(b, None)
    if cnt.get(a):
        out[b] = cnt[a]
    if cnt.get(b):
        out[a] = cnt[b]
    return out


def _swap_pairs(pairs: Mapping[Pair, int], a: Key, b: Key) -> dict[Pair, int]:
    def m(k: Key) -> Key:
        return b if k == a else a if k == b else k

    out: dict[Pair, int] = {}
    for (x, y), n in pairs.items():
        key = (m(x), m(y))
        out[key] = out.get(key, 0) + n
    return out


__all__ = ["THRESHOLDS", "hints"]
