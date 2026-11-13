from __future__ import annotations

import unittest

from gi.repository import GLib

from vibemapp.backend import geometries
from vibemapp.mock_service import voyager_geometry
from vibemapp.models import KeyboardModel, KeyGeometry
from vibemapp.ui.overlay_state import (DEFAULT_FADE_MS, MARGIN_PX, fade_durations, MonitorInfo, OverlayConfig, anchor_edges,
                                       geometry_bounds, place_parts, px_per_mm,
                                       should_show, split_geometry, toggled_mode, unit_px)
from vibemapp.ui.settings import MemorySettings


class ShouldShowTests(unittest.TestCase):
    def test_selected_mode_shows_only_selected_layers(self) -> None:
        config = OverlayConfig(mode="selected", layers=frozenset({1, 3}))
        self.assertFalse(should_show(config, 0, has_layout=True))
        self.assertTrue(should_show(config, 1, has_layout=True))
        self.assertFalse(should_show(config, 2, has_layout=True))
        self.assertTrue(should_show(config, 3, has_layout=True))

    def test_selected_mode_without_layers_never_shows(self) -> None:
        config = OverlayConfig(mode="selected")
        self.assertFalse(any(should_show(config, i, has_layout=True) for i in range(8)))

    def test_always_and_off(self) -> None:
        always = OverlayConfig(mode="always")
        self.assertTrue(should_show(always, 0, has_layout=True))
        self.assertTrue(should_show(always, 5, has_layout=True))
        off = OverlayConfig(mode="off", layers=frozenset({0, 1}))
        self.assertFalse(should_show(off, 1, has_layout=True))

    def test_needs_a_layout_and_no_idle(self) -> None:
        config = OverlayConfig(mode="always")
        self.assertFalse(should_show(config, 1, has_layout=False))
        self.assertFalse(should_show(config, 1, has_layout=True, idle=True))

    def test_preview_overrides_everything(self) -> None:
        for mode in ("off", "selected", "always"):
            config = OverlayConfig(mode=mode)
            self.assertTrue(should_show(config, 0, has_layout=False, idle=True, preview=True))


class ToggleTests(unittest.TestCase):
    def test_on_goes_off(self) -> None:
        self.assertEqual(toggled_mode("selected", "selected", frozenset()), "off")
        self.assertEqual(toggled_mode("always", None, frozenset()), "off")

    def test_off_restores_last_mode(self) -> None:
        self.assertEqual(toggled_mode("off", "always", frozenset({1})), "always")
        self.assertEqual(toggled_mode("off", "selected", frozenset()), "selected")

    def test_off_without_history(self) -> None:
        self.assertEqual(toggled_mode("off", None, frozenset({2})), "selected")
        self.assertEqual(toggled_mode("off", None, frozenset()), "always")


class ConfigTests(unittest.TestCase):
    def test_from_memory_settings(self) -> None:
        settings = MemorySettings()
        config = OverlayConfig.from_settings(settings)
        self.assertEqual(config, OverlayConfig())
        settings.set_string("overlay-mode", "always")
        settings.set_value("overlay-layers", GLib.Variant("ai", [2, 1]))
        settings.set_int("overlay-half-spacing", 40)
        settings.set_int("overlay-size", 120)
        settings.set_string("overlay-anchor", "top-left")
        config = OverlayConfig.from_settings(settings)
        self.assertEqual((config.mode, config.layers, config.half_spacing, config.size,
                          config.anchor),
                         ("always", frozenset({1, 2}), 40, 120, "top-left"))

    def test_unknown_values_fall_back(self) -> None:
        settings = MemorySettings()
        settings.set_string("overlay-mode", "sometimes")
        settings.set_string("overlay-anchor", "middle")
        config = OverlayConfig.from_settings(settings)
        self.assertEqual((config.mode, config.anchor), ("selected", "bottom"))


