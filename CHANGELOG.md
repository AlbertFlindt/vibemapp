# Changelog

All notable changes to Vibemapp. Each version is a tag and a GitHub release.

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
