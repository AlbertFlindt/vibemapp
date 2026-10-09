"""Abstract focus source. Hyprland in v1; GNOME/KDE/X11 can subclass later."""

from __future__ import annotations

from gi.repository import GObject

from ...models import RunningApp


class FocusWatcher(GObject.Object):
    """Reports the focused window as ``focus-changed(app_class, title)``.

    ``app_class`` is ``""`` when no window is focused. Signals are emitted on
    the GLib main loop. ``availability-changed(bool)`` fires when the
    source appears or goes away (for example when the compositor restarts).
    """

    __gtype_name__ = "KmlFocusWatcher"
    __gsignals__ = {
        "focus-changed": (GObject.SignalFlags.RUN_LAST, None, (str, str)),
        "availability-changed": (GObject.SignalFlags.RUN_LAST, None, (bool,)),
    }

    def available(self) -> bool:
        """True when the focus source is reachable right now."""
        raise NotImplementedError

    def list_running_apps(self) -> list[RunningApp]:
        raise NotImplementedError

    def current(self) -> tuple[str, str] | None:
        """Last known (app_class, title), or None if unknown."""
        return None

    def start(self) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError


class NullFocusWatcher(FocusWatcher):
    """Used when no supported compositor is detected."""

    __gtype_name__ = "KmlNullFocusWatcher"

    def available(self) -> bool:
        return False

    def list_running_apps(self) -> list[RunningApp]:
        return []

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass
