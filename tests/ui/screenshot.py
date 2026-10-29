"""Render the app window (with the mock service) to a PNG — a dev tool, not a test.

    PYTHONPATH=src python3 tests/ui/screenshot.py OUT.png [--page smart-layers|heatmap|overlay|insights]
        [--dialog add-rule|edit-rule|preferences|device|about|shortcuts] [--silent-layers 3]
        [--size 1000x680] [--delay 2500] [--layer 2] [--press 3,15]
    PYTHONPATH=src python3 tests/ui/screenshot.py OUT.png --overlay
        [--backdrop checker|busy] [--half-spacing 0] [--anchor bottom]
        [--overlay-size 80] [--width-mm 800] [--opacity 60] [--layer 1]
        [--press 3,15] [--size 3440x1440] [--real-geometry]

``--overlay`` draws the on-screen overlay (mock keyboard) over a checkerboard
or a busy light backdrop of ``--size``, a monitor ``--width-mm`` wide, placed
exactly where the layer-shell surfaces would go (the margins the overlay asks
gtk4-layer-shell for are recorded and replayed), so size, half spacing,
translucency and halo are visible. No overlay surface is shown.
``--real-geometry`` uses the real Voyager geometry instead of the mock's.

``--page insights --record`` also turns key-pair recording on; ``--heatmap-app KEY``
picks the App filter of the Insights page when that page is shown.

Use ADW_DEBUG_COLOR_SCHEME=prefer-dark / prefer-light to pick the style.
Renders through GTK itself, so it works without a screenshot tool.
Sibling tools: ``tooltip_shot.py`` (Layout-view key tooltips) and
``tray_menu_shot.py`` (the tray's dbusmenu, fetched over a private D-Bus).
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

import vibemapp._preload  # noqa: E402,F401  (gtk4-layer-shell must load before GTK)
import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")
from gi.repository import Gio, GLib, Graphene, Gtk  # noqa: E402

from vibemapp.main import Application  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("out")
    parser.add_argument("--page", default="layout",
                        choices=["layout", "smart-layers", "heatmap", "overlay", "insights"])
    parser.add_argument("--overlay-layers",
                        help="comma-separated layers ticked on the Overlay page, e.g. 1,2")
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
    parser.add_argument("--heatmap-app", help="app key to select on the Heatmap or Insights page")
    parser.add_argument("--record", action="store_true", help="turn heatmap recording on (and key-pair recording too "
                             "with --page insights)")
    parser.add_argument("--no-smart", action="store_true",
                        help="turn Smart Layers off before the keyboard connects")
    parser.add_argument("--pattern")
    parser.add_argument("--silent-layers",
                        help="with --dialog preferences: open Silent Layers with these "
                             "comma-separated layers ticked, e.g. 3")
    parser.add_argument("--overlay", action="store_true",
                        help="render the on-screen overlay instead of the window")
    parser.add_argument("--backdrop", choices=["checker", "busy"], default="checker")
    parser.add_argument("--half-spacing", type=int, default=0)
    parser.add_argument("--anchor", default="bottom")
    parser.add_argument("--overlay-size", type=int, default=80)
    parser.add_argument("--width-mm", type=int, default=800)
    parser.add_argument("--opacity", type=int, default=60)
    parser.add_argument("--real-geometry", action="store_true")
    parser.add_argument("--hold-pill", metavar="TEXT",
                        help="with --overlay: also show the hold-action pill, e.g. Ctrl+C")
    args = parser.parse_args()
    if args.overlay:
        if args.size == parser.get_default("size"):
            args.size = "3440x1440"
        return overlay_shot(args)
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
        if args.overlay_layers is not None:
            app.settings.set_value("overlay-layers", GLib.Variant(
                "ai", [int(v) for v in args.overlay_layers.split(",") if v]))
        win.show_page(args.page)
        if args.heatmap_app:
            page = win.insights_page if args.page == "insights" else win.heatmap_page
            page.refresh()
            keys = page._app_keys
            if args.heatmap_app in keys:
                page.app_dropdown.set_selected(keys.index(args.heatmap_app) + 1)
            else:
                print(f"heatmap app {args.heatmap_app!r} not in {keys}")
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
        if args.dialog == "preferences" and args.silent_layers is not None:
            app.settings.set_boolean("notify-layer-changes", True)
            app.settings.set_value("notify-excluded-layers", GLib.Variant(
                "ai", [int(v) for v in args.silent_layers.split(",") if v]))
            win.get_visible_dialog().silent_layers_row.set_expanded(True)
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


def _draw_backdrop(kind: str, cr, width: int, height: int) -> None:
    import random

    if kind == "checker":
        size = 16
        for y in range(0, height, size):
            for x in range(0, width, size):
                shade = 0.8 if (x // size + y // size) % 2 else 0.55
                cr.set_source_rgb(shade, shade, shade)
                cr.rectangle(x, y, size, size)
                cr.fill()
        return
    # "busy": a light, cluttered page (text lines, images, buttons).
    rng = random.Random(7)
    cr.set_source_rgb(0.97, 0.97, 0.96)
    cr.paint()
    for _ in range(60):
        x, y = rng.uniform(0, width), rng.uniform(0, height)
        w, h = rng.uniform(80, 420), rng.uniform(40, 260)
        r, g, b = (rng.uniform(0.55, 1.0) for _c in range(3))
        cr.set_source_rgb(r, g, b)
        cr.rectangle(x, y, w, h)
        cr.fill()
    cr.set_source_rgb(0.15, 0.15, 0.17)
    for row in range(0, height, 22):
        x = rng.uniform(0, 60)
        while x < width:
            w = rng.uniform(20, 90)
            cr.rectangle(x, row + 8, w, 7)
            x += w + rng.uniform(6, 14)
        cr.fill()


def overlay_shot(args) -> int:
    gi.require_version("Adw", "1")
    from dataclasses import replace

    from gi.repository import Adw
    from shot_util import FakeLayerShell

    from vibemapp.backend.geometries import geometry_for
    from vibemapp.mock_service import MockKeyboardService
    from vibemapp.models import KeyboardModel
    from vibemapp.ui import overlay as overlay_mod
    from vibemapp.ui.overlay_state import MonitorInfo, anchor_edges  # noqa: E402
    from vibemapp.ui.resources import load_css
    from vibemapp.ui.settings import MemorySettings

    width, height = (int(v) for v in args.size.split("x"))
    Adw.init()
    load_css()
    ctx = GLib.MainContext.default()

    def spin(until, timeout=5.0) -> bool:
        import time
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not until():
            ctx.iteration(False) or time.sleep(0.005)
        return until()

    service = MockKeyboardService()
    spin(lambda: service.get_connected_device() is not None)
    if service.get_connected_device() is not None:
        service.load_layout_async(service.get_connected_device(), None, lambda *_a: None)
    if not spin(lambda: service.get_layout() is not None):
        print("mock keyboard never loaded a layout", file=sys.stderr)
        return 1
    if args.real_geometry:
        # Real Voyager positions (Oryx order); the mock layers use QMK order,
        # so labels land on other keys. For checking sizes only.
        service._layout = replace(service._layout,
                                  geometry=geometry_for(KeyboardModel.VOYAGER))
    settings = MemorySettings()
    for key, value in (("overlay-mode", "always"), ("overlay-anchor", args.anchor)):
        settings.set_string(key, value)
    for key, value in (("overlay-size", args.overlay_size),
                       ("overlay-opacity", args.opacity),
                       ("overlay-half-spacing", args.half_spacing)):
        settings.set_int(key, value)
    if args.layer is not None:
        service.simulate_layer(args.layer)

    # Never put a real surface on screen: keep the content, drop the windows.
    overlay_mod.OverlaySurface.fade_in = lambda self: self.box.set_opacity(1.0)
    fake = FakeLayerShell()
    info = MonitorInfo("SHOT-1", width, height, args.width_mm)
    controller = overlay_mod.OverlayController(service, settings, layer_shell=fake)
    controller.resolve_monitor = lambda: (None, info)
    controller._update()
    for idx in filter(None, args.press.split(",")):
        service.simulate_key(int(idx), True)

    scene = Gtk.Window(default_width=width, default_height=height, decorated=False)
    stack = Gtk.Overlay()
    backdrop = Gtk.DrawingArea(content_width=width, content_height=height)
    backdrop.set_draw_func(lambda _a, cr, w, h: _draw_backdrop(args.backdrop, cr, w, h))
    stack.set_child(backdrop)
    vertical, _horizontal = anchor_edges(args.anchor)
    for surface in controller.surfaces:
        margins = fake.state(surface)["margin"]
        box = surface.box
        surface.set_child(None)
        box.set_halign(Gtk.Align.START)
        box.set_margin_start(margins["left"])
        box.set_valign(Gtk.Align.END if vertical == "bottom" else Gtk.Align.START)
        if vertical == "bottom":
            box.set_margin_bottom(margins["bottom"])
        else:
            box.set_margin_top(margins["top"])
        stack.add_overlay(box)
        print(f"surface at x={margins['left']} {vertical}={margins[vertical]} "
              f"board {surface.view._fixed_size[0]}x{surface.view._fixed_size[1]}")
    if args.hold_pill:
        from vibemapp.ui import hold_pill as pill_mod
        pill_mod.HoldPillSurface.flash = lambda self, text: (
            self.label.set_label(text), self.label.set_opacity(1.0))
        clearance = max((fake.state(s)["margin"]["bottom"]
                         + s.box.measure(Gtk.Orientation.VERTICAL, -1)[1]
                         for s in controller.surfaces if vertical == "bottom"), default=0)
        pill = pill_mod.HoldPillController(service, settings, fake,
                                           monitor=lambda: (None, height),
                                           clearance=lambda: clearance)
        pill.show(args.hold_pill)
        label = pill.surface.label
        pill.surface.set_child(None)
        label.set_halign(Gtk.Align.CENTER)
        label.set_valign(Gtk.Align.END)
        label.set_margin_bottom(fake.state(pill.surface)["margin"]["bottom"])
        stack.add_overlay(label)
        print(f"hold pill bottom={fake.state(pill.surface)['margin']['bottom']}")
    scene.set_child(stack)
    scene.present()

    def render() -> bool:
        paintable = Gtk.WidgetPaintable.new(stack)
        w, h = stack.get_width(), stack.get_height()
        snapshot = Gtk.Snapshot()
        paintable.snapshot(snapshot, w, h)
        texture = scene.get_renderer().render_texture(
            snapshot.to_node(), Graphene.Rect().init(0, 0, w, h))
        texture.save_to_png(args.out)
        print(f"saved {args.out} ({w}x{h})")
        done.append(True)
        return GLib.SOURCE_REMOVE

    done: list[bool] = []
    GLib.timeout_add(700, render)
    spin(lambda: bool(done), timeout=10)
    controller.destroy()
    scene.destroy()
    service.shutdown()
    return 0 if done else 1


if __name__ == "__main__":
    sys.exit(main())
