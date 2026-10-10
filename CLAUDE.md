# Vibemapp

Native Arch Linux clone of ZSA Keymapp with GNOME (libadwaita) design. Headline
feature: **Smart Layers** — switch the keyboard's layer automatically when the
focused application changes (Hyprland in v1).

Full spec: `docs/SPEC.md`.

## Stack
- Python 3.14, PyGObject, GTK 4.24, libadwaita 1.10 (system packages only — no pip deps).
- Meson build, PKGBUILD for Arch. GtkBuilder `.ui` XML (no blueprint-compiler).
- Device I/O via stdlib only: `/dev/hidraw*` with `os`/`fcntl`, sysfs for enumeration.
- Tests: stdlib `unittest` (`PYTHONPATH=src python -W "ignore::DeprecationWarning:gi.events" -m unittest discover -s tests -t .` from the repo root; the filter hides a PyGObject 3.58 warning under Python 3.14).

## Ownership
| Path | Owner |
|---|---|
| `src/vibemapp/models.py`, `service.py` | PM — the contract. Request changes from the PM, don't edit. |
| `src/vibemapp/backend/`, `data/udev/`, `tests/backend/` | Backend agent |
| `src/vibemapp/ui/`, `main.py`, `__main__.py`, `mock_service.py`, `data/` (except udev), `tests/ui/` | Frontend agent |
| `meson.build` (top level), `PKGBUILD`, `docs/`, `CLAUDE.md` | PM |

## Hard rules
- **Never flash firmware, never write to keyboard EEPROM/persistent config.** The only
  write allowed to the keyboard is the Oryx live set-layer command (RAM state) and the
  Oryx handshake/query commands.
- No sudo, no system-wide installs, no git commits (the PM commits).
- All `KeyboardService` signals are emitted on the GLib main loop.
- Follow GNOME HIG: libadwaita widgets, header-bar actions, sentence-case labels, no
  custom theming beyond what libadwaita colours provide.

## Conventions
- App ID: `io.github.albertflindt.Vibemapp`. Python package: `vibemapp`.
- Type hints everywhere; `from __future__ import annotations`.
- User-visible strings wrapped in `_()` (gettext domain `vibemapp`).
- Config: `$XDG_CONFIG_HOME/vibemapp/`, cache: `$XDG_CACHE_HOME/vibemapp/`.
