"""GSettings access with graceful fallbacks.

Order: installed schema → schema compiled on the fly from the source tree into
``$XDG_CACHE_HOME/keymappforlinux/schemas`` → in-memory settings (not saved).
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from gi.repository import Gio, GLib, GObject

from .resources import APP_ID, source_data_dir

log = logging.getLogger(__name__)

_DEFAULTS: dict[str, object] = {
    "run-in-background": False,
    "smart-layers-on-startup": True,
    "auto-connect": True,
    "window-width": 960,
    "window-height": 660,
    "window-maximized": False,
    "last-page": "layout",
}


class MemorySettings(GObject.Object):
    """Just enough of the Gio.Settings API for this app, kept in memory."""

    __gtype_name__ = "KmlMemorySettings"
    __gsignals__ = {
        "changed": (GObject.SignalFlags.RUN_LAST | GObject.SignalFlags.DETAILED,
                    None, (str,)),
    }

    def __init__(self) -> None:
        super().__init__()
        self._values = dict(_DEFAULTS)

    def _get(self, key: str) -> object:
        return self._values[key]

    def _set(self, key: str, value: object) -> bool:
        if self._values.get(key) != value:
            self._values[key] = value
            self.emit(f"changed::{key}", key)
        return True

    get_boolean = get_int = get_string = _get
    set_boolean = set_int = set_string = _set

    def get_value(self, key: str) -> GLib.Variant:
        value = self._values[key]
        if isinstance(value, bool):
            return GLib.Variant("b", value)
        if isinstance(value, int):
            return GLib.Variant("i", value)
        return GLib.Variant("s", str(value))

    def bind(self, key: str, obj: GObject.Object, prop: str,
             flags: Gio.SettingsBindFlags) -> None:
        obj.set_property(prop, self._values[key])
        obj.connect(f"notify::{prop}", lambda o, _p: self._set(key, o.get_property(prop)))
        self.connect(f"changed::{key}",
                     lambda _s, k: obj.get_property(prop) != self._values[k]
                     and obj.set_property(prop, self._values[k]))

    def create_action(self, key: str) -> Gio.Action:
        action = Gio.SimpleAction.new_stateful(key, None, self.get_value(key))

        def on_change(act: Gio.SimpleAction, value: GLib.Variant) -> None:
            act.set_state(value)
            self._set(key, value.unpack())

        action.connect("change-state", on_change)
        return action


def _compile_dev_schema() -> Gio.SettingsSchemaSource | None:
    data = source_data_dir()
    compiler = shutil.which("glib-compile-schemas")
    if data is None or compiler is None:
        return None
    target = Path(GLib.get_user_cache_dir()) / "keymappforlinux" / "schemas"
    try:
        target.mkdir(parents=True, exist_ok=True)
        shutil.copy2(data / f"{APP_ID}.gschema.xml", target)
        subprocess.run([compiler, "--strict", str(target)], check=True,
                       capture_output=True, timeout=10)
        return Gio.SettingsSchemaSource.new_from_directory(
            str(target), Gio.SettingsSchemaSource.get_default(), False)
    except (OSError, subprocess.SubprocessError, GLib.Error) as exc:
        log.warning("Could not compile the settings schema: %s", exc)
        return None


def create_settings() -> Gio.Settings | MemorySettings:
    default = Gio.SettingsSchemaSource.get_default()
    schema = default.lookup(APP_ID, True) if default is not None else None
    if schema is None:
        source = _compile_dev_schema()
        schema = source.lookup(APP_ID, False) if source is not None else None
    if schema is None:
        log.warning("GSettings schema %s not found; preferences will not be saved", APP_ID)
        return MemorySettings()
    return Gio.Settings.new_full(schema, None, None)
