"""Render the real tray menu to a PNG (dev tool, not a test).

    PYTHONPATH=src python3 tests/ui/tray_menu_shot.py OUT.png
        [--state no-keyboard|ready|smart-off] [--layer N] [--window-visible] [--demo]

There's no grim or screenshot portal, and tray hosts draw the menu themselves,
so the menu can't be screenshotted. Instead this starts a private
``dbus-daemon`` with a fake StatusNotifierWatcher, runs the app's real
TrayController + TrayIcon on it (mock keyboard, in-memory settings), fetches the
menu with ``com.canonical.dbusmenu.GetLayout`` like a tray host does, and draws
it with GTK/libadwaita as a popup menu. The SNI tooltip is drawn under it.

What's drawn: labels (``_`` mnemonics resolved), separators, disabled items,
checkmark and radio toggles, icons, shortcuts, and submenus (expanded and
indented under their parent). Hidden items are left out, as hosts do.
``--demo`` renders a built-in layout that uses all of these instead of the
app's menu, to check the renderer itself.

Use ADW_DEBUG_COLOR_SCHEME=prefer-dark / prefer-light to pick the style. The
user's session bus and running Vibemapp are never touched.
"""

from __future__ import annotations

import argparse
import functools
import os
import shutil
import subprocess
import sys
import time

