"""Overlay decisions without GTK: visibility, the toggle, placement, sizing.

Pure Python so it's unit-testable without a display. ``ui/overlay.py`` does
the drawing and the layer-shell surfaces.

Size
    ``overlay-size`` is a percentage of the keyboard's real size, so the
    overlay looks the same on every monitor: one key unit is 19.05 mm (the
    key pitch the geometry is expressed in; the gap between split halves is
    whatever the QMK geometry gives). Millimetres become logical pixels with
    the monitor's physical width (``Gdk.Monitor`` width_mm and logical
    width, so compositor scaling is respected), or 96 DPI when that's unknown
    or implausible. The board never gets wider than the monitor minus the
    margins.

Half spacing (``overlay-half-spacing``, 0–100 %)
    Each half of a split board has two positions on the anchored edge:

    * *joined* (0 %): where it sits in the whole board placed at the anchor
      (centred for "top"/"bottom", MARGIN_PX from the side for corner
      anchors);
    * *apart* (100 %): the left half MARGIN_PX from the left screen edge, the
      right half MARGIN_PX from the right one.

    In between, each half's x moves linearly from joined to apart. So for the
    centred anchors both halves slide outwards symmetrically, and for a
    corner anchor the half in that corner stays put while the other one
    travels towards the opposite corner. Vertically the halves keep their
    offsets within the board (the thumb clusters stay level with each other)
    and the board keeps MARGIN_PX from the anchored edge. Boards without
    halves (Planck EZ) ignore the spacing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..models import KeyGeometry

MODES = ("off", "selected", "always")
ANCHORS = ("bottom", "bottom-left", "bottom-right", "top", "top-left", "top-right")
DEFAULT_MODE = "selected"
MARGIN_PX = 24
# Smallest gap (key units) between the halves for a board to count as split.
MIN_SPLIT_GAP = 0.5
KEY_PITCH_MM = 19.05          # one key unit on the real keyboard
FALLBACK_DPI = 96.0
# Plausible pixel densities; outside this the monitor's width_mm is ignored.
MIN_PX_PER_MM = 1.5           # ~38 DPI (TVs, projectors)
MAX_PX_PER_MM = 24.0          # ~610 DPI


DEFAULT_FADE_MS = 200          # the overlay-fade-ms schema default
MAX_FADE_MS = 1000


def _get_int(settings, key: str, default: int) -> int:
    """For keys newer than a settings stand-in (MemorySettings in tests)."""
    try:
        return int(settings.get_int(key))
    except KeyError:
        return default


def fade_durations(config: "OverlayConfig") -> tuple[int, int]:
    """(fade-in, fade-out) in ms: ``fade_ms`` in, three quarters of it out
    (hiding should feel quicker than showing). (0, 0) shows and hides at once."""
    fade_in = max(0, min(MAX_FADE_MS, int(config.fade_ms)))
    return fade_in, (fade_in * 3 + 2) // 4


@dataclass(frozen=True)
class OverlayConfig:
    """The overlay-* settings, read in one go."""
    mode: str = DEFAULT_MODE
    layers: frozenset[int] = field(default_factory=frozenset)
    anchor: str = "bottom"
    size: int = 80             # % of the keyboard's real size
    opacity: int = 60          # % while no key is pressed
    half_spacing: int = 0      # 0 joined … 100 halves in the corners
    monitor: str = ""          # connector name; "" follows the focused monitor
    idle_seconds: int = 0      # 0 never hides
    fade_ms: int = DEFAULT_FADE_MS   # fade-in duration; 0 shows/hides at once

    @classmethod
    def from_settings(cls, settings) -> "OverlayConfig":
        layers = settings.get_value("overlay-layers").unpack()
        mode = settings.get_string("overlay-mode")
        anchor = settings.get_string("overlay-anchor")
        return cls(mode=mode if mode in MODES else DEFAULT_MODE,
                   layers=frozenset(int(i) for i in layers),
                   anchor=anchor if anchor in ANCHORS else "bottom",
                   size=int(settings.get_int("overlay-size")),
                   opacity=int(settings.get_int("overlay-opacity")),
                   half_spacing=int(settings.get_int("overlay-half-spacing")),
                   monitor=settings.get_string("overlay-monitor"),
                   idle_seconds=int(settings.get_int("overlay-idle-seconds")),
                   fade_ms=_get_int(settings, "overlay-fade-ms", DEFAULT_FADE_MS))


def should_show(config: OverlayConfig, active_layer: int, *, has_layout: bool,
                idle: bool = False, preview: bool = False) -> bool:
    """Whether the overlay is on screen.

    Preview always shows it. Otherwise it needs a loaded layout, a mode other
    than "off", the active layer among the selected ones ("selected") and no
    idle timeout having run out."""
    if preview:
        return True
    if not has_layout or config.mode == "off" or idle:
        return False
    if config.mode == "always":
        return True
    return active_layer in config.layers


def toggled_mode(current: str, last_on: str | None, layers: frozenset[int]) -> str:
    """``app.toggle-overlay``: "off" ↔ the last mode that wasn't "off".

    Nothing extra is stored, so after a restart with the overlay off the last
    mode is unknown: then "selected" if layers are selected (else it would
    never show) and "always" otherwise."""
    if current != "off":
        return "off"
    if last_on in ("selected", "always"):
        return last_on
    return "selected" if layers else "always"


def anchor_edges(anchor: str) -> tuple[str, str | None]:
    """``"bottom-left"`` -> ("bottom", "left"); ``"top"`` -> ("top", None)."""
    vertical, _sep, horizontal = anchor.partition("-")
    if vertical not in ("top", "bottom"):
        vertical = "bottom"
    return vertical, horizontal if horizontal in ("left", "right") else None


def _key_extent(key: KeyGeometry) -> tuple[float, float]:
    cx = key.x + key.w / 2
    cy = key.y + key.h / 2
    rad = math.radians(key.rotation)
    cos, sin = math.cos(rad), math.sin(rad)
    xs = [cx + dx * cos - dy * sin
          for dx, dy in ((-key.w / 2, -key.h / 2), (key.w / 2, -key.h / 2),
                         (key.w / 2, key.h / 2), (-key.w / 2, key.h / 2))]
    return min(xs), max(xs)


def _flat_gap(left: tuple[KeyGeometry, ...], right: tuple[KeyGeometry, ...]) -> float:
    """Horizontal gap between the halves' unrotated keys (thumb clusters may
    angle towards each other)."""
    flat_left = [k for k in left if not k.rotation] or list(left)
    flat_right = [k for k in right if not k.rotation] or list(right)
    return (min(_key_extent(k)[0] for k in flat_right)
            - max(_key_extent(k)[1] for k in flat_left))


def split_geometry(geometry: tuple[KeyGeometry, ...]
                   ) -> tuple[tuple[KeyGeometry, ...], tuple[KeyGeometry, ...]] | None:
    """Left and right halves of a split board, or None for a one-piece board
    (e.g. the Planck EZ).

    Oryx key indices list the whole left half, then the right half, so the
    halves are normally the first and second half by index. Geometry in
    another order (the mock keyboard interleaves rows) is divided at the
    board's horizontal middle instead. Either way it counts as split only when
    the halves' unrotated keys are at least MIN_SPLIT_GAP apart."""
    if len(geometry) < 2:
        return None
    ordered = sorted(geometry, key=lambda k: k.index)
    half = len(ordered) // 2
    candidates = [(tuple(ordered[:half]), tuple(ordered[half:]))]
    centres = {k.index: sum(_key_extent(k)) / 2 for k in geometry}
    mid = (min(centres.values()) + max(centres.values())) / 2
    candidates.append((tuple(k for k in geometry if centres[k.index] < mid),
                       tuple(k for k in geometry if centres[k.index] >= mid)))
    for left, right in candidates:
        if left and right and _flat_gap(left, right) >= MIN_SPLIT_GAP:
            return left, right
    return None


