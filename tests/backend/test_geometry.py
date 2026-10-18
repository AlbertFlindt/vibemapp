from __future__ import annotations

import math
import unittest

from vibemapp.backend import geometries
from vibemapp.models import Finger, KeyboardModel


class GeometryTests(unittest.TestCase):
    def test_counts_and_indices(self) -> None:
        for model, count in geometries.KEY_COUNTS.items():
            geo = geometries.geometry_for(model)
            self.assertEqual(len(geo), count, model)
            self.assertEqual([k.index for k in geo], list(range(count)), model)
            self.assertEqual(len(geometries.MATRIX_TO_INDEX[model]), count,
                             f"{model}: duplicate matrix positions")

    def test_no_overlapping_centres(self) -> None:
        for model in geometries.KEY_COUNTS:
            centres = [(k.x + k.w / 2, k.y + k.h / 2) for k in geometries.geometry_for(model)]
            for i, a in enumerate(centres):
                for j in range(i + 1, len(centres)):
                    self.assertGreater(math.dist(a, centres[j]), 0.6, f"{model} {i} vs {j}")

    def test_split_boards_are_half_major(self) -> None:
        for model in (KeyboardModel.VOYAGER, KeyboardModel.MOONLANDER, KeyboardModel.ERGODOX_EZ):
            geo = geometries.geometry_for(model)
            half = len(geo) // 2
            mid = (min(k.x for k in geo) + max(k.x + k.w for k in geo)) / 2
            self.assertTrue(all(k.x + k.w / 2 < mid for k in geo[:half]), model)
            self.assertTrue(all(k.x + k.w / 2 > mid for k in geo[half:]), model)

    def test_known_positions(self) -> None:
        v = KeyboardModel.VOYAGER
        # Voyager: left thumbs are 24/25, right thumbs 50/51 (Oryx default layout).
        self.assertEqual(geometries.matrix_to_index(v, 5, 0), 24)
        self.assertEqual(geometries.matrix_to_index(v, 11, 6), 51)
        self.assertEqual(geometries.matrix_to_index(v, 6, 0), 26)
        self.assertEqual(geometries.matrix_to_index(v, 0, 1), 0)
        self.assertIsNone(geometries.matrix_to_index(v, 0, 0))
        p = KeyboardModel.PLANCK_EZ
        self.assertEqual(geometries.geometry_for(p)[41].w, 2.0)
        self.assertEqual(geometries.matrix_to_index(p, 7, 5), 41)
        self.assertEqual(geometries.geometry_for(KeyboardModel.UNKNOWN), ())

    def test_thumbs_rotated(self) -> None:
        for model in (KeyboardModel.VOYAGER, KeyboardModel.MOONLANDER, KeyboardModel.ERGODOX_EZ):
            rots = [k.rotation for k in geometries.geometry_for(model)]
            self.assertTrue(any(r > 0 for r in rots) and any(r < 0 for r in rots), model)


