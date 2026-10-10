"""Steam: installed games and "is the focused window a Steam game?" (SPEC 7). No GTK.

* Libraries: ``<root>/steamapps`` for each Steam root (``~/.local/share/Steam``,
  ``~/.steam/steam``, ``~/.steam/root``), plus every ``"path"`` in their
  ``steamapps/libraryfolders.vdf``. Deduplicated by real path.
* Games: ``appmanifest_<id>.acf`` in each library's ``steamapps``. Proton,
  Steam Linux Runtime and Steamworks Common Redistributables are filtered out.
  The result is cached and invalidated when any manifest or library file mtime
  changes.
* Focus detection: class ``steam_app_<digits>`` (Proton/Wine games), or
  ``SteamAppId``/``SteamGameId`` (non-zero) in ``/proc/<pid>/environ`` (native
  games). The Steam client (class ``steam``) is never a game. Environ lookups
  are cached per pid.
"""

from __future__ import annotations

import glob
import logging
import os
import re
from collections import OrderedDict
from typing import Any

from ..models import FocusedWindow, SteamGame

log = logging.getLogger(__name__)

STEAM_CLIENT_CLASSES = frozenset({"steam", "Steam"})
_CLASS_RE = re.compile(r"steam_app_(\d+)")
_TOOL_PREFIXES = ("Proton", "Steam Linux Runtime", "Steamworks Common Redistributables")
_TOOL_APP_IDS = frozenset({"228980"})          # Steamworks Common Redistributables
_ENV_KEYS = (b"SteamAppId", b"SteamGameId")
_MAX_ENVIRON = 1 << 20


# --- KeyValues (VDF/ACF) parser ---------------------------------------------------

class VdfError(ValueError):
    pass


_ESCAPES = {"n": "\n", "t": "\t", "\\": "\\", '"': '"'}


def _tokens(text: str):
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
        elif c == "/" and text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j + 1
        elif c in "{}":
            yield c, None
            i += 1
        elif c == '"':
            i += 1
            out = []
            while i < n and text[i] != '"':
                if text[i] == "\\" and i + 1 < n:
                    out.append(_ESCAPES.get(text[i + 1], text[i + 1]))
                    i += 2
                else:
                    out.append(text[i])
                    i += 1
            if i >= n:
                raise VdfError("unterminated string")
            i += 1
            yield "s", "".join(out)
        else:
            j = i
            while j < n and not text[j].isspace() and text[j] not in '{}"':
                j += 1
            yield "s", text[i:j]
            i = j


def parse_vdf(text: str) -> dict[str, Any]:
    """Parse Valve KeyValues text into nested dicts. Keys are lower-cased.

    Conditionals like ``[$WIN32]`` are ignored. Duplicate keys: last wins.
    """
    root: dict[str, Any] = {}
    stack = [root]
    key: str | None = None
    for kind, value in _tokens(text):
        if kind == "s":
            if value is not None and value.startswith("[$") and value.endswith("]"):
                continue
            if key is None:
                key = value.lower() if value is not None else ""
            else:
                stack[-1][key] = value
                key = None
        elif kind == "{":
            if key is None:
                raise VdfError("block without key")
            child: dict[str, Any] = {}
            stack[-1][key] = child
            stack.append(child)
            key = None
        else:   # "}"
            if len(stack) == 1:
                raise VdfError("unbalanced }")
            stack.pop()
            key = None
    if len(stack) != 1:
        raise VdfError("unclosed block")
    return root


def _read_vdf(path: str) -> dict[str, Any] | None:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return parse_vdf(f.read())
    except (OSError, VdfError) as exc:
        log.debug("Can't parse %s: %s", path, exc)
        return None


# --- helpers ------------------------------------------------------------------------

def is_tool(app_id: str, name: str) -> bool:
    return app_id in _TOOL_APP_IDS or name.startswith(_TOOL_PREFIXES)


def app_id_from_class(app_class: str) -> str | None:
    m = _CLASS_RE.fullmatch(app_class)
    if m and int(m.group(1)) != 0:
        return m.group(1)
    return None


def app_id_from_environ(pid: int | None, proc_root: str = "/proc") -> str | None:
    """SteamAppId (preferred) or SteamGameId from ``/proc/<pid>/environ``.

    Returns None for a missing pid, unreadable environ (EACCES, the process
    exited, ...) or zero/non-numeric values.
    """
    if pid is None or pid <= 0:
        return None
    try:
        with open(os.path.join(proc_root, str(pid), "environ"), "rb") as f:
            data = f.read(_MAX_ENVIRON)
    except (OSError, ValueError):
        return None
    found: dict[bytes, str] = {}
    for entry in data.split(b"\0"):
        key, sep, value = entry.partition(b"=")
        if sep and key in _ENV_KEYS and value.isdigit() and int(value) != 0:
            found[key] = value.decode()
    for key in _ENV_KEYS:
        if key in found:
            return found[key]
    return None


