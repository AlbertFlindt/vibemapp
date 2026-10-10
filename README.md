# Vibemapp

A GNOME/libadwaita take on ZSA Keymapp for Linux, written in Python with PyGObject.

- **Smart Layers:** switches your ZSA keyboard's layer when the focused app changes.
  Rules can match one app, a window-class regex or all Steam games. Needs Hyprland.
- **Live layout:** shows your Oryx layout and the active layer.
- **Heatmap:** opt-in key-press counts. Only counts are stored, never what you typed.
- **Tray icon:** closing the window keeps Vibemapp running in the tray.

Vibemapp never flashes firmware or writes to the keyboard's EEPROM.

## Requirements

- A ZSA keyboard (Voyager, Moonlander, ErgoDox EZ or Planck EZ) running Oryx firmware.
- Arch Linux packages:
  - Runtime: `python python-gobject gtk4 libadwaita glib2`
  - Build: `meson git`
- Optional:
  - `hyprland`, the only focus source Smart Layers supports.
  - A StatusNotifier tray host such as Noctalia or Waybar, for the tray icon.
  - Steam, for Steam-game rules.

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

## Running from source

```sh
./run-dev.sh                    # real keyboard
VIBEMAPP_MOCK=1 ./run-dev.sh    # simulated keyboard
./check.sh                      # lint and tests (also needs desktop-file-utils and appstream)
```

Quit any installed Vibemapp first (tray → Quit), because both share the same app ID.

## License

GPL-3.0-or-later.
