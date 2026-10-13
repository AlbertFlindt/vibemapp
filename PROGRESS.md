# Progress / handoff

**Last updated:** 2026-10-13. **Released:** 1.1.0 (layout overlay), installed on the user's machine.
How things work: `docs/SPEC.md`. What changed per release: `CHANGELOG.md`.

## Current state

**Verified on hardware (Voyager, Hyprland 0.56, two monitors):**
- Detection, Oryx handshake, layout fetch ("Flindt 1.1", 4 layers) and offline cache.
- Live layer display and live key presses (KEYDOWN/KEYUP arrive in real time).
- Smart Layers: app, window-class regex and all-Steam-games rules, fallback layer, layer 0
  restored on quit/SIGTERM.
- Transparent keys in the layout view.
- Overlay: click-through, key highlighting, follow-focused and fixed monitor, physical size on
  both monitors, half spacing, SUPER+O `--toggle-overlay` bind with the installed package.
- Heatmap: counts land on the right keys (user-confirmed 2026-10-13).
- Tray (Noctalia), migration from `keymappforlinux`, dev app ID.

**Not verified:**
- Steam game focus with a real game running (unit tests only).
- Moonlander, ErgoDox EZ and Planck EZ: geometry and key order come from QMK data; thumb-key
  angles are estimates.
- Overlay with fractional monitor scaling, and the overlay on a Planck (no halves).

## Next steps
1. **Translations.** Add `po/` (`POTFILES`, `LINGUAS`) and meson `i18n.gettext('vibemapp')`.
   Strings are already in `_()`.

## Workflow
- The user likes a PM + subagents setup (UX expert, backend expert): propose the design and
  agree on it before coding, give each agent its own files, and the PM reviews, runs
  `./check.sh` and shows screenshots.
- Releases: GitHub release with the CHANGELOG section, a metainfo `<release>`, tag-pinned
  screenshot URLs (steps in CLAUDE.md).

## Decisions
- **Overlay requires gtk4-layer-shell, no fallback window:** the user keeps the app lean with
  optional packages, as long as the UI says what to install.
- **Overlay settings live on their own sidebar page**, not in Preferences: it has many tweaks.
- **Overlay shows whenever a selected layer is active**, not only while a layer key is held.
- **Overlay size is % of the real keyboard size** (same look on every monitor), default 80;
  half spacing is a 0–100 % value, not a toggle.
- **Screenshots come from the mock service** (`data/screenshots/`, `tests/ui/screenshot.py` and
  `tray_menu_shot.py`; heatmap with `VIBEMAPP_MOCK_HEATMAP_SEED=1 --record --size 1000x1000`;
  overlay page with `--page overlay --overlay-layers 1,2 --size 1000x1000`): no personal data.
- **PKGBUILD builds the GitHub tag, not the working tree:** reproducible packages. Tag and push
  before `makepkg`; use `./run-dev.sh` for untagged work.
- **PKGBUILD in `packaging/arch/`:** `makepkg -c/-C` deletes `./src`, which at the repo root was our code.
- **System Python, PyGObject and libadwaita:** nothing from pip, no Rust toolchain.
- **Hyprland only as focus source:** it's the user's desktop. `FocusWatcher` is pluggable.
- **No flashing or EEPROM writes:** the user chose it; the command allow-list enforces it.
- **`uaccess` udev rules instead of ZSA's `plugdev` group:** no group membership needed.
- **Restore layer 0 on exit:** otherwise the keyboard stays on the last app's layer.
- **Fallback layer defaults to "Don't change":** least surprising for a new layout.
- **Ignore our own window's focus:** opening Vibemapp to edit rules shouldn't reset the layer.
- **Auto-connect is a service property:** the UI binds it to GSettings, so there's one policy.
- **Renamed to Vibemapp with full migration:** the user asked for a complete rename.
- **Heatmap is opt-in and stores counts only:** privacy; typing can't be reconstructed.
- **Close hides to the tray:** Hyprland has no minimise.
- **Steam detection also reads the process environment:** a class regex misses native games.
- **Single-game Steam picker removed:** too Steam-focused. "All Steam Games" stays.
- **Transparent keys fall through to the base layer only** (`transparency.BASE_ONLY`): set-layer is
  `layer_move` (only target and base active), and LT/MO layers are reached from the base. A full
  stack walk showed misleading blanks. Switch in `KeyboardView.set_layer` if that changes.
- **Always open on the Layout page:** the user asked for it; `last-page` was removed.
- **Tray menu** (user-approved): status header / Show|Hide Vibemapp / Smart Layers / Show Overlay
  / Preferences / Quit. No mnemonics.
- **Dev-run conflict is a warning, not a block:** banner with "Quit Installed App".

## Known issues / dead ends
- **Tests:** `./check.sh` passes, 248 tests plus lint and data validation (2026-10-13).
- **PyGObject asyncio warning:** PyGObject 3.58.0 on Python 3.14.7 still emits it (2026-10-12).
  Filtered only where unittest runs (check.sh, PKGBUILD `check()`). Drop the filter once fixed upstream.
- **GSettings dead end:** `Gio.Settings.new_full(schema, None, old_path)` aborts on a fixed-path
  schema. Migration and obsolete-key cleanup use a relocatable copy (`ui/settings.py` `_relocatable_schema()`).
- **gtk4-layer-shell load order:** must load before libwayland, so `_preload.py` comes first in every
  entry point. Imported later, `is_supported()` is quietly False.
- **Test-teardown dead end:** `Gio.TestDBus` stalled 30 s on teardown. Tray tests use a private `dbus-daemon`.
- **Screenshots:** no grim or screenshot portal. GTK renders pages, `tray_menu_shot.py` draws the real
  dbusmenu layout, `tooltip_shot.py` calls `do_query_tooltip` (synthetic pointer warps stall on Hyprland),
  and the overlay is drawn over a test backdrop. None are captures of the real compositor.
- **Dev and installed side by side:** with the real keyboard both drive Smart Layers. The warning banner
  lives in the window, so a dev run in the tray with no window doesn't warn. Accepted for a dev tool.
- **No `.Devel` desktop file or icon:** fine on Hyprland and Noctalia; GNOME Shell wouldn't match it.
- **User's GTK theme warnings:** `~/.config/gtk-4.0/gtk.css` (adw-gtk3 symlink) imports missing
  `libadwaita*.css`, so every GTK4 app prints two import warnings. Not ours; harmless.