def default_steam_roots() -> list[str]:
    home = os.path.expanduser("~")
    data = os.environ.get("XDG_DATA_HOME") or os.path.join(home, ".local", "share")
    return [os.path.join(data, "Steam"), os.path.join(home, ".steam", "steam"),
            os.path.join(home, ".steam", "root")]


# --- library ------------------------------------------------------------------------

class SteamLibrary:
    def __init__(self, roots: list[str] | None = None, proc_root: str = "/proc") -> None:
        self.roots = list(roots) if roots is not None else default_steam_roots()
        self.proc_root = proc_root
        self._signature: tuple | None = None
        self._all: dict[str, str] = {}             # app id -> name (incl. tools)
        self._games: list[SteamGame] = []
        self._pid_cache: OrderedDict[tuple[int, int], str | None] = OrderedDict()

    def library_dirs(self) -> list[str]:
        """Real paths of every library root (each has a ``steamapps`` dir)."""
        seen: list[str] = []

        def add(path: str) -> None:
            if not path:
                return
            real = os.path.realpath(path)
            if real not in seen and os.path.isdir(os.path.join(real, "steamapps")):
                seen.append(real)

        for root in self.roots:
            add(root)
            data = _read_vdf(os.path.join(root, "steamapps", "libraryfolders.vdf")) or {}
            folders = data.get("libraryfolders") or {}
            if isinstance(folders, dict):
                for entry in folders.values():
                    if isinstance(entry, dict):
                        add(str(entry.get("path") or ""))
                    elif isinstance(entry, str) and os.path.isabs(entry):
                        add(entry)    # very old format: "1" "/path"
        return seen

    def _manifests(self) -> list[str]:
        out: list[str] = []
        for lib in self.library_dirs():
            out += glob.glob(os.path.join(glob.escape(lib), "steamapps", "appmanifest_*.acf"))
        return sorted(out)

    def _current_signature(self, manifests: list[str]) -> tuple:
        sig = []
        for path in manifests + [os.path.join(r, "steamapps", "libraryfolders.vdf")
                                 for r in self.roots]:
            try:
                st = os.stat(path)
                sig.append((path, st.st_mtime_ns, st.st_size))
            except OSError:
                sig.append((path, None, None))
        return tuple(sig)

    def _refresh(self) -> None:
        manifests = self._manifests()
        sig = self._current_signature(manifests)
        if sig == self._signature:
            return
        names: dict[str, str] = {}
        for path in manifests:
            data = _read_vdf(path) or {}
            state = data.get("appstate")
            if not isinstance(state, dict):
                continue
            app_id = str(state.get("appid") or "")
            name = str(state.get("name") or "")
            if not app_id.isdigit():
                m = re.search(r"appmanifest_(\d+)\.acf$", path)
                app_id = m.group(1) if m else ""
            if app_id and app_id not in names:
                names[app_id] = name or app_id
        self._all = names
        self._games = sorted((SteamGame(i, n) for i, n in names.items() if not is_tool(i, n)),
                             key=lambda g: (g.name.casefold(), g.app_id))
        self._signature = sig

    def games(self) -> list[SteamGame]:
        try:
            self._refresh()
        except OSError as exc:
            log.warning("Can't read Steam libraries: %s", exc)
        return list(self._games)

    def name_for(self, app_id: str) -> str | None:
        try:
            self._refresh()
        except OSError:
            pass
        return self._all.get(app_id)

    # -- focus --

    def app_id_for_pid(self, pid: int | None) -> str | None:
        if pid is None or pid <= 0:
            return None
        try:   # pids get reused: key on the process dir's ctime too
            key = (pid, os.stat(os.path.join(self.proc_root, str(pid))).st_ctime_ns)
        except OSError:
            return None
        if key in self._pid_cache:
            self._pid_cache.move_to_end(key)
            return self._pid_cache[key]
        app_id = app_id_from_environ(pid, self.proc_root)
        self._pid_cache[key] = app_id
        if len(self._pid_cache) > 256:
            self._pid_cache.popitem(last=False)
        return app_id

    def describe(self, app_class: str, title: str, pid: int | None) -> FocusedWindow:
        app_id: str | None = None
        if app_class and app_class not in STEAM_CLIENT_CLASSES:
            app_id = app_id_from_class(app_class) or self.app_id_for_pid(pid)
        name = self.name_for(app_id) if app_id else None
        return FocusedWindow(app_class=app_class, title=title, pid=pid,
                             steam_app_id=app_id, steam_game_name=name)


__all__ = ["parse_vdf", "VdfError", "SteamLibrary", "app_id_from_class",
           "app_id_from_environ", "is_tool", "default_steam_roots", "STEAM_CLIENT_CLASSES"]
