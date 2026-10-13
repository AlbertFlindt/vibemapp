from __future__ import annotations

import unittest

from vibemapp import _preload


class PreloadTests(unittest.TestCase):
    def test_missing_library_is_not_an_error(self) -> None:
        def loader(name: str):
            raise OSError(f"{name}: cannot open shared object file")
        self.assertIsNone(_preload.preload(loader=loader))

    def test_falls_back_to_unversioned_name(self) -> None:
        tried: list[str] = []

        def loader(name: str):
            tried.append(name)
            if name.endswith(".so.0"):
                raise OSError("no soname")
            return object()
        self.assertEqual(_preload.preload(loader=loader), "libgtk4-layer-shell.so")
        self.assertEqual(tried, list(_preload.LIBRARY_NAMES))

    def test_prefers_soname(self) -> None:
        self.assertEqual(_preload.preload(loader=lambda name: object()),
                         "libgtk4-layer-shell.so.0")

    def test_too_late_detection(self) -> None:
        self.assertTrue(_preload.gtk_already_loaded({"gi.repository.Gtk": object()}))
        self.assertFalse(_preload.gtk_already_loaded({}))

    def test_tests_load_it_before_gtk(self) -> None:
        # tests/__init__.py imports it first, like the app's entry points.
        self.assertFalse(_preload.TOO_LATE)


if __name__ == "__main__":
    unittest.main()