Bounds = tuple[float, float, float, float]


def geometry_bounds(geometry: tuple[KeyGeometry, ...]) -> Bounds:
    """(x0, y0, x1, y1) in key units, honouring rotation (same as KeyboardView)."""
    if not geometry:
        return 0.0, 0.0, 1.0, 1.0
    xs: list[float] = []
    ys: list[float] = []
    for key in geometry:
        cx, cy = key.x + key.w / 2, key.y + key.h / 2
        rad = math.radians(key.rotation)
        cos, sin = math.cos(rad), math.sin(rad)
        for dx, dy in ((-key.w / 2, -key.h / 2), (key.w / 2, -key.h / 2),
                       (key.w / 2, key.h / 2), (-key.w / 2, key.h / 2)):
            xs.append(cx + dx * cos - dy * sin)
            ys.append(cy + dx * sin + dy * cos)
    return min(xs), min(ys), max(xs), max(ys)


@dataclass(frozen=True)
class MonitorInfo:
    """What placement needs to know about a monitor (logical pixels)."""
    connector: str | None
    width: int
    height: int
    width_mm: int = 0


def px_per_mm(monitor: MonitorInfo) -> float:
    """Logical pixels per millimetre, or 96 DPI when the size is unknown."""
    if monitor.width_mm > 0 and monitor.width > 0:
        density = monitor.width / monitor.width_mm
        if MIN_PX_PER_MM <= density <= MAX_PX_PER_MM:
            return density
    return FALLBACK_DPI / 25.4


