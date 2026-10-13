# Vibemapp

A GNOME/libadwaita take on ZSA Keymapp for Linux, written in Python with PyGObject.

![Vibemapp's layout view, showing a Voyager layout with layer tabs](data/screenshots/layout.png)

- **Smart Layers:** switches your ZSA keyboard's layer when the focused app changes.
  Rules can match one app, a window-class regex or all Steam games. Needs Hyprland.
- **Live layout:** shows your Oryx layout and the active layer.
- **Overlay:** a click-through, always-on-top drawing of the active layer with live key
  presses, e.g. while you hold your symbols layer key. Needs `gtk4-layer-shell`.
- **Heatmap:** opt-in key-press counts. Only counts are stored, never what you typed.
- **Tray icon:** closing the window keeps Vibemapp running in the tray.

Vibemapp never flashes firmware or writes to the keyboard's EEPROM.

| Smart Layers | Heatmap |
| --- | --- |
| ![Smart Layers rules for Firefox, kitty, Blender and all Steam games](data/screenshots/smart-layers.png) | ![Heatmap of key presses over the last 7 days, with the top keys](data/screenshots/heatmap.png) |

<p align="center">
  <img src="data/screenshots/tray.png" alt="Tray menu with the current layer, Hide Vibemapp, Smart Layers, Preferences and Quit" width="260">
</p>

## Requirements

- A ZSA keyboard (Voyager, Moonlander, ErgoDox EZ or Planck EZ) running Oryx firmware.
- Arch Linux packages:
  - Runtime: `python python-gobject gtk4 libadwaita glib2`
  - Build: `meson git`
- Optional:
  - `hyprland`, the only focus source Smart Layers supports.
  - A StatusNotifier tray host such as Noctalia or Waybar, for the tray icon.
  - Steam, for Steam-game rules.
  - `gtk4-layer-shell`, for the overlay.

Nothing is installed from pip.

## Installation (Arch Linux)

```sh
sudo pacman -S --needed python python-gobject gtk4 libadwaita glib2 meson git
git clone https://github.com/AlbertFlindt/vibemapp.git
cd vibemapp/packaging/arch
makepkg -si
```

Unplug and replug the keyboard afterwards so the udev rules give you access to it.

To start Vibemapp in the tray when Hyprland starts, add this to
`~/.config/hypr/autostart.lua`:

```lua
hl.on("hyprland.start", function () hl.exec_cmd("vibemapp --background") end)
```

## Overlay

![Overlay settings with the symbols and media layers selected](data/screenshots/overlay.png)

The overlay shows the keyboard's active layer on screen: in "selected" mode only while one
of the layers you picked is active (hold your symbols layer key and it appears, release it
and it fades out), in "always" mode on every layer. It never takes focus, clicks go through
it and it follows the focused monitor (Hyprland), or a fixed one.

Toggle it from the tray ("Show Overlay"), or bind a key in `~/.config/hypr/keybindings.lua`:

```lua
hl.bind("SUPER + O", hl.dsp.exec_cmd("vibemapp --toggle-overlay"))
```

The overlay needs `gtk4-layer-shell` (`sudo pacman -S gtk4-layer-shell`): it draws on a
layer-shell surface (namespace `vibemapp-overlay`) above fullscreen games and panels. Without
the package the overlay is off and its tray item is hidden.

Its size is a percentage of your keyboard's real size, so it looks the same on every monitor
(computed from each monitor's physical width). Half spacing moves the keyboard halves apart,
up to the two corners of the screen edge.

## Running from source

```sh
./run-dev.sh                    # real keyboard
VIBEMAPP_MOCK=1 ./run-dev.sh    # simulated keyboard
./check.sh                      # lint and tests (also needs desktop-file-utils and appstream)
```

`run-dev.sh` runs as a separate development app ("Vibemapp (Development)"), so it can run next
to an installed Vibemapp and uses the same rules and settings. With a real keyboard, quit the
installed one first (tray → Quit) so the two don't both switch layers, or use `VIBEMAPP_MOCK=1`.

## Changes

See [CHANGELOG.md](CHANGELOG.md) and the [releases](https://github.com/AlbertFlindt/vibemapp/releases).

## License

GPL-3.0-or-later.
