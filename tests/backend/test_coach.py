from __future__ import annotations

import unittest

from vibemapp.backend import coach, ergo, geometries
from vibemapp.models import (COACH_MIN_PAIRS, COACH_MIN_PRESSES, CoachHintKind, Finger,
                             KeyBinding, KeyboardModel)

MODEL = KeyboardModel.VOYAGER
GEO = geometries.geometry_for(MODEL)
FINGERS = geometries.FINGERS[MODEL]
HOMES = geometries.HOME_KEYS[MODEL]
PRIME = geometries.prime_keys(GEO, FINGERS, HOMES)
HOME_LIST = sorted(i for i in HOMES.values() if FINGERS[i] not in (Finger.L_THUMB, Finger.R_THUMB))


def reach(i: int) -> float:
    return ergo.key_reach(i, GEO, FINGERS, HOMES)


# A key far from home (not a thumb), and another key on the same finger.
FAR = max((i for i in range(len(FINGERS)) if i not in PRIME), key=lambda i: (reach(i), -i))
SAME = next(i for i in range(len(FINGERS)) if i != FAR and FINGERS[i] == FINGERS[FAR])
NON_PRIME = [i for i in range(len(FINGERS)) if i not in PRIME]


def base_counts(total: int = 10000) -> dict:
    """Evenly spread over the home keys: balanced, no reach."""
    per, rest = divmod(total, len(HOME_LIST))
    return {(0, i): per + (rest if j == 0 else 0) for j, i in enumerate(HOME_LIST)}


def kinds(hs, kind):
    return [h for h in hs if h.kind == kind]


def run(counts, pairs=None, **th):
    return coach.hints(counts, pairs or {}, GEO, FINGERS, HOMES, th or None)


class GateTests(unittest.TestCase):
    def test_below_min_presses_is_empty(self) -> None:
        c = base_counts(8000)
        c[(0, FAR)] = 3000
        self.assertGreaterEqual(sum(c.values()), COACH_MIN_PRESSES)
        self.assertTrue(run(c))
        small = {(0, FAR): COACH_MIN_PRESSES - 1}
        self.assertEqual(run(small), [])
        self.assertTrue(run({(0, FAR): COACH_MIN_PRESSES}))     # the edge passes

    def test_unknown_keys_do_not_count(self) -> None:
        self.assertEqual(run({(0, FAR): 100, (0, 999): 5000}), [])

    def test_empty(self) -> None:
        self.assertEqual(run({}), [])


class FarKeyTests(unittest.TestCase):
    def far_hints(self, n: int, total: int = 10000):
        c = base_counts(total - n)
        c[(0, FAR)] = n
        return kinds(run(c), CoachHintKind.FAR_KEY)

    def test_fires_with_numbers(self) -> None:
        (h,) = self.far_hints(600)
        self.assertEqual(h.keys, ((0, FAR),))
        self.assertEqual(h.numbers["presses"], 600)
        self.assertEqual(h.finger, FINGERS[FAR])
        self.assertAlmostEqual(h.numbers["share"], 0.06)
        self.assertAlmostEqual(h.numbers["reach"], reach(FAR))

    def test_presses_edge(self) -> None:
        self.assertEqual(len(self.far_hints(300)), 1)
        self.assertEqual(self.far_hints(299), [])

    def test_share_edge(self) -> None:
        # 300 presses: share 0.015 at 20000 presses total, below it at 20001
        self.assertEqual(len(self.far_hints(300, 20000)), 1)
        self.assertEqual(self.far_hints(300, 20001), [])

    def test_reach_edge(self) -> None:
        c = base_counts(9000)
        c[(0, FAR)] = 1000
        self.assertEqual(len(kinds(run(c, far_min_reach=reach(FAR)), CoachHintKind.FAR_KEY)), 1)
        self.assertEqual(kinds(run(c, far_min_reach=reach(FAR) + 0.01), CoachHintKind.FAR_KEY), [])

    def test_home_key_is_not_far(self) -> None:
        c = base_counts(4000)
        c[(0, HOME_LIST[0])] = 6000
        self.assertEqual(kinds(run(c), CoachHintKind.FAR_KEY), [])


