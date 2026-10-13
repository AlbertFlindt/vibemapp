# Progress / handoff

**Last updated:** 2026-10-13. **Version:** 1.1.0 (layout overlay).
The PKGBUILD has since moved to `packaging/arch/`, so install with `cd packaging/arch && makepkg -si`.

## Current state
- **Working and verified:**
  - **Device and layout:**
    - Voyager detection and the Oryx handshake.
    - The live layout ("Flindt 1.1", 4 layers) is fetched from Oryx and cached offline.
    - Live active-layer display (key-press highlighting: see "unverified" below).
  - **Smart Layers:**
    - Rules for an exact app, a window-class regex, or all Steam games.
    - Fallback layer.
    - Restores layer 0 on quit or SIGTERM.
  - **Steam detection:** a `steam_app_<id>` class or `SteamAppId` in `/proc/<pid>/environ`.
  - **Heatmap:** opt-in, counts only.
  - **Tray:** a StatusNotifierItem registered with Noctalia. Closing the window hides to the tray.
  - **Migration:** settings and dirs carried over from the old `keymappforlinux` name.
  - **Other:** Hyprland Lua autostart hint, always opens on the Layout page.
- **Partially done or unverified:**
  - **Steam game focus:** detection was never tested with a real game running. It's covered by unit tests only.
  - **Live key presses:** highlighting and heatmap counting from real key presses on hardware haven't been checked live.
  - **Other ZSA boards:** Moonlander, ErgoDox EZ and Planck EZ geometry and key order come from QMK data. They haven't been checked on hardware, and the thumb-key angles are estimates.
  - **Single-game Steam rules** (`RuleMatch.STEAM_GAME` with an app ID) still work, display and edit. They can no longer be created from the UI, because the user removed that option.
