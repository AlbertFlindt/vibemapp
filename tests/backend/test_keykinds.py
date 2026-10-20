from __future__ import annotations

import unittest

from vibemapp.backend.keykinds import letter_char, qwerty_letter
from vibemapp.models import KeyBinding


def kb(code=None, label="") -> KeyBinding:
    return KeyBinding(0, tap_label=label, tap_code=code)


class LetterTests(unittest.TestCase):
    def test_qwerty_letter(self) -> None:
        self.assertEqual(qwerty_letter(kb("KC_Q")), "q")
        self.assertEqual(qwerty_letter(kb("DK_Z")), "z")
        self.assertIsNone(qwerty_letter(kb("DK_AE")))
        self.assertIsNone(qwerty_letter(kb("KC_1")))
        self.assertIsNone(qwerty_letter(kb("KC_LSFT")))
        self.assertIsNone(qwerty_letter(kb("MO")))
        self.assertEqual(qwerty_letter(kb(None, "k")), "k")
        self.assertIsNone(qwerty_letter(kb(None, "Æ")))
        self.assertIsNone(qwerty_letter(kb("KC_BSPC", "A")))        # the code wins

    def test_letter_char(self) -> None:
        self.assertEqual(letter_char(kb("DK_AE")), "Æ")
        self.assertEqual(letter_char(kb("DK_OSTR")), "Ø")
        self.assertEqual(letter_char(kb("DK_ARNG")), "Å")
        self.assertEqual(letter_char(kb("KC_B")), "B")
        self.assertEqual(letter_char(kb("DE_UE")), "Ü")
        self.assertIsNone(letter_char(kb("KC_ENTER")))
        self.assertIsNone(letter_char(kb("KC_F12")))
        self.assertIsNone(letter_char(kb(None, "Shift")))


if __name__ == "__main__":
    unittest.main()
