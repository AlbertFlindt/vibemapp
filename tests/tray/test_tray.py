"""TrayIcon against a fake StatusNotifierWatcher on a private D-Bus (Gio.TestDBus)."""

from __future__ import annotations

import shutil
import subprocess
import time
import unittest

from gi.repository import Gio, GLib

from vibemapp.tray import TrayIcon, TrayMenuItem
from vibemapp.tray.sni import ITEM_PATH, MENU_PATH, WATCHER_NAME

WATCHER_XML = """
<node>
  <interface name="org.kde.StatusNotifierWatcher">
    <method name="RegisterStatusNotifierItem"><arg name="service" type="s" direction="in"/></method>
    <method name="RegisterStatusNotifierHost"><arg name="service" type="s" direction="in"/></method>
    <property name="RegisteredStatusNotifierItems" type="as" access="read"/>
    <property name="IsStatusNotifierHostRegistered" type="b" access="read"/>
    <property name="ProtocolVersion" type="i" access="read"/>
    <signal name="StatusNotifierItemRegistered"><arg type="s"/></signal>
  </interface>
</node>
"""
WATCHER_INFO = Gio.DBusNodeInfo.new_for_xml(WATCHER_XML).interfaces[0]


def spin(until, timeout: float = 2.0) -> bool:
    ctx = GLib.MainContext.default()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        while ctx.pending():
            ctx.iteration(False)
        if until():
            return True
        time.sleep(0.005)
    return until()


def new_connection(address: str) -> Gio.DBusConnection:
    return Gio.DBusConnection.new_for_address_sync(
        address, Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
        | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)


class FakeWatcher:
    def __init__(self, address: str) -> None:
        self.conn = new_connection(address)
        self.items: list[tuple[str, str]] = []      # (sender, service)
        self.reg = self.conn.register_object("/StatusNotifierWatcher", WATCHER_INFO,
                                             self._call, self._get, None)
        self.owned = False
        self.own_id = Gio.bus_own_name_on_connection(
            self.conn, WATCHER_NAME, Gio.BusNameOwnerFlags.NONE,
            lambda *_a: setattr(self, "owned", True), None)

    def _call(self, _c, sender, _p, _i, method, params, invocation) -> None:
        if method == "RegisterStatusNotifierItem":
            self.items.append((sender, params.unpack()[0]))
        invocation.return_value(None)

    def _get(self, _c, _s, _p, _i, name):
        return {"RegisteredStatusNotifierItems": GLib.Variant("as", [s for _x, s in self.items]),
                "IsStatusNotifierHostRegistered": GLib.Variant("b", True),
                "ProtocolVersion": GLib.Variant("i", 0)}.get(name)

    def stop(self) -> None:
        if self.own_id == 0:
            return
        Gio.bus_unown_name(self.own_id)
        self.own_id = 0
        self.conn.unregister_object(self.reg)
        self.conn.close_sync(None)


