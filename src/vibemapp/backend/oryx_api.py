"""Oryx layout fetch (GraphQL) + keycode labels + on-disk cache.

Query shape: the ``layout(hashId, geometry, revisionId)`` root field and the
``revision { hashId qmkVersion title }`` selection come from the community
workflow poulainpi/oryx-with-custom-qmk
(.github/workflows/fetch-and-build-layout.yml @ 26bfbe6). ``layers { title
color position keys }`` was checked against the live endpoint, which has
introspection disabled but validates field names. ``keys`` is a JSON scalar:
a list (one entry per key, in Oryx key-index order, possibly ``null``) of
objects like::

    {"tap": {"code": "KC_A", "layer": null, "modifiers": {...} | null, ...},
     "hold": {"code": "MO", "layer": 1, ...} | null,
     "customLabel": str | null, "emoji": str | null, "glowColor": "#rrggbb" | null, ...}

``revision { config }`` is a JSON scalar of the layout's settings. It holds only
what differs from Oryx's defaults (checked 2026-10-29: a layout with the default
tapping term has no tapping-term entry). Each key object also has its own
``tappingTerm`` (null for the global one).

``revisionId`` may be ``"latest"``. Fetches run on a worker thread with
urllib, and results come back on the GLib main loop.
"""

from __future__ import annotations

import glob
import json
import logging
import os
import re
import threading
import urllib.error
import urllib.request
from typing import Any, Callable

from ..models import KeyBinding, KeyboardModel, Layer, Layout
from . import geometries
from ._i18n import _

log = logging.getLogger(__name__)

GRAPHQL_URL = "https://oryx.zsa.io/graphql"

LAYOUT_QUERY = (
    "query getLayout($hashId: String!, $revisionId: String!, $geometry: String) {"
    " layout(hashId: $hashId, geometry: $geometry, revisionId: $revisionId) {"
    " hashId title geometry"
    " revision { hashId qmkVersion title config layers { title color position keys } }"
    " } }"
)

USER_AGENT = "Vibemapp/1 (+https://github.com/AlbertFlindt/vibemapp)"


# --- keycode labels -------------------------------------------------------------------