class UnusedPrimeTests(unittest.TestCase):
    KEY = HOME_LIST[0]

    def unused(self, layer1: int, key_presses: int):
        c = base_counts(10000 - layer1)
        c[(1, FAR)] = layer1 - key_presses if layer1 > key_presses else 0
        if key_presses:
            c[(1, self.KEY)] = key_presses
        total = sum(c.values())
        self.assertEqual(total, 10000)
        hs = run(c, prime_max=1000, max_hints=100)
        return [h for h in kinds(hs, CoachHintKind.UNUSED_PRIME_KEY) if h.keys == ((1, self.KEY),)]

    def test_fires(self) -> None:
        (h,) = self.unused(1000, 0)
        self.assertEqual(h.numbers["presses"], 0)
        self.assertEqual(h.numbers["thumb"], 0.0)
        self.assertEqual(h.finger, FINGERS[self.KEY])

    def test_thumb_flag(self) -> None:
        thumb = next(iter(i for i in PRIME if FINGERS[i] in (Finger.L_THUMB, Finger.R_THUMB)))
        c = base_counts(9000)
        c[(1, FAR)] = 1000
        hs = [h for h in run(c, prime_max=1000, max_hints=100) if h.keys == ((1, thumb),)]
        self.assertEqual(hs[0].numbers["thumb"], 1.0)
        self.assertEqual(hs[0].finger, FINGERS[thumb])

    def test_layer_share_edge(self) -> None:
        self.assertEqual(len(self.unused(500, 0)), 1)          # exactly 5%
        self.assertEqual(self.unused(499, 0), [])

    def test_key_share_edge(self) -> None:
        self.assertEqual(len(self.unused(1000, 20)), 1)        # exactly 0.2%
        self.assertEqual(self.unused(1000, 21), [])

    def test_non_prime_key_never(self) -> None:
        c = base_counts(9000)
        c[(1, FAR)] = 1000
        for h in run(c, prime_max=1000, max_hints=100):
            if h.kind == CoachHintKind.UNUSED_PRIME_KEY:
                self.assertIn(h.keys[0][1], PRIME)
        self.assertNotIn(FAR, PRIME)


class ImbalanceTests(unittest.TestCase):
    def hand(self, left: int):
        lefts = [i for i in HOME_LIST if FINGERS[i].is_left]
        rights = [i for i in HOME_LIST if not FINGERS[i].is_left]
        c = {(0, lefts[0]): left, (0, rights[0]): 10000 - left}
        return kinds(run(c), CoachHintKind.HAND_IMBALANCE)

    def test_edge(self) -> None:
        (h,) = self.hand(6200)
        self.assertEqual(h.keys, ())
        self.assertAlmostEqual(h.numbers["left_share"], 0.62)
        self.assertAlmostEqual(h.numbers["right_share"], 0.38)
        self.assertEqual(self.hand(6199), [])
        self.assertEqual(len(self.hand(3800)), 1)             # right hand busier too
        self.assertEqual(self.hand(3801), [])


