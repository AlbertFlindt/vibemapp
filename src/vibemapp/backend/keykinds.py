"""Letter detection for the layout comparison (SPEC: alternative layouts). Pure, no GTK.

Looks at ``KeyBinding.tap_code`` (the Oryx/QMK keycode of the tap action) first and
falls back to a one-character ``tap_label`` when the code is unknown (old data,
hand-made bindings).
"""

from __future__ import annotations

import re

from ..models import KeyBinding
from . import oryx_api

# Plain letter codes: KC_A or a two-letter language prefix (DK_A, DE_Z).
_LETTER_CODE = re.compile(r"(?:KC|[A-Z]{2})_([A-Z])")


def letter_char(binding: KeyBinding) -> str | None:
    """The single letter the key types (``"A"``, ``"Æ"``), upper case, or None.
    From ``tap_code`` when set, otherwise from a one-letter ``tap_label``. Æ Ø Å come
    from ``DK_AE``, ``DK_OSTR``, ``DK_ARNG``."""
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


__all__ = ["letter_char", "qwerty_letter"]
