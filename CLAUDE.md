# Vibemapp

GNOME/libadwaita clone of ZSA Keymapp for Arch Linux (Python + PyGObject). Headline
feature: **Smart Layers** — switch the ZSA keyboard's layer when the focused app
changes (Hyprland only). Also: live layout view, opt-in key heatmap, tray icon.
Full spec and design history: @docs/SPEC.md. Handoff state: @PROGRESS.md.

## Install (Arch)
- Runtime: `python python-gobject gtk4 libadwaita glib2`. Build: `meson`.
  `./check.sh` also needs `desktop-file-utils appstream`.
- Optional: `hyprland` (the only focus source), a StatusNotifier tray host
  (e.g. Noctalia, waybar) for the tray icon, Steam for Steam-game rules.
- Keyboard: ZSA board with Oryx firmware (raw HID). Nothing from pip.
- `cd packaging/arch && makepkg -si` (PKGBUILD builds the `v$pkgver` tag from GitHub, so
  commit, tag and push first; use `./run-dev.sh` for untagged changes. Replaces the old
  `keymappforlinux` package), then replug the keyboard (udev `uaccess` rules).
- Autostart goes in `~/.config/hypr/autostart.lua`:
  `hl.on("hyprland.start", function () hl.exec_cmd("vibemapp --background") end)`

## Commands
- **Health check (lint + all tests): `./check.sh`**. Run it before every commit.
- Run from source: `./run-dev.sh`. Simulated keyboard: `VIBEMAPP_MOCK=1 ./run-dev.sh`.
  Quit the installed Vibemapp first (tray → Quit). Same app ID, so otherwise
  run-dev just raises the installed window.
- Single test module: `PYTHONPATH=src python3 -m unittest tests.backend.test_steam`
- Staged install: `meson setup build --prefix=/usr && meson install -C build --destdir /tmp/stage`
- Screenshot (no grim on this machine; GTK renders it):
  `PYTHONPATH=src python3 tests/ui/screenshot.py OUT.png [--page smart-layers|heatmap] [--dialog add-rule|preferences|…]`
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
  package dir. App lifecycle, tray and signals live in `main.py`.
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
- Hyprland config is **Lua** (`hl.on`, `hl.exec_cmd`, `hl.window_rule`), not hyprlang.
  `HYPRLAND_INSTANCE_SIGNATURE` may be unset; the code globs `$XDG_RUNTIME_DIR/hypr/*/`.
- The GSettings schema has a fixed path. `Gio.Settings.new_full(schema, None, other_path)`
  **aborts the process**. `VIBEMAPP_MEMORY_SETTINGS=1` keeps test runs out of dconf.
- PyGObject 3.58 + Python 3.14 emit an asyncio DeprecationWarning from `gi/events.py`.
  It's filtered with `-W ignore::DeprecationWarning:gi.events` (check.sh, `packaging/arch/PKGBUILD`).
- During tests, "Skipping rule with unknown match kind" and "Tray registration failed"
  are expected log lines, not failures.
- System packages only. There's no pip, pytest or blueprint-compiler; use stdlib `unittest`.
- UI follows the GNOME HIG: libadwaita widgets and colours only, title-case headings,
  user-visible strings in `_()` (gettext domain `vibemapp`).
- The PKGBUILD lives in `packaging/arch/` on purpose. `makepkg -c/-C` runs `rm -rf ./src`,
  which would wipe our source tree if the PKGBUILD sat at the repo root.
- Release bump touches: `meson.build` version, `packaging/arch/PKGBUILD` pkgver,
  `ui/resources.py` dev fallback and a metainfo `<release>` entry. Then commit, `git tag vX.Y.Z`,
  `git push origin main --tags`. Repo: https://github.com/AlbertFlindt/vibemapp
