"""Pointer activity: labels, the pill's tracker, the pad drawing's state, the
overlay's pad placement and who asks for pointer_tracking. No display needed."""

from __future__ import annotations

import math
import unittest

from gi.repository import GObject

from vibemapp.models import KeyGeometry, PointerAction as P, PointerActivity as A, PointerKind as K
from vibemapp.ui import pointer_activity as pa
from vibemapp.ui.overlay_state import PAD_GAP, PAD_UNITS, pointer_pad_bounds, union_bounds

PAD, BALL = K.TRACKPAD, K.TRACKBALL
ALL = set(pa.ACTIONS)


class LabelTests(unittest.TestCase):
    def test_pill_labels(self) -> None:
        self.assertEqual(pa.pill_label(A(PAD, P.CLICK, button=1)), "Click")
        self.assertEqual(pa.pill_label(A(PAD, P.CLICK, button=2)), "Right Click")
        self.assertEqual(pa.pill_label(A(BALL, P.CLICK, button=3)), "Middle Click")
        self.assertEqual(pa.pill_label(A(PAD, P.TAP, fingers=1)), "Tap")
        self.assertEqual(pa.pill_label(A(PAD, P.TAP, fingers=2)), "Two-Finger Tap")
        self.assertEqual(pa.pill_label(A(PAD, P.SCROLL, fingers=2, direction="down")), "Scroll ↓")
        self.assertEqual(pa.pill_label(A(PAD, P.TOUCH, fingers=1)), "Move")
        self.assertIsNone(pa.pill_label(A(PAD, P.IDLE)))
        self.assertIsNone(pa.pill_label(A(PAD, P.RELEASE, button=1)))

    def test_live_labels(self) -> None:
        self.assertEqual(pa.live_label(A(PAD, P.SCROLL, fingers=2, direction="up")),
                         "Two-Finger Scroll ↑")
        self.assertEqual(pa.live_label(A(BALL, P.SCROLL, direction="left")), "Scroll ←")
        self.assertEqual(pa.live_label(A(PAD, P.TOUCH, fingers=3)), "Three-Finger Touch")

    def test_titles(self) -> None:
        self.assertEqual(pa.device_title(PAD), "Trackpad")
        self.assertEqual(pa.device_title(BALL), "Trackball")
        self.assertEqual(pa.device_title(None), "Trackpad & Trackball")
        self.assertEqual(pa.device_icon(BALL), "input-mouse-symbolic")
        self.assertEqual(pa.device_icon(None), "input-touchpad-symbolic")


class TrackerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.t = pa.PointerTracker()

    def test_scroll_lasts_until_idle(self) -> None:
        self.assertTrue(self.t.feed(A(PAD, P.SCROLL, fingers=2, direction="down"), 0, ALL))
        self.assertEqual((self.t.text, self.t.active), ("Scroll ↓", True))
        # Re-sent frames with nothing new don't count as a change.
        self.assertFalse(self.t.feed(A(PAD, P.SCROLL, fingers=2, direction="down"), 16, ALL))
        self.assertTrue(self.t.feed(A(PAD, P.IDLE), 400, ALL))
        self.assertEqual((self.t.text, self.t.active), ("Scroll ↓", False))

    def test_tap_ends_at_once(self) -> None:
        self.assertTrue(self.t.feed(A(PAD, P.TAP, fingers=1), 0, ALL))
        self.assertEqual((self.t.text, self.t.active), ("Tap", False))

    def test_click_is_kept_through_a_drag_and_right_after(self) -> None:
        self.t.feed(A(PAD, P.CLICK, button=1, buttons=frozenset({1})), 0, ALL)
        self.t.feed(A(PAD, P.MOVE, fingers=1, buttons=frozenset({1})), 900, ALL)
        self.assertEqual(self.t.text, "Click")              # a drag
        self.t.feed(A(PAD, P.RELEASE, button=1), 1000, ALL)
        self.assertFalse(self.t.active)
        self.t.feed(A(PAD, P.MOVE, fingers=1), 1000 + pa.KEEP_MS - 1, ALL)
        self.assertEqual(self.t.text, "Click")
        self.t.feed(A(PAD, P.MOVE, fingers=1, contacts=((0.1, 0.1),)), 1000 + pa.KEEP_MS, ALL)
        self.assertEqual(self.t.text, "Move")

    def test_filtered_actions_dont_start_it(self) -> None:
        self.assertFalse(self.t.feed(A(PAD, P.MOVE, fingers=1), 0, {"click"}))
        self.assertIsNone(self.t.text)
        self.assertTrue(self.t.feed(A(PAD, P.CLICK, button=2, buttons=frozenset({2})), 0,
                                    {"click"}))
        self.assertEqual(self.t.text, "Right Click")


