# Progress / handoff

**Last updated:** 2026-10-10. **Version:** 0.3.1 (installed by the user with `makepkg -si`, and working).
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
- **Where we stopped:** 0.3.1 was committed with the simpler Add Rule chooser
  (App / All Steam Games / Window Class Pattern) and a quiet `makepkg` build. No work is in progress.

## Next steps
1. **Live-verify Steam rules.** With the user's help, add an "All Steam Games" rule, launch a
   Proton game (e.g. Path of Exile 2, appid 2694490) and a native one. Run with
   `VIBEMAPP_DEBUG=1 ./run-dev.sh` (quit the installed app first) and confirm the layer switches
   and the status row reads "Focused: <game> (Steam)".
2. **Live-verify key events.** Turn on heatmap recording, type on the Voyager, and check that
   counts land on the right keys in the Heatmap page. Index mapping is in `backend/oryx.py` and
   `backend/geometries.py`; it's col/row in the event, then Oryx order.
3. **Translations.** Add a `po/` directory (`POTFILES`, `LINGUAS`) and meson `i18n.gettext('vibemapp')`.
   Strings are already wrapped in `_()`.
4. **Metainfo screenshots.** Add `<screenshots>` to `data/io.github.albertflindt.Vibemapp.metainfo.xml`
   using images from `tests/ui/screenshot.py` (layout, smart layers, heatmap), stored in `data/screenshots/`.
5. **Real GitHub repo.** The `packaging/arch/PKGBUILD` `url`, the About dialog and the metainfo point to the placeholder
   `https://github.com/albertflindt/vibemapp`. Create the repo, or change these to the real URL, and
   switch the PKGBUILD to a git `source=`.

## Decisions
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
- **Always open on the Layout page:** the user asked for it, so the `last-page` setting was removed.

## Known issues / dead ends
- **No failing tests.** `./check.sh` passes: 145 tests plus lint and data validation, OK on 2026-10-10.
  `makepkg -f` and `makepkg -fc` from `packaging/arch/` also succeed (the package wasn't installed).
- **PyGObject asyncio warning:** the upstream PyGObject 3.58 DeprecationWarning is filtered, not fixed. Drop the `-W` filter once PyGObject is updated.
- **GSettings dead end:** `Gio.Settings.new_full(schema, None, old_path)` aborts on a fixed-path schema. Migration
  compiles a relocatable copy of the schema into a temp dir instead (`ui/settings.py`).
- **Test-teardown dead end:** `Gio.TestDBus` stalled the suite for 30 s on teardown. Tray tests use a private `dbus-daemon`.
- **Can't screenshot the tray:** there's no grim or screenshot portal, so tray menus can't be screenshotted and are verified over D-Bus instead.
- **Synthetic cursor stalls:** synthetic cursor warps stall once they pass over an open tooltip (`tests/ui/tooltip_shot.py`). This is a quirk of the test method, not an app bug.
- **Stale dconf key:** the user's dconf still holds an unused `/io/github/albertflindt/Vibemapp/last-page`. It's harmless; `dconf reset` clears it.
- **Dev runs can hit the installed app:** `run-dev.sh` shares the installed app's ID, so quit the installed Vibemapp first.
