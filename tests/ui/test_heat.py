from __future__ import annotations

import unittest

from vibemapp.ui import heat


class HeatRampTests(unittest.TestCase):
    def test_lightness_is_monotonic(self) -> None:
        light = [heat.relative_luminance(heat.color_at(t / 20, False)) for t in range(21)]
        dark = [heat.relative_luminance(heat.color_at(t / 20, True)) for t in range(21)]
        self.assertEqual(light, sorted(light, reverse=True), "light: more = darker")
        self.assertEqual(dark, sorted(dark), "dark: more = brighter")

    def test_text_colour_has_contrast(self) -> None:
        for dark in (False, True):
            for t in range(11):
                fill = heat.color_at(t / 10, dark)
                text = (1.0, 1.0, 1.0) if heat.text_is_light(fill) else (0.1, 0.1, 0.1)
                self.assertGreaterEqual(heat.contrast(fill, text), 4.5, (dark, t))

    def test_intensity(self) -> None:
        self.assertEqual(heat.intensity(0, 100), 0.0)
        self.assertEqual(heat.intensity(5, 0), 0.0)
        self.assertEqual(heat.intensity(100, 100), 1.0)
        self.assertGreaterEqual(heat.intensity(1, 10_000), 0.08)
        self.assertAlmostEqual(heat.intensity(25, 100), 0.5)

    def test_compact_count(self) -> None:
        self.assertEqual(heat.compact_count(999), "999")
        self.assertEqual(heat.compact_count(1000), "1k")
        self.assertEqual(heat.compact_count(1250), "1.2k")
        self.assertEqual(heat.compact_count(12_345), "12k")
        self.assertEqual(heat.compact_count(2_000_000), "2M")

    def test_top_keys(self) -> None:
        self.assertEqual(heat.top_keys({1: 5, 2: 9, 3: 0, 4: 9}, 2), [(2, 9), (4, 9)])
        self.assertEqual(heat.top_keys({}), [])

    def test_share(self) -> None:
        self.assertEqual(heat.share_label(1, 3), "33%")
        self.assertEqual(heat.share_label(1, 40), "2.5%")
        self.assertEqual(heat.share_label(1, 0), "")


if __name__ == "__main__":
    unittest.main()
