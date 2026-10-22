# Vibemapp

GNOME/libadwaita clone of ZSA Keymapp for Arch Linux (Python + PyGObject). Headline
feature: **Smart Layers** — switch the ZSA keyboard's layer when the focused app
changes (Hyprland only). Also: live layout view, click-through layout overlay
(gtk4-layer-shell), opt-in key heatmap, tray icon.
Handoff state, decisions, dead ends: @PROGRESS.md.
How it works, by feature: `docs/SPEC.md` (not loaded; read the feature's section before changing it,
and update it with the change). Release history: `CHANGELOG.md` (not loaded).

## Install (Arch)
- `cd packaging/arch && makepkg -si` (PKGBUILD builds the `v$pkgver` tag from GitHub, so
  commit, tag and push first; use `./run-dev.sh` for untagged changes. Replaces the old
  `keymappforlinux` package), then replug the keyboard (udev `uaccess` rules).

## Commands
- **Health check (lint + all tests): `./check.sh`**. Run it before every commit.
- Run from source: `./run-dev.sh`. Simulated keyboard: `VIBEMAPP_MOCK=1 ./run-dev.sh`.
  run-dev sets `VIBEMAPP_DEVEL=1` → app ID `io.github.albertflindt.Vibemapp.Devel`
  (striped `devel` header, "Vibemapp (Development)"), so it runs beside the installed
  app. It shares the real config, cache and GSettings. With the real keyboard both
  instances drive Smart Layers, so use `VIBEMAPP_MOCK=1` or quit the installed one. The dev
  window shows a banner with a "Quit Installed App" button while it runs (`ui/instance_check.py`).
- Single test module: `PYTHONPATH=src python3 -m unittest tests.backend.test_steam`
- Screenshots: the `screenshots` skill (`.claude/skills/screenshots/`). No grim or screenshot portal.
- Lint is byte-compile plus data validation inside `check.sh`. No ruff, flake8 or mypy installed.

## Architecture
- `src/vibemapp/models.py` + `service.py`: **the contract**. The UI only talks to a
  `KeyboardService` (`backend/zsa_service.py` real, `mock_service.py` fake). All its
  signals are emitted on the GLib main loop.

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
- Releases: run the `/release X.Y.Z` skill (`.claude/skills/release/`). Repo: https://github.com/AlbertFlindt/vibemapp
