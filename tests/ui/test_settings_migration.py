from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