class SameFingerTests(unittest.TestCase):
    def same(self, count: int, pairs_total: int = 1000, reverse: int = 0):
        c = base_counts()
        pairs = {((0, FAR), (0, SAME)): count,
                 ((0, HOME_LIST[0]), (0, HOME_LIST[0])): pairs_total - count - reverse}
        if reverse:
            pairs[((0, SAME), (0, FAR))] = reverse
        return kinds(run(c, pairs), CoachHintKind.SAME_FINGER)

    def test_fires(self) -> None:
        (h,) = self.same(100)
        self.assertEqual(h.keys, ((0, FAR), (0, SAME)))
        self.assertEqual(h.finger, FINGERS[FAR])
        self.assertEqual(h.numbers["count"], 100)
        self.assertAlmostEqual(h.numbers["rate"], 0.1)

    def test_count_edge(self) -> None:
        self.assertEqual(len(self.same(40)), 1)
        self.assertEqual(self.same(39), [])

    def test_rate_edge(self) -> None:
        self.assertEqual(len(self.same(40, 13333)), 1)         # 0.003 of 13333 = 39.99
        self.assertEqual(self.same(40, 13334), [])

    def test_reverse_pair_merged(self) -> None:
        (h,) = self.same(30, reverse=20)
        self.assertEqual(h.numbers["count"], 50)
        self.assertEqual(h.keys, ((0, FAR), (0, SAME)))        # the more frequent direction

    def test_needs_min_pairs(self) -> None:
        self.assertEqual(len(self.same(100, COACH_MIN_PAIRS)), 1)
        self.assertEqual(self.same(100, COACH_MIN_PAIRS - 1), [])

    def test_same_key_and_other_layer_ignored(self) -> None:
        c = base_counts()
        pairs = {((0, FAR), (0, FAR)): 300, ((0, FAR), (1, FAR)): 300}
        self.assertEqual(kinds(run(c, pairs), CoachHintKind.SAME_FINGER), [])


class SwapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.counts = base_counts(7000)
        self.counts[(0, FAR)] = 3000
        self.pairs = {((0, FAR), (0, SAME)): 200, ((0, SAME), (0, FAR)): 100,
                      ((0, HOME_LIST[0]), (0, HOME_LIST[1])): 1000}

    def swaps(self, **th):
        return kinds(run(self.counts, self.pairs, **th), CoachHintKind.SWAP)

    @staticmethod
    def swapped(counts, pairs, a, b):
        def m(k):
            return b if k == a else a if k == b else k
        c = {m(k): n for k, n in counts.items()}
        p: dict = {}
        for (x, y), n in pairs.items():
            p[(m(x), m(y))] = p.get((m(x), m(y)), 0) + n
        return c, p

    def test_delta_is_rescored_difference(self) -> None:
        hs = self.swaps()
        self.assertTrue(hs)
        before = ergo.report(self.counts, self.pairs, GEO, FINGERS, HOMES).score
        for h in hs:
            a, b = h.keys
            self.assertEqual(a[0], b[0])                       # same layer
            c, p = self.swapped(self.counts, self.pairs, a, b)
            after = ergo.report(c, p, GEO, FINGERS, HOMES).score
            self.assertEqual(h.numbers["score_before"], before)
            self.assertEqual(h.numbers["score_after"], after)
            self.assertEqual(h.score_delta, round(after - before, 1))
            self.assertGreaterEqual(h.score_delta, coach.THRESHOLDS["swap_min_gain"])

    def test_best_first_unique_keys_capped(self) -> None:
        hs = self.swaps()
        self.assertLessEqual(len(hs), coach.THRESHOLDS["swap_max"])
        deltas = [h.score_delta for h in hs]
        self.assertEqual(deltas, sorted(deltas, reverse=True))
        keys = [k for h in hs for k in h.keys]
        self.assertEqual(len(keys), len(set(keys)))

    def test_far_key_moves_toward_home(self) -> None:
        h = self.swaps()[0]
        self.assertIn((0, FAR), h.keys)
        other = next(k for k in h.keys if k != (0, FAR))
        self.assertLess(reach(other[1]), reach(FAR))

    def test_gain_edge(self) -> None:
        d = self.swaps()[0].score_delta
        self.assertTrue(self.swaps(swap_min_gain=d))
        self.assertEqual(self.swaps(swap_min_gain=999), [])

    def test_max_and_share(self) -> None:
        self.assertEqual(len(self.swaps(swap_max=1)), 1)
        self.assertEqual(self.swaps(swap_min_share=0.9), [])

    def test_no_swap_when_already_good(self) -> None:
        c = {(0, HOME_LIST[0]): 5000, (0, HOME_LIST[-1]): 5000}
        for h in kinds(run(c), CoachHintKind.SWAP):
            self.assertGreaterEqual(h.score_delta, 1.0)

    def test_fast(self) -> None:
        import time
        c = {(l, i): 10 + (i * 7 + l * 3) % 90 for l in range(4) for i in range(len(FINGERS))}
        p = {((l, i), (l, (i * 5) % len(FINGERS))): 20 for l in range(4)
             for i in range(len(FINGERS))}
        t = time.perf_counter()
        run(c, p)
        self.assertLess(time.perf_counter() - t, 0.5)


