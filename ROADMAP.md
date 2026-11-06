# Roadmap

Each milestone is a major version. Its items land as minor releases on the way; the major
release comes when the milestone is done. Order within a milestone is a suggestion. What's being
worked on right now is under "Next steps" in `PROGRESS.md`.

## 2.0: Ready for other people
Goal: someone with a ZSA keyboard and Hyprland can install it, and tells us what's missing.
- [ ] Everything under "Not verified" in `PROGRESS.md` that a Voyager can check is checked.
- [ ] Published on the AUR, with the README's install section pointing there.
- [ ] Bug report, feature request and "my desktop" issue forms, so feedback arrives in one shape.
- [ ] Announced where ZSA and Hyprland users are (ZSA's community, r/ErgoMechKeyboards,
      r/hyprland), asking which desktop and keyboard people use.

## 3.0: More desktops
Goal: Smart Layers beyond Hyprland. Order follows the feedback from 2.0. `FocusWatcher` is the
plug-in point; the layout view works anywhere already.
- [ ] Sway and niri: both have a focus event stream over IPC, so they're the cheapest.
- [ ] KDE Plasma: a KWin script that reports the active window over D-Bus.
- [ ] GNOME: needs a Shell extension for the focused window. Mutter has no layer-shell, so no
      overlay there; the UI must say so.
- [ ] Moonlander, ErgoDox EZ and Planck EZ confirmed by people who own one.

## Later
- [ ] Translations: `po/` (`POTFILES`, `LINGUAS`) and meson `i18n.gettext('vibemapp')`. Strings
      are already in `_()`.