_NAMED: dict[str, str] = {
    "ENTER": "⏎", "ENT": "⏎", "ESCAPE": "Esc", "ESC": "Esc", "BSPC": "⌫",
    "BACKSPACE": "⌫", "TAB": "Tab", "SPACE": "Space", "SPC": "Space",
    "MINUS": "-", "MINS": "-", "EQUAL": "=", "EQL": "=", "LBRC": "[", "RBRC": "]",
    "LEFT_BRACKET": "[", "RIGHT_BRACKET": "]", "BSLS": "\\", "BACKSLASH": "\\",
    "NONUS_HASH": "#", "NUHS": "#", "SCLN": ";", "SEMICOLON": ";", "QUOTE": "'",
    "QUOT": "'", "GRAVE": "`", "GRV": "`", "COMMA": ",", "COMM": ",", "DOT": ".",
    "SLASH": "/", "SLSH": "/", "CAPS": "Caps", "CAPS_LOCK": "Caps", "PSCR": "PrtSc",
    "PRINT_SCREEN": "PrtSc", "SCRL": "ScrLk", "SCROLL_LOCK": "ScrLk",
    "PAUSE": "Pause", "PAUS": "Pause", "INSERT": "Ins", "INS": "Ins", "HOME": "Home",
    "PAGE_UP": "PgUp", "PGUP": "PgUp", "DELETE": "Del", "DEL": "Del", "END": "End",
    "PAGE_DOWN": "PgDn", "PGDN": "PgDn", "RIGHT": "→", "LEFT": "←", "DOWN": "↓",
    "UP": "↑", "NUM_LOCK": "NumLk", "NUM": "NumLk", "NUBS": "\\", "NONUS_BACKSLASH": "\\",
    "APPLICATION": "Menu", "APP": "Menu",
    "LEFT_CTRL": "Ctrl", "LCTL": "Ctrl", "RIGHT_CTRL": "Ctrl", "RCTL": "Ctrl",
    "LEFT_SHIFT": "Shift", "LSFT": "Shift", "RIGHT_SHIFT": "Shift", "RSFT": "Shift",
    "LEFT_ALT": "Alt", "LALT": "Alt", "RIGHT_ALT": "AltGr", "RALT": "AltGr",
    "LEFT_GUI": "Super", "LGUI": "Super", "RIGHT_GUI": "Super", "RGUI": "Super",
    "TILD": "~", "TILDE": "~", "EXLM": "!", "AT": "@", "HASH": "#", "DLR": "$",
    "PERC": "%", "CIRC": "^", "AMPR": "&", "ASTR": "*", "LPRN": "(", "RPRN": ")",
    "UNDS": "_", "PLUS": "+", "LCBR": "{", "RCBR": "}", "PIPE": "|", "COLN": ":",
    "DQUO": "\"", "DQT": "\"", "LABK": "<", "LT": "<", "RABK": ">", "GT": ">", "QUES": "?",
    "AUDIO_MUTE": "Mute", "MUTE": "Mute", "AUDIO_VOL_UP": "Vol+", "VOLU": "Vol+",
    "AUDIO_VOL_DOWN": "Vol−", "VOLD": "Vol−", "MEDIA_NEXT_TRACK": "Next", "MNXT": "Next",
    "MEDIA_PREV_TRACK": "Prev", "MPRV": "Prev", "MEDIA_PLAY_PAUSE": "Play", "MPLY": "Play",
    "MEDIA_STOP": "Stop", "MSTP": "Stop", "MEDIA_EJECT": "Eject", "EJCT": "Eject",
    "BRIGHTNESS_UP": "Bri+", "BRIU": "Bri+", "BRIGHTNESS_DOWN": "Bri−", "BRID": "Bri−",
    "SYSTEM_POWER": "Power", "PWR": "Power", "SYSTEM_SLEEP": "Sleep", "SLEP": "Sleep",
    "SYSTEM_WAKE": "Wake", "WAKE": "Wake", "CALCULATOR": "Calc", "CALC": "Calc",
    "MAIL": "Mail", "MY_COMPUTER": "PC", "MYCM": "PC", "FIND": "Find",
    "WWW_BACK": "Back", "WBAK": "Back", "WWW_FORWARD": "Fwd", "WFWD": "Fwd",
    "WWW_HOME": "WHome", "WHOM": "WHome", "WWW_REFRESH": "Reload", "WREF": "Reload",
    "WWW_SEARCH": "Search", "WSCH": "Search", "WWW_FAVORITES": "Fav", "WFAV": "Fav",
    "WWW_STOP": "WStop", "PC_UNDO": "Undo", "PC_CUT": "Cut", "PC_COPY": "Copy",
    "PC_PASTE": "Paste", "PC_REDO": "Redo", "PC_SAVE": "Save", "PC_SELECT_ALL": "SelAll",
    "MS_UP": "M↑", "MS_DOWN": "M↓", "MS_LEFT": "M←", "MS_RIGHT": "M→",
    "MS_BTN1": "Click", "MS_BTN2": "RClick", "MS_BTN3": "MClick", "MS_BTN4": "M4",
    "MS_BTN5": "M5", "MS_WH_UP": "Wh↑", "MS_WH_DOWN": "Wh↓", "MS_WH_LEFT": "Wh←",
    "MS_WH_RIGHT": "Wh→", "MS_ACCEL0": "Acc0", "MS_ACCEL1": "Acc1", "MS_ACCEL2": "Acc2",
    "MS_JIGGLER_TOGGLE": "Jiggle",
    "KP_SLASH": "/", "KP_ASTERISK": "*", "KP_MINUS": "-", "KP_PLUS": "+",
    "KP_ENTER": "⏎", "KP_DOT": ".", "KP_COMMA": ",", "KP_EQUAL": "=",
    "TRANSPARENT": "▽", "TRNS": "▽", "NO": "",
}