class FingerMapTests(unittest.TestCase):
    def test_length_hands_and_homes(self) -> None:
        for model in geometries.KEY_COUNTS:
            fingers = geometries.FINGERS[model]
            self.assertEqual(len(fingers), len(geometries.GEOMETRIES[model]), model)
            self.assertEqual(set(fingers), set(Finger), model)
            homes = geometries.HOME_KEYS[model]
            self.assertEqual(set(homes), set(Finger), model)
            for finger, index in homes.items():
                self.assertEqual(fingers[index], finger, f"{model} {finger}")

    def test_hands_split_by_position(self) -> None:
        for model in (KeyboardModel.VOYAGER, KeyboardModel.MOONLANDER, KeyboardModel.ERGODOX_EZ):
            fingers = geometries.FINGERS[model]
            half = len(fingers) // 2
            self.assertTrue(all(f.is_left for f in fingers[:half]), model)
            self.assertFalse(any(f.is_left for f in fingers[half:]), model)
        planck = geometries.FINGERS[KeyboardModel.PLANCK_EZ]
        for g, f in zip(geometries.GEOMETRIES[KeyboardModel.PLANCK_EZ], planck):
            self.assertEqual(f.is_left, g.x < 6, g)

    def test_thumbs_are_exactly_the_clusters(self) -> None:
        for model in (KeyboardModel.VOYAGER, KeyboardModel.MOONLANDER, KeyboardModel.ERGODOX_EZ):
            thumbs = {i for i, f in enumerate(geometries.FINGERS[model])
                      if f in (Finger.L_THUMB, Finger.R_THUMB)}
            rotated = {k.index for k in geometries.GEOMETRIES[model] if k.rotation}
            self.assertEqual(thumbs, rotated, model)
        self.assertEqual(sum(f == Finger.L_THUMB for f in geometries.FINGERS[KeyboardModel.VOYAGER]), 2)
        self.assertEqual(sum(f == Finger.R_THUMB for f in geometries.FINGERS[KeyboardModel.MOONLANDER]), 4)
        self.assertEqual(sum(f == Finger.L_THUMB for f in geometries.FINGERS[KeyboardModel.ERGODOX_EZ]), 6)
        planck = geometries.GEOMETRIES[KeyboardModel.PLANCK_EZ]
        thumbs = {i for i, f in enumerate(geometries.FINGERS[KeyboardModel.PLANCK_EZ])
                  if f in (Finger.L_THUMB, Finger.R_THUMB)}
        self.assertEqual(thumbs, {k.index for k in planck if k.y == 3 and 3 <= k.x <= 8})
        self.assertEqual(geometries.FINGERS[KeyboardModel.PLANCK_EZ][41], Finger.L_THUMB)  # 2u space

    def test_voyager_home_row(self) -> None:
        v = KeyboardModel.VOYAGER
        at = lambda r, c: geometries.matrix_to_index(v, r, c)    # noqa: E731
        homes = geometries.HOME_KEYS[v]
        left = [Finger.L_PINKY, Finger.L_RING, Finger.L_MIDDLE, Finger.L_INDEX]     # A S D F
        right = [Finger.R_INDEX, Finger.R_MIDDLE, Finger.R_RING, Finger.R_PINKY]    # J K L ;
        for col, f in zip(range(2, 6), left):
            self.assertEqual(homes[f], at(2, col), f)
        for col, f in zip(range(1, 5), right):
            self.assertEqual(homes[f], at(8, col), f)
        # columns from the outer edge: pinky, pinky, ring, middle, index, index
        row = [geometries.FINGERS[v][at(0, c)] for c in range(1, 7)]
        self.assertEqual(row, [Finger.L_PINKY, Finger.L_PINKY, Finger.L_RING, Finger.L_MIDDLE,
                               Finger.L_INDEX, Finger.L_INDEX])
        bottom = [geometries.FINGERS[v][at(3, c)] for c in range(1, 6)]
        self.assertEqual(bottom, row[:5])

    def test_ergodox_and_planck_columns(self) -> None:
        e = geometries.FINGERS[KeyboardModel.ERGODOX_EZ]
        geo = geometries.GEOMETRIES[KeyboardModel.ERGODOX_EZ]
        expect = {0: "pinky", 1.5: "pinky", 2.5: "ring", 3.5: "middle", 4.5: "index",
                  5.5: "index", 6.5: "index", 9.5: "index", 10.5: "index", 11.5: "index",
                  12.5: "middle", 13.5: "ring", 14.5: "pinky", 15.5: "pinky"}
        for k in geo:
            if not k.rotation:
                self.assertEqual(e[k.index].value[2:], expect[k.x] if k.x in expect else "pinky",
                                 k)
        p = geometries.FINGERS[KeyboardModel.PLANCK_EZ]
        for k in geometries.GEOMETRIES[KeyboardModel.PLANCK_EZ][:36]:     # three full rows
            names = ["pinky", "pinky", "ring", "middle", "index", "index"]
            names = names + names[::-1]
            self.assertEqual(p[k.index].value[2:], names[int(k.x)], k)


if __name__ == "__main__":
    unittest.main()
