"""What kind of key a binding is (SPEC: layout coach). Pure, no GTK.

The layout coach and the layout comparison need to tell letters from keys that
may be moved freely. ``classify`` looks at ``KeyBinding.tap_code`` (the Oryx/QMK
keycode of the tap action) first and falls back to the labels when the code is
unknown (old data, hand-made bindings).

Kinds, checked in this order:

* ``BLANK``: transparent without a hold action, ``KC_NO`` or an empty key.
* ``LAYER``: a layer key by tap code (MO, TG, TO, TT, OSL, DF, LT, LM, PDF, i.e.
  ``oryx_api.LAYER_CODES``); by label ``L1``, ``TG(2)``…
* ``MODIFIER``: Shift, Ctrl, Alt, AltGr, Super, Meh, Hyper (``KC_LSFT``,
  ``KC_LEFT_SHIFT``, ``KC_RALT``…), one-shot mods (OSM), mod-taps (``MT``,
  ``LCTL_T``…) and the shift-parenthesis keys.
* ``LETTER``: one alphabetic character as the tap: ``KC_A``…``KC_Z`` and language
  variants (``DK_A``, ``DE_Z``, and ``DK_AE``/``DK_OSTR``/``DK_ARNG`` = Æ Ø Å).
  A letter keeps its kind with a hold action (home-row mods).
* ``HOLD``: anything else with a hold action (Space that holds a layer, …).
* ``OTHER``: the rest (Backspace, Enter, Tab, digits, punctuation, F-keys…).
"""

from __future__ import annotations

import enum
import re

from ..models import KeyBinding
from . import oryx_api


class KeyKind(enum.Enum):
    BLANK = "blank"
    LAYER = "layer"
    MODIFIER = "modifier"
    LETTER = "letter"
    HOLD = "hold"
    OTHER = "other"


_NO_CODES = frozenset({"KC_NO", "XXXXXXX", "KC_TRANSPARENT", "KC_TRNS", "_______"})

_MOD_NAMES = frozenset({
    "LCTRL", "LCTL", "LEFT_CTRL", "RCTRL", "RCTL", "RIGHT_CTRL",
    "LSHIFT", "LSFT", "LEFT_SHIFT", "RSHIFT", "RSFT", "RIGHT_SHIFT",
    "LALT", "LOPT", "LEFT_ALT", "RALT", "ROPT", "ALGR", "RIGHT_ALT",
    "LGUI", "LCMD", "LWIN", "LEFT_GUI", "RGUI", "RCMD", "RWIN", "RIGHT_GUI",
    "HYPR", "MEH"})
_MOD_CODES = frozenset({"OSM", "MT", "SC_LSPO", "SC_RSPC", "SC_LAPO", "SC_RAPC",
                        "SC_SENT"}) | frozenset(oryx_api._MOD_TAPS)    # noqa: SLF001
# LCTL_T, SFT_T, LSA_T…: three or more letters before "_T" (DK_T is the letter T).
_MOD_TAP = re.compile(r"[A-Z]{3,}_T")

# Plain letter codes: KC_A or a two-letter language prefix (DK_A, DE_Z).
_LETTER_CODE = re.compile(r"(?:KC|[A-Z]{2})_([A-Z])")

_MOD_LABELS = frozenset({"Ctrl", "Shift", "Alt", "AltGr", "Super", "Meh", "Hyper", "Ctrl⇧",
                         "CtrlAlt", "CAG", "Super⇧", "Alt⇧", "⇧(", "⇧)"})
_LAYER_LABEL = re.compile(r"L(?:\d+|\?)|(?:MO|TG|TO|OSL|TT|DF|LM|PDF)\(\??\d*\)")


def _is_layer_code(code: str) -> bool:
    return code in oryx_api.LAYER_CODES


def _is_mod_code(code: str) -> bool:
    if code in _MOD_CODES:
        return True
    if code.startswith("KC_"):
        return code[3:] in _MOD_NAMES
    return bool(_MOD_TAP.fullmatch(code))


def letter_char(binding: KeyBinding) -> str | None:
    """The single letter the key types (``"A"``, ``"Æ"``), upper case, or None.
    From ``tap_code`` when set, otherwise from a one-letter ``tap_label``."""
    code = binding.tap_code
    if code:
        label = oryx_api.keycode_label(code)
        return label.upper() if len(label) == 1 and label.isalpha() else None
    label = binding.tap_label
    return label.upper() if len(label) == 1 and label.isalpha() else None


def qwerty_letter(binding: KeyBinding) -> str | None:
    """The Latin letter A..Z of a plain letter key, as a lower-case ASCII letter
    (``KC_A`` and ``DK_A`` give ``"a"``), else None (Æ, digits, anything else).
    Uses ``tap_code``; the one-letter label only when there is no code."""
    code = binding.tap_code
    if code:
        m = _LETTER_CODE.fullmatch(code)
        return m.group(1).lower() if m else None
    label = binding.tap_label
    return label.lower() if len(label) == 1 and label.isascii() and label.isalpha() else None


def classify(binding: KeyBinding) -> KeyKind:
    code = binding.tap_code
    hold = bool(binding.hold_label)
    if code in _NO_CODES or (not code and (binding.transparent or not (
            binding.tap_label or binding.glyph))):
        return KeyKind.HOLD if hold else KeyKind.BLANK
    if code:
        if _is_layer_code(code):
            return KeyKind.LAYER
        if _is_mod_code(code):
            return KeyKind.MODIFIER
    else:
        label = binding.tap_label
        if _LAYER_LABEL.fullmatch(label):
            return KeyKind.LAYER
        if label in _MOD_LABELS:
            return KeyKind.MODIFIER
    if letter_char(binding) is not None:
        return KeyKind.LETTER
    return KeyKind.HOLD if hold else KeyKind.OTHER


__all__ = ["KeyKind", "classify", "letter_char", "qwerty_letter"]
