"""StatusNotifierItem + dbusmenu tray icon over Gio D-Bus (no GTK).

API (for the UI)::

    from vibemapp.tray import TrayIcon, TrayMenuItem

    tray = TrayIcon(title="Vibemapp")          # registers right away
    tray.connect("notify::available", ...)     # True while a watcher accepted us
    tray.connect("activate", on_left_click)    # Activate / SecondaryActivate
    tray.connect("menu-item-activated", lambda _t, item_id: ...)
    tray.set_menu([
        TrayMenuItem("show", "Show Vibemapp"),
        TrayMenuItem("smart-layers", "Smart Layers", toggle=True),
        TrayMenuItem("layer", "Layer: Main", enabled=False),
        TrayMenuItem("sep", "", separator=True),
        TrayMenuItem("quit", "Quit"),
    ])
    tray.set_tooltip("Voyager", "Layer: Main")
    tray.set_icon_name("io.github.albertflindt.Vibemapp-symbolic")
    tray.set_status("Active")                  # or "Passive" (hidden by most hosts)
    tray.close()                               # unregister (drops the bus name)

Behaviour:

* Owns ``org.kde.StatusNotifierItem-<pid>-<n>`` on the session bus and
  exports ``/StatusNotifierItem`` (``org.kde.StatusNotifierItem``) and
  ``/MenuBar`` (``com.canonical.dbusmenu``, version 3).
* Watches ``org.kde.StatusNotifierWatcher`` with ``Gio.bus_watch_name`` and
  calls ``RegisterStatusNotifierItem(<bus name>)`` every time a watcher
  appears, so it re-registers when the shell restarts. ``available`` is True
  after a successful registration and False when the watcher goes away.
* Toggle items are *not* flipped by the tray. Handle ``menu-item-activated``
  and call ``set_menu`` with the new state.
* ``IconThemePath`` defaults to the source tree's ``data/icons`` when running
  uninstalled, and to "" when installed (the icon is then in hicolor).
* Signals are emitted on the main context that created the TrayIcon.
"""

from __future__ import annotations

import itertools
import logging
import os
from dataclasses import dataclass
from typing import Any

from gi.repository import Gio, GLib, GObject

log = logging.getLogger(__name__)

WATCHER_NAME = "org.kde.StatusNotifierWatcher"
WATCHER_PATH = "/StatusNotifierWatcher"
WATCHER_IFACE = "org.kde.StatusNotifierWatcher"
ITEM_PATH = "/StatusNotifierItem"
ITEM_IFACE = "org.kde.StatusNotifierItem"
MENU_PATH = "/MenuBar"
MENU_IFACE = "com.canonical.dbusmenu"
APP_ICON = "io.github.albertflindt.Vibemapp"
STATUSES = ("Active", "Passive", "NeedsAttention")

_ITEM_XML = """
<node>
  <interface name="org.kde.StatusNotifierItem">
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="WindowId" type="i" access="read"/>
    <property name="IconThemePath" type="s" access="read"/>
    <property name="Menu" type="o" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="IconPixmap" type="a(iiay)" access="read"/>
    <property name="OverlayIconName" type="s" access="read"/>
    <property name="OverlayIconPixmap" type="a(iiay)" access="read"/>
    <property name="AttentionIconName" type="s" access="read"/>
    <property name="AttentionIconPixmap" type="a(iiay)" access="read"/>
    <property name="AttentionMovieName" type="s" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <method name="ContextMenu"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
    <method name="Activate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
    <method name="SecondaryActivate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
    <method name="Scroll"><arg name="delta" type="i" direction="in"/><arg name="orientation" type="s" direction="in"/></method>
    <signal name="NewTitle"/>
    <signal name="NewIcon"/>
    <signal name="NewAttentionIcon"/>
    <signal name="NewOverlayIcon"/>
    <signal name="NewToolTip"/>
    <signal name="NewMenu"/>
    <signal name="NewIconThemePath"><arg name="icon_theme_path" type="s"/></signal>
    <signal name="NewStatus"><arg name="status" type="s"/></signal>
  </interface>
</node>
"""