class FadeTests(unittest.TestCase):
    def test_out_is_three_quarters_of_in_rounded(self) -> None:
        self.assertEqual(fade_durations(OverlayConfig(fade_ms=200)), (200, 150))
        self.assertEqual(fade_durations(OverlayConfig(fade_ms=1000)), (1000, 750))
        self.assertEqual(fade_durations(OverlayConfig(fade_ms=333)), (333, 250))  # 249.75
        self.assertEqual(fade_durations(OverlayConfig(fade_ms=2)), (2, 2))        # 1.5 rounds up
        self.assertEqual(fade_durations(OverlayConfig(fade_ms=1)), (1, 1))

    def test_zero_is_instant_and_out_of_range_is_clamped(self) -> None:
        self.assertEqual(fade_durations(OverlayConfig(fade_ms=0)), (0, 0))
        self.assertEqual(fade_durations(OverlayConfig(fade_ms=-5)), (0, 0))
        self.assertEqual(fade_durations(OverlayConfig(fade_ms=5000)), (1000, 750))

    def test_default_and_setting(self) -> None:
        settings = MemorySettings()
        self.assertEqual(OverlayConfig.from_settings(settings).fade_ms, DEFAULT_FADE_MS)
        self.assertEqual(OverlayConfig().fade_ms, 200)
        settings.set_int("overlay-fade-ms", 0)
        self.assertEqual(fade_durations(OverlayConfig.from_settings(settings)), (0, 0))


class PlacementTests(unittest.TestCase):
    def test_anchor_edges(self) -> None:
        self.assertEqual(anchor_edges("bottom"), ("bottom", None))
        self.assertEqual(anchor_edges("bottom-left"), ("bottom", "left"))
        self.assertEqual(anchor_edges("top-right"), ("top", "right"))
        self.assertEqual(anchor_edges("nonsense"), ("bottom", None))

    def test_px_per_mm_and_fallback(self) -> None:
        self.assertAlmostEqual(px_per_mm(MonitorInfo("DP-1", 3440, 1440, 800)), 4.3)
        dpi96 = 96 / 25.4
        for width_mm in (0, 10, 5000):             # unknown or implausible
            self.assertAlmostEqual(px_per_mm(MonitorInfo("X", 1920, 1080, width_mm)), dpi96)

    def test_unit_px_is_physical(self) -> None:
        big = MonitorInfo("DP-1", 3440, 1440, 800)       # Dell AW3425DW
        small = MonitorInfo("DP-2", 1920, 1080, 530)     # Dell U2422H
        voyager = 16.0                                   # key units, gap included
        # Life-size: one key unit is 19.05 mm on either monitor.
        self.assertAlmostEqual(unit_px(big, 100, voyager) / px_per_mm(big), 19.05)
        self.assertAlmostEqual(unit_px(small, 100, voyager) / px_per_mm(small), 19.05)
        # The default (80 %) on the owner's monitors: ~1050 px and ~880 px wide.
        self.assertEqual(round(unit_px(big, 80, voyager) * voyager), 1049)
        self.assertEqual(round(unit_px(small, 80, voyager) * voyager), 883)

    def test_unit_px_never_exceeds_the_monitor(self) -> None:
        tiny = MonitorInfo("X", 800, 600, 150)
        self.assertLessEqual(unit_px(tiny, 150, 16.0) * 16.0, 800 - 2 * MARGIN_PX + 1e-6)

    def test_split_boards_by_oryx_index_order(self) -> None:
        for model, n in ((KeyboardModel.VOYAGER, 26), (KeyboardModel.MOONLANDER, 36),
                         (KeyboardModel.ERGODOX_EZ, 38)):
            halves = split_geometry(geometries.geometry_for(model))
            self.assertIsNotNone(halves, model)
            left, right = halves
            self.assertEqual((len(left), len(right)), (n, n))
            self.assertEqual(sorted(k.index for k in left), list(range(n)))

    def test_one_piece_board_is_not_split(self) -> None:
        self.assertIsNone(split_geometry(geometries.geometry_for(KeyboardModel.PLANCK_EZ)))
        self.assertIsNone(split_geometry(()))
        self.assertIsNone(split_geometry((KeyGeometry(0, 0, 0),)))

    def test_row_interleaved_geometry_splits_by_position(self) -> None:
        geometry = voyager_geometry()       # the mock interleaves rows
        left, right = split_geometry(geometry)
        self.assertEqual((len(left), len(right)), (26, 26))
        self.assertLess(max(k.x for k in left if not k.rotation),
                        min(k.x for k in right if not k.rotation))


