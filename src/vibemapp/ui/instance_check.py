"""Notice when a dev run and the installed app both drive the real keyboard.

A dev run (``run-dev.sh``, app ID ``….Vibemapp.Devel``) can run beside the
installed app. With the real keyboard both would run Smart Layers and switch
layers, so the dev window warns while the installed app's bus name
(``io.github.albertflindt.Vibemapp``) has an owner. ``Gio.bus_watch_name``
checks the current owner first and then follows NameOwnerChanged, so the
warning appears and disappears live.

The installed app exports its ``app.quit`` action over ``org.gtk.Actions`` at
its object path; activating it quits that instance cleanly (its shutdown
returns the keyboard to the base layer).
"""

from __future__ import annotations

import logging
from typing import Callable

from gi.repository import Gio, GLib, GObject

from ..appid import BASE_APP_ID

log = logging.getLogger(__name__)

INSTALLED_NAME = BASE_APP_ID
INSTALLED_PATH = "/" + BASE_APP_ID.replace(".", "/")
ACTIONS_IFACE = "org.gtk.Actions"
QUIT_ACTION = "quit"
CALL_TIMEOUT_MS = 5000


def should_watch(devel: bool, using_mock: bool) -> bool:
    """Only a dev run on the real keyboard can clash with the installed app."""
    return devel and not using_mock


class InstalledAppWatcher(GObject.Object):
    """``running`` is True while the installed app owns its bus name;
    ``can-quit`` is True when it exports a ``quit`` action."""

    __gtype_name__ = "KmlInstalledAppWatcher"

    running = GObject.Property(type=bool, default=False)
    can_quit = GObject.Property(type=bool, default=False)

    def __init__(self, connection: Gio.DBusConnection | None = None,
                 name: str = INSTALLED_NAME, path: str = INSTALLED_PATH) -> None:
        super().__init__()
        self._connection = connection
        self._name = name
        self._path = path
        self._watch_id = 0
        self._bus: Gio.DBusConnection | None = None
        self._cancellable: Gio.Cancellable | None = None

    def start(self) -> None:
        if self._watch_id:
            return
        flags = Gio.BusNameWatcherFlags.NONE
        try:
            if self._connection is not None:
                self._watch_id = Gio.bus_watch_name_on_connection(
                    self._connection, self._name, flags, self._on_appeared, self._on_vanished)
            else:
                self._watch_id = Gio.bus_watch_name(
                    Gio.BusType.SESSION, self._name, flags,
                    self._on_appeared, self._on_vanished)
        except GLib.Error as exc:   # no session bus: nothing to warn about
            log.debug("Can't watch %s: %s", self._name, exc)

    def stop(self) -> None:
        if self._watch_id:
            Gio.bus_unwatch_name(self._watch_id)
            self._watch_id = 0
        if self._cancellable is not None:
            self._cancellable.cancel()
            self._cancellable = None
        self._bus = None

    # --- watching --------------------------------------------------------------

    def _on_appeared(self, connection: Gio.DBusConnection, name: str, owner: str) -> None:
        self._bus = connection
        if not self.running:
            log.warning("The installed Vibemapp (%s) is also running; both will switch "
                        "keyboard layers", name)
        self.running = True
        self._query_quit_action(connection)

    def _on_vanished(self, connection: Gio.DBusConnection | None, name: str) -> None:
        if self.running:
            log.info("The installed Vibemapp (%s) is no longer running", name)
        self._bus = None
        self.running = False
        self.can_quit = False

    def _query_quit_action(self, connection: Gio.DBusConnection) -> None:
        if self._cancellable is not None:
            self._cancellable.cancel()
        self._cancellable = cancellable = Gio.Cancellable()

        def done(conn: Gio.DBusConnection, result: Gio.AsyncResult) -> None:
            try:
                (actions,) = conn.call_finish(result).unpack()
            except GLib.Error as exc:
                if not exc.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED):
                    log.debug("Can't list the installed app's actions: %s", exc)
                    self.can_quit = False
                return
            self.can_quit = self.running and QUIT_ACTION in actions

        connection.call(self._name, self._path, ACTIONS_IFACE, "List", None,
                        GLib.VariantType.new("(as)"), Gio.DBusCallFlags.NONE,
                        CALL_TIMEOUT_MS, cancellable, done)

    # --- quitting --------------------------------------------------------------

    def request_quit(self, on_done: Callable[[GLib.Error | None], None] | None = None) -> bool:
        """Ask the installed app to quit. Returns False if it isn't running."""
        bus = self._bus
        if bus is None or not self.running:
            return False

        def done(conn: Gio.DBusConnection, result: Gio.AsyncResult) -> None:
            error = None
            try:
                conn.call_finish(result)
            except GLib.Error as exc:
                log.warning("Couldn't quit the installed Vibemapp: %s", exc.message)
                error = exc
            if on_done is not None:
                on_done(error)

        bus.call(self._name, self._path, ACTIONS_IFACE, "Activate",
                 GLib.Variant("(sava{sv})", (QUIT_ACTION, [], {})), None,
                 Gio.DBusCallFlags.NONE, CALL_TIMEOUT_MS, None, done)
        return True


__all__ = ["INSTALLED_NAME", "INSTALLED_PATH", "InstalledAppWatcher", "should_watch"]