os.environ.setdefault("VIBEMAPP_MEMORY_SETTINGS", "1")
# tests.tray.test_tray (the fake watcher) lives at the repository root.
sys.path.insert(1, os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..")))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from shot_util import TooltipBubble, save_widget_png  # noqa: E402  (sibling module)
from tests.tray.test_tray import FakeWatcher, new_connection  # noqa: E402
from vibemapp.mock_service import MockKeyboardService  # noqa: E402
from vibemapp.tray import TrayIcon  # noqa: E402
from vibemapp.tray.sni import ITEM_PATH, MENU_PATH  # noqa: E402
from vibemapp.ui import tray_controller as tc  # noqa: E402
from vibemapp.ui.controller import DeviceController  # noqa: E402
from vibemapp.ui.settings import MemorySettings  # noqa: E402

CSS = """
.tray-shot-stage { padding: 16px; background-color: var(--window-bg-color); }
.tray-shot-menu {
  background-color: var(--popover-bg-color);
  color: var(--popover-fg-color);
  border-radius: 12px;
  padding: 6px;
  box-shadow: 0 0 0 1px var(--border-color), 0 2px 8px 2px rgb(0 0 6 / 12%);
}
.tray-shot-item { min-height: 32px; padding: 0 12px; border-radius: 6px; }
.tray-shot-menu separator { margin: 6px 0; }
.tray-shot-submenu { margin-left: 18px; padding-left: 6px;
                     box-shadow: inset 1px 0 var(--border-color); }
"""

# Same shape as GetLayout's reply: (id, properties, children).
DEMO_LAYOUT = (0, {"children-display": "submenu"}, [
    (1, {"label": "_Show Vibemapp", "icon-name": "go-home-symbolic"}, []),
    (2, {"type": "separator"}, []),
    (3, {"label": "Smart Layers", "toggle-type": "checkmark", "toggle-state": 1}, []),
    (4, {"label": "Heatmap Recording", "toggle-type": "checkmark", "toggle-state": 0}, []),
    (5, {"label": "Layer: 0 · Base", "enabled": False}, []),
    (6, {"label": "Switch Layer", "children-display": "submenu"}, [
        (7, {"label": "Layer 0 · Base", "toggle-type": "radio", "toggle-state": 1}, []),
        (8, {"label": "Layer 1 · Symbols", "toggle-type": "radio", "toggle-state": 0}, []),
        (9, {"label": "Layer 2 · Media", "toggle-type": "radio", "toggle-state": 0,
             "enabled": False}, []),
    ]),
    (10, {"label": "Hidden item", "visible": False}, []),
    (11, {"type": "separator"}, []),
    (12, {"label": "_Quit", "shortcut": [["Control", "q"]]}, []),
])


# --- fetching ----------------------------------------------------------------

def spin(until, timeout: float = 5.0) -> bool:
    ctx = GLib.MainContext.default()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        while ctx.pending():
            ctx.iteration(False)
        if until():
            return True
        time.sleep(0.005)
    return until()


def call(conn: Gio.DBusConnection, name: str, path: str, iface: str, method: str,
         params: GLib.Variant | None, reply: str):
    """Async call + spin: the tray answers on this same main context."""
    box: list = []

    def done(c: Gio.DBusConnection, res: Gio.AsyncResult) -> None:
        try:
            box.append(c.call_finish(res))
        except GLib.Error as exc:
            box.append(exc)

    conn.call(name, path, iface, method, params, GLib.VariantType.new(reply),
              Gio.DBusCallFlags.NONE, 3000, None, done)
    if not spin(lambda: bool(box)):
        raise TimeoutError(method)
    if isinstance(box[0], GLib.Error):
        raise box[0]
    return box[0].unpack()


def fetch_tray(state: str, layer: int | None,
               window_visible: bool = False) -> tuple[tuple, tuple[str, str]]:
    """Run the real tray on a private bus; return (dbusmenu layout, (tip title, body))."""
    daemon = subprocess.Popen(["dbus-daemon", "--session", "--nofork", "--print-address=1"],
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    conns: list[Gio.DBusConnection] = []
    ctl = svc = watcher = None
    try:
        address = daemon.stdout.readline().strip()
        watcher = FakeWatcher(address)
        if not spin(lambda: watcher.owned):
            raise TimeoutError("fake StatusNotifierWatcher didn't get its name")
        tray_conn, client = new_connection(address), new_connection(address)
        conns += [tray_conn, client]
        tc.TrayIcon = functools.partial(TrayIcon, connection=tray_conn)
        svc = MockKeyboardService(speed=50, autostart=False)
        settings = MemorySettings()
        device = DeviceController(svc, settings)
        ctl = tc.TrayController(svc, device, settings, on_toggle_window=lambda: None,
                                on_show_window=lambda: None, on_quit=lambda: None)
        # As the app does at startup (main.py).
        svc.props.smart_layers_enabled = (state != "smart-off"
                                          and settings.get_boolean("smart-layers-on-startup"))
        if not spin(lambda: ctl.active):
            raise TimeoutError("tray didn't register with the fake watcher")
        if state != "no-keyboard":
            svc.simulate_plug()
            if not spin(lambda: device.state == "ready"):
                raise TimeoutError("mock keyboard didn't become ready")
            if layer is not None:
                svc.simulate_layer(layer)
                spin(lambda: svc.get_active_layer() == layer)
        ctl.window_visible = window_visible     # main.py sets this from the window
        spin(lambda: False, 0.1)
        bus_name = ctl._tray.bus_name
        _rev, layout = call(client, bus_name, MENU_PATH, "com.canonical.dbusmenu", "GetLayout",
                            GLib.Variant("(iias)", (0, -1, [])), "(u(ia{sv}av))")
        (tip,) = call(client, bus_name, ITEM_PATH, "org.freedesktop.DBus.Properties", "Get",
                      GLib.Variant("(ss)", ("org.kde.StatusNotifierItem", "ToolTip")), "(v)")
        return layout, (tip[2], tip[3])
    finally:
        if ctl is not None:
            ctl.shutdown()
        if svc is not None:
            svc.shutdown()
        spin(lambda: False, 0.05)
        for conn in conns:
            conn.close_sync(None)
        if watcher is not None:
            try:
                watcher.stop()
            except GLib.Error:
                pass
        daemon.terminate()
        daemon.wait(timeout=5)
        daemon.stdout.close()


# --- drawing -----------------------------------------------------------------

def shortcut_label(shortcut: list[list[str]]) -> str:
    """dbusmenu ``shortcut`` (aas, e.g. [["Control", "q"]]) as GTK shows it."""
    chords = []
    for keys in shortcut:
        accel = "".join(f"<{k}>" for k in keys[:-1]) + (keys[-1] if keys else "")
        ok, key, mods = Gtk.accelerator_parse(accel)
        chords.append(Gtk.accelerator_get_label(key, mods) if ok else "+".join(keys))
    return ", ".join(chords)


def item_row(props: dict, has_children: bool, indicator_slot: bool) -> Gtk.Widget:
    row = Gtk.Box(spacing=8, css_classes=["tray-shot-item"])
    toggle = props.get("toggle-type", "")
    if indicator_slot:
        slot = Gtk.Image(pixel_size=16)
        if props.get("toggle-state", 0) == 1:
            slot.set_from_icon_name({"checkmark": "object-select-symbolic",
                                     "radio": "media-record-symbolic"}.get(toggle, ""))
        row.append(slot)
    if "icon-data" in props:
        texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(bytes(props["icon-data"])))
        row.append(Gtk.Image(paintable=texture, pixel_size=16))
    elif props.get("icon-name"):
        row.append(Gtk.Image(icon_name=props["icon-name"], pixel_size=16))
    label = Gtk.Label(label=props.get("label", ""), use_underline=True, xalign=0, hexpand=True)
    row.append(label)
    if props.get("shortcut"):
        row.append(Gtk.Label(label=shortcut_label(props["shortcut"]), css_classes=["dim-label"]))
    if has_children:
        row.append(Gtk.Image(icon_name="go-next-symbolic", pixel_size=16))
    row.set_sensitive(props.get("enabled", True))
    return row


def build_menu(children: list) -> Gtk.Box:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    visible = [c for c in children if c[1].get("visible", True)]
    # Like GTK menus: reserve the indicator column if any sibling toggles.
    slot = any(c[1].get("toggle-type") in ("checkmark", "radio") for c in visible)
    for _id, props, kids in visible:
        if props.get("type") == "separator":
            box.append(Gtk.Separator())
            continue
        submenu = [k for k in kids if k[1].get("visible", True)]
        box.append(item_row(props, bool(submenu), slot))
        if submenu:
            sub = build_menu(submenu)
            sub.add_css_class("tray-shot-submenu")
            sub.set_sensitive(props.get("enabled", True))
            box.append(sub)
    return box


def build_stage(layout: tuple, tooltip: tuple[str, str] | None) -> Gtk.Widget:
    stage = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16,
                    halign=Gtk.Align.START, valign=Gtk.Align.START,
                    css_classes=["tray-shot-stage"])
    menu = build_menu(layout[2])
    menu.add_css_class("tray-shot-menu")
    menu.set_size_request(220, -1)
    stage.append(menu)
    if tooltip is not None:
        text = "\n".join(t for t in tooltip if t)
        stage.append(Gtk.Label(label="Tray tooltip:", xalign=0, css_classes=["caption", "dim-label"]))
        bubble = TooltipBubble(text=text)
        bubble.set_halign(Gtk.Align.START)
        stage.append(bubble)
    return stage