_MENU_XML = """
<node>
  <interface name="com.canonical.dbusmenu">
    <property name="Version" type="u" access="read"/>
    <property name="TextDirection" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconThemePath" type="as" access="read"/>
    <method name="GetLayout">
      <arg type="i" name="parentId" direction="in"/>
      <arg type="i" name="recursionDepth" direction="in"/>
      <arg type="as" name="propertyNames" direction="in"/>
      <arg type="u" name="revision" direction="out"/>
      <arg type="(ia{sv}av)" name="layout" direction="out"/>
    </method>
    <method name="GetGroupProperties">
      <arg type="ai" name="ids" direction="in"/>
      <arg type="as" name="propertyNames" direction="in"/>
      <arg type="a(ia{sv})" name="properties" direction="out"/>
    </method>
    <method name="GetProperty">
      <arg type="i" name="id" direction="in"/>
      <arg type="s" name="name" direction="in"/>
      <arg type="v" name="value" direction="out"/>
    </method>
    <method name="Event">
      <arg type="i" name="id" direction="in"/>
      <arg type="s" name="eventId" direction="in"/>
      <arg type="v" name="data" direction="in"/>
      <arg type="u" name="timestamp" direction="in"/>
    </method>
    <method name="EventGroup">
      <arg type="a(isvu)" name="events" direction="in"/>
      <arg type="ai" name="idErrors" direction="out"/>
    </method>
    <method name="AboutToShow">
      <arg type="i" name="id" direction="in"/>
      <arg type="b" name="needUpdate" direction="out"/>
    </method>
    <method name="AboutToShowGroup">
      <arg type="ai" name="ids" direction="in"/>
      <arg type="ai" name="updatesNeeded" direction="out"/>
      <arg type="ai" name="idErrors" direction="out"/>
    </method>
    <signal name="ItemsPropertiesUpdated">
      <arg type="a(ia{sv})" name="updatedProps"/>
      <arg type="a(ias)" name="removedProps"/>
    </signal>
    <signal name="LayoutUpdated">
      <arg type="u" name="revision"/>
      <arg type="i" name="parent"/>
    </signal>
    <signal name="ItemActivationRequested">
      <arg type="i" name="id"/>
      <arg type="u" name="timestamp"/>
    </signal>
  </interface>
</node>
"""

_ITEM_INFO = Gio.DBusNodeInfo.new_for_xml(_ITEM_XML).interfaces[0]
_MENU_INFO = Gio.DBusNodeInfo.new_for_xml(_MENU_XML).interfaces[0]
_instance_counter = itertools.count(1)


@dataclass(frozen=True)
class TrayMenuItem:
    id: str
    label: str
    enabled: bool = True
    toggle: bool | None = None      # None = plain item, else a checkmark with this state
    separator: bool = False


def default_icon_theme_path() -> str:
    """``<repo>/data/icons`` when running from the source tree, else ""."""
    here = os.path.dirname(os.path.abspath(__file__))
    candidate = os.path.normpath(os.path.join(here, "..", "..", "..", "data", "icons"))
    return candidate if os.path.isdir(os.path.join(candidate, "hicolor")) else ""


def item_properties(item: TrayMenuItem) -> dict[str, GLib.Variant]:
    if item.separator:
        return {"type": GLib.Variant("s", "separator"), "visible": GLib.Variant("b", True)}
    props = {
        # dbusmenu labels use "_" for mnemonics; escape literal underscores.
        "label": GLib.Variant("s", item.label.replace("_", "__")),
        "enabled": GLib.Variant("b", item.enabled),
        "visible": GLib.Variant("b", True),
    }
    if item.toggle is not None:
        props["toggle-type"] = GLib.Variant("s", "checkmark")
        props["toggle-state"] = GLib.Variant("i", 1 if item.toggle else 0)
    return props


ROOT_PROPERTIES = {"children-display": GLib.Variant("s", "submenu")}


