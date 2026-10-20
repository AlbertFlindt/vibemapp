"""MockKeyboardService — a simulated ZSA Voyager for UI development.

Selected with ``VIBEMAPP_MOCK=1``. Everything happens on the GLib main loop,
like the real backend. Behaviour can be tuned with environment variables:

``VIBEMAPP_MOCK_SPEED``       time multiplier (default 1.0; 2 = twice as fast)
``VIBEMAPP_MOCK_FOCUS=0``     no simulated focus changes
``VIBEMAPP_MOCK_KEYS=0``      no simulated key presses
``VIBEMAPP_MOCK_HOTPLUG=0``   no simulated unplug/replug cycle
``VIBEMAPP_MOCK_NO_DEVICE=1`` start with nothing plugged in
``VIBEMAPP_MOCK_DENIED=1``    device is listed with ``access_denied`` (udev hint)
``VIBEMAPP_MOCK_NO_ORYX=1``   firmware without Oryx raw HID
``VIBEMAPP_MOCK_NO_HYPRLAND=1`` focus source unavailable
``VIBEMAPP_MOCK_NO_RULES=1``  start without the demo rules
``VIBEMAPP_MOCK_NO_STEAM=1``  Steam not installed (no games)
``VIBEMAPP_MOCK_HEATMAP_SEED=1`` pre-fill 30 days of plausible heatmap counts

Tests construct it with ``autostart=False`` and drive it through the
``simulate_*`` methods.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import replace
import itertools
import os
import random
import time
from typing import Callable

from gi.repository import Gio, GLib

from .models import (AppRule, Device, ErgoReport, LayoutComparison, FocusedWindow, HeatmapApp, HeatmapPeriod, RuleMatch, SteamGame, KeyBinding, KeyboardModel, KeyGeometry,
                     Layer, Layout, RunningApp)
from .backend import ergo, geometries, layouts
from .service import KeyboardService
from .ui.i18n import _
from .ui.rules_model import OWN_APP_CLASS, is_own_app_class, target_layer

MOCK_DEVICE_ID = "mock:/sys/devices/usb3/3-2"
MOCK_LAYOUT_ID = "pXEbA"
MOCK_REVISION_ID = "7a4Jd"


def _env_flag(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value.lower() not in ("0", "false", "no", "off")


# --- simulated Voyager --------------------------------------------------------

# Column stagger of the left half, outer pinky column first (key units).
_STAGGER = (0.45, 0.45, 0.2, 0.0, 0.2, 0.3)
_HALF_GAP = 2.4
_ROWS = 4
_COLS = 6


def voyager_geometry() -> tuple[KeyGeometry, ...]:
    """52 keys: rows interleave left (6) and right (6) halves, thumbs last."""
    keys: list[KeyGeometry] = []
    right_x0 = _COLS + _HALF_GAP
    for row in range(_ROWS):
        for col in range(_COLS):
            keys.append(KeyGeometry(len(keys), float(col), row + _STAGGER[col]))
        for col in range(_COLS):
            keys.append(KeyGeometry(len(keys), right_x0 + col,
                                    row + _STAGGER[_COLS - 1 - col]))
    total_w = right_x0 + _COLS
    thumbs_left = ((4.85, 4.62, 14.0), (6.0, 4.95, 28.0))
    for x, y, rot in thumbs_left:
        keys.append(KeyGeometry(len(keys), x, y, rotation=rot))
    for x, y, rot in reversed(thumbs_left):
        keys.append(KeyGeometry(len(keys), total_w - x - 1.0, y, rotation=-rot))
    return tuple(keys)


def _mock_to_oryx_index(i: int) -> int:
    """Oryx key index of mock Voyager key ``i``.

    The mock is row-interleaved (row r: 6 left keys then 6 right keys, rows 0-3
    -> 0..47; then left thumbs 48, 49 and right thumbs 50, 51). Oryx is
    half-major: the whole left half (4 rows of 6, then 2 thumbs = 26 keys),
    then the right half. Thumbs keep their order (outer to inner on the left,
    inner to outer on the right in both)."""
    if i >= 48:
        return 24 + (i - 48) if i < 50 else i
    row, rest = divmod(i, 12)
    half, col = divmod(rest, 6)
    return half * 26 + row * 6 + col


MOCK_TO_ORYX: tuple[int, ...] = tuple(_mock_to_oryx_index(i) for i in range(52))
_ORYX_TO_MOCK: dict[int, int] = {o: m for m, o in enumerate(MOCK_TO_ORYX)}


# Oryx keycodes of the mock's tap labels (letters and digits are derived).
_MOCK_CODES: dict[str, str] = {
    "Esc": "KC_ESCAPE", "Tab": "KC_TAB", "⌫": "KC_BSPACE", "Shift": "KC_LSHIFT",
    "Space": "KC_SPACE", "Enter": "KC_ENTER", "-": "KC_MINUS", "\\": "KC_BSLASH",
    ";": "KC_SCOLON", "'": "KC_QUOTE", ",": "KC_COMMA", ".": "KC_DOT", "/": "KC_SLASH",
    "`": "KC_GRAVE", "Æ": "DK_AE", "Ø": "DK_OSTR", "Å": "DK_ARNG",
    "!": "KC_EXLM", "@": "KC_AT", "#": "KC_HASH", "$": "KC_DLR", "%": "KC_PERC",
    "^": "KC_CIRC", "&": "KC_AMPR", "*": "KC_ASTR", "(": "KC_LPRN", ")": "KC_RPRN",
    "{": "KC_LCBR", "}": "KC_RCBR", "[": "KC_LBRACKET", "]": "KC_RBRACKET",
    "|": "KC_PIPE", "~": "KC_TILD", "+": "KC_PLUS", "PgUp": "KC_PGUP", "PgDn": "KC_PGDN",
    "Home": "KC_HOME", "End": "KC_END", "↑": "KC_UP", "↓": "KC_DOWN", "←": "KC_LEFT",
    "→": "KC_RIGHT", "Vol−": "KC_AUDIO_VOL_DOWN", "Vol+": "KC_AUDIO_VOL_UP",
    "Mute": "KC_AUDIO_MUTE", "Prev": "KC_MEDIA_PREV_TRACK", "Play": "KC_MEDIA_PLAY_PAUSE",
    "Next": "KC_MEDIA_NEXT_TRACK", "Reset": "QK_BOOT", "Undo": "PC_UNDO", "Cut": "PC_CUT",
    "Copy": "PC_COPY", "Paste": "PC_PASTE", "Ctrl": "KC_LCTRL", "Alt": "KC_LALT",
    "Super": "KC_LGUI",
}


def _mock_code(label: str) -> str | None:
    if not label:
        return "KC_NO"
    if label in _MOCK_CODES:
        return _MOCK_CODES[label]
    if len(label) == 1 and label.isascii() and (label.isalpha() or label.isdigit()):
        return "KC_" + label.upper()
    if label[0] == "F" and label[1:].isdigit():
        return "KC_" + label
    return None


def _layer_keys(rows: list[list[object]], thumbs: list[object],
                color: str | None = None,
                blank_transparent: bool = False) -> tuple[KeyBinding, ...]:
    """``rows`` hold 12 entries each (left 6, right 6); an entry is a tap label,
    a ``(tap, hold)`` tuple or a ``dict`` of KeyBinding fields. ``None`` is a
    transparent key (Oryx ▽); with ``blank_transparent`` so is ``""``, otherwise
    ``""`` is a blocked key (KC_NO)."""
    out: list[KeyBinding] = []

    def add(entry: object) -> None:
        idx = len(out)
        if entry is None or (blank_transparent and entry == ""):
            out.append(KeyBinding(idx, transparent=True))
        elif isinstance(entry, dict):
            out.append(KeyBinding(idx, **{"tap_code": _mock_code(entry.get("tap_label", "")),
                                          **entry}))
        elif isinstance(entry, tuple):
            out.append(KeyBinding(idx, tap_label=entry[0], hold_label=entry[1],
                                  color=color, tap_code=_mock_code(entry[0])))
        else:
            out.append(KeyBinding(idx, tap_label=str(entry),
                                  color=color if entry else None,
                                  tap_code=_mock_code(str(entry))))

    for row in rows:
        for entry in row:
            add(entry)
    for entry in thumbs:
        add(entry)
    return tuple(out)


def voyager_layout() -> Layout:
    geometry = voyager_geometry()
    base = _layer_keys(
        [["Esc", "1", "2", "3", "4", "5", "6", "7", "8", "9", "0", "-"],
         ["Tab", "Q", "W", "E", "R", "T", "Y", "U", "I", "O", "P", "\\"],
         ["⌫", ("A", "Ctrl"), ("S", "Alt"), ("D", "Super"), ("F", "Shift"), "G",
          "H", ("J", "Shift"), ("K", "Super"), ("L", "Alt"), ("Æ", "Ctrl"), "Ø"],
         ["Shift", "Z", "X", "C", "V", "B", "N", "M", ",", ".", "/", "Shift"]],
        [("Space", "L1"), ("Esc", "L3"), ("Enter", "L2"), "⌫"])
    symbols = _layer_keys(
        [["`", "F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9", "F10", "F11"],
         ["", "!", "@", "{", "}", "|", "", "7", "8", "9", "*", "F12"],
         ["", "#", "$", "(", ")", "`", "", "4", "5", "6", "+", ""],
         ["", "%", "^", "[", "]", "~", "&", "1", "2", "3", "\\", ""]],
        ["", "", "", "0"], color="#c64600", blank_transparent=True)
    media_color = "#2190a4"
    media = _layer_keys(
        [["", "", "", "", "", "", "", "", "", "", "", "Reset"],
         ["", "", "", "", "", "", "", {"tap_label": "Vol−",
          "glyph": "audio-volume-low-symbolic", "color": media_color},
          {"tap_label": "Vol+", "glyph": "audio-volume-high-symbolic",
           "color": media_color},
          {"tap_label": "Mute", "glyph": "audio-volume-muted-symbolic",
           "color": media_color}, "", ""],
         ["", "", "", "", "", "", "",
          {"tap_label": "Prev", "glyph": "media-skip-backward-symbolic",
           "color": media_color},
          {"tap_label": "Play", "glyph": "media-playback-start-symbolic",
           "color": media_color},
          {"tap_label": "Next", "glyph": "media-skip-forward-symbolic",
           "color": media_color}, "", ""],
         ["", "", "", "", "", "", "", "☀−", "☀+", "", "", ""]],
        [None, None, None, None])     # blanks above are blocked (KC_NO)
    nav = _layer_keys(
        [["", "", "", "", "", "", "", "", "", "", "", ""],
         ["", "", "", "", "", "", "PgUp", "Home", "↑", "End", "", ""],
         ["", "Ctrl", "Alt", "Super", "Shift", "", "PgDn", "←", "↓", "→", "", ""],
         ["", "Undo", "Cut", "Copy", "Paste", "", "", "", "", "", "", ""]],
        ["", "", "", ""], color="#26a269", blank_transparent=True)
    layers = (
        Layer(0, "Base", None, base),
        Layer(1, "Symbols", "#c64600", symbols),
        Layer(2, "Media", media_color, media),
        Layer(3, "Navigation", "#26a269", nav),
    )
    return Layout(MOCK_LAYOUT_ID, "Albert's Voyager", geometry, layers,
                  revision_id=MOCK_REVISION_ID)


def demo_rules() -> list[AppRule]:
    return [
        AppRule("firefox", "Firefox", 2, title_pattern="YouTube|Spotify",
                icon_name="firefox"),
        AppRule("kitty", "kitty", 1, icon_name="kitty"),
        AppRule("blender", "Blender", 3, icon_name="blender"),
        AppRule("", "All Steam Games", 3, icon_name="input-gaming-symbolic",
                match=RuleMatch.STEAM_GAME),
    ]


MOCK_STEAM_GAMES: tuple[SteamGame, ...] = (
    SteamGame("1086940", "Baldur's Gate 3"),
    SteamGame("570", "Dota 2"),
    SteamGame("1245620", "ELDEN RING"),
    SteamGame("1145360", "Hades"),
    SteamGame("413150", "Stardew Valley"),
)
# Native Linux games are recognised by SteamAppId in their environment, not
# by their class; the mock just knows which classes those are.
_NATIVE_STEAM_CLASSES = {"dota2": "570", "StardewValley": "413150"}


def steam_app_id_for(app_class: str) -> str | None:
    if app_class.startswith("steam_app_"):
        return app_class[len("steam_app_"):] or None
    return _NATIVE_STEAM_CLASSES.get(app_class)


_SEED_GAME = "steam:1245620"
_GAME_KEYS = {"W": 9.0, "A": 7.0, "S": 7.0, "D": 7.0, "E": 4.0, "Q": 3.0, "R": 3.0, "F": 3.0,
              "Space": 6.0, "Shift": 5.0, "Tab": 3.0, "Ctrl": 3.0, "1": 3.0, "2": 3.0,
              "3": 3.0, "4": 3.0, "5": 3.0, "Esc": 2.0}


# English letter frequencies (per mille, the usual corpus tables); Æ and Ø are rare.
# Scaled so E weighs 9, as before.
_ENGLISH_LETTERS: dict[str, float] = {
    "E": 127, "T": 91, "A": 82, "O": 75, "I": 70, "N": 67, "S": 63, "H": 61, "R": 60,
    "D": 43, "L": 40, "C": 28, "U": 28, "M": 24, "W": 24, "F": 22, "G": 20, "Y": 20,
    "P": 19, "B": 15, "V": 10, "K": 8, "J": 2, "X": 2, "Q": 1, "Z": 1, "Æ": 2, "Ø": 2}
_LETTER_SCALE = 9 / 127
# Common English bigrams (relative counts, from the usual corpus tables).
_ENGLISH_BIGRAMS: dict[str, int] = {
    "th": 356, "he": 307, "in": 243, "er": 205, "an": 199, "re": 185, "on": 176, "at": 149,
    "en": 145, "nd": 135, "ti": 134, "es": 134, "or": 128, "te": 120, "of": 117, "ed": 117,
    "is": 113, "it": 112, "al": 109, "ar": 107, "st": 105, "to": 104, "nt": 104, "ng": 95,
    "se": 93, "ha": 93, "as": 87, "ou": 87, "io": 83, "le": 83, "ve": 83, "co": 79, "me": 79,
    "de": 76, "hi": 76, "ri": 73, "ro": 73, "ic": 70, "ne": 69, "ea": 69, "ra": 69, "ce": 65,
    "li": 62, "ch": 60, "ll": 58, "be": 58, "ma": 57, "si": 55, "om": 55, "ur": 54,
}


def _is_letter(label: str) -> bool:
    return len(label) == 1 and label.isalpha()


# (app key, weight profile(tap label, layer) -> relative share of presses)
_SEED_APPS: tuple[tuple[str, Callable[[str, int], float]], ...] = (
    ("firefox", lambda label, layer: (2.2 if _is_letter(label) else 0.8) / (1 + layer)),
    ("code", lambda label, layer: (1.0 if _is_letter(label) else 1.2) * (1.0 + 2.5 * (layer > 0))),
    ("kitty", lambda label, layer: (0.7 if _is_letter(label) else 1.0) * (1.0 + 1.0 * (layer > 0))),
    (_SEED_GAME, lambda label, layer: _GAME_KEYS.get(label, 0.05) / (1 + layer) * 2.5),
)

_FOCUS_SCRIPT: tuple[tuple[str, str], ...] = (
    ("kitty", "~/Projects/vibemapp — fish"),
    ("firefox", "GNOME Human Interface Guidelines — Mozilla Firefox"),
    ("blender", "Blender [~/Models/voyager.blend]"),
    ("org.gnome.Nautilus", "Downloads"),
    ("firefox", "Lo-fi beats — YouTube — Mozilla Firefox"),
    ("steam_app_1245620", "ELDEN RING™"),
    ("kitty", "nvim service.py"),
    ("dota2", "Dota 2"),
    ("steam", "Steam"),
)


Key = tuple[int, int]


class MockKeyboardService(KeyboardService):
    __gtype_name__ = "KmlMockKeyboardService"
    PAIR_WINDOW_S = 1.0

    def __init__(self, speed: float | None = None, *, autostart: bool = True,
                 monotonic: Callable[[], float] = time.monotonic) -> None:
        super().__init__()
        self._monotonic = monotonic
        self._prev_press: tuple[int, int, float] | None = None
        if speed is None:
            try:
                speed = float(os.environ.get("VIBEMAPP_MOCK_SPEED", "1") or 1)
            except ValueError:
                speed = 1.0
        self._speed = max(speed, 0.01)
        self._timers: set[int] = set()
        self._plugged = False
        self._denied = _env_flag("VIBEMAPP_MOCK_DENIED", False)
        self._oryx = not _env_flag("VIBEMAPP_MOCK_NO_ORYX", False)
        self._focus_available = not _env_flag("VIBEMAPP_MOCK_NO_HYPRLAND", False)
        self._device: Device | None = None
        self._layout: Layout | None = None
        self._active_layer = 0
        self._pressed: set[int] = set()
        self._monitor: str | None = None
        self._focused: tuple[str, str] | None = None
        self._focus_step = 0
        self._last_sent: int | None = None
        # layout id -> {(iso day, layer, key index): presses}
        self._counts: dict[str, dict[tuple[str, int, int], int]] = {}
        # layout id -> {(iso day, layer, key index, app key): presses}; sums to _counts
        self._app_counts: dict[str, dict[tuple[str, int, int, str], int]] = {}
        self._app_names: dict[str, str] = {}
        # layout id -> {(iso day, first, second, app key or ""): pairs}; total = sum over apps
        self._pair_counts: dict[str, dict[tuple[str, Key, Key, str], int]] = {}
        self._counts_pending = 0
        self._seed_heatmap = _env_flag("VIBEMAPP_MOCK_HEATMAP_SEED", False)
        seed = [] if _env_flag("VIBEMAPP_MOCK_NO_RULES", False) else demo_rules()
        self._rule_sets: dict[str, tuple[list[AppRule], int | None]] = {
            MOCK_LAYOUT_ID: (seed, 0),
        }
        self._rng = random.Random(4)
        self._connecting = False
        self.connect("notify::smart-layers-enabled", lambda *_a: self._evaluate())
        self.connect("notify::pair-recording", self._clear_prev_press)
        self.connect("notify::heatmap-recording", self._clear_prev_press)
        self.connect("notify::auto-connect", lambda *_a: self._maybe_auto_connect())

        if autostart:
            if not _env_flag("VIBEMAPP_MOCK_NO_DEVICE", False):
                self._after(250, self._plug)
            if self._focus_available:
                self._focused = _FOCUS_SCRIPT[0]
                if _env_flag("VIBEMAPP_MOCK_FOCUS", True):
                    self._every(4500, self._next_focus)
            if _env_flag("VIBEMAPP_MOCK_KEYS", True):
                self._after(700, self._random_key)
            if _env_flag("VIBEMAPP_MOCK_HOTPLUG", True):
                self._every(90_000, self._hotplug_cycle)

    # --- timers ----------------------------------------------------------

    def _ms(self, ms: float) -> int:
        return max(1, int(ms / self._speed))

    def _after(self, ms: float, func: Callable[..., object], *args: object) -> int:
        source_id = 0

        def fire() -> bool:
            self._timers.discard(source_id)
            func(*args)
            return GLib.SOURCE_REMOVE

        source_id = GLib.timeout_add(self._ms(ms), fire)
        self._timers.add(source_id)
        return source_id

    def _every(self, ms: float, func: Callable[[], object]) -> int:
        def fire() -> bool:
            func()
            return GLib.SOURCE_CONTINUE

        source_id = GLib.timeout_add(self._ms(ms), fire)
        self._timers.add(source_id)
        return source_id

    # --- simulation ------------------------------------------------------

    def _make_device(self) -> Device:
        return Device(
            id=MOCK_DEVICE_ID,
            model=KeyboardModel.VOYAGER,
            name="Voyager",
            hidraw_path="/dev/hidraw10",
            firmware_version=f"{MOCK_LAYOUT_ID}/{MOCK_REVISION_ID}" if self._oryx else None,
            layout_id=MOCK_LAYOUT_ID if self._oryx else None,
            revision_id=MOCK_REVISION_ID if self._oryx else None,
            geometry=voyager_geometry(),
            oryx_available=self._oryx,
            access_denied=self._denied,
        )

    def _plug(self) -> None:
        if not self._plugged:
            self._plugged = True
            self.emit("devices-changed")
            self._maybe_auto_connect()

    def _maybe_auto_connect(self) -> None:
        if (self.props.auto_connect and self._plugged and self._device is None
                and not self._connecting and not self._denied):
            self.connect_device(MOCK_DEVICE_ID)

    def _unplug(self) -> None:
        if not self._plugged:
            return
        self._plugged = False
        if self._device is not None:
            self._drop_connection()
        self.emit("devices-changed")

    def _hotplug_cycle(self) -> None:
        if self._plugged:
            self._unplug()
            self._after(4000, self._plug)

    def _next_focus(self) -> None:
        self._focus_step = (self._focus_step + 1) % len(_FOCUS_SCRIPT)
        self.simulate_focus(*_FOCUS_SCRIPT[self._focus_step])

    def _random_key(self) -> None:
        if self._device is not None:
            index = self._rng.randrange(len(self._device.geometry))
            self.simulate_key(index, True)
            self._after(self._rng.uniform(70, 180), self.simulate_key, index, False)
        self._after(self._rng.uniform(180, 900), self._random_key)

    def _clear_prev_press(self, *_args: object) -> None:
        self._prev_press = None

    def _drop_connection(self) -> None:
        self._prev_press = None
        self._device = None
        self._layout = None
        self._active_layer = 0
        self._pressed.clear()
        self._last_sent = None
        self.emit("disconnected")

    # --- public simulation hooks (tests) ---------------------------------

    def simulate_plug(self) -> None:
        self._plug()

    def simulate_unplug(self) -> None:
        self._unplug()

    def simulate_focus(self, app_class: str, title: str) -> None:
        if not self._focus_available:
            return
        self._focused = (app_class, title)
        self._prev_press = None
        self.emit("focus-changed", app_class, title)
        self._evaluate()

    def simulate_focus_source(self, available: bool) -> None:
        """Hyprland appeared or went away."""
        if available != self._focus_available:
            self._focus_available = available
            if not available:
                self._focused = None
            self.emit("focus-source-changed", available)

    def simulate_key(self, index: int, pressed: bool) -> None:
        if self._device is not None:
            if pressed:
                self._pressed.add(index)
            else:
                self._pressed.discard(index)
            self.emit("key-event", index, pressed)
            if pressed:
                self._count_press(index)

    # --- heatmap ---------------------------------------------------------

    @staticmethod
    def _today() -> dt.date:
        return dt.date.today()

    def _count_press(self, index: int) -> None:
        if not self.props.heatmap_recording or self._layout is None:
            return
        counts = self._counts.setdefault(self._layout.id, {})
        key = (self._today().isoformat(), self._active_layer, index)
        counts[key] = counts.get(key, 0) + 1
        app, name = self._heatmap_app()
        if app:
            akey = (*key, app)
            acounts = self._app_counts.setdefault(self._layout.id, {})
            acounts[akey] = acounts.get(akey, 0) + 1
            if name != app:
                self._app_names[app] = name
        self._count_pair(index, app)
        self._schedule_counts_changed()

    def _count_pair(self, index: int, app: str | None) -> None:
        """Pair this press with the previous one if within PAIR_WINDOW_S (like the real service)."""
        if not self.props.pair_recording or self._layout is None:
            self._prev_press = None
            return
        now = self._monotonic()
        prev, self._prev_press = self._prev_press, (self._active_layer, index, now)
        if prev is not None and 0 <= now - prev[2] < self.PAIR_WINDOW_S:
            pairs = self._pair_counts.setdefault(self._layout.id, {})
            key = (self._today().isoformat(), (prev[0], prev[1]),
                   (self._active_layer, index), app or "")
            pairs[key] = pairs.get(key, 0) + 1

    def _heatmap_app(self) -> tuple[str | None, str]:
        window = self.get_focused_window()
        if window is None:
            return None, ""
        if window.steam_app_id:
            key = f"steam:{window.steam_app_id}"
            return key, window.steam_game_name or key
        return window.app_class, window.app_class

    def _schedule_counts_changed(self) -> None:
        if self._counts_pending:
            return

        def fire() -> None:
            self._counts_pending = 0
            self.emit("key-counts-changed")

        # Throttled, like the real backend.
        self._counts_pending = self._after(1000, fire)

    def _seed_counts(self, layout: Layout) -> None:
        """Plausible typing history: home row and thumbs hot, edges cold."""
        rng = random.Random(7)
        split_rng = random.Random(11)    # separate, so the totals are unchanged
        counts = self._counts.setdefault(layout.id, {})
        base = layout.layers[0].keys
        hot = {c: f * _LETTER_SCALE for c, f in _ENGLISH_LETTERS.items()}
        hot.update({"Space": 14, "⌫": 6, "Enter": 4})
        today = self._today()
        for day in range(30):
            date = (today - dt.timedelta(days=day)).isoformat()
            volume = rng.uniform(0.4, 1.2) * (1.6 if day == 0 else 1.0)
            for binding in base:
                weight = hot.get(binding.tap_label, 1 if binding.tap_label else 0)
                n = int(weight * volume * rng.uniform(20, 40))
                if n:
                    counts[(date, 0, binding.index)] = n
                    self._split_seed(layout, date, 0, binding.tap_label, binding.index, n, split_rng)
            for layer in layout.layers[1:]:
                for binding in layer.keys:
                    if binding.tap_label or binding.glyph:
                        n = int(volume * rng.uniform(0, 25) * (4 - layer.index) / 3)
                        if n:
                            counts[(date, layer.index, binding.index)] = n
                            self._split_seed(layout, date, layer.index, binding.tap_label,
                                             binding.index, n, split_rng)
        self._seed_pairs(layout)

    def _seed_pairs(self, layout: Layout) -> None:
        """Bigrams per app and day drawn from that app's key counts: mostly same
        layer, some same-finger pairs (~2%), a few layer switches."""
        rng = random.Random(13)
        fingers = geometries.FINGERS[KeyboardModel.VOYAGER]
        per: dict[tuple[str, str], list[tuple[Key, int]]] = {}
        for (day, layer, key, app), n in sorted(self._app_counts.get(layout.id, {}).items()):
            per.setdefault((day, app), []).append(((layer, key), n))
        pairs = self._pair_counts.setdefault(layout.id, {})
        # Letter pairs follow common English bigrams (so same-finger pairs and the layout
        # comparison behave like real typing); the rest is drawn from the key counts.
        base = {b.tap_label: b.index for b in layout.layers[0].keys if b.tap_label}
        follow: dict[Key, tuple[list[Key], list[int]]] = {}
        for bg, w in _ENGLISH_BIGRAMS.items():
            a, b = bg[0].upper(), bg[1].upper()
            if a in base and b in base:
                ks, ws = follow.setdefault((0, base[a]), ([], []))
                ks.append((0, base[b]))
                ws.append(w)

        def finger(k: Key) -> object:
            return fingers[MOCK_TO_ORYX[k[1]]] if k[1] < len(MOCK_TO_ORYX) else None

        for (day, app), items in sorted(per.items()):
            keys = [k for k, _n in items]
            cum = list(itertools.accumulate(n for _k, n in items))
            by_layer: dict[int, tuple[list[Key], list[int]]] = {}
            for layer in {k[0] for k in keys}:
                sub = [(k, n) for k, n in items if k[0] == layer]
                by_layer[layer] = ([k for k, _n in sub],
                                   list(itertools.accumulate(n for _k, n in sub)))
            for first in rng.choices(keys, cum_weights=cum, k=cum[-1] // 2):
                lkeys, lcum = by_layer[first[0]]
                for _try in range(3):
                    if first in follow and rng.random() < 0.8:
                        ks, ws = follow[first]
                        second = rng.choices(ks, weights=ws)[0]      # an English bigram
                        break
                    if rng.random() < 0.05:
                        second = rng.choices(keys, cum_weights=cum)[0]      # layer switch
                    else:
                        second = rng.choices(lkeys, cum_weights=lcum)[0]
                    if (second == first or finger(second) != finger(first)
                            or rng.random() < 0.3):
                        break                       # redraw most same-finger pairs
                pk = (day, first, second, app)
                pairs[pk] = pairs.get(pk, 0) + 1

    def _split_seed(self, layout: Layout, date: str, layer: int, label: str,
                    index: int, n: int, rng: random.Random) -> None:
        """Split a seeded (day, layer, key) total over the demo apps, summing to ``n``."""
        day_no = (self._today() - dt.date.fromisoformat(date)).days
        weights = []
        for app, profile in _SEED_APPS:
            w = profile(label, layer) * rng.uniform(0.7, 1.3)
            if app == _SEED_GAME and day_no % 3 != 0:
                w = 0.0                        # the game is only played every third day
            weights.append(w)
        total = sum(weights)
        if total <= 0:
            weights, total = [1.0] + [0.0] * (len(weights) - 1), 1.0
        shares = [int(n * w / total) for w in weights]
        shares[weights.index(max(weights))] += n - sum(shares)
        acounts = self._app_counts.setdefault(layout.id, {})
        for (app, _p), share in zip(_SEED_APPS, shares):
            if share:
                acounts[(date, layer, index, app)] = share
        self._app_names[_SEED_GAME] = next(
            g.name for g in MOCK_STEAM_GAMES if g.app_id == _SEED_GAME.split(":")[1])

    def _period_first(self, period: HeatmapPeriod) -> str:
        today = self._today()
        if period == HeatmapPeriod.TODAY:
            first = today
        elif period == HeatmapPeriod.WEEK:
            first = today - dt.timedelta(days=6)
        else:
            first = dt.date.min
        return first.isoformat()

    def get_key_counts(self, period: HeatmapPeriod,
                       layer: int | None = None,
                       app: str | None = None) -> dict[int, int]:
        if self._layout is None:
            return {}
        first_iso = self._period_first(period)
        out: dict[int, int] = {}
        if app is None:
            for (day, layer_index, key), n in self._counts.get(self._layout.id, {}).items():
                if day >= first_iso and (layer is None or layer == layer_index):
                    out[key] = out.get(key, 0) + n
            return out
        for (day, layer_index, key, a), n in self._app_counts.get(self._layout.id, {}).items():
            if a == app and day >= first_iso and (layer is None or layer == layer_index):
                out[key] = out.get(key, 0) + n
        return out

    def list_heatmap_apps(self, period: HeatmapPeriod) -> list[HeatmapApp]:
        if self._layout is None:
            return []
        first_iso = self._period_first(period)
        totals: dict[str, int] = {}
        for (day, _l, _k, app), n in self._app_counts.get(self._layout.id, {}).items():
            if day >= first_iso and n > 0:
                totals[app] = totals.get(app, 0) + n
        apps = [HeatmapApp(k, self._app_names.get(k, k), n) for k, n in totals.items()]
        apps.sort(key=lambda a: (-a.presses, a.name.casefold(), a.key))
        return apps

    def _period_pairs(self, period: HeatmapPeriod, app: str | None) -> dict[tuple[Key, Key], int]:
        out: dict[tuple[Key, Key], int] = {}
        if self._layout is None:
            return out
        first_iso = self._period_first(period)
        for (day, a, b, papp), n in self._pair_counts.get(self._layout.id, {}).items():
            if day >= first_iso and (app is None or papp == app):
                out[(a, b)] = out.get((a, b), 0) + n
        return out

    def _oryx_inputs(self, period: HeatmapPeriod, app: str | None):
        """(counts, pairs, geometry, fingers, home keys) in Oryx indices, or None."""
        if self._layout is None or self._device is None:
            return None
        model = self._device.model
        fingers = geometries.FINGERS.get(model)
        if fingers is None:
            return None
        first_iso = self._period_first(period)
        counts: dict[Key, int] = {}
        if app is None:
            for (day, layer, key), n in self._counts.get(self._layout.id, {}).items():
                if day >= first_iso:
                    counts[(layer, key)] = counts.get((layer, key), 0) + n
        else:
            for (day, layer, key, a), n in self._app_counts.get(self._layout.id, {}).items():
                if a == app and day >= first_iso:
                    counts[(layer, key)] = counts.get((layer, key), 0) + n
        # The mock's key order is not Oryx's: score on the real board's indices.
        oryx_counts = {(layer, MOCK_TO_ORYX[key]): n for (layer, key), n in counts.items()
                       if key < len(MOCK_TO_ORYX)}
        oryx_pairs: dict[tuple[Key, Key], int] = {}
        for (a, b), n in self._period_pairs(period, app).items():
            if a[1] < len(MOCK_TO_ORYX) and b[1] < len(MOCK_TO_ORYX):
                pair = ((a[0], MOCK_TO_ORYX[a[1]]), (b[0], MOCK_TO_ORYX[b[1]]))
                oryx_pairs[pair] = oryx_pairs.get(pair, 0) + n
        return (oryx_counts, oryx_pairs, geometries.geometry_for(model), fingers,
                geometries.HOME_KEYS[model])

    @staticmethod
    def _to_mock_key(k: Key) -> Key:
        return (k[0], _ORYX_TO_MOCK[k[1]])

    def get_ergo_report(self, period: HeatmapPeriod,
                        app: str | None = None) -> ErgoReport | None:
        inputs = self._oryx_inputs(period, app)
        if inputs is None:
            return None
        rep = ergo.report(*inputs)
        # Report keys in the mock's own indices, as the contract requires.
        worst = tuple(replace(w, first=self._to_mock_key(w.first),
                              second=self._to_mock_key(w.second))
                      for w in rep.worst_same_finger)
        return replace(rep, worst_same_finger=worst)

    def _oryx_bindings(self, layer: Layer) -> list[KeyBinding]:
        """The layer's bindings re-indexed to Oryx key order, like the counts."""
        return [replace(b, index=MOCK_TO_ORYX[b.index]) for b in layer.keys
                if b.index < len(MOCK_TO_ORYX)]

    def get_layout_comparisons(self, period: HeatmapPeriod,
                               app: str | None = None) -> list[LayoutComparison]:
        inputs = self._oryx_inputs(period, app)
        if inputs is None or not self._layout.layers:
            return []
        return layouts.compare(self._oryx_bindings(self._layout.layers[0]), *inputs)

    def reset_key_counts(self) -> None:
        if self._layout is not None:
            self._pair_counts.pop(self._layout.id, None)
            self._counts.pop(self._layout.id, None)
            self._app_counts.pop(self._layout.id, None)
            self.emit("key-counts-changed")

    def simulate_monitor(self, connector: str) -> None:
        """The compositor focused another monitor."""
        if self._focus_available and connector != self._monitor:
            self._monitor = connector
            self.emit("focused-monitor-changed", connector)

    def simulate_layer(self, index: int) -> None:
        """The user pressed a layer key on the keyboard."""
        if self._device is not None and index != self._active_layer:
            self._active_layer = index
            self.emit("layer-changed", index)

    # --- Smart Layers ----------------------------------------------------

    def _rule_set(self) -> tuple[list[AppRule], int | None]:
        if self._device is None or not self._device.layout_id:
            return [], None
        return self._rule_sets.get(self._device.layout_id, ([], 0))

    def _evaluate(self) -> None:
        if (not self.props.smart_layers_enabled or self._device is None
                or self._layout is None or self._focused is None):
            return
        app_class, title = self._focused
        if is_own_app_class(app_class):
            return
        rules, fallback = self._rule_set()
        target, rule = target_layer(rules, fallback, app_class, title,
                                    steam_app_id_for(app_class))
        if target is None or not 0 <= target < len(self._layout.layers):
            return
        if target == self._active_layer and target == self._last_sent:
            return
        self._last_sent = target
        if target != self._active_layer:
            self._active_layer = target
            self.emit("layer-changed", target)
        self.emit("rule-applied", rule.app_class if rule else "", target)

    # --- KeyboardService: devices ----------------------------------------

    def list_devices(self) -> list[Device]:
        if not self._plugged:
            return []
        return [self._device or self._make_device()]

    def get_connected_device(self) -> Device | None:
        return self._device

    def connect_device(self, device_id: str) -> None:
        if self._connecting or (self._device is not None and self._device.id == device_id):
            return
        self._connecting = True

        def finish() -> None:
            self._connecting = False
            if not self._plugged or device_id != MOCK_DEVICE_ID:
                self.emit("error", _("The keyboard is no longer connected"))
                return
            if self._denied:
                self.emit("error", _("Permission denied opening /dev/hidraw10. "
                                     "Install the udev rules and replug the keyboard."))
                return
            if self._device is not None:
                return
            self._device = self._make_device()
            self._active_layer = 0
            self._last_sent = None
            self.emit("connected", self._device)
            self.emit("rules-changed")
            self.emit("layer-changed", 0)

        self._after(350, finish)

    def disconnect_device(self) -> None:
        if self._device is not None:
            self._drop_connection()

    # --- KeyboardService: layout -----------------------------------------

    def load_layout_async(self, device: Device,
                          cancellable: Gio.Cancellable | None,
                          callback: Callable[[Layout | None, str | None], None]) -> None:
        def finish() -> None:
            if cancellable is not None and cancellable.is_cancelled():
                callback(None, _("Cancelled"))
                return
            if self._device is None or self._device.id != device.id:
                callback(None, _("The keyboard is not connected"))
                return
            if not self._device.oryx_available or not self._device.layout_id:
                callback(None, _("This firmware does not report an Oryx layout"))
                return
            self._layout = voyager_layout()
            if self._seed_heatmap and self._layout.id not in self._counts:
                self._seed_counts(self._layout)
            self.emit("layout-loaded", self._layout)
            callback(self._layout, None)
            self._evaluate()

        self._after(600, finish)

    def get_layout(self) -> Layout | None:
        return self._layout

    def get_active_layer(self) -> int:
        return self._active_layer

    def get_pressed_keys(self) -> set[int]:
        return set(self._pressed)

    def request_layer(self, index: int) -> None:
        if self._device is None:
            self.emit("error", _("No keyboard is connected"))
            return
        if not self._device.oryx_available:
            self.emit("error", _("This keyboard's firmware does not support Oryx"))
            return
        n_layers = len(self._layout.layers) if self._layout else 0
        if not 0 <= index < max(n_layers, 1):
            self.emit("error", _("Layer {index} does not exist").format(index=index))
            return

        def finish() -> None:
            if self._device is not None and index != self._active_layer:
                self._active_layer = index
                self.emit("layer-changed", index)

        self._last_sent = index
        self._after(25, finish)

    # --- KeyboardService: smart layers -----------------------------------

    def get_rules(self) -> list[AppRule]:
        return list(self._rule_set()[0])

    def set_rules(self, rules: list[AppRule]) -> None:
        if self._device is None or not self._device.layout_id:
            self.emit("error", _("Connect a keyboard to edit its rules"))
            return
        _old, fallback = self._rule_set()
        self._rule_sets[self._device.layout_id] = (list(rules), fallback)
        self.emit("rules-changed")
        self._evaluate()

    def get_fallback_layer(self) -> int | None:
        return self._rule_set()[1]

    def set_fallback_layer(self, index: int | None) -> None:
        if self._device is None or not self._device.layout_id:
            self.emit("error", _("Connect a keyboard to edit its rules"))
            return
        rules, _old = self._rule_set()
        self._rule_sets[self._device.layout_id] = (rules, index)
        self.emit("rules-changed")
        self._evaluate()

    def list_running_apps(self) -> list[RunningApp]:
        if not self._focus_available:
            return []
        windows = dict.fromkeys(_FOCUS_SCRIPT + (
            ("org.gnome.TextEditor", "Untitled Document 1"),
            (OWN_APP_CLASS, "Vibemapp")))
        return [RunningApp(app_class, title) for app_class, title in windows]

    def focus_source_available(self) -> bool:
        return self._focus_available

    def get_focused_window(self) -> FocusedWindow | None:
        if self._focused is None:
            return None
        app_class, title = self._focused
        app_id = steam_app_id_for(app_class)
        name = next((g.name for g in MOCK_STEAM_GAMES if g.app_id == app_id), None)
        return FocusedWindow(app_class, title, pid=4242, steam_app_id=app_id,
                             steam_game_name=name)

    def list_steam_games(self) -> list[SteamGame]:
        if _env_flag("VIBEMAPP_MOCK_NO_STEAM", False):
            return []
        return sorted(MOCK_STEAM_GAMES, key=lambda g: g.name.casefold())

    def get_focused_app(self) -> tuple[str, str] | None:
        return self._focused

    def get_focused_monitor(self) -> str | None:
        return self._monitor if self._focus_available else None

    def shutdown(self) -> None:
        for source_id in list(self._timers):
            GLib.source_remove(source_id)
        self._timers.clear()
        self._device = None
        self._layout = None