_SPECIAL: dict[str, str] = {
    "RGB_TOG": "RGB", "RGB_MOD": "RGB→", "RGB_MODE_FORWARD": "RGB→",
    "RGB_RMOD": "RGB←", "RGB_MODE_REVERSE": "RGB←", "RGB_HUI": "Hue+", "RGB_HUD": "Hue−",
    "RGB_SAI": "Sat+", "RGB_SAD": "Sat−", "RGB_VAI": "Bri+", "RGB_VAD": "Bri−",
    "RGB_SPI": "Spd+", "RGB_SPD": "Spd−", "RGB_SLD": "Solid", "RGB": "Color",
    "TOGGLE_LAYER_COLOR": "LayerClr", "LED_LEVEL": "LED", "QK_BOOT": "Boot",
    "RESET": "Boot", "EPRM": "EEPROM", "EE_CLR": "EEPROM", "CW_TOGG": "CapsWd",
    "AS_TOGG": "AS", "AS_UP": "AS+", "AS_DOWN": "AS−", "AS_RPT": "AS?",
    "MU_TOGG": "Music", "AU_TOGG": "Audio", "SC_LSPO": "⇧(", "SC_RSPC": "⇧)",
    "LOWER": "Lower", "RAISE": "Raise", "QK_LLCK": "Lock", "QK_LAYER_LOCK": "Lock",
    "DYN_REC_START1": "Rec1", "DYN_REC_START2": "Rec2", "DYN_MACRO_PLAY1": "Play1",
    "DYN_MACRO_PLAY2": "Play2", "DYN_REC_STOP": "RecStop", "QK_REP": "Rep",
    "QK_AREP": "AltRep",
}

_MOD_TAPS: dict[str, str] = {
    "CTL_T": "Ctrl", "LCTL_T": "Ctrl", "RCTL_T": "Ctrl", "SFT_T": "Shift",
    "LSFT_T": "Shift", "RSFT_T": "Shift", "ALT_T": "Alt", "LALT_T": "Alt",
    "RALT_T": "AltGr", "ALGR_T": "AltGr", "OPT_T": "Alt", "GUI_T": "Super",
    "LGUI_T": "Super", "RGUI_T": "Super", "CMD_T": "Super", "MEH_T": "Meh",
    "ALL_T": "Hyper", "HYPR_T": "Hyper", "C_S_T": "Ctrl⇧", "LCA_T": "CtrlAlt",
    "LCAG_T": "CAG", "SGUI_T": "Super⇧", "LSA_T": "Alt⇧", "RCS_T": "Ctrl⇧",
}

_LAYER_CODES = {"MO", "TG", "TO", "OSL", "TT", "DF", "LT", "LM", "PDF"}

_GLYPHS: dict[str, str] = {
    "AUDIO_MUTE": "audio-volume-muted-symbolic", "AUDIO_VOL_UP": "audio-volume-high-symbolic",
    "AUDIO_VOL_DOWN": "audio-volume-low-symbolic",
    "MEDIA_NEXT_TRACK": "media-skip-forward-symbolic",
    "MEDIA_PREV_TRACK": "media-skip-backward-symbolic",
    "MEDIA_PLAY_PAUSE": "media-playback-start-symbolic",
    "MEDIA_STOP": "media-playback-stop-symbolic", "MEDIA_EJECT": "media-eject-symbolic",
    "BRIGHTNESS_UP": "display-brightness-symbolic", "BRIU": "display-brightness-symbolic",
    "BRIGHTNESS_DOWN": "display-brightness-symbolic", "BRID": "display-brightness-symbolic",
    "SYSTEM_POWER": "system-shutdown-symbolic", "CALCULATOR": "accessories-calculator-symbolic",
    "MAIL": "mail-unread-symbolic", "WWW_BACK": "go-previous-symbolic",
    "WWW_FORWARD": "go-next-symbolic", "WWW_HOME": "go-home-symbolic",
    "WWW_REFRESH": "view-refresh-symbolic", "WWW_SEARCH": "system-search-symbolic",
    "FIND": "edit-find-symbolic",
}