class HalfSpacingTests(unittest.TestCase):
    MON = MonitorInfo("DP-1", 2000, 1000, 0)
    BOARD = (0.0, 0.0, 16.0, 6.0)
    LEFT = (0.0, 0.0, 6.0, 6.0)
    RIGHT = (10.0, 0.5, 16.0, 5.0)
    UNIT = 50.0                      # board 800 px wide

    def place(self, anchor: str, spacing: int):
        return place_parts(self.MON, anchor, self.UNIT, self.BOARD,
                           [self.LEFT, self.RIGHT], spacing)

    def test_joined_board(self) -> None:
        (whole,) = place_parts(self.MON, "bottom", self.UNIT, self.BOARD, [self.BOARD], 70)
        self.assertEqual((whole.x, whole.edge_margin, whole.width, whole.height),
                         (600, MARGIN_PX, 800, 300))
        (left,) = place_parts(self.MON, "top-left", self.UNIT, self.BOARD, [self.BOARD], 0)
        self.assertEqual(left.x, MARGIN_PX)
        (right,) = place_parts(self.MON, "bottom-right", self.UNIT, self.BOARD, [self.BOARD], 0)
        self.assertEqual(right.x + right.width, 2000 - MARGIN_PX)

    def test_zero_spacing_halves_sit_where_the_joined_board_would(self) -> None:
        left, right = self.place("bottom", 0)
        self.assertEqual((left.x, right.x), (600, 1100))
        self.assertEqual(right.x + right.width, 1400)

    def test_full_spacing_puts_halves_in_the_corners(self) -> None:
        for anchor in ("bottom", "top", "bottom-left", "top-right"):
            left, right = self.place(anchor, 100)
            self.assertEqual(left.x, MARGIN_PX, anchor)
            self.assertEqual(right.x + right.width, 2000 - MARGIN_PX, anchor)

    def test_centre_anchor_moves_both_halves_symmetrically(self) -> None:
        left, right = self.place("bottom", 50)
        self.assertEqual(left.x, (600 + MARGIN_PX) // 2)
        self.assertEqual(2000 - (right.x + right.width), left.x)

    def test_corner_anchor_keeps_its_half_and_moves_the_other(self) -> None:
        for spacing in (0, 40, 100):
            left, right = self.place("bottom-left", spacing)
            self.assertEqual(left.x, MARGIN_PX)
            left_r, right_r = self.place("top-right", spacing)
            self.assertEqual(right_r.x + right_r.width, 2000 - MARGIN_PX)
        _l, r40 = self.place("bottom-left", 40)
        _l, r0 = self.place("bottom-left", 0)
        self.assertGreater(r40.x, r0.x)

    def test_halves_keep_their_vertical_offsets(self) -> None:
        left, right = self.place("bottom", 30)
        self.assertEqual(left.edge_margin, MARGIN_PX)                  # reaches the bottom
        self.assertEqual(right.edge_margin, MARGIN_PX + 50)            # 1u higher up
        left, right = self.place("top", 30)
        self.assertEqual((left.edge_margin, right.edge_margin), (MARGIN_PX, MARGIN_PX + 25))

    def test_bounds_match_the_view(self) -> None:
        from vibemapp.ui.keyboard_view import geometry_bounds as view_bounds
        geometry = geometries.geometry_for(KeyboardModel.VOYAGER)
        self.assertEqual(geometry_bounds(geometry), view_bounds(geometry))


if __name__ == "__main__":
    unittest.main()
