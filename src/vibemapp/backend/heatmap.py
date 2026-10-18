"""Heatmap key-press counts (SPEC 6.4). No GTK.

Storage: one file per Oryx layout, ``$XDG_DATA_HOME/vibemapp/heatmap/<layout_id>.json``::

    {"version": 2, "layout_id": "Bre9W",
     "days": {"2026-10-10": {"<layer>": {"<key index>": count}}},
     "app_days": {"2026-10-10": {"<app key>": {"<layer>": {"<key index>": count}}}},
     "app_names": {"steam:1245620": "Elden Ring"},
     "pairs": {"2026-10-10": {"0:12>0:13": 4}},
     "app_pairs": {"2026-10-10": {"firefox": {"0:12>0:13": 3}}}}

``days`` counts every press (the "All Apps" total). ``app_days`` splits the
presses made while a known app was focused (window class, or ``steam:<id>``),
``app_names`` holds display names where they differ from the key. Version 1
files have neither and load as totals only. ``pairs`` / ``app_pairs`` (only
written while pair recording is on) count "key B within ~1 s after key A" as
``"<layer>:<key>><layer>:<key>"``, per day, as an all-apps total and per app.
Never anything longer than a pair.

Privacy: only per (local calendar day, app, layer, key index) counts are kept; never window titles.
There's no key order and nothing finer than a day. Writes are atomic (temp
file, fsync, rename). The store tracks a dirty flag, and the caller decides
when to ``flush()`` (the service flushes at most every ~30 s and on shutdown).
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
import tempfile
from typing import Any, Callable

from ..models import HeatmapApp, HeatmapPeriod

log = logging.getLogger(__name__)

FILE_VERSION = 2
MAX_APP_KEY = 200
WEEK_DAYS = 7

Pair = tuple[tuple[int, int], tuple[int, int]]
Clock = Callable[[], dt.date]


def default_heatmap_dir() -> str:
    base = os.environ.get("XDG_DATA_HOME")
    if not base or not os.path.isabs(base):
        base = os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "vibemapp", "heatmap")


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name) or "_"


def period_start(period: HeatmapPeriod, today: dt.date) -> dt.date | None:
    """First day included in ``period`` (None means no lower bound)."""
    if period == HeatmapPeriod.TODAY:
        return today
    if period == HeatmapPeriod.WEEK:
        return today - dt.timedelta(days=WEEK_DAYS - 1)
    return None


class HeatmapStore:
    def __init__(self, directory: str | None = None, clock: Clock = dt.date.today) -> None:
        self.directory = directory or default_heatmap_dir()
        self._clock = clock
        self._layout_id: str | None = None
        # day ISO string -> layer -> key index -> count
        self._days: dict[str, dict[int, dict[int, int]]] = {}
        # day ISO string -> app key -> layer -> key index -> count
        self._app_days: dict[str, dict[str, dict[int, dict[int, int]]]] = {}
        self._app_names: dict[str, str] = {}
        # day -> (layer, key) pair -> count; and day -> app -> pair -> count
        self._pairs: dict[str, dict[Pair, int]] = {}
        self._app_pairs: dict[str, dict[str, dict[Pair, int]]] = {}
        self._dirty = False

    # -- state --

    @property
    def layout_id(self) -> str | None:
        return self._layout_id

    @property
    def dirty(self) -> bool:
        return self._dirty

    def path_for(self, layout_id: str) -> str:
        return os.path.join(self.directory, f"{_safe(layout_id)}.json")

    def set_layout(self, layout_id: str | None) -> None:
        """Switch layouts. Flushes the previous one and loads the new one."""
        if layout_id == self._layout_id:
            return
        try:
            self.flush()
        except OSError as exc:      # don't block the switch; unsaved counts are lost
            log.warning("Can't save heatmap for %s: %s", self._layout_id, exc)
        self._layout_id = layout_id
        (self._days, self._app_days, self._app_names,
         self._pairs, self._app_pairs) = (
            self._load(layout_id) if layout_id else ({}, {}, {}, {}, {}))
        self._dirty = False

    # -- counting --

    def record(self, layer: int, index: int, app: str | None = None,
               app_name: str | None = None) -> None:
        if self._layout_id is None or layer < 0 or index < 0:
            return
        day = self._clock().isoformat()
        keys = self._days.setdefault(day, {}).setdefault(layer, {})
        keys[index] = keys.get(index, 0) + 1
        app = _clean_app(app)
        if app:
            akeys = self._app_days.setdefault(day, {}).setdefault(app, {}).setdefault(layer, {})
            akeys[index] = akeys.get(index, 0) + 1
            name = _clean_app(app_name)
            if name and name != app:
                self._app_names[app] = name
            else:
                self._app_names.pop(app, None)
        self._dirty = True

    def record_pair(self, first: tuple[int, int], second: tuple[int, int],
                    app: str | None = None) -> None:
        """Count "``second`` pressed shortly after ``first``", both (layer, key index)."""
        if self._layout_id is None or min(*first, *second) < 0:
            return
        day = self._clock().isoformat()
        pair = (tuple(first), tuple(second))
        bucket = self._pairs.setdefault(day, {})
        bucket[pair] = bucket.get(pair, 0) + 1     # type: ignore[index]
        app = _clean_app(app)
        if app:
            abucket = self._app_pairs.setdefault(day, {}).setdefault(app, {})
            abucket[pair] = abucket.get(pair, 0) + 1   # type: ignore[index]
        self._dirty = True

    def pairs(self, period: HeatmapPeriod, app: str | None = None) -> dict[Pair, int]:
        if self._layout_id is None:
            return {}
        start = period_start(HeatmapPeriod(period), self._clock())
        out: dict[Pair, int] = {}
        for day, entry in self._pairs.items() if app is None else self._app_pairs.items():
            if not self._in_period(day, start):
                continue
            bucket = entry if app is None else entry.get(app, {})
            for pair, n in bucket.items():
                out[pair] = out.get(pair, 0) + n
        return out

    def layer_counts(self, period: HeatmapPeriod,
                     app: str | None = None) -> dict[tuple[int, int], int]:
        """Counts keyed by (layer, key index), for the ergo score."""
        if self._layout_id is None:
            return {}
        start = period_start(HeatmapPeriod(period), self._clock())
        out: dict[tuple[int, int], int] = {}
        for day, entry in self._days.items() if app is None else self._app_days.items():
            if not self._in_period(day, start):
                continue
            for layers in ([entry] if app is None else [entry.get(app, {})]):
                for lay, keys in layers.items():
                    for idx, n in keys.items():
                        out[(lay, idx)] = out.get((lay, idx), 0) + n
        return out

    def _in_period(self, day: str, start: dt.date | None) -> bool:
        if start is None:
            return True
        try:
            return dt.date.fromisoformat(day) >= start
        except ValueError:
            return False

    def counts(self, period: HeatmapPeriod, layer: int | None = None,
               app: str | None = None) -> dict[int, int]:
        """Per key counts; ``app`` None is the all-apps total."""
        if self._layout_id is None:
            return {}
        start = period_start(HeatmapPeriod(period), self._clock())
        out: dict[int, int] = {}
        for day, entry in self._days.items() if app is None else self._app_days.items():
            if not self._in_period(day, start):
                continue
            layers_list = [entry] if app is None else [entry.get(app, {})]
            for layers in layers_list:
                for lay, keys in layers.items():
                    if layer is not None and lay != layer:
                        continue
                    for idx, n in keys.items():
                        out[idx] = out.get(idx, 0) + n
        return out

    def apps(self, period: HeatmapPeriod) -> list[HeatmapApp]:
        """Apps with presses in ``period``, most presses first, then by name."""
        if self._layout_id is None:
            return []
        start = period_start(HeatmapPeriod(period), self._clock())
        totals: dict[str, int] = {}
        for day, apps in self._app_days.items():
            if not self._in_period(day, start):
                continue
            for key, layers in apps.items():
                n = sum(sum(keys.values()) for keys in layers.values())
                if n:
                    totals[key] = totals.get(key, 0) + n
        out = [HeatmapApp(k, self._app_names.get(k, k), n) for k, n in totals.items()]
        out.sort(key=lambda a: (-a.presses, a.name.casefold(), a.key))
        return out

    def reset(self) -> None:
        """Delete every count for the current layout, in memory and on disk."""
        if self._layout_id is None:
            return
        self._days = {}
        self._app_days = {}
        self._app_names = {}
        self._pairs = {}
        self._app_pairs = {}
        self._dirty = False
        try:
            os.unlink(self.path_for(self._layout_id))
        except FileNotFoundError:
            pass

    # -- persistence --

    def to_json(self) -> dict[str, Any]:
        def layers_json(layers: dict[int, dict[int, int]]) -> dict[str, Any]:
            return {str(l): {str(i): n for i, n in sorted(keys.items())}
                    for l, keys in sorted(layers.items())}

        return {
            "version": FILE_VERSION,
            "layout_id": self._layout_id,
            "days": {day: layers_json(layers) for day, layers in sorted(self._days.items())},
            "app_days": {day: {app: layers_json(layers) for app, layers in sorted(apps.items())}
                         for day, apps in sorted(self._app_days.items())},
            "app_names": dict(sorted(self._app_names.items())),
            "pairs": {day: _pairs_json(b) for day, b in sorted(self._pairs.items())},
            "app_pairs": {day: {app: _pairs_json(b) for app, b in sorted(apps.items())}
                          for day, apps in sorted(self._app_pairs.items())},
        }

    def flush(self) -> bool:
        """Write if dirty. Returns True if a write happened. OSError propagates."""
        if not self._dirty or self._layout_id is None:
            return False
        os.makedirs(self.directory, exist_ok=True)
        path = self.path_for(self._layout_id)
        fd, tmp = tempfile.mkstemp(prefix=".heatmap.", suffix=".tmp", dir=self.directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.to_json(), f, separators=(",", ":"))
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        self._dirty = False
        return True

    def _load(self, layout_id: str) -> tuple[
            dict[str, dict[int, dict[int, int]]],
            dict[str, dict[str, dict[int, dict[int, int]]]],
            dict[str, str], dict[str, dict[Pair, int]],
            dict[str, dict[str, dict[Pair, int]]]]:
        empty: tuple[dict, dict, dict, dict, dict] = ({}, {}, {}, {}, {})
        path = self.path_for(layout_id)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return empty
        except (OSError, ValueError) as exc:
            log.warning("Can't read heatmap %s: %s", path, exc)
            try:
                os.replace(path, path + ".corrupt")
            except OSError:
                pass
            return empty
        if not isinstance(data, dict) or not isinstance(data.get("days"), dict):
            return empty
        days = _parse_layers_by_day(data["days"])
        app_days: dict[str, dict[str, dict[int, dict[int, int]]]] = {}
        raw_app_days = data.get("app_days")
        if isinstance(raw_app_days, dict):
            for day, apps in raw_app_days.items():
                if not isinstance(apps, dict):
                    continue
                for app, layers in apps.items():
                    key = _clean_app(app)
                    if not key or not isinstance(layers, dict):
                        continue
                    parsed = _parse_layers_by_day({day: layers}).get(day)
                    if parsed:
                        merged = app_days.setdefault(day, {}).setdefault(key, {})
                        for lay, keys in parsed.items():
                            for idx, n in keys.items():
                                m = merged.setdefault(lay, {})
                                m[idx] = m.get(idx, 0) + n
        names: dict[str, str] = {}
        raw_names = data.get("app_names")
        if isinstance(raw_names, dict):
            for k, v in raw_names.items():
                key, name = _clean_app(k), _clean_app(v)
                if key and name:
                    names[key] = name
        pairs: dict[str, dict[Pair, int]] = {}
        raw_pairs = data.get("pairs")
        if isinstance(raw_pairs, dict):
            for day, bucket in raw_pairs.items():
                parsed = _parse_pairs(day, bucket)
                if parsed:
                    pairs[day] = parsed
        app_pairs: dict[str, dict[str, dict[Pair, int]]] = {}
        raw_app_pairs = data.get("app_pairs")
        if isinstance(raw_app_pairs, dict):
            for day, apps in raw_app_pairs.items():
                if not isinstance(apps, dict):
                    continue
                for app, bucket in apps.items():
                    key = _clean_app(app)
                    parsed = _parse_pairs(day, bucket) if key else {}
                    if parsed:
                        merged = app_pairs.setdefault(day, {}).setdefault(key, {})
                        for pair, n in parsed.items():
                            merged[pair] = merged.get(pair, 0) + n
        return days, app_days, names, pairs, app_pairs


def _pairs_json(bucket: dict[Pair, int]) -> dict[str, int]:
    return {f"{a[0]}:{a[1]}>{b[0]}:{b[1]}": n for (a, b), n in sorted(bucket.items())}


_PAIR_RE = re.compile(r"^(\d{1,4}):(\d{1,4})>(\d{1,4}):(\d{1,4})$")


def _parse_pairs(day: object, bucket: object) -> dict[Pair, int]:
    """Valid ``"la:ka>lb:kb": n`` entries of one day; {} for anything malformed."""
    if not isinstance(bucket, dict):
        return {}
    try:
        dt.date.fromisoformat(day)    # type: ignore[arg-type]
    except (TypeError, ValueError):
        return {}
    out: dict[Pair, int] = {}
    for text, n in bucket.items():
        m = _PAIR_RE.match(text) if isinstance(text, str) else None
        try:
            cnt = int(n)
        except (TypeError, ValueError):
            continue
        if m and cnt > 0:
            la, ka, lb, kb = (int(g) for g in m.groups())
            out[((la, ka), (lb, kb))] = cnt
    return out


def _clean_app(value: object) -> str:
    """Sanitise an app key or display name: a bounded plain string, else ''."""
    if not isinstance(value, str):
        return ""
    return value.strip()[:MAX_APP_KEY]


def _parse_layers_by_day(raw_days: dict) -> dict[str, dict[int, dict[int, int]]]:
    days: dict[str, dict[int, dict[int, int]]] = {}
    for day, layers in raw_days.items():
        if not isinstance(layers, dict):
            continue
        try:
            dt.date.fromisoformat(day)
        except (TypeError, ValueError):
            continue
        for lay, keys in layers.items():
            if not isinstance(keys, dict):
                continue
            for idx, n in keys.items():
                try:
                    li, ki, cnt = int(lay), int(idx), int(n)
                except (TypeError, ValueError):
                    continue
                if li >= 0 and ki >= 0 and cnt > 0:
                    days.setdefault(day, {}).setdefault(li, {})[ki] = cnt
    return days


__all__ = ["HeatmapStore", "default_heatmap_dir", "period_start"]
