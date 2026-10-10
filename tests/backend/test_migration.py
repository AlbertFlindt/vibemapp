from __future__ import annotations

import os
import tempfile
import unittest

from vibemapp.backend.migration import migrate_dir, migrate_legacy_dirs


def write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def read(path: str) -> str:
    with open(path) as f:
        return f.read()


class MigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = self.tmp.name
        self.env = {"HOME": self.home,
                    "XDG_CONFIG_HOME": os.path.join(self.home, "cfg"),
                    "XDG_CACHE_HOME": os.path.join(self.home, "cache")}

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def p(self, *parts: str) -> str:
        return os.path.join(self.home, *parts)

    def test_moves_both_dirs_when_new_missing(self) -> None:
        write(self.p("cfg", "keymappforlinux", "smart-layers.json"), "rules")
        write(self.p("cache", "keymappforlinux", "layouts", "a-b.json"), "layout")
        res = migrate_legacy_dirs(self.env)
        self.assertEqual(res.errors, [])
        self.assertEqual(read(self.p("cfg", "vibemapp", "smart-layers.json")), "rules")
        self.assertEqual(read(self.p("cache", "vibemapp", "layouts", "a-b.json")), "layout")
        self.assertFalse(os.path.exists(self.p("cfg", "keymappforlinux")))
        self.assertFalse(os.path.exists(self.p("cache", "keymappforlinux")))
        # idempotent
        self.assertEqual(migrate_legacy_dirs(self.env).moved, [])

    def test_never_overwrites_existing_new_dir(self) -> None:
        write(self.p("cfg", "keymappforlinux", "smart-layers.json"), "old")
        write(self.p("cfg", "vibemapp", "smart-layers.json"), "new")
        # Cache: UI already created vibemapp/schemas; old layouts get merged in.
        write(self.p("cache", "keymappforlinux", "layouts", "x.json"), "L")
        write(self.p("cache", "keymappforlinux", "schemas", "old.compiled"), "S-old")
        write(self.p("cache", "vibemapp", "schemas", "gschemas.compiled"), "S-new")
        res = migrate_legacy_dirs(self.env)
        self.assertEqual(res.errors, [])
        self.assertEqual(read(self.p("cfg", "vibemapp", "smart-layers.json")), "new")
        self.assertEqual(read(self.p("cfg", "keymappforlinux", "smart-layers.json")), "old")
        self.assertEqual(read(self.p("cache", "vibemapp", "layouts", "x.json")), "L")
        self.assertEqual(read(self.p("cache", "vibemapp", "schemas", "gschemas.compiled")), "S-new")
        self.assertFalse(os.path.exists(self.p("cache", "vibemapp", "schemas", "old.compiled")))
        self.assertTrue(os.path.exists(self.p("cache", "keymappforlinux", "schemas", "old.compiled")))

    def test_nothing_to_do_and_defaults(self) -> None:
        res = migrate_legacy_dirs({"HOME": self.home})
        self.assertEqual((res.moved, res.errors), ([], []))
        self.assertFalse(os.path.exists(self.p(".config", "vibemapp")))

    def test_errors_are_not_fatal(self) -> None:
        old = self.p("cfg", "keymappforlinux")
        write(os.path.join(old, "f"), "x")
        write(self.p("cfg", "vibemapp"), "a file, not a dir")
        res = migrate_dir(old, self.p("cfg", "vibemapp"))
        self.assertEqual(res.moved, [])
        self.assertEqual(res.skipped, [old])
        self.assertTrue(os.path.exists(os.path.join(old, "f")))

    @unittest.skipIf(os.geteuid() == 0, "root bypasses permissions")
    def test_unreadable_old_dir_is_reported_not_raised(self) -> None:
        old = self.p("cfg", "keymappforlinux")
        write(os.path.join(old, "f"), "x")
        os.makedirs(self.p("cfg", "vibemapp"))
        os.chmod(old, 0)
        try:
            res = migrate_legacy_dirs(self.env)
            self.assertTrue(res.errors)
        finally:
            os.chmod(old, 0o755)


if __name__ == "__main__":
    unittest.main()
