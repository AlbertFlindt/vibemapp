"""Load gtk4-layer-shell before GTK (optional dependency).

gtk4-layer-shell hooks GTK's Wayland surface code, so its shared library has
to be loaded *before* libgtk-4 is. Importing ``Gtk4LayerShell`` through
GObject introspection afterwards is too late: ``is_supported()`` then returns
False and the overlay falls back to a normal window.

Import this module first in every entry point, before anything imports
``gi.repository.Gtk``::

    from vibemapp import _preload  # noqa: F401  (must come before Gtk)

It never raises. ``LOADED`` says whether the library was found, ``TOO_LATE``
whether GTK had already been loaded when this module ran.
"""

from __future__ import annotations

import ctypes
import logging
import sys

log = logging.getLogger(__name__)

# The runtime package ships only the versioned soname; the unversioned .so is
# a dev symlink some distributions leave out.
LIBRARY_NAMES = ("libgtk4-layer-shell.so.0", "libgtk4-layer-shell.so")

LOADED = False
TOO_LATE = False


def gtk_already_loaded(modules: dict | None = None) -> bool:
    mods = sys.modules if modules is None else modules
    return "gi.repository.Gtk" in mods


def preload(names: tuple[str, ...] = LIBRARY_NAMES,
            loader=ctypes.CDLL) -> str | None:
    """Load the first library in ``names`` that exists. Returns its name, or
    None when gtk4-layer-shell isn't installed."""
    for name in names:
        try:
            loader(name)
        except OSError:
            continue
        return name
    return None


TOO_LATE = gtk_already_loaded()
if TOO_LATE:
    log.debug("GTK was imported before vibemapp._preload; layer-shell won't work")
LOADED = preload() is not None
