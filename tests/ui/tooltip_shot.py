"""Render Layout-view key tooltips to PNGs (dev tool, not a test).

    PYTHONPATH=src python3 tests/ui/tooltip_shot.py OUT_PREFIX [KEY_INDEX ...]
        [--layer N] [--size 1000x680]

Writes OUT_PREFIX-<key>.png per key: the window with that key's tooltip drawn
where GTK puts it, and prints the tooltip text and the tip area GTK was given.

The pointer is never moved. The old version warped the real cursor with
``hyprctl dispatch cursor.move`` and composited GTK's tooltip popup, but that
stalled: once a tooltip popup maps, Hyprland can hand pointer focus to it, so
GTK sees a leave, hides the tooltip and destroys the popup while it still has
pointer focus. After that, warps inside the window deliver no motion events
until the pointer leaves the window (see PROGRESS.md, "Synthetic cursor stalls").
Its composite was also shrunk: it painted the popup at its content size, which
leaves out the tooltip's CSS padding, so the paintable scaled the tooltip down.

This version calls the view's real ``query-tooltip`` handler at the key's centre
with a recording stand-in for ``Gtk.Tooltip``, so the text and tip area come
from app code. The tooltip is drawn as a widget with GTK's tooltip CSS node
(``tooltip.background > box > label``), which picks up the libadwaita tooltip
style. It's placed the way GTK places it: centred below the tip area, flipped
above when it doesn't fit, and slid sideways to stay in the window.
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
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")
gi.require_version("Gsk", "4.0")
from gi.repository import Gdk, Gio, GLib, Graphene, Gsk, Gtk  # noqa: E402

from shot_util import TooltipBubble  # noqa: E402  (sibling module)
from vibemapp.main import Application  # noqa: E402

# Gap between the tip area and the tooltip, as in GTK's tooltip positioning.
TIP_GAP = 4


class TooltipRecorder:
    """Duck-typed ``Gtk.Tooltip``: records what a query-tooltip handler sets."""

    def __init__(self) -> None:
        self.text: str | None = None
        self.markup: str | None = None
        self.tip_area: Gdk.Rectangle | None = None

    def set_text(self, text: str | None) -> None:
        self.text = text

    def set_markup(self, markup: str | None) -> None:
        self.markup = markup

    def set_tip_area(self, rect: Gdk.Rectangle) -> None:
        self.tip_area = rect

    def set_icon(self, *_args: object) -> None:
        pass

    set_icon_from_icon_name = set_icon_from_gicon = set_custom = set_icon


def query_key(view, geo) -> TooltipRecorder | None:
    """Run the view's query-tooltip handler at the centre of ``geo``."""
    r = view.key_rect(geo)
    rec = TooltipRecorder()
    # The class handler is Python, so it accepts the stand-in; emitting the
    # signal would need a real Gtk.Tooltip, which can't be constructed.
    handled = type(view).do_query_tooltip(view, r.x + r.width // 2, r.y + r.height // 2,
                                          False, rec)
    return rec if handled else None


def place(tip: tuple[float, float, float, float], size: tuple[int, int],
          bounds: tuple[int, int]) -> tuple[int, int]:
    """GTK's tooltip placement: south of the tip area (flip to north), slide on x."""
    tx, ty, tw, th = tip
    w, h = size
    bw, bh = bounds
    x = round(tx + tw / 2 - w / 2)
    x = max(0, min(x, bw - w))
    y = round(ty + th + TIP_GAP)
    if y + h > bh:
        y = round(ty - TIP_GAP - h)
    return x, y


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("prefix")
    parser.add_argument("keys", nargs="*", type=int)
    parser.add_argument("--layer", type=int, default=0, help="layer shown in the layout view")
    parser.add_argument("--size", default="1000x680")
    args = parser.parse_args()
    keys = args.keys or [14, 26]
    width, height = (int(v) for v in args.size.split("x"))

    # Never talk to (or become) the user's running instance, and don't add a
    # second tray icon.
    import vibemapp.ui.tray_controller as tray_controller
    tray_controller.TrayIcon = None
    app = Application()
    app.set_flags(app.get_flags() | Gio.ApplicationFlags.NON_UNIQUE)
    status = {"code": 0}

    def capture(key: int) -> None:
        win = app.window
        view = win.layout_page.keyboard_view
        out = f"{args.prefix}-{key}.png"
        w, h = win.get_width(), win.get_height()
        snap = Gtk.Snapshot()
        Gtk.WidgetPaintable.new(win).snapshot(snap, w, h)
        geo = next((g for g in view._geometry if g.index == key), None)
        rec = query_key(view, geo) if geo is not None else None
        if rec is None:
            print(f"key {key}: no tooltip")
        else:
            area = rec.tip_area or view.key_rect(geo)
            ok, origin = view.compute_point(win, Graphene.Point().init(area.x, area.y))
            if not ok:
                raise RuntimeError("keyboard view isn't in the window")
            bubble = TooltipBubble(rec.text, rec.markup)
            bubble.set_parent(win)
            try:
                _m, bw = bubble.measure(Gtk.Orientation.HORIZONTAL, -1)[:2]
                _m, bh = bubble.measure(Gtk.Orientation.VERTICAL, bw)[:2]
                x, y = place((origin.x, origin.y, area.width, area.height), (bw, bh), (w, h))
                bubble.allocate(bw, bh, -1, Gsk.Transform().translate(
                    Graphene.Point().init(x, y)))
                win.snapshot_child(bubble, snap)
            finally:
                bubble.unparent()
            text = rec.text if rec.markup is None else rec.markup
            print(f"key {key}: tooltip {text!r} at ({x},{y}) size {bw}x{bh}; "
                  f"tip area ({area.x}, {area.y}, {area.width}, {area.height})")
        texture = win.get_renderer().render_texture(snap.to_node(),
                                                    Graphene.Rect().init(0, 0, w, h))
        texture.save_to_png(out)
        print(f"saved {out}")

    def run(i: int) -> bool:
        # One key per main-loop turn: parenting the bubble invalidates the
        # window's render node, which is only rebuilt on the next frame.
        try:
            capture(keys[i])
        except Exception:  # noqa: BLE001 - report and exit non-zero, don't hang
            import traceback
            traceback.print_exc()
            status["code"] = 1
            i = len(keys)
        if i + 1 < len(keys):
            GLib.timeout_add(150, run, i + 1)
        else:
            app.quit()
        return GLib.SOURCE_REMOVE

    def start() -> bool:
        win = app.window
        win.show_page("layout")
        win.layout_page.view_layer_index(args.layer, animate=False)
        GLib.timeout_add(900, run, 0)
        return GLib.SOURCE_REMOVE

    def float_window() -> bool:
        # Hyprland tiles windows; float ours (matched by PID) at the requested size.
        pid = os.getpid()
        for cmd in (f'hl.dsp.window.float({{action="enable", window="pid:{pid}"}})',
                    f'hl.dsp.window.resize({{x={width}, y={height}, exact=true, '
                    f'window="pid:{pid}"}})'):
            subprocess.run(["hyprctl", "dispatch", cmd], capture_output=True, check=False)
        return GLib.SOURCE_REMOVE

    def on_activate(_app: Application) -> None:
        app.window.set_default_size(width, height)
        if shutil.which("hyprctl") and os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
            GLib.timeout_add(400, float_window)
        GLib.timeout_add(2500, start)

    app.connect_after("activate", on_activate)
    app.run([sys.argv[0]])
    return status["code"]


if __name__ == "__main__":
    sys.exit(main())
