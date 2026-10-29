"""Hold actions: telling from key events when a key's hold action fired.

Pure Python (no GTK), so it's unit-testable without a display.

The keyboard reports raw presses and releases, sent before QMK decides between
tap and hold (``pre_process_record_oryx`` in zsa/qmk_modules), so it never says
which one happened. Oryx builds "hold for another key" (hold C = Ctrl+C) as a
tap dance, and a tap dance resolves as a hold when the key is still down one
tapping term after its press and no other key was pressed meanwhile. That is
what ``HoldDetector`` watches for. A press within a tapping term of the same
key's tap continues the dance (a double tap), so it never counts.

Tapping term: the key's own Oryx value, else the layout's, else
``DEFAULT_TAPPING_TERM_MS`` (Oryx and QMK's default).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from ..models import KeyBinding, Layout
from .rules_model import layer_label
from .transparency import BASE_ONLY, is_transparent, resolve_fallthrough

DEFAULT_TAPPING_TERM_MS = 200


@dataclass(frozen=True)
class HoldAction:
    """A key whose hold sends a key combination, e.g. ``"Ctrl+C"``."""
    layer: int
    index: int
    combo: str
    tap_label: str
    tapping_term_ms: int


def key_id(layer: int, index: int) -> str:
    """The id a hold action has in ``hold-pill-excluded``: ``"layer:index"``."""
    return f"{layer}:{index}"


def tapping_term_for(binding: KeyBinding, layout: Layout | None) -> int:
    if binding.tapping_term_ms:
        return binding.tapping_term_ms
    if layout is not None and layout.tapping_term_ms:
        return layout.tapping_term_ms
    return DEFAULT_TAPPING_TERM_MS


def effective_binding(layout: Layout | None, layer: int,
                      index: int) -> tuple[KeyBinding, int] | None:
    """What key ``index`` does on ``layer``, and the layer that defines it: a
    transparent key falls through to the base layer, as in the layout view."""
    if layout is None or not 0 <= layer < len(layout.layers):
        return None
    binding = next((k for k in layout.layers[layer].keys if k.index == index), None)
    if binding is None:
        return None
    if not is_transparent(binding):
        return binding, layer
    fall = resolve_fallthrough(layout.layers, layer, index, BASE_ONLY)
    return (fall.binding, fall.layer_position) if fall is not None else None


def hold_action(layout: Layout | None, layer: int, index: int) -> HoldAction | None:
    found = effective_binding(layout, layer, index)
    if found is None or not found[0].hold_combo:
        return None
    binding, own_layer = found
    return HoldAction(own_layer, index, binding.hold_combo, binding.tap_label,
                      tapping_term_for(binding, layout))


def layout_hold_actions(layout: Layout | None) -> list[HoldAction]:
    """Every key's own hold action, layer by layer (fall-throughs left out:
    the base layer lists those)."""
    if layout is None:
        return []
    return [HoldAction(layer.index, key.index, key.hold_combo, key.tap_label,
                       tapping_term_for(key, layout))
            for layer in layout.layers for key in layer.keys
            if key.hold_combo and not is_transparent(key)]


def hold_action_subtitle(action: HoldAction, layout: Layout | None) -> str:
    """``"Hold C on Layer 0 · Base"`` for the settings list."""
    from .i18n import _
    layers = layout.layers if layout is not None else ()
    layer = layers[action.layer] if 0 <= action.layer < len(layers) else None
    name = layer_label(layer, action.layer)
    if action.tap_label:
        # Translators: a key's tap label, then its layer, e.g. "Hold C on Layer 0 · Base"
        return _("Hold {key} on {layer}").format(key=action.tap_label, layer=name)
    return name


def toggle_excluded(excluded: Iterable[str], key: str, shown: bool) -> list[str]:
    """Sorted excluded ids with ``key`` removed (shown) or added (hidden).
    Ids the current layout doesn't have are kept: another layout's."""
    result = set(excluded)
    if shown:
        result.discard(key)
    else:
        result.add(key)
    return sorted(result)


Schedule = Callable[[int, Callable[[], None]], object]
Cancel = Callable[[object], None]


class HoldDetector:
    """Calls ``fired(action)`` when a hold action resolves as a hold.

    Feed it every key event with ``key(index, pressed, now_ms)``. ``lookup``
    gives the hold action of a key at press time, or None; ``schedule(ms, cb)``
    and ``cancel(source)`` are the timer (GLib in the app, fake in tests)."""

    def __init__(self, lookup: Callable[[int], HoldAction | None],
                 fired: Callable[[HoldAction], None],
                 schedule: Schedule, cancel: Cancel) -> None:
        self._lookup = lookup
        self._fired = fired
        self._schedule = schedule
        self._cancel = cancel
        self._pending: HoldAction | None = None
        self._timer: object | None = None
        # Release time of keys whose last press was a tap: a dance may go on.
        self._tapped: dict[int, float] = {}

    @property
    def pending(self) -> HoldAction | None:
        return self._pending

    def key(self, index: int, pressed: bool, now_ms: float) -> None:
        if not pressed:
            if self._pending is not None and self._pending.index == index:
                self.reset()     # released in time: a tap
                self._tapped[index] = now_ms
            return
        # Another key pressed first interrupts the dance: a tap.
        self.reset()
        action = self._lookup(index)
        tapped = self._tapped.pop(index, None)
        if action is None:
            return
        if tapped is not None and now_ms - tapped < action.tapping_term_ms:
            return               # a second tap of the same dance
        self._pending = action
        self._timer = self._schedule(action.tapping_term_ms, self._on_timeout)

    def reset(self) -> None:
        """Forget a pending key, e.g. on disconnect."""
        if self._timer is not None:
            self._cancel(self._timer)
        self._timer = None
        self._pending = None

    def _on_timeout(self) -> None:
        action, self._pending, self._timer = self._pending, None, None
        if action is not None:
            self._fired(action)


__all__ = ["DEFAULT_TAPPING_TERM_MS", "HoldAction", "HoldDetector", "effective_binding",
           "hold_action", "hold_action_subtitle", "key_id", "layout_hold_actions",
           "tapping_term_for", "toggle_excluded"]
