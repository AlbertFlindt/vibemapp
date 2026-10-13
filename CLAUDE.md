# Vibemapp

GNOME/libadwaita clone of ZSA Keymapp for Arch Linux (Python + PyGObject). Headline
feature: **Smart Layers** — switch the ZSA keyboard's layer when the focused app
changes (Hyprland only). Also: live layout view, click-through layout overlay
(gtk4-layer-shell), opt-in key heatmap, tray icon.
How it works, by feature: @docs/SPEC.md. Handoff state, decisions, dead ends: @PROGRESS.md.
Release history: `CHANGELOG.md` (not loaded; read it when needed).

## Install (Arch)
- Runtime: `python python-gobject gtk4 libadwaita glib2`. Build: `meson`.
  `./check.sh` also needs `desktop-file-utils appstream`.
- Optional: `hyprland` (the only focus source), a StatusNotifier tray host
  (e.g. Noctalia, waybar) for the tray icon, Steam for Steam-game rules,
  `gtk4-layer-shell` (required for the overlay; without it the overlay is off).
- Keyboard: ZSA board with Oryx firmware (raw HID). Nothing from pip.
- `cd packaging/arch && makepkg -si` (PKGBUILD builds the `v$pkgver` tag from GitHub, so
  commit, tag and push first; use `./run-dev.sh` for untagged changes. Replaces the old
  `keymappforlinux` package), then replug the keyboard (udev `uaccess` rules).
- Autostart goes in `~/.config/hypr/autostart.lua`:
  `hl.on("hyprland.start", function () hl.exec_cmd("vibemapp --background") end)`

## Commands
- **Health check (lint + all tests): `./check.sh`**. Run it before every commit.
- Run from source: `./run-dev.sh`. Simulated keyboard: `VIBEMAPP_MOCK=1 ./run-dev.sh`.
  run-dev sets `VIBEMAPP_DEVEL=1` → app ID `io.github.albertflindt.Vibemapp.Devel`
  (striped `devel` header, "Vibemapp (Development)"), so it runs beside the installed
  app. It shares the real config, cache and GSettings. With the real keyboard both
  instances drive Smart Layers, so use `VIBEMAPP_MOCK=1` or quit the installed one. The dev
  window shows a banner with a "Quit Installed App" button while it runs (`ui/instance_check.py`).
- Single test module: `PYTHONPATH=src python3 -m unittest tests.backend.test_steam`
- Staged install: `meson setup build --prefix=/usr && meson install -C build --destdir /tmp/stage`
- Screenshot (no grim on this machine; GTK renders it):
  `PYTHONPATH=src python3 tests/ui/screenshot.py OUT.png [--page smart-layers|heatmap|overlay] [--dialog add-rule|preferences|…]`
  Tray menu (private dbus-daemon, real dbusmenu layout): `PYTHONPATH=src python3 tests/ui/tray_menu_shot.py OUT.png [--state ready|no-keyboard|smart-off] [--window-visible]`.
  Overlay over a checkerboard/busy backdrop: `PYTHONPATH=src python3 tests/ui/screenshot.py OUT.png --overlay [--backdrop busy] [--half-spacing 40] [--size 1920x1080 --width-mm 530] [--layer 1] [--press 3,15] [--anchor top-right]`.
  Key tooltips (no pointer moves): `PYTHONPATH=src python3 tests/ui/tooltip_shot.py PREFIX [--layer N] KEY…`
- Lint is byte-compile plus data validation inside `check.sh`. No ruff, flake8 or mypy installed.

## Architecture
- `src/vibemapp/models.py` + `service.py`: **the contract**. The UI only talks to a
  `KeyboardService` (`backend/zsa_service.py` real, `mock_service.py` fake). All its
  signals are emitted on the GLib main loop.
- `backend/`: `devices` (sysfs hidraw), `oryx` (raw-HID protocol), `oryx_api`
  (GraphQL layout + cache), `focus/hyprland` (IPC sockets), `smart_layers` (rules
  engine + JSON), `steam`, `heatmap`, `migration`, `geometries`.
