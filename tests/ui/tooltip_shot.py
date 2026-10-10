"""Capture real Layout-view tooltips (dev tool, not a test; Hyprland only).

    PYTHONPATH=src python3 tests/ui/tooltip_shot.py OUT_PREFIX [KEY_INDEX ...]

Moves the real pointer (hyprctl) over each key, waits for GTK's tooltip, then
composites the window and the tooltip popup surface at its real position into
OUT_PREFIX-<key>.png. The pointer is restored afterwards. Also prints the tip
area GTK was given, to check it matches the hovered key.
"""

from __future__ import annotations

import json
import os
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
from vibemapp.ui.keyboard_view import KeyboardView  # noqa: E402


def hypr(cmd: str) -> str:
    return subprocess.run(["hyprctl", "dispatch", cmd], capture_output=True,
                          text=True).stdout


def main() -> int:
    prefix = sys.argv[1]
    keys = [int(k) for k in sys.argv[2:]] or [14, 26]
    pid = os.getpid()
    original_cursor = subprocess.run(["hyprctl", "cursorpos"], capture_output=True,
                                     text=True).stdout.strip()
    # Never talk to (or become) the user's running instance, and don't add a
    # second tray icon.
    import vibemapp.ui.tray_controller as tray_controller
    tray_controller.TrayIcon = None
    app = Application()
    app.set_flags(app.get_flags() | Gio.ApplicationFlags.NON_UNIQUE)
    state: dict = {"custom": None, "tip": None}

    def on_query(view, x, y, keyboard_mode, tooltip):
        # Runs before the class handler: do exactly what it does (tip area +
        # text), then swap in an identical custom label so the popup can be
        # found and composited.
        handled = KeyboardView.do_query_tooltip(view, x, y, keyboard_mode, tooltip)
        if os.environ.get("TOOLTIP_DEBUG"):
            print("query", x, y, handled, file=sys.stderr)
        geo = view.key_at(x, y)
        if handled and geo is not None:
            label = Gtk.Label(label=view.tooltip_text_for(geo))
            tooltip.set_custom(label)
            state["custom"] = label
            r = view.key_rect(geo)
            state["tip"] = (geo.index, r.x, r.y, r.width, r.height)
        return handled

    def window_origin() -> tuple[int, int]:
        clients = json.loads(subprocess.run(["hyprctl", "clients", "-j"],
                                            capture_output=True, text=True).stdout)
        at = next(c["at"] for c in clients if c["pid"] == pid)
        return at[0], at[1]

    def capture(key: int) -> None:
        win = app.window
        view = win.layout_page.keyboard_view
        label = state["custom"]
        out = f"{prefix}-{key}.png"
        w, h = win.get_width(), win.get_height()
        snap = Gtk.Snapshot()
        Gtk.WidgetPaintable.new(win).snapshot(snap, w, h)
        if label is not None and label.get_mapped():
            native = label.get_native()
            popup = native.get_surface()
            tx, ty = native.get_surface_transform()
            wx, wy = win.get_surface_transform()
            px = popup.get_position_x() + tx - wx
            py = popup.get_position_y() + ty - wy
            nw, nh = native.get_width(), native.get_height()
            snap.save()
            snap.translate(Graphene.Point().init(px, py))
            Gtk.WidgetPaintable.new(native).snapshot(snap, nw, nh)
            snap.restore()
            print(f"key {key}: tooltip {label.get_label()!r} at ({px:.0f},{py:.0f}); "
                  f"tip area {state['tip']}")
        else:
            print(f"key {key}: no tooltip visible")
        texture = win.get_renderer().render_texture(snap.to_node(),
                                                    Graphene.Rect().init(0, 0, w, h))
        texture.save_to_png(out)
        print(f"saved {out}")

    def hover(i: int) -> bool:
        if i >= len(keys):
            x, y = (int(v) for v in original_cursor.split(", "))
            hypr(f"hl.dsp.cursor.move({{x={x}, y={y}}})")
            app.quit()
            return GLib.SOURCE_REMOVE
        win = app.window
        view = win.layout_page.keyboard_view
        geo = next(g for g in view._geometry if g.index == keys[i])
        r = view.key_rect(geo)
        ok, point = view.compute_point(win, Graphene.Point().init(r.x + r.width / 2,
                                                                  r.y + r.height / 2))
        ox, oy = window_origin()
        target = (ox + int(point.x), oy + int(point.y))
        start = state.get("last") or target
        steps = 12 if start != target else 1
        for n in range(1, steps + 1):
            # Glide like a real mouse: GTK needs a stream of motion events.
            x = start[0] + (target[0] - start[0]) * n // steps
            y = start[1] + (target[1] - start[1]) * n // steps
            hypr(f"hl.dsp.cursor.move({{x={x}, y={y}}})")
            GLib.usleep(15000)
            while GLib.MainContext.default().iteration(False):
                pass
        state["last"] = target
        GLib.timeout_add(1500, lambda: (capture(keys[i]), GLib.timeout_add(
            300, hover, i + 1))[0] and False)
        return GLib.SOURCE_REMOVE

    def start() -> bool:
        win = app.window
        win.layout_page.keyboard_view.connect("query-tooltip", on_query)
        win.show_page("layout")
        win.layout_page.view_layer_index(0, animate=False)
        hypr(f'hl.dsp.window.float({{action="enable", window="pid:{pid}"}})')
        hypr(f'hl.dsp.window.resize({{x=1000, y=680, exact=true, window="pid:{pid}"}})')
        hypr(f'hl.dsp.window.center({{window="pid:{pid}"}})')
        GLib.timeout_add(800, hover, 0)
        return GLib.SOURCE_REMOVE

    app.connect_after("activate", lambda *_a: GLib.timeout_add(2500, start))
    return app.run([sys.argv[0]])


if __name__ == "__main__":
    sys.exit(main())
