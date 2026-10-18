from __future__ import annotations

import unittest

from vibemapp.backend.keykinds import KeyKind, classify, letter_char, qwerty_letter
from vibemapp.models import KeyBinding


def kb(code=None, label="", hold="", transparent=False) -> KeyBinding:
    return KeyBinding(0, tap_label=label, hold_label=hold, transparent=transparent, tap_code=code)


class ClassifyTests(unittest.TestCase):
    def test_by_code(self) -> None:
        cases = {
            "KC_A": KeyKind.LETTER, "KC_Z": KeyKind.LETTER, "DK_A": KeyKind.LETTER,
            "DK_AE": KeyKind.LETTER, "DK_OSTR": KeyKind.LETTER, "DK_ARNG": KeyKind.LETTER,
            "DE_UE": KeyKind.LETTER, "DK_T": KeyKind.LETTER, "SE_ADIA": KeyKind.LETTER,
            "KC_LSHIFT": KeyKind.MODIFIER, "KC_LSFT": KeyKind.MODIFIER,
            "KC_LEFT_SHIFT": KeyKind.MODIFIER, "KC_RSHIFT": KeyKind.MODIFIER,
            "KC_LCTRL": KeyKind.MODIFIER, "KC_RIGHT_CTRL": KeyKind.MODIFIER,
            "KC_LALT": KeyKind.MODIFIER, "KC_RALT": KeyKind.MODIFIER,
            "KC_LGUI": KeyKind.MODIFIER, "KC_LEFT_GUI": KeyKind.MODIFIER,
            "KC_HYPR": KeyKind.MODIFIER, "KC_MEH": KeyKind.MODIFIER, "OSM": KeyKind.MODIFIER,
            "MT": KeyKind.MODIFIER, "LCTL_T": KeyKind.MODIFIER, "SFT_T": KeyKind.MODIFIER,
            "ALL_T": KeyKind.MODIFIER, "C_S_T": KeyKind.MODIFIER,
            "MO": KeyKind.LAYER, "TG": KeyKind.LAYER, "TO": KeyKind.LAYER,
            "TT": KeyKind.LAYER, "OSL": KeyKind.LAYER, "DF": KeyKind.LAYER,
            "LT": KeyKind.LAYER, "LM": KeyKind.LAYER, "PDF": KeyKind.LAYER,
            "KC_NO": KeyKind.BLANK, "KC_TRANSPARENT": KeyKind.BLANK,
            "KC_BSPACE": KeyKind.OTHER, "KC_BSPC": KeyKind.OTHER, "KC_ENTER": KeyKind.OTHER,
            "KC_SPACE": KeyKind.OTHER, "KC_TAB": KeyKind.OTHER, "KC_COMMA": KeyKind.OTHER,
            "KC_SCOLON": KeyKind.OTHER, "KC_1": KeyKind.OTHER, "KC_F12": KeyKind.OTHER,
            "KC_CAPS": KeyKind.OTHER, "KC_UP": KeyKind.OTHER, "DK_ACUT": KeyKind.OTHER,
            "KC_AUDIO_MUTE": KeyKind.OTHER,
        }
        for code, kind in cases.items():
            self.assertEqual(classify(kb(code, "?")), kind, code)

    def test_hold(self) -> None:
        self.assertEqual(classify(kb("KC_A", "A", "Ctrl")), KeyKind.LETTER)     # home-row mod
        self.assertEqual(classify(kb("KC_SPACE", "Space", "L1")), KeyKind.HOLD)
        self.assertEqual(classify(kb("KC_ENTER", "⏎", "Alt")), KeyKind.HOLD)
        self.assertEqual(classify(kb(None, "", "L2", transparent=True)), KeyKind.HOLD)

    def test_blank(self) -> None:
        self.assertEqual(classify(kb(transparent=True)), KeyKind.BLANK)
        self.assertEqual(classify(kb(None, "")), KeyKind.BLANK)
        self.assertEqual(classify(KeyBinding(3, glyph="x")), KeyKind.OTHER)

    def test_label_fallback(self) -> None:
        for label, kind in (("A", KeyKind.LETTER), ("Æ", KeyKind.LETTER), ("ß", KeyKind.LETTER),
                            ("Shift", KeyKind.MODIFIER), ("Ctrl", KeyKind.MODIFIER),
                            ("Super", KeyKind.MODIFIER), ("L1", KeyKind.LAYER),
                            ("TG(2)", KeyKind.LAYER), ("⌫", KeyKind.OTHER), ("1", KeyKind.OTHER),
                            (",", KeyKind.OTHER), ("Macro", KeyKind.OTHER), ("", KeyKind.BLANK)):
            self.assertEqual(classify(kb(None, label)), kind, label)

    def test_code_wins_over_label(self) -> None:
        self.assertEqual(classify(kb("KC_BSPC", "A")), KeyKind.OTHER)
        self.assertEqual(classify(kb("KC_A", "Macro")), KeyKind.LETTER)


class LetterTests(unittest.TestCase):
    def test_qwerty_letter(self) -> None:
        self.assertEqual(qwerty_letter(kb("KC_Q")), "q")
        self.assertEqual(qwerty_letter(kb("DK_Z")), "z")
        self.assertIsNone(qwerty_letter(kb("DK_AE")))
        self.assertIsNone(qwerty_letter(kb("KC_1")))
        self.assertIsNone(qwerty_letter(kb("KC_LSFT")))
        self.assertEqual(qwerty_letter(kb(None, "k")), "k")
        self.assertIsNone(qwerty_letter(kb(None, "Æ")))

    def test_letter_char(self) -> None:
        self.assertEqual(letter_char(kb("DK_AE")), "Æ")
        self.assertEqual(letter_char(kb("KC_B")), "B")
        self.assertIsNone(letter_char(kb("KC_ENTER")))


if __name__ == "__main__":
    unittest.main()