def _filter(props: dict[str, GLib.Variant], names: list[str]) -> dict[str, GLib.Variant]:
    return {k: v for k, v in props.items() if k in names} if names else dict(props)


class TrayIcon(GObject.Object):
    __gtype_name__ = "VmTrayIcon"
    __gsignals__ = {
        "activate": (GObject.SignalFlags.RUN_LAST, None, ()),
        "menu-item-activated": (GObject.SignalFlags.RUN_LAST, None, (str,)),
    }

    def __init__(self, *, item_id: str = "vibemapp", title: str = "Vibemapp",
                 icon_name: str = APP_ICON, icon_theme_path: str | None = None,
                 category: str = "ApplicationStatus",
                 connection: Gio.DBusConnection | None = None,
                 watcher_name: str = WATCHER_NAME) -> None:
        super().__init__()
        self._item_id = item_id
        self._title = title
        self._icon_name = icon_name
        self._icon_theme_path = (default_icon_theme_path() if icon_theme_path is None
                                 else icon_theme_path)
        self._category = category
        self._status = "Active"
        self._tooltip = ("", "")
        self._watcher_name = watcher_name
        self._items: list[TrayMenuItem] = []
        self._revision = 1
        self._available = False
        self._closed = False
        self._name_acquired = False
        self._watcher_owner: str | None = None
        self.bus_name = f"org.kde.StatusNotifierItem-{os.getpid()}-{next(_instance_counter)}"

        self._conn = connection or Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self._reg_ids = [
            self._conn.register_object(ITEM_PATH, _ITEM_INFO, self._on_item_call,
                                       self._on_item_get, None),
            self._conn.register_object(MENU_PATH, _MENU_INFO, self._on_menu_call,
                                       self._on_menu_get, None),
        ]
        self._own_id = Gio.bus_own_name_on_connection(
            self._conn, self.bus_name, Gio.BusNameOwnerFlags.NONE,
            self._on_name_acquired, self._on_name_lost)
        self._watch_id = Gio.bus_watch_name_on_connection(
            self._conn, watcher_name, Gio.BusNameWatcherFlags.NONE,
            self._on_watcher_appeared, self._on_watcher_vanished)

    # --- public API ------------------------------------------------------------

    @GObject.Property(type=bool, default=False)
    def available(self) -> bool:
        """True while a StatusNotifierWatcher has accepted our registration."""
        return self._available

    @property
    def menu_items(self) -> list[TrayMenuItem]:
        return list(self._items)

    def set_menu(self, items: list[TrayMenuItem]) -> None:
        ids = [i.id for i in items]
        if len(set(ids)) != len(ids):
            raise ValueError("menu item ids must be unique")
        self._items = list(items)
        self._revision += 1
        self._emit(MENU_PATH, MENU_IFACE, "LayoutUpdated",
                   GLib.Variant("(ui)", (self._revision, 0)))

    def set_tooltip(self, title: str, body: str = "") -> None:
        self._tooltip = (title, body)
        self._emit(ITEM_PATH, ITEM_IFACE, "NewToolTip", None)

    def set_title(self, title: str) -> None:
        self._title = title
        self._emit(ITEM_PATH, ITEM_IFACE, "NewTitle", None)

    def set_icon_name(self, name: str) -> None:
        self._icon_name = name
        self._emit(ITEM_PATH, ITEM_IFACE, "NewIcon", None)

    def set_status(self, status: str) -> None:
        if status not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES}")
        self._status = status
        self._emit(ITEM_PATH, ITEM_IFACE, "NewStatus", GLib.Variant("(s)", (status,)))

    def close(self) -> None:
        """Unregister: drop the bus name (watchers remove the item) and objects."""
        if self._closed:
            return
        self._closed = True
        Gio.bus_unwatch_name(self._watch_id)
        Gio.bus_unown_name(self._own_id)
        for rid in self._reg_ids:
            self._conn.unregister_object(rid)
        self._reg_ids = []
        self._set_available(False)

    # --- registration -------------------------------------------------------------

    def _set_available(self, value: bool) -> None:
        if value != self._available:
            self._available = value
            self.notify("available")

    def _on_name_acquired(self, _conn: Gio.DBusConnection, _name: str) -> None:
        self._name_acquired = True
        self._register()

    def _on_name_lost(self, _conn: Gio.DBusConnection, name: str) -> None:
        if not self._closed:
            log.warning("Lost tray bus name %s", name)
        self._name_acquired = False
        self._set_available(False)

    def _on_watcher_appeared(self, _conn: Gio.DBusConnection, _name: str, owner: str) -> None:
        self._watcher_owner = owner
        self._register()

    def _on_watcher_vanished(self, _conn: Gio.DBusConnection, _name: str) -> None:
        self._watcher_owner = None
        self._set_available(False)

    def _register(self) -> None:
        if self._closed or not self._name_acquired or self._watcher_owner is None:
            return
        owner = self._watcher_owner

        def done(conn: Gio.DBusConnection, res: Gio.AsyncResult) -> None:
            try:
                conn.call_finish(res)
            except GLib.Error as exc:
                log.warning("Tray registration failed: %s", exc.message)
                if owner == self._watcher_owner:
                    self._set_available(False)
                return
            if not self._closed and owner == self._watcher_owner:
                self._set_available(True)

        self._conn.call(self._watcher_name, WATCHER_PATH, WATCHER_IFACE,
                        "RegisterStatusNotifierItem", GLib.Variant("(s)", (self.bus_name,)),
                        None, Gio.DBusCallFlags.NONE, 5000, None, done)

    def _emit(self, path: str, iface: str, name: str, params: GLib.Variant | None) -> None:
        if self._closed:
            return
        try:
            self._conn.emit_signal(None, path, iface, name, params)
        except GLib.Error as exc:
            log.debug("emit %s failed: %s", name, exc.message)

    # --- org.kde.StatusNotifierItem -------------------------------------------------

    def _item_property(self, name: str) -> GLib.Variant | None:
        empty_pixmaps: list[Any] = []
        values = {
            "Category": GLib.Variant("s", self._category),
            "Id": GLib.Variant("s", self._item_id),
            "Title": GLib.Variant("s", self._title),
            "Status": GLib.Variant("s", self._status),
            "WindowId": GLib.Variant("i", 0),
            "IconThemePath": GLib.Variant("s", self._icon_theme_path),
            "Menu": GLib.Variant("o", MENU_PATH),
            "ItemIsMenu": GLib.Variant("b", False),
            "IconName": GLib.Variant("s", self._icon_name),
            "IconPixmap": GLib.Variant("a(iiay)", empty_pixmaps),
            "OverlayIconName": GLib.Variant("s", ""),
            "OverlayIconPixmap": GLib.Variant("a(iiay)", empty_pixmaps),
            "AttentionIconName": GLib.Variant("s", ""),
            "AttentionIconPixmap": GLib.Variant("a(iiay)", empty_pixmaps),
            "AttentionMovieName": GLib.Variant("s", ""),
            "ToolTip": GLib.Variant("(sa(iiay)ss)",
                                    ("", empty_pixmaps, self._tooltip[0], self._tooltip[1])),
        }
        return values.get(name)

    def _on_item_get(self, _conn, _sender, _path, _iface, name: str) -> GLib.Variant | None:
        return self._item_property(name)

    def _on_item_call(self, _conn, _sender, _path, _iface, method: str,
                      _params: GLib.Variant, invocation: Gio.DBusMethodInvocation) -> None:
        if method in ("Activate", "SecondaryActivate"):
            invocation.return_value(None)
            self.emit("activate")
        elif method in ("ContextMenu", "Scroll"):
            invocation.return_value(None)   # hosts show our dbusmenu themselves
        else:
            invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method)

    # --- com.canonical.dbusmenu -----------------------------------------------------

    def _props_for(self, menu_id: int) -> dict[str, GLib.Variant] | None:
        if menu_id == 0:
            return dict(ROOT_PROPERTIES)
        if 1 <= menu_id <= len(self._items):
            return item_properties(self._items[menu_id - 1])
        return None

    def _node(self, menu_id: int, depth: int, names: list[str]) -> GLib.Variant:
        props = _filter(self._props_for(menu_id) or {}, names)
        children: list[GLib.Variant] = []
        if menu_id == 0 and depth != 0:
            children = [self._node(i, depth - 1 if depth > 0 else -1, names)
                        for i in range(1, len(self._items) + 1)]
        return GLib.Variant("(ia{sv}av)", (menu_id, props, children))

    def _handle_event(self, menu_id: int, event_id: str) -> bool:
        if not 1 <= menu_id <= len(self._items):
            return menu_id == 0
        item = self._items[menu_id - 1]
        if event_id == "clicked" and item.enabled and not item.separator:
            self.emit("menu-item-activated", item.id)
        return True

    def _on_menu_get(self, _conn, _sender, _path, _iface, name: str) -> GLib.Variant | None:
        return {
            "Version": GLib.Variant("u", 3),
            "TextDirection": GLib.Variant("s", "ltr"),
            "Status": GLib.Variant("s", "normal"),
            "IconThemePath": GLib.Variant("as", [self._icon_theme_path] if self._icon_theme_path else []),
        }.get(name)

    def _on_menu_call(self, _conn, _sender, _path, _iface, method: str,
                      params: GLib.Variant, invocation: Gio.DBusMethodInvocation) -> None:
        args = params.unpack()
        try:
            if method == "GetLayout":
                parent, depth, names = args
                if self._props_for(parent) is None:
                    invocation.return_dbus_error("org.freedesktop.DBus.Error.InvalidArgs",
                                                 f"unknown menu id {parent}")
                    return
                node = self._node(parent, depth, list(names))
                invocation.return_value(GLib.Variant.new_tuple(
                    GLib.Variant("u", self._revision), node))
            elif method == "GetGroupProperties":
                ids, names = args
                ids = list(ids) or list(range(0, len(self._items) + 1))
                out = [(i, _filter(p, list(names))) for i in ids
                       if (p := self._props_for(i)) is not None]
                invocation.return_value(GLib.Variant("(a(ia{sv}))", (out,)))
            elif method == "GetProperty":
                menu_id, name = args
                value = (self._props_for(menu_id) or {}).get(name)
                if value is None:
                    invocation.return_dbus_error("org.freedesktop.DBus.Error.InvalidArgs",
                                                 f"no property {name} on {menu_id}")
                    return
                invocation.return_value(GLib.Variant("(v)", (value,)))
            elif method == "Event":
                menu_id, event_id, _data, _ts = args
                invocation.return_value(None)
                self._handle_event(menu_id, event_id)
            elif method == "EventGroup":
                (events,) = args
                errors = [mid for mid, _e, _d, _t in events if self._props_for(mid) is None]
                invocation.return_value(GLib.Variant("(ai)", (errors,)))
                for mid, event_id, _d, _t in events:
                    if mid not in errors:
                        self._handle_event(mid, event_id)
            elif method == "AboutToShow":
                invocation.return_value(GLib.Variant("(b)", (False,)))
            elif method == "AboutToShowGroup":
                (ids,) = args
                errors = [i for i in ids if self._props_for(i) is None]
                invocation.return_value(GLib.Variant("(aiai)", ([], errors)))
            else:
                invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method)
        except Exception as exc:   # noqa: BLE001 - never leave a call unanswered
            log.exception("dbusmenu %s failed", method)
            invocation.return_dbus_error("org.freedesktop.DBus.Error.Failed", str(exc))


__all__ = ["TrayIcon", "TrayMenuItem", "default_icon_theme_path", "item_properties",
           "WATCHER_NAME", "ITEM_PATH", "MENU_PATH", "APP_ICON"]
