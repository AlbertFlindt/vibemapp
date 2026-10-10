"""Shared data models — part of the backend/frontend contract.

Owned by the PM. Do not change field names or semantics without going through
the PM; both the backend and the UI depend on these.

All geometry is expressed in *key units* (1.0 == width of a standard 1u key).
Key ``index`` values follow the Oryx key-index order for the given model, so
``KeyGeometry.index``, ``KeyBinding.index`` and ``key-event`` indices all line
up with each other.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class KeyboardModel(str, Enum):
    VOYAGER = "voyager"
    MOONLANDER = "moonlander"
    ERGODOX_EZ = "ergodox-ez"
    PLANCK_EZ = "planck-ez"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class KeyGeometry:
    index: int
    x: float          # left edge, key units
    y: float          # top edge, key units
    w: float = 1.0
    h: float = 1.0
    rotation: float = 0.0   # degrees, clockwise, around the key centre


@dataclass(frozen=True)
class Device:
    id: str                       # stable id, e.g. sysfs path of the USB device
    model: KeyboardModel
    name: str                     # human readable, e.g. "Voyager"
    hidraw_path: str              # e.g. "/dev/hidraw10" (the Oryx raw-HID interface)
    firmware_version: str | None = None   # raw string reported by the firmware
    layout_id: str | None = None          # Oryx layout hash id, e.g. "abcde"
    revision_id: str | None = None        # Oryx revision hash id
    geometry: tuple[KeyGeometry, ...] = ()
    oryx_available: bool = True   # False when firmware lacks Oryx raw HID / is unreachable
    access_denied: bool = False   # True when found but /dev/hidrawN can't be opened (udev rules)


@dataclass(frozen=True)
class KeyBinding:
    index: int
    tap_label: str = ""           # short label for tap action ("A", "Esc", "⌘")
    hold_label: str = ""          # label for hold action ("Ctrl", "L2"), may be empty
    glyph: str | None = None      # optional symbolic icon name instead of tap_label
    color: str | None = None      # optional "#rrggbb" key colour from Oryx


@dataclass(frozen=True)
class Layer:
    index: int
    title: str                    # Oryx layer title, or "Layer N" fallback
    color: str | None = None      # "#rrggbb"
    keys: tuple[KeyBinding, ...] = ()


@dataclass(frozen=True)
class Layout:
    id: str                       # Oryx layout hash id ("" for unknown/local)
    title: str
    geometry: tuple[KeyGeometry, ...]
    layers: tuple[Layer, ...]
    revision_id: str | None = None


@dataclass(frozen=True)
class AppRule:
    """Smart Layers rule: when ``app_class`` is focused, switch to ``layer_index``.

    ``app_class`` is matched exactly (case-sensitive) against the window class
    reported by the focus source (Hyprland ``class``). If ``title_pattern`` is
    set it is a Python regex that must also ``re.search`` the window title.
    Rules are evaluated in list order; the first enabled match wins.
    """
    app_class: str
    display_name: str
    layer_index: int
    title_pattern: str | None = None
    enabled: bool = True
    icon_name: str | None = None


@dataclass(frozen=True)
class RunningApp:
    app_class: str
    title: str
    icon_name: str | None = None


class HeatmapPeriod(str, Enum):
    """Time window for heatmap queries. Days are local calendar days."""
    TODAY = "today"
    WEEK = "week"         # today and the 6 days before it
    ALL = "all"


# Sentinel for set_fallback_layer(): leave the layer untouched when no rule matches.
FALLBACK_KEEP: None = None


__all__ = [
    "KeyboardModel", "KeyGeometry", "Device", "KeyBinding", "Layer", "Layout",
    "AppRule", "RunningApp", "HeatmapPeriod", "FALLBACK_KEEP", "field",
]
