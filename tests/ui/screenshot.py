"""Render the app window (with the mock service) to a PNG — a dev tool, not a test.

    PYTHONPATH=src python3 tests/ui/screenshot.py OUT.png [--page smart-layers]
        [--dialog add-rule|edit-rule|preferences|device|about|shortcuts]
        [--size 1000x680] [--delay 2500] [--layer 2] [--press 3,15]

Use ADW_DEBUG_COLOR_SCHEME=prefer-dark / prefer-light to pick the style.
Renders through GTK itself, so it works without a screenshot tool.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

os.environ.setdefault("VIBEMAPP_MOCK", "1")
os.environ.setdefault("VIBEMAPP_MOCK_FOCUS", "0")
os.environ.setdefault("VIBEMAPP_MOCK_KEYS", "0")
os.environ.setdefault("VIBEMAPP_MOCK_HOTPLUG", "0")
os.environ.setdefault("VIBEMAPP_MOCK_SPEED", "4")
os.environ.setdefault("VIBEMAPP_MEMORY_SETTINGS", "1")

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")
from gi.repository import Gio, GLib, Graphene, Gtk  # noqa: E402

from vibemapp.main import Application  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("out")
    parser.add_argument("--page", default="layout")
    parser.add_argument("--dialog")
    parser.add_argument("--size", default="1000x680")
    parser.add_argument("--delay", type=int, default=2500)
    parser.add_argument("--layer", type=int)
    parser.add_argument("--view-layer", type=int)
    parser.add_argument("--press", default="")
    parser.add_argument("--search", default="")
    parser.add_argument("--pick")
    parser.add_argument("--kind", choices=["app", "steam-any", "pattern"])
    parser.add_argument("--focus", default="firefox|Lo-fi beats — YouTube — Mozilla Firefox",
                        help="class|title of the simulated focused window")
    parser.add_argument("--record", action="store_true", help="turn heatmap recording on")
    parser.add_argument("--no-smart", action="store_true",
                        help="turn Smart Layers off before the keyboard connects")
    parser.add_argument("--pattern")
    args = parser.parse_args()
    width, height = (int(v) for v in args.size.split("x"))

    # Never talk to (or become) the user's running instance, and don't add a
    # second tray icon.
    import vibemapp.ui.tray_controller as tray_controller
    tray_controller.TrayIcon = None
    app = Application()
    app.set_flags(app.get_flags() | Gio.ApplicationFlags.NON_UNIQUE)

    def render() -> bool:
        win = app.window
        paintable = Gtk.WidgetPaintable.new(win)
        w, h = win.get_width(), win.get_height()
        snapshot = Gtk.Snapshot()
        paintable.snapshot(snapshot, w, h)
        node = snapshot.to_node()
        texture = win.get_renderer().render_texture(node, Graphene.Rect().init(0, 0, w, h))
        texture.save_to_png(args.out)
        print(f"saved {args.out} ({w}x{h})")
        app.quit()
        return GLib.SOURCE_REMOVE

    def stage() -> bool:
        win = app.window
        svc = app.service
        if args.record:
            svc.props.heatmap_recording = True
        win.show_page(args.page)
        mock = hasattr(svc, "simulate_focus")
        if mock:
            svc.simulate_focus(*args.focus.split("|", 1))
        if args.layer is not None and mock:
            svc.simulate_layer(args.layer)
        if args.view_layer is not None:
            win.layout_page.view_layer_index(args.view_layer, animate=False)
        for idx in filter(None, args.press.split(",") if mock else []):
            svc.simulate_key(int(idx), True)
        if args.dialog == "add-rule":
            win.smart_layers_page.add_rule()
            dlg = win.get_visible_dialog()
            if args.kind:
                row = {"app": dlg.kind_app_row, "steam-any": dlg.kind_steam_any_row,
                       "pattern": dlg.kind_pattern_row}[args.kind]
                dlg.on_kind_activated(row)
            if args.search:
                dlg.search_entry.set_text(args.search)
            if args.pick:
                from vibemapp.ui.apps import AppIndex
                dlg._pick_app(AppIndex.get().entry_for(args.pick))
            if args.pattern is not None:
                dlg.pattern_row.set_text(args.pattern)
        elif args.dialog == "edit-rule":
            win.smart_layers_page.edit_rule(0)
        elif args.dialog:
            app.activate_action({"preferences": "preferences", "about": "about",
                                 "shortcuts": "shortcuts"}.get(args.dialog, "about"), None) \
                if args.dialog != "device" else win.activate_action("win.device-info", None)
        GLib.timeout_add(900, render)
        return GLib.SOURCE_REMOVE

    def float_window() -> bool:
        # Hyprland tiles windows; float ours (matched by PID) at the requested size.
        pid = os.getpid()
        lua = (f'hl.dsp.window.float({{action="enable", window="pid:{pid}"}})',
               f'hl.dsp.window.resize({{x={width}, y={height}, exact=true, '
               f'window="pid:{pid}"}})')
        legacy = (f"setfloating pid:{pid}", f"resizewindowpixel exact {width} {height},pid:{pid}")
        for lua_cmd, legacy_cmd in zip(lua, legacy):
            out = subprocess.run(["hyprctl", "dispatch", lua_cmd], capture_output=True,
                                 text=True, check=False).stdout
            if "error" in out:
                subprocess.run(["hyprctl", "dispatch", *legacy_cmd.split(" ", 1)],
                               capture_output=True, check=False)
        return GLib.SOURCE_REMOVE

    def on_activate(_app: Application) -> None:
        if args.no_smart:
            app.service.props.smart_layers_enabled = False
        app.window.set_default_size(width, height)
        if shutil.which("hyprctl") and os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
            GLib.timeout_add(400, float_window)
        GLib.timeout_add(args.delay, stage)

    app.connect_after("activate", on_activate)
    return app.run([sys.argv[0]])


if __name__ == "__main__":
    sys.exit(main())
