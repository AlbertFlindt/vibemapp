---
name: screenshots
description: Render Vibemapp screenshots (pages, dialogs, tray menu, overlay, key tooltips) with the GTK tools in tests/ui. Use when asked for a screenshot or to check a UI change visually.
---
There is no grim or screenshot portal; GTK renders the pages off screen from the mock service.

- Pages and dialogs: `PYTHONPATH=src python3 tests/ui/screenshot.py OUT.png [--page smart-layers|heatmap|overlay] [--dialog add-rule|preferences|…]`
- Tray menu (private dbus-daemon, real dbusmenu layout): `PYTHONPATH=src python3 tests/ui/tray_menu_shot.py OUT.png [--state ready|no-keyboard|smart-off] [--window-visible]`
- Overlay over a checkerboard/busy backdrop: `PYTHONPATH=src python3 tests/ui/screenshot.py OUT.png --overlay [--backdrop busy] [--half-spacing 40] [--size 1920x1080 --width-mm 530] [--layer 1] [--press 3,15] [--anchor top-right]`
- The real overlay on a live compositor (for `grim`): `PYTHONPATH=src python3 tests/ui/overlay_live.py [--layer 1] [--press 3,15]`
- Key tooltips (no pointer moves): `PYTHONPATH=src python3 tests/ui/tooltip_shot.py PREFIX [--layer N] KEY…`

## Release screenshots (`data/screenshots/`)
From the mock service, so they hold no personal data; the mock uses the real Voyager geometry
(`geometries.py`). Dark style, blue accent, Adwaita Sans, on a Wayland compositor with
layer-shell (else the Overlay page shows "Overlay Unavailable").
- Heatmap: `VIBEMAPP_MOCK_HEATMAP_SEED=1 … --record --size 1000x1000`. Insights: the same with
  `--page insights --size 1000x700`.
- Overlay page: `--page overlay --overlay-layers 1,2 --size 1000x1300`. Tray: `--window-visible --overlay`.
- `overlay-view.png`: `tests/ui/overlay_live.py --layer 1 --press 3,15` over GNOME Text Editor and
  foot, tiled in sway at 1920x1080 on the `adwaita-d` wallpaper, taken with `grim`.
- Without a recent libadwaita (cloud sessions): an Ubuntu 26.04 debootstrap chroot under headless
  sway with `ADW_DEBUG_COLOR_SCHEME=prefer-dark ADW_DEBUG_ACCENT_COLOR=blue`.
