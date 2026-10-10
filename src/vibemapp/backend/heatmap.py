"""Heatmap key-press counts (SPEC 6.4). No GTK.

Storage: one file per Oryx layout, ``$XDG_DATA_HOME/vibemapp/heatmap/<layout_id>.json``::

    {"version": 1, "layout_id": "Bre9W",
     "days": {"2026-10-10": {"<layer>": {"<key index>": count}}}}

Privacy: only per (local calendar day, layer, key index) counts are kept.
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

from ..models import HeatmapPeriod

log = logging.getLogger(__name__)

FILE_VERSION = 1
WEEK_DAYS = 7

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
        self._days = self._load(layout_id) if layout_id else {}
        self._dirty = False

    # -- counting --

    def record(self, layer: int, index: int) -> None:
        if self._layout_id is None or layer < 0 or index < 0:
            return
        day = self._clock().isoformat()
        keys = self._days.setdefault(day, {}).setdefault(layer, {})
        keys[index] = keys.get(index, 0) + 1
        self._dirty = True

    def counts(self, period: HeatmapPeriod, layer: int | None = None) -> dict[int, int]:
        if self._layout_id is None:
            return {}
        start = period_start(HeatmapPeriod(period), self._clock())
        out: dict[int, int] = {}
        for day, layers in self._days.items():
            if start is not None:
                try:
                    if dt.date.fromisoformat(day) < start:
                        continue
                except ValueError:
                    continue
            for lay, keys in layers.items():
                if layer is not None and lay != layer:
                    continue
                for idx, n in keys.items():
                    out[idx] = out.get(idx, 0) + n
        return out

    def reset(self) -> None:
        """Delete every count for the current layout, in memory and on disk."""
        if self._layout_id is None:
            return
        self._days = {}
        self._dirty = False
        try:
            os.unlink(self.path_for(self._layout_id))
        except FileNotFoundError:
            pass

    # -- persistence --

    def to_json(self) -> dict[str, Any]:
        return {
            "version": FILE_VERSION,
            "layout_id": self._layout_id,
            "days": {day: {str(l): {str(i): n for i, n in sorted(keys.items())}
                           for l, keys in sorted(layers.items())}
                     for day, layers in sorted(self._days.items())},
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

    def _load(self, layout_id: str) -> dict[str, dict[int, dict[int, int]]]:
        path = self.path_for(layout_id)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            log.warning("Can't read heatmap %s: %s", path, exc)
            try:
                os.replace(path, path + ".corrupt")
            except OSError:
                pass
            return {}
        days: dict[str, dict[int, dict[int, int]]] = {}
        raw_days = data.get("days") if isinstance(data, dict) else None
        if not isinstance(raw_days, dict):
            return {}
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
