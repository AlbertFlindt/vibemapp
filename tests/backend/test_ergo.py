from __future__ import annotations

import unittest

from vibemapp.backend import ergo, geometries
from vibemapp.models import Finger, KeyboardModel, KeyGeometry, KeyPairCount

# Tiny board, 1u keys. Index: finger @ (x, y)
#   0 L_INDEX @ (0, 0) home      1 L_INDEX @ (3, 4)   (5 units from home)
#   2 R_INDEX @ (10, 0) home     3 L_PINKY @ (0, 1)   (1 unit from its home)
#   4 L_PINKY @ (0, 0) home
GEO = [KeyGeometry(index=i, x=x, y=y, w=1.0, h=1.0)
       for i, (x, y) in enumerate([(0, 0), (3, 4), (10, 0), (0, 1), (0, 0)])]
FINGERS = [Finger.L_INDEX, Finger.L_INDEX, Finger.R_INDEX, Finger.L_PINKY, Finger.L_PINKY]
HOMES = {Finger.L_INDEX: 0, Finger.R_INDEX: 2, Finger.L_PINKY: 4}


def rep(counts, pairs=None):
    return ergo.report(counts, pairs or {}, GEO, FINGERS, HOMES)


class ErgoTests(unittest.TestCase):
    def test_empty(self) -> None:
        r = rep({})
        self.assertEqual((r.presses, r.pairs, r.score), (0, 0, 100.0))
        self.assertEqual(r.left_share, 0.0)
        self.assertEqual(set(r.finger_load.values()), {0.0})
        self.assertEqual(set(r.penalties), set(ergo.PENALTY_KEYS))
        self.assertEqual(sum(r.penalties.values()), 0.0)

    def test_balanced_home_presses(self) -> None:
        r = rep({(0, 0): 1, (0, 2): 1})
        self.assertEqual(r.presses, 2)
        self.assertEqual(r.finger_load[Finger.L_INDEX], 0.5)
        self.assertEqual(r.left_share, 0.5)
        self.assertEqual(r.reach_cost, 0.0)                    # home keys
        self.assertAlmostEqual(r.penalties["hand_imbalance"], 0.0)
        # overload: (0.5 - 0.20) * 2 fingers = 0.6 -> 60
        self.assertAlmostEqual(r.penalties["finger_overload"], 60.0)
        self.assertEqual(r.score, 40.0)

    def test_imbalance_overload_and_clamp(self) -> None:
        r = rep({(0, 0): 4})                                   # one finger, one hand
        self.assertEqual(r.left_share, 1.0)
        self.assertAlmostEqual(r.penalties["hand_imbalance"], 50.0)
        self.assertAlmostEqual(r.penalties["finger_overload"], 80.0)   # 1.0 - 0.2
        self.assertEqual(r.score, 0.0)                         # 100 - 130 clamped

    def test_imbalance_partial(self) -> None:
        r = rep({(0, 0): 3, (0, 2): 1})                        # left 0.75
        self.assertAlmostEqual(r.penalties["hand_imbalance"], 50 * 0.5)

    def test_reach(self) -> None:
        r = rep({(0, 1): 2, (0, 0): 2})
        # key 1: 5 units / 1.0, home: 0 -> (2*5 + 0) / 4
        self.assertAlmostEqual(r.reach_cost, 2.5)
        self.assertAlmostEqual(r.penalties["reach"], 37.5)
        # pinky is weak: 1 unit / 0.5
        self.assertAlmostEqual(ergo.key_reach(3, GEO, FINGERS, HOMES), 2.0)
        self.assertAlmostEqual(ergo.key_reach(1, GEO, FINGERS, HOMES), 5.0)
        self.assertEqual(ergo.key_reach(0, GEO, FINGERS, HOMES), 0.0)
        self.assertEqual(ergo.key_reach(99, GEO, FINGERS, HOMES), 0.0)

    def test_unknown_keys_ignored(self) -> None:
        r = rep({(0, 0): 1, (0, 99): 50})
        self.assertEqual(r.presses, 1)

    def test_same_finger(self) -> None:
        pairs = {((0, 0), (0, 1)): 3,          # same finger, different key
                 ((0, 0), (0, 2)): 1}          # different hands
        r = rep({(0, 0): 1}, pairs)
        self.assertEqual(r.pairs, 4)
        self.assertAlmostEqual(r.same_finger_rate, 0.75)
        self.assertAlmostEqual(r.penalties["same_finger"], 225.0)
        self.assertEqual(r.worst_same_finger, (KeyPairCount((0, 0), (0, 1), 3),))
        self.assertEqual(r.layer_switch_rate, 0.0)

    def test_same_key_and_other_layer_are_not_sfb(self) -> None:
        r = rep({}, {((0, 0), (0, 0)): 2, ((0, 0), (1, 0)): 1})
        self.assertEqual(r.same_finger_rate, 0.0)
        self.assertEqual(r.worst_same_finger, ())
        self.assertAlmostEqual(r.layer_switch_rate, 1 / 3)
        self.assertAlmostEqual(r.penalties["layer_switch"], 20 / 3)

    def test_layer_switch_and_sfb_together(self) -> None:
        r = rep({}, {((0, 0), (1, 1)): 1, ((0, 0), (0, 2)): 3})
        self.assertAlmostEqual(r.same_finger_rate, 0.25)
        self.assertAlmostEqual(r.layer_switch_rate, 0.25)
        self.assertEqual(r.worst_same_finger[0].second, (1, 1))

    def test_worst_is_top_five_with_stable_ties(self) -> None:
        pairs = {((0, 0), (l, 1)): 1 for l in range(1, 8)}
        pairs[((0, 1), (0, 0))] = 9
        worst = rep({}, pairs).worst_same_finger
        self.assertEqual(len(worst), 5)
        self.assertEqual(worst[0], KeyPairCount((0, 1), (0, 0), 9))
        self.assertEqual([w.second[0] for w in worst[1:]], [1, 2, 3, 4])

    def test_score_rounded_to_one_decimal(self) -> None:
        r = rep({(0, 1): 1, (0, 2): 2})
        self.assertEqual(r.score, round(r.score, 1))
        self.assertGreaterEqual(r.score, 0.0)

    def test_weights_documented_set(self) -> None:
        self.assertEqual(ergo.WEIGHTS["finger_strength"]["pinky"], 0.5)
        self.assertEqual(ergo.WEIGHTS["finger_capacity"]["index"], 0.20)
        self.assertEqual([ergo.WEIGHTS[k] for k in ergo.PENALTY_KEYS], [100, 50, 300, 15, 20])

    def test_real_board_home_keys_have_no_reach(self) -> None:
        m = KeyboardModel.VOYAGER
        geo, fingers, homes = (geometries.GEOMETRIES[m], geometries.FINGERS[m],
                               geometries.HOME_KEYS[m])
        r = ergo.report({(0, i): 1 for i in homes.values()}, {}, geo, fingers, homes)
        self.assertEqual(r.reach_cost, 0.0)
        self.assertEqual(r.presses, 10)
        self.assertAlmostEqual(r.left_share, 0.5)


if __name__ == "__main__":
    unittest.main()