@unittest.skipUnless(shutil.which("dbus-daemon"), "dbus-daemon not installed")
class TrayTests(unittest.TestCase):
    """Private ``dbus-daemon --session`` (not Gio.TestDBus: its down() waits ~30 s
    when other tests in the process touched the session-bus singleton)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.daemon = subprocess.Popen(
            ["dbus-daemon", "--session", "--nofork", "--print-address=1"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        cls.bus_address = cls.daemon.stdout.readline().strip()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.daemon.terminate()
        cls.daemon.wait(timeout=5)
        cls.daemon.stdout.close()

    def setUp(self) -> None:
        self.address = self.bus_address
        self.watcher = FakeWatcher(self.address)
        self.assertTrue(spin(lambda: self.watcher.owned))
        self.tray_conn = new_connection(self.address)
        self.client = new_connection(self.address)
        self.tray = TrayIcon(connection=self.tray_conn, icon_theme_path="/tmp/icons")
        self.activations = 0
        self.items: list[str] = []
        self.tray.connect("activate", lambda _t: self._activated())
        self.tray.connect("menu-item-activated", lambda _t, i: self.items.append(i))

    def _activated(self) -> None:
        self.activations += 1

    def tearDown(self) -> None:
        self.tray.close()
        spin(lambda: False, 0.05)
        for c in (self.tray_conn, self.client):
            c.close_sync(None)
        try:
            self.watcher.stop()
        except GLib.Error:
            pass

    def call(self, path: str, iface: str, method: str, params: GLib.Variant | None,
             reply: str | None = None):
        # Async + spin: the tray dispatches on this same main context.
        box: list = []
        self.client.call(self.tray.bus_name, path, iface, method, params,
                         GLib.VariantType.new(reply) if reply else None,
                         Gio.DBusCallFlags.NONE, 2000, None,
                         lambda conn, res: box.append(self._finish(conn, res)))
        self.assertTrue(spin(lambda: bool(box), 3.0), method)
        result = box[0]
        if isinstance(result, GLib.Error):
            raise result
        return result.unpack() if result is not None else None

    @staticmethod
    def _finish(conn: Gio.DBusConnection, res: Gio.AsyncResult):
        try:
            return conn.call_finish(res)
        except GLib.Error as exc:
            return exc

    def get(self, path: str, iface: str, prop: str):
        return self.call(path, "org.freedesktop.DBus.Properties", "Get",
                         GLib.Variant("(ss)", (iface, prop)), "(v)")[0]

    def test_registers_and_exposes_item(self) -> None:
        self.assertTrue(spin(lambda: self.tray.props.available))
        self.assertEqual(self.watcher.items[0][1], self.tray.bus_name)
        self.assertTrue(self.tray.bus_name.startswith("org.kde.StatusNotifierItem-"))
        iface = "org.kde.StatusNotifierItem"
        self.assertEqual(self.get(ITEM_PATH, iface, "Id"), "vibemapp")
        self.assertEqual(self.get(ITEM_PATH, iface, "Menu"), MENU_PATH)
        self.assertEqual(self.get(ITEM_PATH, iface, "IconThemePath"), "/tmp/icons")
        self.assertEqual(self.get(ITEM_PATH, iface, "IconName"), "io.github.albertflindt.Vibemapp")
        self.tray.set_tooltip("Voyager", "Layer: Main")
        self.tray.set_icon_name("x-symbolic")
        self.tray.set_status("Passive")
        self.assertEqual(self.get(ITEM_PATH, iface, "ToolTip"), ("", [], "Voyager", "Layer: Main"))
        self.assertEqual(self.get(ITEM_PATH, iface, "IconName"), "x-symbolic")
        self.assertEqual(self.get(ITEM_PATH, iface, "Status"), "Passive")
        with self.assertRaises(ValueError):
            self.tray.set_status("Bogus")

    def test_activate_signals(self) -> None:
        self.assertTrue(spin(lambda: self.tray.props.available))
        self.call(ITEM_PATH, "org.kde.StatusNotifierItem", "Activate", GLib.Variant("(ii)", (1, 2)))
        self.call(ITEM_PATH, "org.kde.StatusNotifierItem", "SecondaryActivate",
                  GLib.Variant("(ii)", (1, 2)))
        self.assertTrue(spin(lambda: self.activations == 2))

    def test_dbusmenu(self) -> None:
        updates: list[tuple] = []
        self.client.signal_subscribe(self.tray.bus_name, "com.canonical.dbusmenu", "LayoutUpdated",
                                     MENU_PATH, None, Gio.DBusSignalFlags.NONE,
                                     lambda *a: updates.append(a[5].unpack()))
        self.assertTrue(spin(lambda: self.tray.props.available))
        self.tray.set_menu([
            TrayMenuItem("show", "Show Vibemapp"),
            TrayMenuItem("smart", "Smart_Layers", toggle=True),
            TrayMenuItem("layer", "Layer: Main", enabled=False),
            TrayMenuItem("sep", "", separator=True),
            TrayMenuItem("quit", "Quit"),
        ])
        self.assertTrue(spin(lambda: bool(updates)))
        rev = updates[-1][0]
        self.assertEqual(updates[-1][1], 0)
        dm = "com.canonical.dbusmenu"
        self.assertEqual(self.get(MENU_PATH, dm, "Version"), 3)
        revision, (root_id, root_props, children) = self.call(
            MENU_PATH, dm, "GetLayout", GLib.Variant("(iias)", (0, -1, [])), "(u(ia{sv}av))")
        self.assertEqual((revision, root_id), (rev, 0))
        self.assertEqual(root_props, {"children-display": "submenu"})
        self.assertEqual([c[0] for c in children], [1, 2, 3, 4, 5])
        self.assertEqual(children[1][1]["label"], "Smart__Layers")
        self.assertEqual(children[1][1]["toggle-type"], "checkmark")
        self.assertEqual(children[1][1]["toggle-state"], 1)
        self.assertFalse(children[2][1]["enabled"])
        self.assertEqual(children[3][1]["type"], "separator")
        # depth 0: no children; property filter
        _r, (_i, props, kids) = self.call(MENU_PATH, dm, "GetLayout",
                                          GLib.Variant("(iias)", (0, 0, [])), "(u(ia{sv}av))")
        self.assertEqual(kids, [])
        (group,) = self.call(MENU_PATH, dm, "GetGroupProperties",
                             GLib.Variant("(aias)", ([1, 5, 99], ["label"])), "(a(ia{sv}))")
        self.assertEqual(group, [(1, {"label": "Show Vibemapp"}), (5, {"label": "Quit"})])
        self.assertEqual(self.call(MENU_PATH, dm, "GetProperty",
                                   GLib.Variant("(is)", (5, "label")), "(v)")[0], "Quit")
        self.assertEqual(self.call(MENU_PATH, dm, "AboutToShow", GLib.Variant("(i)", (0,)), "(b)"),
                         (False,))
        self.assertEqual(self.call(MENU_PATH, dm, "AboutToShowGroup",
                                   GLib.Variant("(ai)", ([0, 42],)), "(aiai)"), ([], [42]))
        # events
        self.call(MENU_PATH, dm, "Event",
                  GLib.Variant("(isvu)", (2, "clicked", GLib.Variant("s", ""), 0)))
        self.call(MENU_PATH, dm, "Event",
                  GLib.Variant("(isvu)", (3, "clicked", GLib.Variant("s", ""), 0)))   # disabled
        self.call(MENU_PATH, dm, "Event",
                  GLib.Variant("(isvu)", (5, "hovered", GLib.Variant("s", ""), 0)))
        errors = self.call(MENU_PATH, dm, "EventGroup", GLib.Variant(
            "(a(isvu))", ([(1, "clicked", GLib.Variant("i", 0), 0),
                           (77, "clicked", GLib.Variant("i", 0), 0)],)), "(ai)")
        self.assertEqual(errors, ([77],))
        self.assertTrue(spin(lambda: self.items == ["smart", "show"]))
        with self.assertRaises(GLib.Error):
            self.call(MENU_PATH, dm, "GetLayout", GLib.Variant("(iias)", (99, -1, [])),
                      "(u(ia{sv}av))")
        with self.assertRaises(ValueError):
            self.tray.set_menu([TrayMenuItem("a", "A"), TrayMenuItem("a", "B")])

    def test_reregisters_when_watcher_restarts(self) -> None:
        self.assertTrue(spin(lambda: self.tray.props.available))
        self.watcher.stop()
        self.assertTrue(spin(lambda: not self.tray.props.available))
        self.watcher = FakeWatcher(self.address)
        self.assertTrue(spin(lambda: self.tray.props.available))
        self.assertEqual([s for _x, s in self.watcher.items], [self.tray.bus_name])

    def test_close_releases_name(self) -> None:
        self.assertTrue(spin(lambda: self.tray.props.available))
        name = self.tray.bus_name
        self.tray.close()
        self.assertFalse(self.tray.props.available)

        def gone() -> bool:
            (has,) = self.client.call_sync(
                "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                "NameHasOwner", GLib.Variant("(s)", (name,)), GLib.VariantType.new("(b)"),
                Gio.DBusCallFlags.NONE, 1000, None).unpack()
            return not has
        self.assertTrue(spin(gone))

    def test_unavailable_without_watcher(self) -> None:
        self.watcher.stop()
        conn = new_connection(self.address)
        other = TrayIcon(connection=conn)
        try:
            spin(lambda: False, 0.2)
            self.assertFalse(other.props.available)
        finally:
            other.close()
            conn.close_sync(None)


if __name__ == "__main__":
    unittest.main()
