"""Shared data models — part of the backend/frontend contract.

Do not change field names or semantics lightly: both the backend and the UI
depend on these.

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
    transparent: bool = False     # falls through to the layer below (Oryx ▽)
    # Oryx/QMK keycode of the tap action ("KC_A", "DK_AE", "KC_LSHIFT", "MO"),
    # None when unknown or transparent. Additive (1.2): the layout comparison uses
    # it to find the letters.
    tap_code: str | None = None
    # What holding the key sends when that is a key, not a layer or a lone
    # modifier: "Ctrl+C" for hold C = Ctrl+C. Empty otherwise. Additive (1.5):
    # the hold-action pill shows it.
    hold_combo: str = ""
    # This key's own Oryx tapping term in ms, None for the layout's global one.
    # Additive (1.5).
    tapping_term_ms: int | None = None


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
    # The layout's global tapping term in ms when Oryx says it was changed, None
    # for the default. Additive (1.5).
    tapping_term_ms: int | None = None


class LayoutSync(str, Enum):
    """How the shown layout relates to the firmware on the keyboard. Additive (1.7)."""
    # No firmware revision to compare with, or not known yet.
    UNKNOWN = "unknown"
    # The flashed revision, and Oryx has nothing newer (or couldn't be asked).
    FLASHED = "flashed"
    # The flashed revision is shown, but Oryx has a newer one that isn't flashed.
    ORYX_NEWER = "oryx-newer"
    # A different revision than the flashed one is shown (Oryx sent another one,
    # or offline with only another revision cached), so keys may be wrong.
    NOT_FLASHED = "not-flashed"


class RuleMatch(str, Enum):
    """How an ``AppRule`` decides whether the focused window matches."""
    # ``app_class`` is compared exactly (case-sensitive) with the window class.
    APP = "app"
    # ``app_class`` is a Python regex that must ``re.fullmatch`` the window
    # class, e.g. ``steam_app_.*`` (same idea as Hyprland window-rule regexes).
    CLASS_PATTERN = "class-pattern"
    # Steam games. ``app_class`` == "" matches *any* Steam game; otherwise it is
    # a Steam app id ("1245620") and matches only that game. See FocusedWindow.
    STEAM_GAME = "steam-game"


@dataclass(frozen=True)
class AppRule:
    """Smart Layers rule: when a matching window is focused, switch to ``layer_index``.

    ``match`` decides how ``app_class`` is interpreted (see ``RuleMatch``;
    defaults to an exact window-class match). If ``title_pattern`` is set it is
    a Python regex that must also ``re.search`` the window title.
    Rules are evaluated in list order; the first enabled match wins, so
    specific rules should come before catch-all ones.
    """
    app_class: str
    display_name: str
    layer_index: int
    title_pattern: str | None = None
    enabled: bool = True
    icon_name: str | None = None
    match: RuleMatch = RuleMatch.APP


@dataclass(frozen=True)
class FocusedWindow:
    """Everything known about the focused window."""
    app_class: str
    title: str
    pid: int | None = None
    # Set when the window belongs to a Steam game: detected from a
    # ``steam_app_<id>`` class or from SteamAppId/SteamGameId in the process
    # environment (native Linux games). Never set for the Steam client itself.
    steam_app_id: str | None = None
    steam_game_name: str | None = None


@dataclass(frozen=True)
class SteamGame:
    app_id: str
    name: str


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


@dataclass(frozen=True)
class HeatmapApp:
    """An app with recorded key presses (additive, 1.2: heatmap per app).

    ``key`` is what ``get_key_counts(app=...)`` takes: the window class, or
    ``steam:<app id>`` for a Steam game. ``name`` is for display (the Steam
    game's name, else the window class). ``presses`` is the total in the
    queried period."""
    key: str
    name: str
    presses: int


class Finger(str, Enum):
    """Finger that presses a key (additive, 1.2: ergo score). Left to right."""
    L_PINKY = "l-pinky"
    L_RING = "l-ring"
    L_MIDDLE = "l-middle"
    L_INDEX = "l-index"
    L_THUMB = "l-thumb"
    R_THUMB = "r-thumb"
    R_INDEX = "r-index"
    R_MIDDLE = "r-middle"
    R_RING = "r-ring"
    R_PINKY = "r-pinky"

    @property
    def is_left(self) -> bool:
        return self.value.startswith("l-")


@dataclass(frozen=True)
class KeyPairCount:
    """How often key B was pressed within about a second after key A.
    Keys are (layer, Oryx key index). Only counts per day are stored.
    ``finger`` is set for same-finger pairs (``ErgoReport.worst_same_finger``):
    the finger that presses both keys. Additive (1.2)."""
    first: tuple[int, int]
    second: tuple[int, int]
    count: int
    finger: Finger | None = None


@dataclass(frozen=True)
class ErgoReport:
    """Ergonomic score of the recorded typing, as its parts (additive, 1.2).

    Shares are 0..1. ``score`` is 0..100 (higher is better); ``penalties``
    holds each part's weighted deduction, keyed like ``backend.ergo.WEIGHTS``.
    Rates from pairs are 0 when ``pairs`` is 0. Keys in ``worst_same_finger``
    use the service's own key indices (the same as ``Layout`` bindings and
    ``key-event``), so the UI can label them from the layout."""
    presses: int                          # presses on keys with a known finger
    pairs: int                            # recorded key pairs
    finger_load: dict[Finger, float]      # share of presses per finger
    left_share: float                     # share of presses made by the left hand
    same_finger_rate: float               # same finger, different key / pairs
    worst_same_finger: tuple[KeyPairCount, ...]   # most frequent, at most 5
    reach_cost: float                     # mean strength-weighted distance from home, key units
    layer_switch_rate: float              # pairs whose layers differ / pairs
    score: float
    penalties: dict[str, float] = field(default_factory=dict)


# Layout comparisons need at least this many presses in the queried period.
COMPARISON_MIN_PRESSES = 2000


@dataclass(frozen=True)
class LayoutComparison:
    """Your recorded typing scored as if the letters sat on a well-known layout
    (additive, 1.2). Only the base layer's letter block moves (the 26 Latin
    letters and the four punctuation keys beside them); every other key and
    letter (Æ, Ø, Å, …) stays where it is. ``name`` is a proper noun ("Colemak-DH"),
    not translated. ``current`` is True for the layout the base layer already
    uses. ``score_delta`` = ``report.score`` - the current score (> 0 is better)."""
    name: str
    report: ErgoReport
    score_delta: float
    current: bool = False


# Sentinel for set_fallback_layer(): leave the layer untouched when no rule matches.
FALLBACK_KEEP: None = None


__all__ = [
    "KeyboardModel", "KeyGeometry", "Device", "KeyBinding", "Layer", "Layout",
    "AppRule", "RuleMatch", "FocusedWindow", "SteamGame", "RunningApp", "HeatmapPeriod", "HeatmapApp", "Finger", "KeyPairCount",
    "ErgoReport", "COMPARISON_MIN_PRESSES", "LayoutComparison",
    "FALLBACK_KEEP", "field",
]
