from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from gi.repository import Gio

from vibemapp.ui import settings as settings_mod
from vibemapp.ui.resources import APP_ID

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(shutil.which("glib-compile-schemas"), "needs glib-compile-schemas")
class MigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        shutil.copy(ROOT / "data" / f"{APP_ID}.gschema.xml", self.tmp.name)
        subprocess.run(["glib-compile-schemas", self.tmp.name], check=True)
        source = Gio.SettingsSchemaSource.new_from_directory(self.tmp.name, None, False)
        self.schema = source.lookup(APP_ID, False)
        self.backend = Gio.memory_settings_backend_new()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def new(self) -> Gio.Settings:
        return Gio.Settings.new_full(self.schema, self.backend, None)

    def old(self) -> Gio.Settings:
        # The fixed-path schema can't be opened at the old path (GSettings
        # aborts), so write the "old app's" values through a relocatable copy.
        legacy, tmp = settings_mod._relocatable_copy(self.schema)
        self.addCleanup(shutil.rmtree, tmp, True)
        return Gio.Settings.new_full(legacy, self.backend, settings_mod.OLD_PATH)

    def test_copies_user_values_once(self) -> None:
        old = self.old()
        old.set_boolean("run-in-background", True)
        old.set_boolean("auto-connect", False)
        old.set_int("window-width", 1234)
        current = self.new()
        copied = settings_mod.migrate_from_keymappforlinux(current, self.schema, self.backend)
        self.assertEqual(copied, 3)
        self.assertTrue(current.get_boolean("run-in-background"))
        self.assertFalse(current.get_boolean("auto-connect"))
        self.assertEqual(current.get_int("window-width"), 1234)
        self.assertTrue(current.get_boolean(settings_mod.MIGRATION_KEY))
        # Defaults that were never set are not copied.
        self.assertIsNone(current.get_user_value("smart-layers-on-startup"))

        old.set_boolean("run-in-background", False)
        self.assertEqual(
            settings_mod.migrate_from_keymappforlinux(current, self.schema, self.backend), 0)
        self.assertTrue(current.get_boolean("run-in-background"))

    def test_existing_new_values_win(self) -> None:
        self.old().set_boolean("auto-connect", False)
        current = self.new()
        current.set_boolean("auto-connect", True)
        settings_mod.migrate_from_keymappforlinux(current, self.schema, self.backend)
        self.assertTrue(current.get_boolean("auto-connect"))


