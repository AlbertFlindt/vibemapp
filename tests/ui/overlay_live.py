"""Show the real overlay (mock keyboard) on the running compositor — a dev tool, not a test.

    PYTHONPATH=src python3 tests/ui/overlay_live.py [--layer 1] [--press 3,15]
        [--size 80] [--half-spacing 0] [--opacity 60]

Unlike ``screenshot.py --overlay``, this puts the real gtk4-layer-shell surfaces
on screen, so a screenshot tool (grim) captures the overlay over real windows.
Needs a compositor with wlr-layer-shell (Hyprland, sway). Runs until killed.
"""

from __future__ import annotations

import argparse
import os

os.environ.setdefault("VIBEMAPP_MOCK", "1")
os.environ.setdefault("VIBEMAPP_MOCK_FOCUS", "0")
os.environ.setdefault("VIBEMAPP_MOCK_KEYS", "0")
os.environ.setdefault("VIBEMAPP_MOCK_HOTPLUG", "0")
os.environ.setdefault("VIBEMAPP_MEMORY_SETTINGS", "1")

import vibemapp._preload  # noqa: E402,F401  (gtk4-layer-shell must load before GTK)
import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib  # noqa: E402

from vibemapp.mock_service import MockKeyboardService  # noqa: E402
from vibemapp.ui import overlay as overlay_mod  # noqa: E402
from vibemapp.ui.resources import load_css  # noqa: E402
from vibemapp.ui.settings import MemorySettings  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--layer", type=int, default=1)
    parser.add_argument("--press", default="", help="comma-separated mock key indices")
    parser.add_argument("--size", type=int, default=80)
    parser.add_argument("--half-spacing", type=int, default=0)
    parser.add_argument("--opacity", type=int, default=60)
    args = parser.parse_args()

    Adw.init()
    load_css()
    ctx = GLib.MainContext.default()
    service = MockKeyboardService()
    while service.get_connected_device() is None:
        ctx.iteration(True)
    service.load_layout_async(service.get_connected_device(), None, lambda *_a: None)
    while service.get_layout() is None:
        ctx.iteration(True)

    settings = MemorySettings()
    settings.set_string("overlay-mode", "always")
    for key, value in (("overlay-size", args.size), ("overlay-opacity", args.opacity),
                       ("overlay-half-spacing", args.half_spacing)):
        settings.set_int(key, value)
    service.simulate_layer(args.layer)
    controller = overlay_mod.OverlayController(service, settings)
    if not controller.available:
        print("gtk4-layer-shell is not available here", flush=True)
        return 1
    controller._update()
    presses = [int(i) for i in args.press.split(",") if i]
    GLib.timeout_add(800, lambda: [service.simulate_key(i, True) for i in presses] and False)
    GLib.MainLoop().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
