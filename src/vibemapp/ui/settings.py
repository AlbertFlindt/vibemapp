"""GSettings access with graceful fallbacks.

Order: installed schema → schema compiled on the fly from the source tree into
``$XDG_CACHE_HOME/vibemapp/schemas`` → in-memory settings (not saved).
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from xml.sax.saxutils import escape
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
    "show-in-tray": True,
    "heatmap-recording": False,
    "migrated-from-keymappforlinux": True,
    "obsolete-keys-version": 0,
    "overlay-mode": "selected",
    "overlay-layers": [],
    "overlay-anchor": "bottom",
    "overlay-size": 80,
    "overlay-opacity": 60,
    "overlay-fade-ms": 200,
    "overlay-half-spacing": 0,
    "overlay-monitor": "",
    "overlay-idle-seconds": 0,
}

OLD_PATH = "/io/github/albertflindt/KeyMappForLinux/"
MIGRATION_KEY = "migrated-from-keymappforlinux"


class MemorySettings(GObject.Object):
    """Just enough of the Gio.Settings API for this app, kept in memory."""

    __gtype_name__ = "KmlMemorySettings"
    __gsignals__ = {
        "changed": (GObject.SignalFlags.RUN_LAST | GObject.SignalFlags.DETAILED,
                    None, (str,)),
    }

    def __init__(self) -> None:
        super().__init__()
        self._values = {key: list(value) if isinstance(value, list) else value
                        for key, value in _DEFAULTS.items()}

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
        if isinstance(value, list):
            return GLib.Variant("ai", [int(v) for v in value])
        return GLib.Variant("s", str(value))

    def set_value(self, key: str, value: GLib.Variant) -> bool:
        unpacked = value.unpack()
        return self._set(key, list(unpacked) if isinstance(unpacked, (list, tuple))
                         else unpacked)

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
    target = Path(GLib.get_user_cache_dir()) / "vibemapp" / "schemas"
    try:
        target.mkdir(parents=True, exist_ok=True)
        # The pre-rename schema may have been moved here by the dir migration;
        # glib-compile-schemas --strict would otherwise compile it too.
        (target / "io.github.albertflindt.KeyMappForLinux.gschema.xml").unlink(missing_ok=True)
        shutil.copy2(data / f"{APP_ID}.gschema.xml", target)
        subprocess.run([compiler, "--strict", str(target)], check=True,
                       capture_output=True, timeout=10)
        return Gio.SettingsSchemaSource.new_from_directory(
            str(target), Gio.SettingsSchemaSource.get_default(), False)
    except (OSError, subprocess.SubprocessError, GLib.Error) as exc:
        log.warning("Could not compile the settings schema: %s", exc)
        return None


def create_settings() -> Gio.Settings | MemorySettings:
    if os.environ.get("VIBEMAPP_MEMORY_SETTINGS") == "1":
        # Tests / screenshot runs: never touch the user's dconf.
        return MemorySettings()
    default = Gio.SettingsSchemaSource.get_default()
    schema = default.lookup(APP_ID, True) if default is not None else None
    if schema is None:
        source = _compile_dev_schema()
        schema = source.lookup(APP_ID, False) if source is not None else None
    if schema is None:
        log.warning("GSettings schema %s not found; preferences will not be saved", APP_ID)
        return MemorySettings()
    settings = Gio.Settings.new_full(schema, None, None)
    for step in (migrate_from_keymappforlinux, reset_obsolete_keys):
        try:
            step(settings, schema)
        except Exception:  # noqa: BLE001 — best effort, never block startup
            log.exception("Settings cleanup step %s failed", getattr(step, "__name__", step))
    return settings


LEGACY_SCHEMA_ID = "io.github.albertflindt.Vibemapp.LegacyMigration"


# Keys removed from the schema whose stale values may still sit in the user's
# dconf: name -> (GVariant type, default). Bump OBSOLETE_KEYS_VERSION when
# adding one so the cleanup runs again.
OBSOLETE_KEYS: dict[str, tuple[str, str]] = {
    "last-page": ("s", "''"),        # removed in 0.2.x; the window always opens on Layout
}
OBSOLETE_KEYS_VERSION = 1
OBSOLETE_VERSION_KEY = "obsolete-keys-version"


def _relocatable_copy(schema: Gio.SettingsSchema) -> tuple[Gio.SettingsSchema, str] | None:
    """Same keys and types as ``schema`` but without a fixed path.

    GSettings aborts the process if a schema with a fixed path is opened at
    another path, so the old dconf path is read through this relocatable copy.
    Returns ``(schema, tmpdir)``; the caller removes ``tmpdir``."""
    keys = []
    for name in schema.list_keys():
        key = schema.get_key(name)
        keys.append((name, key.get_value_type().dup_string(),
                     key.get_default_value().print_(False)))
    return _relocatable_schema(keys)


def _relocatable_schema(keys: list[tuple[str, str, str]]
                        ) -> tuple[Gio.SettingsSchema, str] | None:
    """Compile a relocatable schema with ``(name, type, default)`` keys into a
    temp dir. Returns ``(schema, tmpdir)``; the caller removes ``tmpdir``."""
    compiler = shutil.which("glib-compile-schemas")
    if compiler is None:
        return None
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<schemalist>",
             f'<schema id="{LEGACY_SCHEMA_ID}">']
    for name, vtype, default in keys:
        lines.append(f'<key name="{escape(name)}" type="{escape(vtype)}">'
                     f'<default>{escape(default)}</default></key>')
    lines += ["</schema>", "</schemalist>"]
    tmp = tempfile.mkdtemp(prefix="vibemapp-migrate-")
    try:
        Path(tmp, f"{LEGACY_SCHEMA_ID}.gschema.xml").write_text("\n".join(lines))
        subprocess.run([compiler, tmp], check=True, capture_output=True, timeout=10)
        source = Gio.SettingsSchemaSource.new_from_directory(tmp, None, False)
        legacy = source.lookup(LEGACY_SCHEMA_ID, False)
    except (OSError, subprocess.SubprocessError, GLib.Error) as exc:
        log.warning("Could not prepare the settings migration: %s", exc)
        shutil.rmtree(tmp, ignore_errors=True)
        return None
    if legacy is None:
        shutil.rmtree(tmp, ignore_errors=True)
        return None
    return legacy, tmp


def migrate_from_keymappforlinux(settings: Gio.Settings, schema: Gio.SettingsSchema,
                                 backend: Gio.SettingsBackend | None = None) -> int:
    """Copy values the user set under the old app's dconf path, once (SPEC 6.1).

    Only keys that were explicitly set there (``get_user_value``) and aren't
    already set in the new path are copied. Returns how many were copied."""
    if settings.get_boolean(MIGRATION_KEY):
        return 0
    copied = 0
    prepared = _relocatable_copy(schema)
    if prepared is not None:
        legacy, tmp = prepared
        try:
            old = Gio.Settings.new_full(legacy, backend, OLD_PATH)
            for key in schema.list_keys():
                if key == MIGRATION_KEY:
                    continue
                value = old.get_user_value(key)
                if value is not None and settings.get_user_value(key) is None \
                        and schema.get_key(key).range_check(value):
                    settings.set_value(key, value)
                    copied += 1
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    settings.set_boolean(MIGRATION_KEY, True)
    if copied:
        log.info("Migrated %d settings from KeyMapp for Linux", copied)
    return copied


def reset_obsolete_keys(settings: Gio.Settings, schema: Gio.SettingsSchema,
                        backend: Gio.SettingsBackend | None = None) -> list[str]:
    """Reset values of keys removed from the schema (``OBSOLETE_KEYS``), once.

    GSettings can't address keys outside a schema, so a relocatable schema
    holding just the obsolete keys is opened at our own path and each key is
    reset (which deletes it from dconf). Keys still in ``schema`` are never
    touched. Needs no ``dconf`` CLI. Returns the keys that had a value."""
    if settings.get_int(OBSOLETE_VERSION_KEY) >= OBSOLETE_KEYS_VERSION:
        return []
    current = set(schema.list_keys())
    keys = [(name, vtype, default) for name, (vtype, default) in OBSOLETE_KEYS.items()
            if name not in current]
    cleared: list[str] = []
    if keys:
        prepared = _relocatable_schema(keys)
        if prepared is None:
            return []          # no compiler: try again next start
        obsolete, tmp = prepared
        try:
            stale = Gio.Settings.new_full(obsolete, backend, schema.get_path())
            for name, _vtype, _default in keys:
                if stale.get_user_value(name) is not None:
                    cleared.append(name)
                # Reset even if the stored type differs (get_user_value is None then).
                stale.reset(name)
            Gio.Settings.sync()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    settings.set_int(OBSOLETE_VERSION_KEY, OBSOLETE_KEYS_VERSION)
    if cleared:
        log.info("Removed obsolete settings: %s", ", ".join(cleared))
    return cleared
