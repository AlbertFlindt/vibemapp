"""Heatmap colour ramp and number helpers. Pure Python (no GTK), unit-testable.

The ramp is a single blue hue whose lightness changes monotonically, so the
order is readable without relying on hue (and survives colour-vision
deficiencies and greyscale). Light mode runs light → dark (many presses =
darkest); dark mode flips the anchor so "few" sits near the dark surface and
"many" is brightest. Counts are also printed on the keys, so colour is never
the only channel.
"""

from __future__ import annotations

import math

from .i18n import _

Rgb = tuple[float, float, float]

# Blue sequential steps 100 → 700 (validated reference ramp).
_STEPS = ("#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b")


def _hex(spec: str) -> Rgb:
    spec = spec.lstrip("#")
    return tuple(int(spec[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


LIGHT_RAMP: tuple[Rgb, ...] = tuple(_hex(s) for s in _STEPS)
DARK_RAMP: tuple[Rgb, ...] = tuple(reversed(LIGHT_RAMP))


def ramp(dark: bool) -> tuple[Rgb, ...]:
    return DARK_RAMP if dark else LIGHT_RAMP


def color_at(t: float, dark: bool) -> Rgb:
    """Interpolated ramp colour for ``t`` in [0, 1]."""
    stops = ramp(dark)
    t = max(0.0, min(1.0, t)) * (len(stops) - 1)
    i = min(int(t), len(stops) - 2)
    f = t - i
    a, b = stops[i], stops[i + 1]
    return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f)


def relative_luminance(rgb: Rgb) -> float:
    def channel(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: Rgb, b: Rgb) -> float:
    la, lb = sorted((relative_luminance(a), relative_luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def text_is_light(fill: Rgb) -> bool:
    """True when white text reads better than near-black on ``fill``."""
    return contrast(fill, (1.0, 1.0, 1.0)) >= contrast(fill, (0.1, 0.1, 0.1))


def intensity(count: int, maximum: int) -> float:
    """Position on the ramp. Square-root scale, so a handful of very hot keys
    (space, e, backspace) doesn't wash every other key out. 0 for no presses;
    any press lands at least on the first visible step."""
    if count <= 0 or maximum <= 0:
        return 0.0
    return max(0.08, math.sqrt(count / maximum))


def compact_count(n: int) -> str:
    """Short label for a key: 999, 1.2k, 12k, 1.3M."""
    if n < 1000:
        return str(n)
    if n < 10_000:
        return f"{n / 1000:.1f}k".replace(".0k", "k")
    if n < 1_000_000:
        return f"{n // 1000}k"
    return f"{n / 1_000_000:.1f}M".replace(".0M", "M")


def format_count(n: int) -> str:
    """Full number with locale-ish grouping (thin space-free): 12,345."""
    return f"{n:,}"


def top_keys(counts: dict[int, int], limit: int = 5) -> list[tuple[int, int]]:
    """``(index, count)`` pairs, most pressed first; ties broken by index."""
    ranked = sorted(((i, c) for i, c in counts.items() if c > 0),
                    key=lambda item: (-item[1], item[0]))
    return ranked[:limit]


def share_label(count: int, total: int) -> str:
    if total <= 0:
        return ""
    pct = 100 * count / total
    return _("{pct}%").format(pct=f"{pct:.1f}" if pct < 10 else f"{pct:.0f}")
