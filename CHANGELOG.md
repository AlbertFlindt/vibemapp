# Changelog

All notable changes to Vibemapp. Each version is a tag and a GitHub release.

## 1.6.0 (2026-10-29)

### Added
- **Hold actions on screen** (Overlay page, Hold Actions group, off by default): when holding a
  key sends something, such as hold C for Ctrl+C, a key labelled "Ctrl+C", drawn like the
  overlay's keys, briefly appears near the bottom of the screen, above the overlay if it's
  shown there. It works without the overlay.
  Choose which keys show it under Keys. The keyboard doesn't report taps and holds, so
  Vibemapp tells them apart by timing, using each key's tapping term from Oryx (200 ms unless
  you changed it).

### Changed
- Hold actions with modifiers are labelled with them in the layout, e.g. ⌃C instead of C.

## 1.5.1 (2026-10-29)

### Changed
- The Insights screenshot shows the page as it is now, with Finger Load only.

## 1.5.0 (2026-10-29)

### Changed
- **Insights shows Finger Load only:** the load per finger against its comfortable share, and
  the hand split, with the same Period and App filters.

### Removed
- Same-finger pairs, reach, layer switches, the ergonomic score and Alternative Layouts on the
  Insights page, and the Record Key Pairs switch. Key pairs are no longer recorded; pairs
  recorded earlier stay on disk.

## 1.4.0 (2026-10-23)

### Added
- **Silent Layers** (Preferences, under Notify on Layer Changes): choose layers that never
  cause a layer change notification, such as a mouse layer the keyboard turns on when you
  touch the trackpad. While such a layer is on, switching windows only notifies when the
  layer really differs from the one you were on before it.

## 1.3.1 (2026-10-23)

### Fixed
- The Arch package depends on `python-cairo`. The overlay needs it, and without it Vibemapp
  could fail to start on systems where nothing else had installed it.

## 1.3.0 (2026-10-23)

### Added
- **Layer change notifications** (Preferences, Smart Layers group, off by default): when Smart
  Layers switches the layer, a desktop notification shows the layer's name and the app or
  Steam game it switched for, or that no rule matched and the fallback layer is used. Each
  notification replaces the previous one, and none appear while the Vibemapp window is active.

## 1.2.0 (2026-10-20)

### Added
- **Heatmap per app:** an App filter on the Heatmap page, next to Period and Layer. While
  heatmap recording is on, each key press is also counted for the focused app (its window
  class, or the game's name for Steam games; never window titles). Counts recorded before
  this version show under All Apps.
- **Insights page** (Ctrl+5): an ergonomic score of your typing, shown as its parts: load per
  finger and hand, same-finger pairs (target under 1%) with the worst pairs, reach and layer
  switches, then the total. Key-pair counting is a separate opt-in next to the heatmap one;
  it stores only which key position follows which, never what you type.
- **Alternative Layouts:** your typing scored on QWERTY, Colemak, Colemak-DH, Workman and
  Dvorak. Letters outside A–Z (Æ, Ø, Å…) and all other keys stay where they are. Vibemapp
  only suggests; it never changes your keyboard's layout.

## 1.1.4 (2026-10-13)

### Added
- **Fade Duration** setting on the Overlay page: how long the overlay takes to fade in, in
  milliseconds (default 200; fading out takes three quarters of it; 0 shows it at once).

### Fixed
- The overlay visibly fades in and out again. Since 1.1.3 it no longer closes its window to
  hide, so the compositor's own fade stopped applying and only a very short fade was left.
  Vibemapp now runs the fade itself, on any compositor.

### Changed
- The README and AppStream screenshot shows the whole Overlay page.

## 1.1.3 (2026-10-13)

### Fixed
- Hiding the overlay no longer takes keyboard focus away from another app's panel. Opening a
  shell's session menu (e.g. Noctalia) on a held layer and releasing the layer key used to
  leave the menu unresponsive. The overlay now stays in place, invisible and below all
  windows, instead of closing.
- The overlay no longer flashes the previous layer for a moment when it appears.

## 1.1.2 (2026-10-13)

### Fixed
- Smart Layers no longer undoes a layer you switched to by hand when the window title
  changes. Shells and terminals that set the title on every command used to snap the
  keyboard back to the app's layer. Switching to another app still applies its layer.

## 1.1.1 (2026-10-13)

### Fixed
- Smart Layers no longer switches layers while you hold keys. Holding a layer key and moving
  focus (e.g. Super+arrow on a system layer) used to apply the new window's layer or the
  fallback at once, which dropped the held layer. It now switches when you release the keys.

## 1.1.0 (2026-10-13)

### Added
- **Overlay:** a see-through, click-through drawing of the active layer that lights up the
  keys you press. It shows on the layers you pick (e.g. while you hold your symbols layer
  key) or always, and never takes focus.
  - Its own sidebar page (Ctrl+4) with a live preview: position, monitor (focused or
    fixed), size, half spacing, opacity and hide-when-idle.
  - Size is a percentage of the keyboard's real size, so it looks the same on every monitor.
  - Half spacing moves the keyboard halves apart, up to the two screen corners.
  - Toggle it from the tray ("Show Overlay") or with `vibemapp --toggle-overlay` bound to a
    Hyprland key.
  - Needs the optional `gtk4-layer-shell` package. Without it the Overlay page says what to
    install and the rest of Vibemapp works as before.

### Development
- `tests/ui/screenshot.py --overlay` renders the overlay on a test backdrop, and
  `--page overlay` renders its settings page.

## 1.0.2 (2026-10-12)

### Changed
- The tray menu shows the keyboard and current layer at the top, as a header.
- The tray's Show Vibemapp item becomes Hide Vibemapp while the window is open.
- The tray menu has a Preferences entry.

### Fixed
- A stale `last-page` setting left over from older versions is cleaned up once, automatically.
- Replaced deprecated GLib calls for file-descriptor and signal sources.

### Development
- `./run-dev.sh` runs as a separate app (`io.github.albertflindt.Vibemapp.Devel`, "Vibemapp
  (Development)" with a striped header), so it no longer just raises the installed window.
- A dev run on the real keyboard shows a banner while the installed app is also running,
  with a button to quit the installed one.
- New screenshot tools: `tests/ui/tray_menu_shot.py` renders the real tray menu, and
  `tests/ui/tooltip_shot.py` no longer moves the pointer (it stalled on Hyprland).
- Screenshots in the README and the AppStream metainfo.

## 1.0.1 (2026-10-10)

- Transparent keys are shown in the layout view, with the key they fall through to.
- The package no longer includes stale tray bytecode.

## 1.0.0 (2026-10-10)

- First stable release.

## 0.3.1 (2026-10-10)

- Simpler Add Rule choices; All Steam Games uses the Steam icon.
- No more deprecation warnings when building the package.

## 0.3.0 (2026-10-10)

- Smart Layers rule for all Steam games at once.
- Advanced rules that match window classes with a regular expression, with a live preview.
- Steam games are recognised by their Proton window class or by the game ID Steam gives native games.
- The Hyprland autostart hint uses the Lua configuration syntax.
- The window always opens on the Layout page.

## 0.2.0 (2026-10-10)

KeyMapp for Linux is now called Vibemapp. Your settings and Smart Layers rules are carried
over automatically.

- New Heatmap page: opt-in per-key press counts for today, the last 7 days or all time,
  per layer, with your most used keys.
- Tray icon: closing the window keeps Vibemapp in the tray, with quick access to Smart Layers
  and the current layer.
- Key tooltips follow the pointer from key to key.

## 0.1.0 (2026-10-09)

- First release, with Smart Layers for Hyprland and a live layout viewer.
