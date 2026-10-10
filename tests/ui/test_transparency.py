from __future__ import annotations

import unittest

from vibemapp.models import KeyBinding, Layer
from vibemapp.ui import transparency as tr


def _trns(index: int, hold: str = "") -> KeyBinding:
    return KeyBinding(index, hold_label=hold, transparent=True)


BASE = Layer(0, "Base", None, (KeyBinding(0, "A", "Ctrl"), KeyBinding(1, "B"),
                               KeyBinding(2, ""), KeyBinding(3, "D")))
SYM = Layer(1, "Symbols", None, (_trns(0), KeyBinding(1, "!"), _trns(2), _trns(3)))
NAV = Layer(2, "Navigation", None, (_trns(0), _trns(1), KeyBinding(2, "←"), _trns(3, "L1")))
LAYERS = (BASE, SYM, NAV)


class FallThroughTests(unittest.TestCase):
    def test_resolves_to_first_non_transparent_below(self) -> None:
        fall = tr.resolve_fallthrough(LAYERS, 2, 1)
        self.assertEqual((fall.binding.tap_label, fall.layer.title, fall.layer_position),
                         ("!", "Symbols", 1))

    def test_skips_several_transparent_layers_down_to_base(self) -> None:
        fall = tr.resolve_fallthrough(LAYERS, 2, 0)
        self.assertEqual((fall.binding.tap_label, fall.layer_position), ("A", 0))

    def test_unassigned_lower_key_is_the_effective_binding(self) -> None:
        fall = tr.resolve_fallthrough(LAYERS, 1, 2)
        self.assertIs(fall.binding, BASE.keys[2])

    def test_nothing_below_base_or_missing_key(self) -> None:
        self.assertIsNone(tr.resolve_fallthrough(LAYERS, 0, 0))
        self.assertIsNone(tr.resolve_fallthrough(LAYERS, 1, 9))
        self.assertIsNone(tr.resolve_fallthrough((), 3, 0))

    def test_map_covers_only_transparent_keys(self) -> None:
        mapping = tr.fallthrough_map(LAYERS, NAV)
        self.assertEqual(sorted(mapping), [0, 1, 3])
        self.assertEqual(mapping[3].binding.tap_label, "D")
        self.assertEqual(tr.fallthrough_map(LAYERS, BASE), {})
        self.assertEqual(tr.fallthrough_map((), NAV), {})
        self.assertEqual(tr.fallthrough_map(LAYERS, None), {})

    def test_map_finds_equal_layer_copies(self) -> None:
        copy = Layer(2, "Navigation", None, NAV.keys)
        self.assertEqual(sorted(tr.fallthrough_map(LAYERS, copy)), [0, 1, 3])

    def test_tooltips(self) -> None:
        self.assertEqual(tr.transparent_tooltip(tr.resolve_fallthrough(LAYERS, 2, 1)),
                         "Transparent: uses “!” from Layer 1 · Symbols")
        self.assertEqual(tr.transparent_tooltip(tr.resolve_fallthrough(LAYERS, 1, 2)),
                         "Transparent: no key assigned on Layer 0 · Base")
        self.assertEqual(tr.transparent_tooltip(None), "Transparent: no layer below")
        self.assertEqual(tr.transparent_tooltip(tr.resolve_fallthrough(LAYERS, 2, 3), "L1"),
                         "Transparent: uses “D” from Layer 0 · Base · hold for L1")

    def test_active_layers_limit_the_walk(self) -> None:
        # Navigation reached straight from Base: Symbols isn't active.
        fall = tr.resolve_fallthrough(LAYERS, 2, 1, tr.BASE_ONLY)
        self.assertEqual((fall.binding.tap_label, fall.layer_position), ("B", 0))
        self.assertEqual(tr.fallthrough_map(LAYERS, NAV, tr.BASE_ONLY)[1].binding.tap_label,
                         "B")
        self.assertIsNone(tr.resolve_fallthrough(LAYERS, 2, 1, ()))

    def test_is_transparent(self) -> None:
        self.assertTrue(tr.is_transparent(_trns(0)))
        self.assertFalse(tr.is_transparent(KeyBinding(0)))
        self.assertFalse(tr.is_transparent(None))


if __name__ == "__main__":
    unittest.main()