def make_layers(default="KC_ENTER", **special):
    """Layer 0 (and 1) with every key ``default`` except {index: code or KeyBinding}."""
    out = {}
    for layer in (0, 1):
        keys = []
        for i in range(len(FINGERS)):
            sp = special.get(f"k{i}")
            if isinstance(sp, KeyBinding):
                keys.append(sp)
            else:
                keys.append(KeyBinding(i, tap_label="x" if sp or default else "",
                                       tap_code=sp or default))
        out[layer] = keys
    return out


class KindFilterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.counts = base_counts(7000)
        self.counts[(0, FAR)] = 3000
        self.pairs = {((0, FAR), (0, SAME)): 200, ((0, HOME_LIST[0]), (0, HOME_LIST[1])): 1000}

    def hints(self, layers, kind):
        return kinds(coach.hints(self.counts, self.pairs, GEO, FINGERS, HOMES, layers=layers), kind)

    def test_other_keys_still_swap(self) -> None:
        self.assertTrue(self.hints(make_layers(), CoachHintKind.SWAP))
        self.assertTrue(self.hints(make_layers(), CoachHintKind.FAR_KEY))

    def test_letter_far_key_not_flagged_or_swapped(self) -> None:
        layers = make_layers(**{f"k{FAR}": "KC_E"})
        self.assertEqual(self.hints(layers, CoachHintKind.FAR_KEY), [])
        for h in self.hints(layers, CoachHintKind.SWAP):
            self.assertNotIn((0, FAR), h.keys)

    def test_ae_letter_not_flagged(self) -> None:
        self.assertEqual(self.hints(make_layers(**{f"k{FAR}": "DK_AE"}), CoachHintKind.FAR_KEY), [])

    def test_shift_and_layer_keys_never_swap(self) -> None:
        for code in ("KC_LSHIFT", "MO", "OSM"):
            self.assertEqual(self.hints(make_layers(**{f"k{FAR}": code}), CoachHintKind.SWAP), [], code)
        hold = KeyBinding(FAR, tap_label="Space", tap_code="KC_SPACE", hold_label="L1")
        self.assertEqual(self.hints(make_layers(**{f"k{FAR}": hold}), CoachHintKind.SWAP), [])

    def test_no_letter_partner(self) -> None:
        # Every key but the far one is a letter or a modifier: no swap partners.
        layers = make_layers(default="KC_E")
        layers[0][FAR] = KeyBinding(FAR, tap_label="x", tap_code="KC_ENTER")
        self.assertEqual(self.hints(layers, CoachHintKind.SWAP), [])
        layers = make_layers(default="KC_LSHIFT")
        layers[0][FAR] = KeyBinding(FAR, tap_label="x", tap_code="KC_ENTER")
        self.assertEqual(self.hints(layers, CoachHintKind.SWAP), [])

    def test_blank_partner_allowed(self) -> None:
        # Only the far key and a blank key near home are free; everything else is a letter.
        layers = make_layers(default="KC_E")
        layers[0][FAR] = KeyBinding(FAR, tap_label="x", tap_code="KC_ENTER")
        free = 24                                      # a thumb: the swap the full layer finds
        layers[0][free] = KeyBinding(free, transparent=True)
        swaps = self.hints(layers, CoachHintKind.SWAP)
        self.assertTrue(swaps)
        for h in swaps:
            self.assertEqual(h.keys[0], (0, FAR))
            self.assertEqual(h.keys[1][1], free)

    def test_same_finger_needs_a_non_letter(self) -> None:
        pairs = {((0, FAR), (0, SAME)): 300, ((0, HOME_LIST[0]), (0, HOME_LIST[0])): 700}

        def same(a, b):
            layers = make_layers(**{f"k{FAR}": a, f"k{SAME}": b})
            return kinds(coach.hints(base_counts(), pairs, GEO, FINGERS, HOMES, layers=layers),
                         CoachHintKind.SAME_FINGER)

        self.assertEqual(same("KC_D", "KC_E"), [])
        self.assertEqual(same("DK_AE", "KC_E"), [])
        self.assertEqual(len(same("KC_D", "KC_BSPACE")), 1)
        self.assertEqual(len(same("KC_ENTER", "KC_E")), 1)
        self.assertEqual(len(same("KC_LSHIFT", "KC_E")), 1)

    def test_unused_prime_key_never_a_letter(self) -> None:
        c = base_counts(9000)
        c[(1, FAR)] = 1000
        key = HOME_LIST[0]

        def unused(code):
            layers = make_layers(**{f"k{key}": code})
            hs = coach.hints(c, {}, GEO, FINGERS, HOMES, {"prime_max": 1000, "max_hints": 100},
                             layers=layers)
            return [h for h in hs if h.kind == CoachHintKind.UNUSED_PRIME_KEY
                    and h.keys == ((1, key),)]

        self.assertEqual(unused("KC_J"), [])
        self.assertEqual(unused("DK_AE"), [])
        for code in ("KC_ENTER", "KC_LSHIFT", "MO", "KC_NO"):
            self.assertEqual(len(unused(code)), 1, code)

    def test_no_binding_means_blank(self) -> None:
        layers = {0: [KeyBinding(FAR, tap_label="x", tap_code="KC_ENTER")]}
        swaps = self.hints(layers, CoachHintKind.SWAP)
        self.assertTrue(swaps)                       # missing keys are free slots
        self.assertEqual(swaps[0].keys[0], (0, FAR))