# Language-specific aliases (DE_AE, DK_OSTR...). Prefix -> suffix -> label.
_LANG_LETTERS: dict[str, dict[str, str]] = {
    "DE": {"AE": "Ä", "OE": "Ö", "UE": "Ü", "SS": "ß", "SQ2": "²", "SQ3": "³",
           "ACUT": "´", "DEG": "°", "SECT": "§", "EURO": "€", "MICR": "µ"},
    "DK": {"AE": "Æ", "OSTR": "Ø", "ARNG": "Å", "ACUT": "´", "HALF": "½", "SECT": "§"},
    "NO": {"AE": "Æ", "OSTR": "Ø", "ARNG": "Å", "ACUT": "´", "SECT": "§"},
    "SE": {"ADIA": "Ä", "ODIA": "Ö", "ARNG": "Å", "ACUT": "´", "SECT": "§"},
    "FI": {"ADIA": "Ä", "ODIA": "Ö", "ARNG": "Å", "ACUT": "´", "SECT": "§"},
    "FR": {"EACU": "é", "EGRV": "è", "AGRV": "à", "CCED": "ç", "UGRV": "ù", "DEG": "°"},
    "ES": {"NTIL": "Ñ", "IEXL": "¡", "IQUE": "¿", "ORDM": "º", "FORD": "ª", "CCED": "Ç"},
}
_GENERIC_LANG = {"ADIA": "Ä", "ODIA": "Ö", "UDIA": "Ü", "ARNG": "Å", "OSTR": "Ø",
                 "EURO": "€", "SECT": "§", "DEG": "°", "PND": "£", "ACUT": "´"}

_MOD_GLYPHS = [("leftCtrl", "⌃"), ("rightCtrl", "⌃"), ("leftShift", "⇧"), ("rightShift", "⇧"),
               ("leftAlt", "⌥"), ("rightAlt", "⌥"), ("leftGui", "❖"), ("rightGui", "❖")]

_HEX = re.compile(r"^#?([0-9a-fA-F]{6})$")