- **Where we stopped:** 1.0.0 is released and pushed to https://github.com/AlbertFlindt/vibemapp
  (`main` + tag `v1.0.0`). Since then, also on `main`:
  - The PKGBUILD builds the `v$pkgver` tag from GitHub.
  - Tray `__pycache__` is excluded from the install.

  - A short `README.md` (features, required packages, Arch install, running from source),
    added 2026-10-10.
  - **Transparent keys** (2026-10-10). Oryx stores a transparent key as an all-null key object
    (`tap`/`hold`/`doubleTap`/`tapHold` all null), and that used to render blank. Now:
    - `KeyBinding.transparent` (additive) is set by `oryx_api.key_binding_from_json`. `KC_TRNS`/`KC_TRANSPARENT` count as transparent too; `KC_NO` doesn't.
    - The layout view draws transparent keys as a faint, dashed cap. It shows the dimmed fall-through label from the base layer
      plus a small ▽, and a tooltip names the source key. `KC_NO` blanks keep a solid faint cap.
    - The logic is in `ui/transparency.py`, with tests in `tests/ui/test_transparency.py`. The heatmap shows a dimmed ▽.
    - Checked against the user's cached layout, and the user confirmed it looks right in `./run-dev.sh`.
  - **1.0.1 released** (tag `v1.0.1`, pushed): transparent keys plus the tray `__pycache__` fix.

  - **Known-issues pass** (2026-10-12), released as **1.0.2** with the first GitHub release:
    - Dev runs use app ID `io.github.albertflindt.Vibemapp.Devel` (`appid.py`, `VIBEMAPP_DEVEL=1` from run-dev.sh),
      striped `devel` header and "Vibemapp (Development)" title. Both IDs count as "own window" for Smart Layers.
      Config, cache and GSettings stay shared with the installed app.
    - One-time Gio-native reset of obsolete dconf keys (`ui/settings.py` `OBSOLETE_KEYS`, guarded by the new
      `obsolete-keys-version` schema key). Clears the stale `last-page` key on the next start.
    - `GLibUnix.fd_add_full` / `GLibUnix.signal_add` replace deprecated GLib calls (with fallback).
    - New `tests/ui/tray_menu_shot.py` (renders the real tray menu) and a pointer-free `tests/ui/tooltip_shot.py`.
    - Dev-run warning: a non-mock dev run watches for the installed app on D-Bus (`ui/instance_check.py`) and shows
      an `Adw.Banner` with "Quit Installed App" (`org.gtk.Actions` `quit`). The user approved a warning, not a block.
    - Tray menu (user-approved): status header on top / — / Show|Hide Vibemapp (follows window visibility) /
      Smart Layers / — / Preferences / Quit. No mnemonics (user's choice).

  - **Overlay, released as 1.1.0** (2026-10-13). Built by the PM + UX/backend subagent team.
    - `ui/overlay.py` (layer-shell surfaces + `OverlayController`, owned by `Application`, not
      attached to it), pure logic in `ui/overlay_state.py` (visibility, physical sizing at
      19.05 mm/unit with a 96 DPI fallback, half-spacing placement), `ui/view_binder.py`
      (shared key/layer wiring for Layout page and overlay), `ui/overlay_page.py`/`.ui` (sidebar page).
    - `_preload.py` must be imported before any Gtk import (gtk4-layer-shell load order).
    - Additive contract: `get_pressed_keys()`, `get_focused_monitor()`, `focused-monitor-changed`.
    - Verified by the user on hardware: click-through, live key highlighting (this also
      confirms live key events), monitor follow/fixed, physical size on both monitors, half spacing.
      `--toggle-overlay` was verified against a running dev instance, not yet from the user's
      Hyprland bind with the installed package.

## Next steps
1. **Live-verify heatmap counts.** Key events are confirmed live via the overlay; still check that
   heatmap counts land on the right keys. Also confirm the SUPER+O `--toggle-overlay` bind with 1.1.0 installed.
2. **Translations.** Add a `po/` directory (`POTFILES`, `LINGUAS`) and meson `i18n.gettext('vibemapp')`.
   Strings are already wrapped in `_()`.

## Decisions
- **Overlay requires gtk4-layer-shell, no fallback window:** the user keeps the app lean with optional
  packages, as long as the UI says what to install.
- **Overlay settings live on their own sidebar page**, not in Preferences: it has many tweaks.
- **Overlay shows whenever a selected layer is active**, not only while a layer key is physically held.
- **Overlay size is % of the real keyboard size** (same look on every monitor), default 80; half
  spacing is a 0–100 % value, not a toggle.
- **GitHub releases with `CHANGELOG.md`:** the user wants releases with changelogs, not bare tags. Each release
  gets a CHANGELOG section, a metainfo `<release>`, and `gh release create` with those notes.
- **Screenshots come from the mock service** (`data/screenshots/`, rendered with `tests/ui/screenshot.py` and
  `tray_menu_shot.py`, heatmap with `VIBEMAPP_MOCK_HEATMAP_SEED=1 --record --size 1000x1000`): no personal data in them.
- **PKGBUILD builds the GitHub tag, not the working tree:** reproducible packages that match the
  release. The cost is that you must tag and push before `makepkg`; use `./run-dev.sh` for untagged work.
- **1.0.0 is a version bump only:** the unverified items above remain open.
- **PKGBUILD in `packaging/arch/`:** `makepkg -c/-C` deletes `./src`, which at the repo root was our code. Verified safe after the move.
- **Python, PyGObject and libadwaita:** all already installed, so nothing from pip and no Rust toolchain needed.
- **Hyprland only as focus source:** it's the user's desktop. `FocusWatcher` is pluggable for GNOME, KDE or X11 later.
- **No flashing in v1:** the user chose it, and the command allow-list enforces it.
- **`uaccess` udev rules instead of ZSA's `plugdev` group:** no need to add the user to a group.
- **Restore layer 0 on exit:** otherwise the keyboard stays on the last app's layer.
- **Fallback layer defaults to "Don't change":** least surprising for a new layout.
- **Ignore our own window's focus:** opening Vibemapp to edit rules shouldn't reset the layer.
- **Auto-connect is a service property:** the UI binds it to GSettings, so there's one policy.
- **Renamed to Vibemapp with full migration:** the user asked for a complete rename.
- **Heatmap is opt-in and stores counts only:** privacy; it can't be used to reconstruct typing.
- **Close hides to the tray:** Hyprland has no minimise, so "minimise to tray" means close-to-tray.
- **Steam detection also reads the process environment:** a class regex alone misses native Linux games.
- **Single-game Steam picker removed:** the user felt it was too Steam-focused. "All Steam Games" stays.
- **Transparent keys fall through to the base layer only** (`transparency.BASE_ONLY`): Smart Layers'
  set-layer is `layer_move` (only the target and base are active), and LT/MO layers are reached from the base.
  Walking the whole layer stack showed misleading blanks. Switch to the full walk in `KeyboardView.set_layer` if that changes.
- **Always open on the Layout page:** the user asked for it, so the `last-page` setting was removed.

## Known issues / dead ends
- **No failing tests.** `./check.sh` passes: 181 tests plus lint and data validation, OK on 2026-10-12.
- **PyGObject asyncio warning:** still emitted by PyGObject 3.58.0 on Python 3.14.7 (checked 2026-10-12). It's filtered
  only where unittest runs (check.sh, PKGBUILD `check()`); app runs hide DeprecationWarnings by default. Drop the filter once PyGObject is updated.
- **GSettings dead end:** `Gio.Settings.new_full(schema, None, old_path)` aborts on a fixed-path schema. Migration and the
  obsolete-key cleanup compile a relocatable copy of the schema into a temp dir instead (`ui/settings.py` `_relocatable_schema()`).
- **Test-teardown dead end:** `Gio.TestDBus` stalled the suite for 30 s on teardown. Tray tests use a private `dbus-daemon` (about 1 s).
- **Tray screenshots (mitigated):** there's still no grim or screenshot portal. `tests/ui/tray_menu_shot.py` fetches the real
  dbusmenu layout on a private bus and draws it with GTK. It's a faithful rendering, not a capture of Noctalia's own popup.
- **Tooltip stalls (fixed by changing method):** on Hyprland the tooltip surface takes pointer focus, so GTK destroys it and stops
  sending motion events, which made synthetic cursor warps unreliable. `tooltip_shot.py` now calls the view's real
  `do_query_tooltip` and draws a tooltip-styled bubble where GTK would place it. The position isn't pixel-matched against a real tooltip.
- **Dev and installed side by side:** with the real keyboard, both drive Smart Layers. The dev window shows a warning banner, but
  the watcher lives in the window, so a dev run sitting in the tray with no window doesn't warn. Accepted for a developer tool.
- **No `.Devel` desktop file or icon:** the dev ID reuses the base icon. That's fine on Hyprland and Noctalia, but GNOME Shell wouldn't match it.