class OrderTests(unittest.TestCase):
    def full(self):
        c = base_counts(4000)
        c[(0, FAR)] = 3000
        lefts = [i for i in HOME_LIST if FINGERS[i].is_left]
        for i in lefts:
            c[(0, i)] += 1500
        c[(1, FAR)] = 1000
        pairs = {((0, FAR), (0, SAME)): 100, ((0, lefts[0]), (0, lefts[0])): 900}
        return c, pairs

    def test_order_cap_and_determinism(self) -> None:
        c, p = self.full()
        hs = run(c, p)
        order = [coach._KIND_ORDER[h.kind] for h in hs]         # noqa: SLF001
        self.assertEqual(order, sorted(order))
        self.assertEqual({h.kind for h in hs} >= {CoachHintKind.SWAP, CoachHintKind.HAND_IMBALANCE,
                                                  CoachHintKind.SAME_FINGER, CoachHintKind.FAR_KEY},
                         True)
        self.assertLessEqual(len(hs), 8)
        self.assertEqual(hs, run(c, p))
        self.assertEqual(len(run(c, p, max_hints=2)), 2)
        self.assertEqual(run(c, p, max_hints=2), hs[:2])


class PrimeKeyTests(unittest.TestCase):
    def test_voyager(self) -> None:
        for f in (Finger.L_THUMB, Finger.R_THUMB):
            self.assertTrue({i for i, x in enumerate(FINGERS) if x == f} <= PRIME)
        for i in HOME_LIST:
            self.assertIn(i, PRIME)
        self.assertNotIn(FAR, PRIME)
        self.assertLess(len(PRIME), len(FINGERS))


if __name__ == "__main__":
    unittest.main()
