from __future__ import annotations

import math
import unittest

from vibemapp.backend import geometries
from vibemapp.models import KeyboardModel


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


if __name__ == "__main__":
    unittest.main()