@unittest.skipUnless(shutil.which("glib-compile-schemas"), "needs glib-compile-schemas")
class ObsoleteKeysTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        shutil.copy(ROOT / "data" / f"{APP_ID}.gschema.xml", self.tmp.name)
        subprocess.run(["glib-compile-schemas", self.tmp.name], check=True)
        source = Gio.SettingsSchemaSource.new_from_directory(self.tmp.name, None, False)
        self.schema = source.lookup(APP_ID, False)
        self.backend = Gio.memory_settings_backend_new()
        self.settings = Gio.Settings.new_full(self.schema, self.backend, None)

    def stale(self) -> Gio.Settings:
        """The obsolete keys at our own path, as an older version wrote them."""
        keys = [(n, t, d) for n, (t, d) in settings_mod.OBSOLETE_KEYS.items()]
        schema, tmp = settings_mod._relocatable_schema(keys)
        self.addCleanup(shutil.rmtree, tmp, True)
        return Gio.Settings.new_full(schema, self.backend, self.schema.get_path())

    def test_obsolete_keys_not_in_schema(self) -> None:
        self.assertIn("last-page", settings_mod.OBSOLETE_KEYS)
        self.assertFalse(set(settings_mod.OBSOLETE_KEYS) & set(self.schema.list_keys()))

    def test_resets_stale_value_once_and_keeps_schema_keys(self) -> None:
        self.stale().set_string("last-page", "heatmap")
        self.settings.set_boolean("run-in-background", True)
        self.settings.set_int("window-width", 1234)
        cleared = settings_mod.reset_obsolete_keys(self.settings, self.schema, self.backend)
        self.assertEqual(cleared, ["last-page"])
        self.assertIsNone(self.stale().get_user_value("last-page"))
        self.assertTrue(self.settings.get_boolean("run-in-background"))
        self.assertEqual(self.settings.get_int("window-width"), 1234)
        self.assertEqual(self.settings.get_int(settings_mod.OBSOLETE_VERSION_KEY),
                         settings_mod.OBSOLETE_KEYS_VERSION)
        # One-time: a value written later (e.g. by an old build) is left alone.
        self.stale().set_string("last-page", "layout")
        self.assertEqual(
            settings_mod.reset_obsolete_keys(self.settings, self.schema, self.backend), [])
        self.assertIsNotNone(self.stale().get_user_value("last-page"))

    def test_nothing_stale(self) -> None:
        self.assertEqual(
            settings_mod.reset_obsolete_keys(self.settings, self.schema, self.backend), [])
        self.assertEqual(self.settings.get_int(settings_mod.OBSOLETE_VERSION_KEY),
                         settings_mod.OBSOLETE_KEYS_VERSION)

    def test_never_touches_keys_in_schema(self) -> None:
        self.settings.set_boolean("auto-connect", False)
        with mock.patch.dict(settings_mod.OBSOLETE_KEYS, {"auto-connect": ("b", "true")}):
            settings_mod.reset_obsolete_keys(self.settings, self.schema, self.backend)
        self.assertFalse(self.settings.get_boolean("auto-connect"))

    def test_no_compiler_is_harmless_and_retries(self) -> None:
        self.stale().set_string("last-page", "heatmap")
        with mock.patch.object(settings_mod.shutil, "which", return_value=None):
            self.assertEqual(
                settings_mod.reset_obsolete_keys(self.settings, self.schema, self.backend), [])
        self.assertEqual(self.settings.get_int(settings_mod.OBSOLETE_VERSION_KEY), 0)
        self.assertEqual(
            settings_mod.reset_obsolete_keys(self.settings, self.schema, self.backend),
            ["last-page"])

    def test_compiler_failure_is_harmless(self) -> None:
        with mock.patch.object(settings_mod.subprocess, "run",
                               side_effect=subprocess.TimeoutExpired("glib-compile-schemas", 10)), \
                self.assertLogs(settings_mod.log, "WARNING"):
            self.assertEqual(
                settings_mod.reset_obsolete_keys(self.settings, self.schema, self.backend), [])

    def test_memory_settings_skip_dconf(self) -> None:
        with mock.patch.dict("os.environ", {"VIBEMAPP_MEMORY_SETTINGS": "1"}), \
                mock.patch.object(settings_mod, "reset_obsolete_keys") as reset:
            result = settings_mod.create_settings()
        self.assertIsInstance(result, settings_mod.MemorySettings)
        self.assertEqual(result.get_int(settings_mod.OBSOLETE_VERSION_KEY), 0)
        reset.assert_not_called()

    def test_create_settings_survives_cleanup_errors(self) -> None:
        source = Gio.SettingsSchemaSource.new_from_directory(self.tmp.name, None, False)
        with mock.patch.dict("os.environ", {"VIBEMAPP_MEMORY_SETTINGS": ""}), \
                mock.patch.object(settings_mod.Gio.SettingsSchemaSource, "get_default",
                                  return_value=source), \
                mock.patch.object(settings_mod.Gio.Settings, "new_full",
                                  return_value=self.settings), \
                mock.patch.object(settings_mod, "migrate_from_keymappforlinux",
                                  side_effect=RuntimeError("boom")), \
                mock.patch.object(settings_mod, "reset_obsolete_keys",
                                  side_effect=OSError("no dconf")) as reset, \
                self.assertLogs(settings_mod.log, "ERROR"):
            self.assertIs(settings_mod.create_settings(), self.settings)
        reset.assert_called_once()


if __name__ == "__main__":
    unittest.main()
