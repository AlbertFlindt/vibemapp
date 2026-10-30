# Progress / handoff

**Last updated:** 2026-10-30. **Latest release:** 1.7.0 (details in `CHANGELOG.md`).
How things work: `docs/SPEC.md`.

## Current state

**Verified on hardware (Voyager, Hyprland 0.56, two monitors):**
- Detection, Oryx handshake, layout fetch ("Flindt 1.1", 4 layers) and offline cache.
- Live layer display and live key presses (KEYDOWN/KEYUP arrive in real time).
- Smart Layers: app, window-class regex and all-Steam-games rules, fallback layer, layer 0
  restored on quit/SIGTERM.
- Transparent keys in the layout view.
- Overlay: click-through, key highlighting, follow-focused and fixed monitor, physical size on
  both monitors, half spacing, SUPER+O `--toggle-overlay` bind with the installed package.
- Heatmap: counts land on the right keys (2026-10-13).
- Heatmap per app and Insights recording (2026-10-18).
- Tray (Noctalia), migration from `keymappforlinux`, dev app ID.

**Not verified:**
- Steam game focus with a real game running (unit tests only).
- Moonlander, ErgoDox EZ and Planck EZ: geometry and key order come from QMK data; thumb-key
  angles are estimates.
- Overlay with fractional monitor scaling, and the overlay on a Planck (no halves).
- Layout sync banners (1.7.0): that the firmware serial's revision equals Oryx's revision `hashId`
  is assumed (fetching by it and the cache already rely on it), and the newest-revision query
  wasn't run against the live Oryx API (blocked in the cloud session). Check with an unflashed edit.
- A changed layout-wide tapping term: Oryx's `config` holds only non-default settings and no
  layout with a custom term has been seen, so its name there (`tappingTerm`) is a guess. Check
  the cached layout JSON after changing it in Oryx. Per-key terms are verified to exist.

## Next steps
1. **Translations.** Add `po/` (`POTFILES`, `LINGUAS`) and meson `i18n.gettext('vibemapp')`.
   Strings are already in `_()`.

## Workflow
- Agree on the design before coding. UX and backend work are split by file; every change
  is reviewed, passes `./check.sh` and comes with screenshots.
- Releases: every PR that changes the app bumps the version (the `release` skill); merging
  it tags and publishes the release via GitHub Actions.

## Decisions
- **Vibemapp owns the overlay's behaviour, not the compositor:** e.g. the fade is our own
  animation with a setting, rather than Hyprland's layer animations.
- **Insights is Finger Load only** (2026-10-29): the owner wasn't sold on the score, same-finger pairs,
  reach, layer switches and Alternative Layouts. Their backend stays (additive contract); pair recording is off.
- **Overlay requires gtk4-layer-shell, no fallback window:** the app stays lean with
  optional packages, as long as the UI says what to install.
- **Overlay settings live on their own sidebar page**, not in Preferences: it has many tweaks.
- **Hold actions show as an on-screen pill, not a desktop notification** (2026-10-29): Albert
  holds C for Ctrl+C every few seconds; a notification would pile up and may play a sound.
  The firmware never says tap or hold, so it's inferred from tap-dance timing (SPEC 4.3.1).
  Its settings sit on the Overlay page (same layer-shell requirement). Not verified on hardware.
- **Overlay shows whenever a selected layer is active**, not only while a layer key is held.
- **Overlay size is % of the real keyboard size** (same look on every monitor), default 80;
  half spacing is a 0–100 % value, not a toggle.