def dump(children: list, depth: int = 0) -> None:
    for menu_id, props, kids in children:
        print(f"{'  ' * depth}{menu_id}: {props}")
        dump(kids, depth + 1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("out")
    parser.add_argument("--state", choices=["no-keyboard", "ready", "smart-off"],
                        default="ready")
    parser.add_argument("--layer", type=int, help="active keyboard layer (mock)")
    parser.add_argument("--window-visible", action="store_true",
                        help="menu as shown while the main window is open")
    parser.add_argument("--demo", action="store_true",
                        help="render a built-in layout that uses every feature")
    args = parser.parse_args()

    if args.demo:
        layout, tooltip = DEMO_LAYOUT, ("Voyager", "Layer 0 · Base")
    else:
        if not shutil.which("dbus-daemon"):
            print("dbus-daemon not installed", file=sys.stderr)
            return 1
        layout, tooltip = fetch_tray(args.state, args.layer, args.window_visible)
    dump(layout[2])
    print(f"tooltip: {tooltip!r}")

    Adw.init()
    provider = Gtk.CssProvider()
    provider.load_from_string(CSS)
    Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider,
                                              Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    stage = build_stage(layout, tooltip)
    win = Gtk.Window(title="Tray Menu Shot", default_width=1, default_height=1, child=stage)
    loop = GLib.MainLoop()
    status = {"code": 0}

    def render() -> bool:
        try:
            w, h = save_widget_png(stage, args.out)
            print(f"saved {args.out} ({w}x{h})")
        except Exception:  # noqa: BLE001 - report and exit non-zero, don't hang
            import traceback
            traceback.print_exc()
            status["code"] = 1
        loop.quit()
        return GLib.SOURCE_REMOVE

    def give_up() -> bool:
        print("timed out waiting for the window to draw", file=sys.stderr)
        status["code"] = 1
        loop.quit()
        return GLib.SOURCE_REMOVE

    win.present()
    GLib.timeout_add(700, render)
    GLib.timeout_add_seconds(15, give_up)
    loop.run()
    win.destroy()
    return status["code"]


if __name__ == "__main__":
    sys.exit(main())
