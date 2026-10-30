"""Held modifiers: following which modifiers are down, and the keys pressed with them.

Pure Python (no GTK), so it's unit-testable without a display.

The keyboard reports raw presses and releases (see ``hold_actions``), so a
modifier key counts from its press. A mod-tap (hold A = Ctrl) counts as QMK
decides by default: once it's still down one tapping term after its press.
Keys pressed in that term are sent with the modifier, so the last of them
shows; released in time it was a tap and types its own key.

``ModifierTracker`` reports the text to show, such as ``"Ctrl+Shift+T"``:
the held modifiers in Ctrl, Shift, Alt, AltGr, Super order, then the last key
pressed while they're held. Shift alone never shows: that is typing capitals.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..models import Layout
from .hold_actions import Cancel, Schedule, effective_binding, tapping_term_for

MODIFIER_ORDER = ("Ctrl", "Shift", "Alt", "AltGr", "Super")
# Tap codes that only switch layers: pressing one adds no key to the text.
_LAYER_ONLY_CODES = frozenset({"MO", "TG", "TO", "OSL", "TT", "DF", "LM", "PDF", "KC_NO"})


@dataclass(frozen=True)
class HeldKey:
    """What a key does for the tracker. ``modifiers`` is ``"Ctrl"`` or
    ``"Ctrl+Shift"`` for a modifier (``tapping_term_ms`` 0) or a mod-tap; ``label``
    is the key it types (the tap of a mod-tap), empty when it types nothing."""
    modifiers: str = ""
    label: str = ""
    tapping_term_ms: int = 0


def held_key(layout: Layout | None, layer: int, index: int) -> HeldKey | None:
    """Key ``index`` on ``layer`` (a transparent key uses the base layer's)."""
    found = effective_binding(layout, layer, index)
    if found is None:
        return None
    binding = found[0]
    if binding.modifier:
        return HeldKey(binding.modifier)
    label = "" if binding.tap_code in _LAYER_ONLY_CODES else binding.tap_label
    if binding.hold_modifier:
        return HeldKey(binding.hold_modifier, label, tapping_term_for(binding, layout))
    return HeldKey(label=label) if label else None


def combine(modifiers: list[str]) -> list[str]:
    """Modifier names of several ``"Ctrl+Shift"`` strings, once each, in order."""
    held = {name for mods in modifiers for name in mods.split("+") if name}
    return [name for name in MODIFIER_ORDER if name in held]


class ModifierTracker:
    """Calls ``changed(text)`` with the text to show, or ``None`` when no
    modifier (other than Shift alone) is held any more.

    Feed it every key event with ``key(index, pressed)``. ``lookup`` gives a key's
    ``HeldKey`` at press time, or None; ``schedule(ms, cb)`` and ``cancel(source)``
    are the timer (GLib in the app, fake in tests)."""

    def __init__(self, lookup: Callable[[int], HeldKey | None],
                 changed: Callable[[str | None], None],
                 schedule: Schedule, cancel: Cancel) -> None:
        self._lookup = lookup
        self._changed = changed
        self._schedule = schedule
        self._cancel = cancel
        self._held: dict[int, str] = {}                      # index -> modifiers
        self._pending: dict[int, tuple[HeldKey, object]] = {}  # mod-taps in their term
        self._last = ""
        self._text: str | None = None

    @property
    def text(self) -> str | None:
        return self._text

    def key(self, index: int, pressed: bool) -> None:
        if pressed:
            self._press(index)
        else:
            self._release(index)

    def reset(self) -> None:
        """Forget every key, e.g. on disconnect (no ``changed`` call)."""
        for _key, timer in self._pending.values():
            self._cancel(timer)
        self._pending.clear()
        self._held.clear()
        self._last = ""
        self._text = None

    def _press(self, index: int) -> None:
        info = self._lookup(index)
        if not self._held and not self._pending:
            self._last = ""          # a fresh chord: forget keys typed before it
        if info is None:
            return
        if info.modifiers and not info.tapping_term_ms:
            self._held[index] = info.modifiers
        elif info.modifiers:
            old = self._pending.pop(index, None)
            if old is not None:
                self._cancel(old[1])
            timer = self._schedule(info.tapping_term_ms, lambda: self._resolve(index))
            self._pending[index] = (info, timer)
            return
        elif self._held or self._pending:
            self._last = info.label
        self._publish()

    def _release(self, index: int) -> None:
        if index in self._held:
            del self._held[index]
            self._publish()
        elif index in self._pending:
            info, timer = self._pending.pop(index)
            self._cancel(timer)
            if info.label and (self._held or self._pending):
                self._last = info.label       # a tap, typed with what's held
                self._publish()

    def _resolve(self, index: int) -> None:
        entry = self._pending.pop(index, None)
        if entry is not None:
            self._held[index] = entry[0].modifiers
            self._publish()

    def _publish(self) -> None:
        names = combine(list(self._held.values()))
        if not names or names == ["Shift"]:
            text = None
            if not self._pending:
                self._last = ""      # keep a key pressed in a mod-tap's term
        else:
            text = "+".join([*names, self._last] if self._last else names)
        if text != self._text:
            self._text = text
            self._changed(text)


__all__ = ["HeldKey", "MODIFIER_ORDER", "ModifierTracker", "combine", "held_key"]
