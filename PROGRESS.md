# Progress / handoff

**Last updated:** 2026-10-10. **Version:** 1.0.0 (version bump only).
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
    - Checked against the user's cached layout. Not yet seen in the running app on real hardware.

  `makepkg -f` from the tag succeeds. The user hasn't installed 1.0.0 yet. No work is in progress.

## Next steps
1. **Look at transparent keys in the real app** (`./run-dev.sh`, then layers 1–4 of "Flindt 1.1"). Ship them in 1.0.1 (step 5).
2. **Live-verify key events.** Turn on heatmap recording, type on the Voyager, and check that
   counts land on the right keys in the Heatmap page. Index mapping is in `backend/oryx.py` and
   `backend/geometries.py`; it's col/row in the event, then Oryx order.
3. **Translations.** Add a `po/` directory (`POTFILES`, `LINGUAS`) and meson `i18n.gettext('vibemapp')`.
   Strings are already wrapped in `_()`.
4. **Metainfo screenshots.** Add `<screenshots>` to `data/io.github.albertflindt.Vibemapp.metainfo.xml`
   using images from `tests/ui/screenshot.py` (layout, smart layers, heatmap), stored in `data/screenshots/`.
5. **Next release (1.0.1):** the tray `__pycache__` fix and transparent keys only reach packages once a new tag
   exists, because the PKGBUILD builds from the tag. Until then, `makepkg` warns
   "Package contains reference to $srcdir". The warning is harmless (stale .pyc files). To release:
   1. Bump the version (CLAUDE.md lists the files).
   2. Commit, then `git tag v1.0.1 && git push origin main --tags`.

## Decisions
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
- **No failing tests.** `./check.sh` passes: 156 tests plus lint and data validation, OK on 2026-10-10 after the transparent-keys change.
  `makepkg -f` and `makepkg -fc` from `packaging/arch/` also succeed (the package wasn't installed).
- **PyGObject asyncio warning:** the upstream PyGObject 3.58 DeprecationWarning is filtered, not fixed. Drop the `-W` filter once PyGObject is updated.
- **GSettings dead end:** `Gio.Settings.new_full(schema, None, old_path)` aborts on a fixed-path schema. Migration
  compiles a relocatable copy of the schema into a temp dir instead (`ui/settings.py`).
- **Test-teardown dead end:** `Gio.TestDBus` stalled the suite for 30 s on teardown. Tray tests use a private `dbus-daemon`.
- **Can't screenshot the tray:** there's no grim or screenshot portal, so tray menus can't be screenshotted and are verified over D-Bus instead.
- **Synthetic cursor stalls:** synthetic cursor warps stall once they pass over an open tooltip (`tests/ui/tooltip_shot.py`). This is a quirk of the test method, not an app bug.
- **Stale dconf key:** the user's dconf still holds an unused `/io/github/albertflindt/Vibemapp/last-page`. It's harmless; `dconf reset` clears it.
- **Dev runs can hit the installed app:** `run-dev.sh` shares the installed app's ID, so quit the installed Vibemapp first.
