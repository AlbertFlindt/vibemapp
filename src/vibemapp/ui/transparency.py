"""Transparent keys: resolving what a ``▽`` key falls through to.

Pure Python (no GTK), so it's unit-testable without a display.

A transparent key passes the press to the next *active* layer below. Which
lower layers are active is runtime state we can't see, so callers say which
ones to consider. The layout view uses only the base layer: higher layers are
usually reached from the base (MO/LT/TO), and Smart Layers' set-layer is
``layer_move``, which leaves only the target and the base layer active. With
``active=None`` every lower layer counts (QMK's full stack order).
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass

from ..models import KeyBinding, Layer
from .i18n import _
from .rules_model import layer_label

# The Oryx convention for a transparent key.
TRANSPARENT_MARK = "▽"

# Layer positions a transparent key falls through to in the layout view.
BASE_ONLY = frozenset({0})


def is_transparent(binding: KeyBinding | None) -> bool:
    return binding is not None and bool(binding.transparent)


def binding_label(binding: KeyBinding | None) -> str:
    """Short human name for a binding: the tap label, else the hold label."""
    if binding is None:
        return ""
    return binding.tap_label or binding.hold_label


@dataclass(frozen=True)
class FallThrough:
    """The effective binding behind a transparent key."""
    binding: KeyBinding
    layer: Layer
    layer_position: int   # position of ``layer`` in the layout's layer tuple


def layer_position(layers: Sequence[Layer], layer: Layer) -> int | None:
    """Position of ``layer`` in ``layers`` (by identity, then equality, then index)."""
    for pos, candidate in enumerate(layers):
        if candidate is layer:
            return pos
    for pos, candidate in enumerate(layers):
        if candidate == layer:
            return pos
    for pos, candidate in enumerate(layers):
        if candidate.index == layer.index:
            return pos
    return None


def resolve_fallthrough(layers: Sequence[Layer], position: int, key_index: int,
                        active: Collection[int] | None = None) -> FallThrough | None:
    """First non-transparent binding for ``key_index`` below ``layers[position]``.

    ``active`` holds the layer positions to consider (None: all of them).
    Returns None when nothing lies below (a transparent key on the base layer)
    or a lower layer doesn't define the key at all. A lower key that is simply
    unassigned is returned as is: that's what the press really does.
    """
    for pos in range(min(position, len(layers)) - 1, -1, -1):
        if active is not None and pos not in active:
            continue
        layer = layers[pos]
        binding = next((k for k in layer.keys if k.index == key_index), None)
        if binding is None:
            return None
        if not is_transparent(binding):
            return FallThrough(binding, layer, pos)
    return None


def fallthrough_map(layers: Sequence[Layer], layer: Layer | None,
                    active: Collection[int] | None = None) -> dict[int, FallThrough]:
    """Effective bindings for every transparent key on ``layer``."""
    if layer is None:
        return {}
    position = layer_position(layers, layer)
    if position is None:
        return {}
    out: dict[int, FallThrough] = {}
    for binding in layer.keys:
        if is_transparent(binding):
            resolved = resolve_fallthrough(layers, position, binding.index, active)
            if resolved is not None:
                out[binding.index] = resolved
    return out


def transparent_tooltip(fall: FallThrough | None, hold_label: str = "") -> str:
    """Tooltip for a transparent key in the layout view.

    ``hold_label`` is the key's own hold action (``LT(n, KC_TRNS)``): only the
    tap falls through then.
    """
    if fall is None:
        text = _("Transparent: no layer below")
    else:
        title = layer_label(fall.layer, fall.layer.index)
        label = binding_label(fall.binding)
        if not label:
            text = _("Transparent: no key assigned on {layer}").format(layer=title)
        else:
            text = _("Transparent: uses “{label}” from {layer}").format(label=label,
                                                                       layer=title)
    if hold_label:
        return _("{tap} · hold for {hold}").format(tap=text, hold=hold_label)
    return text
