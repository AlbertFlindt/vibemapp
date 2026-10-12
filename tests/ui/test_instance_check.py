"""Installed-app detection for dev runs, against a private dbus-daemon."""

from __future__ import annotations

import shutil
import subprocess
import unittest

from gi.repository import Gio, GLib

from tests.tray.test_tray import new_connection, spin
from vibemapp.ui import instance_check
from vibemapp.ui.instance_check import (INSTALLED_NAME, INSTALLED_PATH, InstalledAppWatcher,
                                        should_watch)


class DecisionTests(unittest.TestCase):
    def test_only_dev_runs_on_the_real_keyboard(self) -> None:
        self.assertTrue(should_watch(devel=True, using_mock=False))
        self.assertFalse(should_watch(devel=True, using_mock=True))
        self.assertFalse(should_watch(devel=False, using_mock=False))
        self.assertFalse(should_watch(devel=False, using_mock=True))

    def test_watches_the_installed_name_and_path(self) -> None:
        self.assertEqual(INSTALLED_NAME, "io.github.albertflindt.Vibemapp")
        self.assertEqual(INSTALLED_PATH, "/io/github/albertflindt/Vibemapp")

    def test_quit_without_installed_app_is_a_no_op(self) -> None:
        self.assertFalse(InstalledAppWatcher().request_quit())


class FakeInstalledApp:
    """Owns the installed app's name and exports ``org.gtk.Actions`` like
    GApplication does."""

    def __init__(self, address: str, actions: tuple[str, ...] = ("quit", "about")) -> None:
        self.conn = new_connection(address)
        self.activated: list[str] = []
        group = Gio.SimpleActionGroup()
        for name in actions:
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda a, _p: self.activated.append(a.get_name()))
            group.add_action(action)
        self.export_id = self.conn.export_action_group(INSTALLED_PATH, group)
        self.owned = False
        self.own_id = Gio.bus_own_name_on_connection(
            self.conn, INSTALLED_NAME, Gio.BusNameOwnerFlags.NONE,
            lambda *_a: setattr(self, "owned", True), None)

    def stop(self) -> None:
        if not self.own_id:
            return
        Gio.bus_unown_name(self.own_id)
        self.own_id = 0
        self.conn.unexport_action_group(self.export_id)
        self.conn.close_sync(None)


@unittest.skipUnless(shutil.which("dbus-daemon"), "dbus-daemon not installed")
class WatcherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.daemon = subprocess.Popen(
            ["dbus-daemon", "--session", "--nofork", "--print-address=1"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        cls.address = cls.daemon.stdout.readline().strip()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.daemon.terminate()
        cls.daemon.wait(timeout=5)
        cls.daemon.stdout.close()

    def setUp(self) -> None:
        self.conn = new_connection(self.address)
        self.watcher = InstalledAppWatcher(connection=self.conn)
        self.apps: list[FakeInstalledApp] = []

    def tearDown(self) -> None:
        self.watcher.stop()
        for app in self.apps:
            app.stop()
        spin(lambda: False, 0.05)
        self.conn.close_sync(None)

    def start_app(self, **kwargs) -> FakeInstalledApp:
        app = FakeInstalledApp(self.address, **kwargs)
        self.apps.append(app)
        self.assertTrue(spin(lambda: app.owned))
        return app

    def test_not_running(self) -> None:
        self.watcher.start()
        spin(lambda: False, 0.2)
        self.assertFalse(self.watcher.props.running)
        self.assertFalse(self.watcher.props.can_quit)
        self.assertFalse(self.watcher.request_quit())

    def test_already_running_then_quits(self) -> None:
        app = self.start_app()
        with self.assertLogs(instance_check.log, "WARNING"):
            self.watcher.start()
            self.assertTrue(spin(lambda: self.watcher.props.running))
        self.assertTrue(spin(lambda: self.watcher.props.can_quit))
        app.stop()
        self.assertTrue(spin(lambda: not self.watcher.props.running))
        self.assertFalse(self.watcher.props.can_quit)

    def test_appears_live(self) -> None:
        self.watcher.start()
        spin(lambda: False, 0.1)
        self.assertFalse(self.watcher.props.running)
        with self.assertLogs(instance_check.log, "WARNING"):
            self.start_app()
            self.assertTrue(spin(lambda: self.watcher.props.running))

    def test_request_quit_activates_quit_action(self) -> None:
        app = self.start_app()
        with self.assertLogs(instance_check.log, "WARNING"):
            self.watcher.start()
            self.assertTrue(spin(lambda: self.watcher.props.can_quit))
        results: list = []
        self.assertTrue(self.watcher.request_quit(results.append))
        self.assertTrue(spin(lambda: bool(results)))
        self.assertEqual(results, [None])
        self.assertEqual(app.activated, ["quit"])

    def test_no_quit_action_means_no_button(self) -> None:
        self.start_app(actions=("about",))
        with self.assertLogs(instance_check.log, "WARNING"):
            self.watcher.start()
            self.assertTrue(spin(lambda: self.watcher.props.running))
        spin(lambda: False, 0.2)
        self.assertFalse(self.watcher.props.can_quit)
        results: list = []
        with self.assertLogs(instance_check.log, "WARNING"):
            self.assertTrue(self.watcher.request_quit(results.append))
            self.assertTrue(spin(lambda: bool(results)))
        self.assertIsInstance(results[0], GLib.Error)


if __name__ == "__main__":
    unittest.main()
