"""Application ID selection for installed vs. dev (run-dev.sh) runs."""

from __future__ import annotations

import os
import unittest
from unittest import mock

from vibemapp.appid import (BASE_APP_ID, DEVEL_APP_ID, DEVEL_ENV, application_id, is_devel,
                            is_own_app_class)


class AppIdTest(unittest.TestCase):
    def test_installed_by_default(self) -> None:
        self.assertEqual(application_id({}), "io.github.albertflindt.Vibemapp")
        self.assertFalse(is_devel({}))

    def test_devel_env(self) -> None:
        env = {DEVEL_ENV: "1"}
        self.assertTrue(is_devel(env))
        self.assertEqual(application_id(env), "io.github.albertflindt.Vibemapp.Devel")
        self.assertEqual(DEVEL_ENV, "VIBEMAPP_DEVEL")

    def test_only_exact_one_enables_devel(self) -> None:
        for value in ("", "0", "true", "yes"):
            with self.subTest(value=value):
                self.assertEqual(application_id({DEVEL_ENV: value}), BASE_APP_ID)

    def test_ids_are_valid_and_distinct(self) -> None:
        from gi.repository import Gio
        self.assertNotEqual(BASE_APP_ID, DEVEL_APP_ID)
        for app_id in (BASE_APP_ID, DEVEL_APP_ID):
            self.assertTrue(Gio.Application.id_is_valid(app_id), app_id)

    def test_own_app_classes(self) -> None:
        self.assertTrue(is_own_app_class(BASE_APP_ID))
        self.assertTrue(is_own_app_class(DEVEL_APP_ID))
        for other in ("firefox", "", None, BASE_APP_ID + ".Other"):
            self.assertFalse(is_own_app_class(other))

    def test_resources_keep_schema_and_icon_id(self) -> None:
        # The schema has a fixed path and the icon is installed under the base
        # ID; neither may follow the dev application ID.
        from vibemapp.ui import resources
        self.assertEqual(resources.APP_ID, BASE_APP_ID)

    def test_title_marks_dev_runs(self) -> None:
        from vibemapp.ui.resources import app_title
        with mock.patch.dict(os.environ, {DEVEL_ENV: "1"}):
            self.assertEqual(app_title(), "Vibemapp (Development)")
        with mock.patch.dict(os.environ):
            os.environ.pop(DEVEL_ENV, None)
            self.assertEqual(app_title(), "Vibemapp")


if __name__ == "__main__":
    unittest.main()