def unit_px(monitor: MonitorInfo, size_percent: int, board_width_units: float) -> float:
    """Pixels per key unit for ``size_percent`` of the real keyboard, clamped
    so the whole board fits between the side margins."""
    unit = max(size_percent, 1) / 100 * KEY_PITCH_MM * px_per_mm(monitor)
    fit = max(monitor.width - 2 * MARGIN_PX, 1) / max(board_width_units, 0.1)
    return min(unit, fit)


@dataclass(frozen=True)
class PartPlacement:
    """Where one surface goes: ``x`` from the monitor's left edge and
    ``edge_margin`` from the anchored (top or bottom) edge, in logical px;
    ``width``/``height`` of its board part."""
    x: int
    edge_margin: int
    width: int
    height: int


def place_parts(monitor: MonitorInfo, anchor: str, unit: float, board: Bounds,
                parts: list[Bounds], half_spacing: int) -> list[PartPlacement]:
    """Placement of the whole board (one part) or its two halves (left, right)
    for ``half_spacing`` percent; see the module docstring for the rule."""
    vertical, horizontal = anchor_edges(anchor)
    bx0, by0, bx1, by1 = board
    board_w = (bx1 - bx0) * unit
    if horizontal == "left":
        board_left = float(MARGIN_PX)
    elif horizontal == "right":
        board_left = monitor.width - MARGIN_PX - board_w
    else:
        board_left = (monitor.width - board_w) / 2
    t = max(0, min(100, half_spacing)) / 100 if len(parts) == 2 else 0.0
    out = []
    for n, (x0, y0, x1, y1) in enumerate(parts):
        width = (x1 - x0) * unit
        joined = board_left + (x0 - bx0) * unit
        apart = MARGIN_PX if n == 0 else monitor.width - MARGIN_PX - width
        x = joined + (apart - joined) * t
        inset = (by1 - y1) if vertical == "bottom" else (y0 - by0)
        out.append(PartPlacement(x=int(round(x)), edge_margin=int(round(MARGIN_PX + inset * unit)),
                                 width=max(int(round(width)), 1),
                                 height=max(int(round((y1 - y0) * unit)), 1)))
    return out


__all__ = ["ANCHORS", "DEFAULT_FADE_MS", "DEFAULT_MODE", "fade_durations", "KEY_PITCH_MM", "MARGIN_PX", "MODES", "MonitorInfo",
           "OverlayConfig", "PartPlacement", "anchor_edges", "geometry_bounds",
           "place_parts", "px_per_mm", "should_show", "split_geometry", "toggled_mode",
           "unit_px"]
