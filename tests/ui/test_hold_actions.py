"""Hold-action detection (pure, no display)."""

from __future__ import annotations

import unittest

from vibemapp.models import KeyBinding, Layer, Layout
from vibemapp.ui import hold_actions as ha


def layout(term: int | None = None) -> Layout:
    base = (KeyBinding(0, tap_label="C", hold_label="⌃C", hold_combo="Ctrl+C"),
            KeyBinding(1, tap_label="V", hold_combo="Ctrl+V", tapping_term_ms=300),
            KeyBinding(2, tap_label="A", hold_label="Ctrl"),
            KeyBinding(3, tap_label="B"))
    upper = (KeyBinding(0, transparent=True),
             KeyBinding(1, tap_label="1", hold_combo="Shift+1"),
             KeyBinding(2, transparent=True),
             KeyBinding(3, tap_label="2"))
    return Layout("x", "X", (), (Layer(0, "Base", None, base), Layer(1, "Sym", None, upper)),
                  tapping_term_ms=term)


class LookupTests(unittest.TestCase):
    def test_hold_action_and_terms(self) -> None:
        lay = layout()
        self.assertEqual(ha.hold_action(lay, 0, 0),
                         ha.HoldAction(0, 0, "Ctrl+C", "C", ha.DEFAULT_TAPPING_TERM_MS))
        self.assertEqual(ha.hold_action(lay, 0, 1).tapping_term_ms, 300)
        self.assertIsNone(ha.hold_action(lay, 0, 2))       # a mod-tap
        self.assertIsNone(ha.hold_action(lay, 0, 3))
        self.assertIsNone(ha.hold_action(lay, 5, 0))
        self.assertIsNone(ha.hold_action(None, 0, 0))
        self.assertEqual(ha.hold_action(layout(term=180), 0, 0).tapping_term_ms, 180)
        self.assertEqual(ha.hold_action(layout(term=180), 0, 1).tapping_term_ms, 300)

    def test_transparent_falls_through_to_base(self) -> None:
        action = ha.hold_action(layout(), 1, 0)
        self.assertEqual((action.layer, action.combo), (0, "Ctrl+C"))
        self.assertEqual(ha.hold_action(layout(), 1, 1).combo, "Shift+1")
        self.assertIsNone(ha.hold_action(layout(), 1, 2))

    def test_layout_list_and_subtitle(self) -> None:
        lay = layout()
        actions = ha.layout_hold_actions(lay)
        self.assertEqual([(a.layer, a.index, a.combo) for a in actions],
                         [(0, 0, "Ctrl+C"), (0, 1, "Ctrl+V"), (1, 1, "Shift+1")])
        self.assertEqual(ha.hold_action_subtitle(actions[0], lay), "Hold C on Layer 0 · Base")
        self.assertEqual(ha.layout_hold_actions(None), [])

    def test_excluded_ids(self) -> None:
        self.assertEqual(ha.key_id(1, 21), "1:21")
        self.assertEqual(ha.toggle_excluded(["9:9"], "0:1", False), ["0:1", "9:9"])
        self.assertEqual(ha.toggle_excluded(["0:1", "9:9"], "0:1", True), ["9:9"])
        self.assertEqual(ha.toggle_excluded([], "0:1", True), [])


class FakeTimers:
    def __init__(self) -> None:
        self.timers: dict[int, tuple[int, object]] = {}
        self._next = 0

    def schedule(self, ms, cb):
        self._next += 1
        self.timers[self._next] = (ms, cb)
        return self._next

    def cancel(self, source) -> None:
        self.timers.pop(source, None)

    def fire_all(self) -> None:
        for source in list(self.timers):
            _ms, cb = self.timers.pop(source)
            cb()


class DetectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.lay = layout()
        self.timers = FakeTimers()
        self.fired: list[str] = []
        self.det = ha.HoldDetector(lambda i: ha.hold_action(self.lay, 0, i),
                                   lambda a: self.fired.append(a.combo),
                                   self.timers.schedule, self.timers.cancel)

    def test_held_past_term_fires(self) -> None:
        self.det.key(0, True, 1000)
        self.assertEqual([ms for ms, _cb in self.timers.timers.values()], [200])
        self.timers.fire_all()
        self.assertEqual(self.fired, ["Ctrl+C"])
        self.det.key(0, False, 1500)
        self.assertEqual(self.fired, ["Ctrl+C"])

    def test_tap_cancels(self) -> None:
        self.det.key(0, True, 1000)
        self.det.key(0, False, 1100)
        self.assertEqual(self.timers.timers, {})
        self.timers.fire_all()
        self.assertEqual(self.fired, [])

    def test_other_key_interrupts(self) -> None:
        self.det.key(0, True, 1000)
        self.det.key(3, True, 1050)          # rolled into B: C was a tap
        self.timers.fire_all()
        self.assertEqual(self.fired, [])
        self.det.key(3, False, 1080)
        # Releasing another key doesn't interrupt (QMK only counts presses).
        self.det.key(1, True, 2000)
        self.det.key(3, False, 2010)
        self.timers.fire_all()
        self.assertEqual(self.fired, ["Ctrl+V"])

    def test_double_tap_hold_does_not_count(self) -> None:
        self.det.key(0, True, 1000)
        self.det.key(0, False, 1080)
        self.det.key(0, True, 1150)          # within the term of the release
        self.assertIsNone(self.det.pending)
        self.timers.fire_all()
        self.assertEqual(self.fired, [])
        self.det.key(0, False, 1500)
        self.det.key(0, True, 1800)          # a fresh press again
        self.timers.fire_all()
        self.assertEqual(self.fired, ["Ctrl+C"])

    def test_hold_again_right_after_a_hold(self) -> None:
        self.det.key(0, True, 1000)
        self.timers.fire_all()
        self.det.key(0, False, 1300)
        self.det.key(0, True, 1350)          # a new dance: the last one was a hold
        self.timers.fire_all()
        self.assertEqual(self.fired, ["Ctrl+C", "Ctrl+C"])

    def test_keys_without_hold_action_and_reset(self) -> None:
        self.det.key(2, True, 1000)
        self.det.key(3, True, 1010)
        self.assertEqual(self.timers.timers, {})
        self.det.key(0, True, 2000)
        self.det.reset()
        self.assertEqual(self.timers.timers, {})
        self.assertIsNone(self.det.pending)


if __name__ == "__main__":
    unittest.main()