- `tray/`: StatusNotifierItem + dbusmenu over Gio D-Bus (no GTK).
- `ui/`: libadwaita pages and dialogs. `.ui` files are GtkBuilder XML loaded from the
  package dir. App lifecycle, tray and signals live in `main.py`. Overlay: `overlay.py`
  (layer-shell surfaces), `overlay_state.py` (pure logic, unit-tested), `overlay_page.py`;
  `view_binder.py` wires key/layer signals into any `KeyboardView`.
- `data/`: desktop, metainfo, gschema, icons, `udev/50-zsa.rules`.

## Gotchas and constraints
- **Never flash firmware or write EEPROM.** `oryx.ALLOWED_COMMANDS` allows only fw
  version, pairing, set-layer and protocol version. Live hardware tests must
  return the keyboard to layer 0 (try/finally).
- The Oryx protocol source is `zsa/qmk_modules` (`oryx/oryx.c`), not
  `keyboards/zsa/common`. Reports are 32 bytes: write with a leading `0x00`, and replies end at `0xFE`.
- Oryx key index order is the whole left half, then the right half (not QMK's row
  interleave). Geometry, layouts, key events and heatmap all use it.
- Contract and rules JSON changes must stay additive and backward compatible (a missing
  `match` field means `APP`). Update `models.py`/`service.py`, then the backend, mock and UI together.
- Hyprland config is **Lua** (`hl.on`, `hl.exec_cmd`, `hl.bind`, `hl.dsp.exec_cmd`), not hyprlang.
  Vibemapp only reads Hyprland state (`j/` queries); it never sends `keyword`/dispatch.
  `HYPRLAND_INSTANCE_SIGNATURE` may be unset; the code globs `$XDG_RUNTIME_DIR/hypr/*/`.
- The GSettings schema has a fixed path. `Gio.Settings.new_full(schema, None, other_path)`
  **aborts the process**. `VIBEMAPP_MEMORY_SETTINGS=1` keeps test runs out of dconf.
- PyGObject 3.58.0 + Python 3.14.7 emit an asyncio DeprecationWarning from `gi/events.py`
  (still true 2026-10-12). It's filtered with `-W ignore::DeprecationWarning:gi.events` where
  unittest runs (check.sh, `packaging/arch/PKGBUILD`); app runs hide it by default.
- **gtk4-layer-shell must be loaded before GTK.** `vibemapp/_preload.py` `ctypes.CDLL`s
  `libgtk4-layer-shell.so.0` and must be the first import in every entry point
  (`build-aux/vibemapp.in`, `__main__.py`, `main.py`, `tests/__init__.py`, the `tests/ui/*shot.py`
  tools). Imported later, `Gtk4LayerShell.is_supported()` is False and the overlay is off (no
  fallback window). Overlay windows are never added to the Gtk.Application (they'd keep it alive).
  `VIBEMAPP_NO_LAYER_SHELL=1` simulates the package missing. Tests and the screenshot tool use
  `tests/ui/shot_util.FakeLayerShell` so surfaces are built off screen.
- During tests, "Skipping rule with unknown match kind" and "Tray registration failed"
  are expected log lines, not failures.
- System packages only. There's no pip, pytest or blueprint-compiler; use stdlib `unittest`.
- UI follows the GNOME HIG: libadwaita widgets and colours only, title-case headings,
  user-visible strings in `_()` (gettext domain `vibemapp`).
- The PKGBUILD lives in `packaging/arch/` on purpose. `makepkg -c/-C` runs `rm -rf ./src`,
  which would wipe our source tree if the PKGBUILD sat at the repo root.
- Release bump touches: `meson.build` version, `packaging/arch/PKGBUILD` pkgver,
  `ui/resources.py` dev fallback, a metainfo `<release>` entry and a `CHANGELOG.md` section.
  Metainfo screenshot URLs point at the tag (`.../vibemapp/vX.Y.Z/data/screenshots/`), so bump them too.
  Then commit, `git tag vX.Y.Z`, `git push origin main --tags`, and publish a GitHub release
  with that version's changelog section (write NOTES.md in the scratchpad, not the repo):
  `gh release create vX.Y.Z --title "Vibemapp X.Y.Z" --notes-file NOTES.md`.
  Repo: https://github.com/AlbertFlindt/vibemapp