class PadModelTests(unittest.TestCase):
    def test_to_disc_clamps(self) -> None:
        self.assertEqual(pa.to_disc(0.5, 0.5), (0.0, 0.0))
        self.assertEqual(pa.to_disc(0.0, 0.5), (-1.0, 0.0))
        x, y = pa.to_disc(1.0, 1.0)
        self.assertAlmostEqual(math.hypot(x, y), 1.0)

    def test_fingers_trails_fade(self) -> None:
        m = pa.PadModel(PAD)
        m.feed(A(PAD, P.TOUCH, fingers=2, contacts=((0.4, 0.5), (0.6, 0.5))), 0)
        self.assertEqual(len(m.contacts), 2)
        self.assertEqual(m.trails, [])                     # touching, not moving
        self.assertFalse(m.animating(0))                  # nothing fades: no redraws
        for i in range(1, 5):
            m.feed(A(PAD, P.SCROLL, fingers=2, direction="down",
                     contacts=((0.4, 0.5 + i / 20), (0.6, 0.5 + i / 20))), i * 16)
        self.assertEqual([len(t) for t in m.trails], [4, 4])
        self.assertTrue(m.animating(64))
        m.feed(A(PAD, P.IDLE), 100)
        self.assertEqual(m.contacts, ())
        self.assertEqual(len(m.lifted), 2)                # fading out
        m.prune(64 + pa.TRAIL_MS + 1)
        self.assertEqual(m.trails, [[], []])
        self.assertFalse(m.animating(100 + pa.TRAIL_MS + pa.FINGER_FADE_MS))

    def test_tap_ripples_and_click_flash(self) -> None:
        m = pa.PadModel(PAD)
        m.feed(A(PAD, P.TAP, fingers=1, contacts=((1.0, 0.5),)), 0)
        self.assertEqual([(r.x, r.y) for r in m.ripples], [(1.0, 0.0)])
        self.assertTrue(m.animating(10))
        m.prune(pa.RIPPLE_MS)
        self.assertEqual(m.ripples, [])
        m.feed(A(PAD, P.CLICK, button=1, buttons=frozenset({1})), 1000)
        self.assertEqual(m.flash(5000), 1.0)               # held
        m.feed(A(PAD, P.RELEASE, button=1), 5000)
        self.assertEqual(m.flash(5000 + pa.FLASH_MS), 0.0)

    def test_trackball_rolls_with_motion(self) -> None:
        m = pa.PadModel(BALL)
        front = pa.ball_marks(0, 0)[0]
        self.assertAlmostEqual(front[0], 0.0)
        m.feed(A(BALL, P.MOVE, motion=(10, 0)), 0)
        self.assertGreater(m.rot_x, 0)
        self.assertGreater(pa.ball_marks(m.rot_x, m.rot_y)[0][0], 0)  # surface moved right
        self.assertTrue(m.animating(0))
        self.assertFalse(m.animating(pa.TRAIL_MS))
        m.feed(A(BALL, P.SCROLL, direction="up", motion=(0, 1)), 10)
        self.assertLess(m.rot_y, 0)                       # top of the ball moves up


class PadPlacementTests(unittest.TestCase):
    # A right half: main rows y 0..3 (outer column at x 15), thumbs below and inside.
    RIGHT = (KeyGeometry(0, 10, 0), KeyGeometry(1, 14, 3),
             KeyGeometry(2, 10, 4.5, rotation=-15.0), KeyGeometry(3, 11, 4.5, rotation=-15.0))

    def test_outside_the_outer_column_level_with_the_main_rows(self) -> None:
        x0, y0, x1, y1 = pointer_pad_bounds(self.RIGHT)
        self.assertAlmostEqual(x0, 15 + PAD_GAP)
        self.assertAlmostEqual(x1 - x0, PAD_UNITS)
        self.assertAlmostEqual((y0 + y1) / 2, 2.0)        # rows 0..4, not the thumbs

    def test_left_side_mirrors(self) -> None:
        left = (KeyGeometry(0, 0, 0), KeyGeometry(1, 4, 3),
                KeyGeometry(2, 4, 4.5, rotation=15.0))
        x0, y0, x1, y1 = pointer_pad_bounds(left, "left")
        self.assertAlmostEqual(x1, -PAD_GAP)
        self.assertAlmostEqual(x1 - x0, PAD_UNITS)
        self.assertAlmostEqual((y0 + y1) / 2, 2.0)

    def test_board_without_thumb_keys_is_centred_on_its_side(self) -> None:
        board = tuple(KeyGeometry(i, float(i % 12), float(i // 12)) for i in range(48))
        self.assertEqual(pointer_pad_bounds(board),
                         (12 + PAD_GAP, 2 - PAD_UNITS / 2, 12 + PAD_GAP + PAD_UNITS,
                          2 + PAD_UNITS / 2))
        self.assertEqual(pointer_pad_bounds(board, "left")[2], -PAD_GAP)
        self.assertEqual(union_bounds((0, 0, 1, 1), (2, -1, 3, 0.5)), (0, -1, 3, 1))

    def test_voyager_beside_the_outer_column_not_under_the_keys(self) -> None:
        from vibemapp.backend.geometries import GEOMETRIES
        from vibemapp.models import KeyboardModel
        from vibemapp.ui.overlay_state import geometry_bounds, split_geometry
        left, right = split_geometry(GEOMETRIES[KeyboardModel.VOYAGER])
        for side, half in (("right", right), ("left", left)):
            with self.subTest(side=side):
                hb = geometry_bounds(half)
                main = geometry_bounds(tuple(k for k in half if not k.rotation))
                x0, y0, x1, y1 = pointer_pad_bounds(half, side)
                if side == "right":
                    self.assertAlmostEqual(x0, hb[2] + PAD_GAP)
                else:
                    self.assertAlmostEqual(x1, hb[0] - PAD_GAP)
                self.assertAlmostEqual((y0 + y1) / 2, (main[1] + main[3]) / 2)
                self.assertGreaterEqual(y0, main[1])        # within the main rows' height
                self.assertLessEqual(y1, main[3])


class FakeService(GObject.Object):
    pointer_tracking = GObject.Property(type=bool, default=False, nick="pointer-tracking")


class DemandTests(unittest.TestCase):
    def test_tracking_while_anyone_wants_it(self) -> None:
        svc = FakeService()
        pa.want_pointer(svc, "pill", True)
        pa.want_pointer(svc, "page", True)
        self.assertTrue(svc.props.pointer_tracking)
        pa.want_pointer(svc, "pill", False)
        self.assertTrue(svc.props.pointer_tracking)
        pa.want_pointer(svc, "page", False)
        self.assertFalse(svc.props.pointer_tracking)


if __name__ == "__main__":
    unittest.main()
