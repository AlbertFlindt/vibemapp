---
name: screenshots
description: Render Vibemapp screenshots (pages, dialogs, tray menu, overlay, key tooltips) with the GTK tools in tests/ui. Use when asked for a screenshot or to check a UI change visually.
---
There is no grim or screenshot portal; GTK renders the pages off screen from the mock service.

- Pages and dialogs: `PYTHONPATH=src python3 tests/ui/screenshot.py OUT.png [--page smart-layers|heatmap|overlay] [--dialog add-rule|preferences|…]`
- Tray menu (private dbus-daemon, real dbusmenu layout): `PYTHONPATH=src python3 tests/ui/tray_menu_shot.py OUT.png [--state ready|no-keyboard|smart-off] [--window-visible]`
- Overlay over a checkerboard/busy backdrop: `PYTHONPATH=src python3 tests/ui/screenshot.py OUT.png --overlay [--backdrop busy] [--half-spacing 40] [--size 1920x1080 --width-mm 530] [--layer 1] [--press 3,15] [--anchor top-right]`
- Key tooltips (no pointer moves): `PYTHONPATH=src python3 tests/ui/tooltip_shot.py PREFIX [--layer N] KEY…`
- Release screenshots in `data/screenshots/`: see the "Screenshots come from the mock service" decision in PROGRESS.md for the exact flags.