- **Screenshots come from the mock service** (`data/screenshots/`, `tests/ui/screenshot.py` and
  `tray_menu_shot.py`; heatmap with `VIBEMAPP_MOCK_HEATMAP_SEED=1 --record --size 1000x1000`; insights the same with
  `--page insights --size 1000x700`;
  overlay page with `--page overlay --overlay-layers 1,2 --size 1000x1300`; tray with `--window-visible --overlay`;
  `overlay-view.png` is `tests/ui/overlay_live.py --layer 1 --press 3,15` (the real layer-shell overlay)
  over GNOME Text Editor and foot, tiled in sway at 1920x1080 on the `adwaita-d` wallpaper, taken with
  `grim`): no personal data. The mock uses the real Voyager geometry (`geometries.py`), so thumbs match the app. Dark style, blue accent, Adwaita Sans, on a Wayland
  compositor with layer-shell (else the Overlay page shows "Overlay Unavailable"). Cloud sessions
  (Ubuntu, old libadwaita) rendered them in an Ubuntu 26.04 debootstrap chroot under headless sway
  with `ADW_DEBUG_COLOR_SCHEME=prefer-dark ADW_DEBUG_ACCENT_COLOR=blue` (Ubuntu's default accent is orange).
- **PKGBUILD builds the GitHub tag, not the working tree:** reproducible packages. Tag and push
  before `makepkg`; use `./run-dev.sh` for untagged work.
- **Distribution, if any, is the AUR** (2026-10-18): no AppImage or Flatpak (GTK4 bundling; the sandbox
  blocks hidraw, the Hyprland socket and `/proc` for Steam). `appstreamcli` is an optional lint in `check.sh`.
- **PKGBUILD in `packaging/arch/`:** `makepkg -c/-C` deletes `./src`, which at the repo root was our code.
- **System Python, PyGObject and libadwaita:** nothing from pip, no Rust toolchain.
- **Hyprland only as focus source:** it's the target desktop. `FocusWatcher` is pluggable.
- **No flashing or EEPROM writes:** by design; the command allow-list enforces it.
- **`uaccess` udev rules instead of ZSA's `plugdev` group:** no group membership needed.
- **Restore layer 0 on exit:** otherwise the keyboard stays on the last app's layer.
- **Fallback layer defaults to "Don't change":** least surprising for a new layout.
- **Ignore our own window's focus:** opening Vibemapp to edit rules shouldn't reset the layer.
- **Auto-connect is a service property:** the UI binds it to GSettings, so there's one policy.
- **Renamed to Vibemapp with full migration:** a complete rename, nothing left under the old name.
- **Heatmap is opt-in and stores counts only:** privacy; typing can't be reconstructed.
- **Per-app heatmap counts are part of the heatmap opt-in** (window class or Steam game, never
  titles); `days` stays the all-apps total so old files load unchanged.
- **Ergo score gets its own Insights sidebar page**; the Heatmap page only gains the App filter.
- **No rule-based coach** (dropped 2026-10-20 after trying it on a Voyager). It
  suggested odd swaps (O↔Æ, Shift↔U); even restricted to non-letter keys it wasn't useful. Letters are
  compared as whole well-known layouts instead (Alternative Layouts), with Æ Ø Å kept in place.
- **Close hides to the tray:** Hyprland has no minimise.
- **Steam detection also reads the process environment:** a class regex misses native games.
- **Single-game Steam picker removed:** too Steam-focused. "All Steam Games" stays.
- **Transparent keys fall through to the base layer only** (`transparency.BASE_ONLY`): set-layer is
  `layer_move` (only target and base active), and LT/MO layers are reached from the base. A full
  stack walk showed misleading blanks. Switch in `KeyboardView.set_layer` if that changes.
- **Always open on the Layout page:** `last-page` was removed.
- **Tray menu**: status header / Show|Hide Vibemapp / Smart Layers / Show Overlay
  / Preferences / Quit. No mnemonics.
- **Dev-run conflict is a warning, not a block:** banner with "Quit Installed App".

## Known issues / dead ends
- **GSettings dead end:** `Gio.Settings.new_full(schema, None, old_path)` aborts on a fixed-path
  schema. Migration and obsolete-key cleanup use a relocatable copy (`ui/settings.py` `_relocatable_schema()`).
- **Overlay must never unmap to hide:** Hyprland then moves keyboard focus off other layer-shell
  panels (found with Noctalia's session menu). It parks on BACKGROUND instead (`ui/overlay.py`).
- **Test-teardown dead end:** `Gio.TestDBus` stalled 30 s on teardown. Tray tests use a private `dbus-daemon`.
- **Screenshots:** no grim or screenshot portal. GTK renders pages, `tray_menu_shot.py` draws the real
  dbusmenu layout, `tooltip_shot.py` calls `do_query_tooltip` (synthetic pointer warps stall on Hyprland),
  and the overlay is drawn over a test backdrop. None are captures of the real compositor.
- **Dev and installed side by side:** with the real keyboard both drive Smart Layers. The warning banner
  lives in the window, so a dev run in the tray with no window doesn't warn. Accepted for a dev tool.
- **No `.Devel` desktop file or icon:** fine on Hyprland and Noctalia; GNOME Shell wouldn't match it.