def normalize_color(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    m = _HEX.match(value.strip())
    return f"#{m.group(1).lower()}" if m else None


def keycode_label(code: str | None, layer: int | None = None) -> str:
    """Short label for a QMK/Oryx keycode name (e.g. ``KC_ESCAPE`` -> ``Esc``)."""
    if not code:
        return ""
    if code in _LAYER_CODES:
        n = "?" if layer is None else str(layer)
        return f"L{n}" if code in ("MO", "LT") else f"{code}({n})"
    if code in _SPECIAL:
        return _SPECIAL[code]
    if code in _MOD_TAPS:
        return _MOD_TAPS[code]
    if code.startswith("KC_"):
        name = code[3:]
        if name in _NAMED:
            return _NAMED[name]
        if len(name) == 1:
            return name.upper()
        if re.fullmatch(r"F\d{1,2}", name):
            return name
        if name.startswith("KP_"):
            rest = name[3:]
            return rest if len(rest) == 1 else _NAMED.get(name, rest.title())
        return name.replace("_", " ").title()
    m = re.fullmatch(r"([A-Z]{2})_([A-Z0-9_]+)", code)
    if m:
        lang, suffix = m.groups()
        table = _LANG_LETTERS.get(lang, {})
        if suffix in table:
            return table[suffix]
        if suffix in _GENERIC_LANG:
            return _GENERIC_LANG[suffix]
        if suffix in _NAMED:
            return _NAMED[suffix]
        if len(suffix) == 1:
            return suffix
        return suffix.title()
    return code.replace("_", " ").title()


def keycode_glyph(code: str | None) -> str | None:
    if not code or not code.startswith("KC_"):
        return None
    return _GLYPHS.get(code[3:])


def hold_label(action: dict[str, Any] | None) -> str:
    if not isinstance(action, dict):
        return ""
    code = action.get("code")
    if not isinstance(code, str):
        return ""
    if code in _LAYER_CODES:
        layer = action.get("layer")
        return f"L{layer}" if isinstance(layer, int) else "L?"
    label = keycode_label(code, action.get("layer"))
    prefix = _modifier_prefix(action.get("modifiers"))
    return prefix + label if prefix and label and not _is_modifier(code) else label


# Plain modifier keycodes: holding one is a mod-tap, not a key being sent.
_MODIFIER_NAMES = frozenset({
    "LCTL", "LEFT_CTRL", "LCTRL", "RCTL", "RIGHT_CTRL", "RCTRL",
    "LSFT", "LEFT_SHIFT", "LSHIFT", "RSFT", "RIGHT_SHIFT", "RSHIFT",
    "LALT", "LEFT_ALT", "LOPT", "RALT", "RIGHT_ALT", "ROPT", "ALGR",
    "LGUI", "LEFT_GUI", "LCMD", "LWIN", "RGUI", "RIGHT_GUI", "RCMD", "RWIN"})

# Modifier names for ``hold_combo``, in the usual Ctrl, Shift, Alt, Super order.
_MOD_NAMES = [("leftCtrl", "Ctrl"), ("rightCtrl", "Ctrl"), ("leftShift", "Shift"),
              ("rightShift", "Shift"), ("leftAlt", "Alt"), ("rightAlt", "AltGr"),
              ("leftGui", "Super"), ("rightGui", "Super")]


def _is_modifier(code: str) -> bool:
    return code.startswith("KC_") and code[3:] in _MODIFIER_NAMES


def hold_combo(action: dict[str, Any] | None) -> str:
    """``"Ctrl+C"`` when holding the key sends a key (with any modifiers), as
    Oryx's "hold for another key" does. Empty for no hold, layer keys, mod-taps
    and lone modifiers, which hold rather than send something."""
    if not isinstance(action, dict):
        return ""
    code = action.get("code")
    if (not isinstance(code, str) or code in _LAYER_CODES or code in _MOD_TAPS
            or code in _TRANSPARENT_CODES or code == "KC_NO" or _is_modifier(code)):
        return ""
    label = keycode_label(code, action.get("layer"))
    if not label:
        return ""
    mods = action.get("modifiers")
    names: list[str] = []
    if isinstance(mods, dict):
        for key, name in _MOD_NAMES:
            if mods.get(key) and name not in names:
                names.append(name)
    return "+".join([*names, label])


def tapping_term(value: Any) -> int | None:
    """A tapping term in ms from Oryx data, or None when unset or nonsense."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    term = int(value)
    return term if 0 < term <= 5000 else None


def _config(revision: dict[str, Any]) -> dict[str, Any]:
    config = revision.get("config")
    if isinstance(config, str):     # defensively accept a JSON-encoded scalar
        try:
            config = json.loads(config)
        except ValueError:
            return {}
    return config if isinstance(config, dict) else {}


def _modifier_prefix(mods: Any) -> str:
    if not isinstance(mods, dict):
        return ""
    out = []
    for key, glyph in _MOD_GLYPHS:
        if mods.get(key) and glyph not in out:
            out.append(glyph)
    return "".join(out)


_TRANSPARENT_CODES = frozenset({"KC_TRANSPARENT", "KC_TRNS", "_______"})
_ACTION_FIELDS = ("tap", "hold", "doubleTap", "tapHold")
_LABEL_FIELDS = ("customLabel", "emoji", "icon")


def _is_set(value: Any) -> bool:
    return bool(value.strip()) if isinstance(value, str) else bool(value)


def key_binding_from_json(index: int, key: Any) -> KeyBinding:
    """One Oryx key object. Oryx sends a transparent key as an object whose actions
    are all null; an explicit null slot inside a layer's key list means the same.
    ``KC_NO`` is a blocked key, not a transparent one."""
    if not isinstance(key, dict):
        return KeyBinding(index=index, transparent=True)
    tap = key.get("tap") if isinstance(key.get("tap"), dict) else None
    hold = key.get("hold") if isinstance(key.get("hold"), dict) else None
    code = tap.get("code") if tap else None
    color = normalize_color(key.get("glowColor")) or (normalize_color(tap.get("color")) if tap else None)
    no_action = not any(_is_set(key.get(f)) for f in _ACTION_FIELDS)
    no_label = not any(_is_set(key.get(f)) for f in _LABEL_FIELDS)
    if (no_action and no_label) or code in _TRANSPARENT_CODES:
        # Keep a real hold action (e.g. LT(n, KC_TRNS)) so it isn't lost.
        return KeyBinding(index=index, hold_label=hold_label(hold), color=color,
                          transparent=True)
    tap_label = keycode_label(code, tap.get("layer") if tap else None)
    glyph = keycode_glyph(code)
    prefix = _modifier_prefix(tap.get("modifiers")) if tap else ""
    if prefix and tap_label:
        tap_label = prefix + tap_label
        glyph = None
    custom = key.get("customLabel")
    emoji = key.get("emoji")
    if isinstance(custom, str) and custom.strip():
        tap_label, glyph = custom.strip(), None
    elif isinstance(emoji, str) and emoji.strip():
        tap_label, glyph = emoji.strip(), None
    return KeyBinding(index=index, tap_label=tap_label, hold_label=hold_label(hold),
                      glyph=glyph, color=color,
                      tap_code=code if isinstance(code, str) and code else None,
                      hold_combo=hold_combo(hold),
                      tapping_term_ms=tapping_term(key.get("tappingTerm")))


def parse_layout(data: dict[str, Any], model: KeyboardModel | None = None) -> Layout:
    """Turn the GraphQL ``data.layout`` object into a ``Layout``."""
    revision = data.get("revision") or {}
    if model is None or model == KeyboardModel.UNKNOWN:
        try:
            model = KeyboardModel(str(data.get("geometry") or ""))
        except ValueError:
            model = KeyboardModel.UNKNOWN
    layers_raw = revision.get("layers") or []
    layers_sorted = sorted(
        (l for l in layers_raw if isinstance(l, dict)),
        key=lambda l: l.get("position") if isinstance(l.get("position"), int) else 0)
    layers: list[Layer] = []
    for i, layer in enumerate(layers_sorted):
        keys = layer.get("keys") or []
        if isinstance(keys, str):     # defensively accept JSON-encoded scalars
            try:
                keys = json.loads(keys)
            except ValueError:
                keys = []
        title = layer.get("title")
        layers.append(Layer(
            index=i,
            title=title.strip() if isinstance(title, str) and title.strip() else _("Layer {n}").format(n=i),
            color=normalize_color(layer.get("color")),
            keys=tuple(key_binding_from_json(k, key) for k, key in enumerate(keys)),
        ))
    return Layout(
        id=str(data.get("hashId") or ""),
        title=str(data.get("title") or ""),
        geometry=geometries.geometry_for(model),
        layers=tuple(layers),
        revision_id=str(revision.get("hashId")) if revision.get("hashId") else None,
        # The name of a changed global term inside ``config`` is a guess: no
        # layout with one has been seen yet.
        tapping_term_ms=tapping_term(_config(revision).get("tappingTerm")),
    )


# --- network + cache ------------------------------------------------------------------

class LayoutFetchError(Exception):
    def __init__(self, message: str, offline: bool = False) -> None:
        super().__init__(message)
        self.offline = offline


def fetch_layout_json(layout_id: str, revision_id: str | None, geometry: str | None,
                      timeout: float = 15.0, url: str = GRAPHQL_URL) -> dict[str, Any]:
    """Blocking GraphQL request. Returns ``data.layout``."""
    variables: dict[str, Any] = {"hashId": layout_id, "revisionId": revision_id or "latest"}
    if geometry:
        variables["geometry"] = geometry
    body = json.dumps({"query": LAYOUT_QUERY, "variables": variables}).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json", "Accept": "application/json",
        "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise LayoutFetchError(_("Oryx returned HTTP {code}").format(code=exc.code)) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise LayoutFetchError(_("Can't reach Oryx"), offline=True) from exc
    except ValueError as exc:
        raise LayoutFetchError(_("Oryx returned an invalid response")) from exc
    if payload.get("errors"):
        msg = "; ".join(str(e.get("message")) for e in payload["errors"] if isinstance(e, dict))
        raise LayoutFetchError(_("Oryx error: {message}").format(message=msg))
    layout = (payload.get("data") or {}).get("layout")
    if not isinstance(layout, dict):
        raise LayoutFetchError(_("Layout not found on Oryx"))
    return layout


def default_cache_dir() -> str:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "vibemapp", "layouts")


def _safe(part: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", part)


class LayoutCache:
    def __init__(self, directory: str | None = None) -> None:
        self.directory = directory or default_cache_dir()

    def path_for(self, layout_id: str, revision_id: str) -> str:
        return os.path.join(self.directory, f"{_safe(layout_id)}-{_safe(revision_id)}.json")

    def load(self, layout_id: str, revision_id: str | None) -> dict[str, Any] | None:
        if revision_id and revision_id != "latest":
            path = self.path_for(layout_id, revision_id)
            candidates = [path] if os.path.exists(path) else []
        else:
            pattern = os.path.join(glob.escape(self.directory), f"{_safe(layout_id)}-*.json")
            candidates = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
        for path in candidates:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
            except (OSError, ValueError) as exc:
                log.warning("Bad cache file %s: %s", path, exc)
        return None

    def store(self, layout_id: str, data: dict[str, Any]) -> None:
        revision = (data.get("revision") or {}).get("hashId") or "latest"
        os.makedirs(self.directory, exist_ok=True)
        path = self.path_for(layout_id, str(revision))
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)


Fetcher = Callable[[str, str | None, str | None], dict[str, Any]]


class LayoutRepository:
    """Loads layouts from cache or Oryx on a worker thread."""

    def __init__(self, cache: LayoutCache | None = None, fetcher: Fetcher | None = None) -> None:
        self.cache = cache or LayoutCache()
        self._fetch = fetcher or (lambda lid, rev, geo: fetch_layout_json(lid, rev, geo))

    def load_blocking(self, layout_id: str, revision_id: str | None,
                      model: KeyboardModel) -> Layout:
        """Exact cached revision > network > newest cached revision for the layout."""
        geometry = model.value if model != KeyboardModel.UNKNOWN else None
        if revision_id and revision_id != "latest":
            cached = self.cache.load(layout_id, revision_id)
            if cached is not None:
                return parse_layout(cached, model)
        try:
            data = self._fetch(layout_id, revision_id, geometry)
        except LayoutFetchError as exc:
            cached = self.cache.load(layout_id, None)
            if cached is not None:
                log.info("Using cached layout for %s (%s)", layout_id, exc)
                return parse_layout(cached, model)
            raise
        try:
            self.cache.store(layout_id, data)
        except OSError as exc:
            log.warning("Can't write layout cache: %s", exc)
        return parse_layout(data, model)

    def load_async(self, layout_id: str, revision_id: str | None, model: KeyboardModel,
                   callback: Callable[[Layout | None, str | None], None],
                   cancellable: Any = None) -> None:
        from gi.repository import GLib

        def finish(layout: Layout | None, error: str | None) -> bool:
            if cancellable is not None and cancellable.is_cancelled():
                callback(None, _("Cancelled"))
            else:
                callback(layout, error)
            return GLib.SOURCE_REMOVE

        def work() -> None:
            try:
                layout = self.load_blocking(layout_id, revision_id, model)
                GLib.idle_add(finish, layout, None)
            except LayoutFetchError as exc:
                GLib.idle_add(finish, None, str(exc))
            except Exception as exc:     # noqa: BLE001 - surface anything to the UI
                log.exception("layout load failed")
                GLib.idle_add(finish, None, _("Couldn't load the layout: {error}").format(error=exc))

        threading.Thread(target=work, name="oryx-layout", daemon=True).start()


__all__ = ["GRAPHQL_URL", "LAYOUT_QUERY", "keycode_label", "keycode_glyph", "hold_label",
           "hold_combo", "tapping_term", "key_binding_from_json", "parse_layout", "fetch_layout_json", "LayoutCache",
           "LayoutRepository", "LayoutFetchError", "normalize_color", "default_cache_dir"]
