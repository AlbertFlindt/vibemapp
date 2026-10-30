"""Held-modifier tracking (pure, no display)."""

from __future__ import annotations

import unittest

from vibemapp.models import KeyBinding, Layer, Layout
from vibemapp.ui import held_modifiers as hm

from .test_hold_actions import FakeTimers

CTRL, SHIFT, A_CTRL, C, MO1, ALT, GUI_T = range(7)


def layout() -> Layout:
    base = (KeyBinding(CTRL, tap_label="Ctrl", tap_code="KC_LCTL", modifier="Ctrl"),
            KeyBinding(SHIFT, tap_label="Shift", tap_code="KC_LSFT", modifier="Shift"),
            KeyBinding(A_CTRL, tap_label="A", tap_code="KC_A", hold_label="Ctrl",
                       hold_modifier="Ctrl"),
            KeyBinding(C, tap_label="C", tap_code="KC_C"),
            KeyBinding(MO1, tap_label="L1", tap_code="MO"),
            KeyBinding(ALT, tap_label="Alt", tap_code="KC_LALT", modifier="Alt"),
            KeyBinding(GUI_T, tap_label="T", tap_code="KC_T", hold_modifier="Super",
                       tapping_term_ms=300))
    upper = (KeyBinding(0, transparent=True), KeyBinding(1, transparent=True),
             KeyBinding(2, transparent=True), KeyBinding(3, tap_label="!", tap_code="KC_EXLM"),
             KeyBinding(4, transparent=True), KeyBinding(5, transparent=True),
             KeyBinding(6, transparent=True))
    return Layout("x", "X", (), (Layer(0, "Base", None, base), Layer(1, "Sym", None, upper)),
                  tapping_term_ms=None)


class LookupTests(unittest.TestCase):
    def test_held_key(self) -> None:
        lay = layout()
        self.assertEqual(hm.held_key(lay, 0, CTRL), hm.HeldKey("Ctrl"))
        self.assertEqual(hm.held_key(lay, 0, A_CTRL), hm.HeldKey("Ctrl", "A", 200))
        self.assertEqual(hm.held_key(lay, 0, GUI_T).tapping_term_ms, 300)
        self.assertEqual(hm.held_key(lay, 0, C), hm.HeldKey(label="C"))
        self.assertIsNone(hm.held_key(lay, 0, MO1))         # only switches layers
        self.assertEqual(hm.held_key(lay, 1, CTRL), hm.HeldKey("Ctrl"))  # falls through
        self.assertEqual(hm.held_key(lay, 1, C), hm.HeldKey(label="!"))
        self.assertIsNone(hm.held_key(None, 0, C))

    def test_combine(self) -> None:
        self.assertEqual(hm.combine(["Super", "Shift+Ctrl", "Ctrl"]), ["Ctrl", "Shift", "Super"])
        self.assertEqual(hm.combine([]), [])


class TrackerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.lay = layout()
        self.layer = 0
        self.timers = FakeTimers()
        self.shown: list[str | None] = []
        self.t = hm.ModifierTracker(lambda i: hm.held_key(self.lay, self.layer, i),
                                    self.shown.append, self.timers.schedule, self.timers.cancel)

    def tap(self, index: int) -> None:
        self.t.key(index, True)
        self.t.key(index, False)

    def test_modifier_then_keys(self) -> None:
        self.t.key(CTRL, True)
        self.tap(C)
        self.t.key(SHIFT, True)
        self.tap(C)
        self.t.key(SHIFT, False)
        self.t.key(CTRL, False)
        self.assertEqual(self.shown, ["Ctrl", "Ctrl+C", "Ctrl+Shift+C", "Ctrl+C", None])

    def test_last_key_replaces_and_layer_keys_add_nothing(self) -> None:
        self.t.key(ALT, True)
        self.tap(C)
        self.t.key(MO1, True)
        self.layer = 1
        self.tap(C)
        self.assertEqual(self.shown, ["Alt", "Alt+C", "Alt+!"])

    def test_shift_alone_is_typing(self) -> None:
        self.t.key(SHIFT, True)
        self.tap(C)
        self.t.key(SHIFT, False)
        self.assertEqual(self.shown, [])
        self.assertIsNone(self.t.text)

    def test_keys_typed_before_are_forgotten(self) -> None:
        self.tap(C)
        self.t.key(CTRL, True)
        self.assertEqual(self.shown, ["Ctrl"])
        self.t.key(CTRL, False)
        self.t.key(SHIFT, True)
        self.tap(C)
        self.t.key(CTRL, True)
        self.assertEqual(self.shown, ["Ctrl", None, "Ctrl+Shift"])

    def test_mod_tap_held_past_term(self) -> None:
        self.t.key(A_CTRL, True)
        self.assertEqual([ms for ms, _cb in self.timers.timers.values()], [200])
        self.assertEqual(self.shown, [])
        self.timers.fire_all()
        self.tap(C)
        self.t.key(A_CTRL, False)
        self.assertEqual(self.shown, ["Ctrl", "Ctrl+C", None])

    def test_key_in_mod_tap_term_is_sent_with_it(self) -> None:
        self.t.key(A_CTRL, True)
        self.tap(C)                       # within the term: QMK buffers it
        self.assertEqual(self.shown, [])
        self.timers.fire_all()
        self.assertEqual(self.shown, ["Ctrl+C"])

    def test_mod_tap_released_in_term_is_a_tap(self) -> None:
        self.t.key(A_CTRL, True)
        self.t.key(A_CTRL, False)
        self.assertEqual(self.timers.timers, {})
        self.assertEqual(self.shown, [])
        # With a modifier held, the tap is the key pressed.
        self.t.key(CTRL, True)
        self.tap(A_CTRL)
        self.assertEqual(self.shown, ["Ctrl", "Ctrl+A"])
        # Two mod-taps: the first resolves, the second is held too.
        self.t.key(CTRL, False)
        self.t.key(A_CTRL, True)
        self.t.key(GUI_T, True)
        self.timers.fire_all()
        self.assertEqual(self.t.text, "Ctrl+Super")

    def test_reset(self) -> None:
        self.t.key(CTRL, True)
        self.t.key(A_CTRL, True)
        self.t.reset()
        self.assertEqual(self.timers.timers, {})
        self.assertIsNone(self.t.text)
        self.t.key(CTRL, False)               # a release after reset is ignored
        self.assertEqual(self.shown, ["Ctrl"])


if __name__ == "__main__":
    unittest.main()
