"""Locating UI files, CSS and data both in the source tree and when installed.

``.ui`` files live next to this module, so they are found relative to the
package in both cases. ``VIBEMAPP_UI_DIR`` can point elsewhere (e.g. a
meson-installed copy). Data files (icons, schema) are looked up in the
source tree's ``data/`` directory when running uncompiled.
"""

from __future__ import annotations

import os
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gdk, Gtk  # noqa: E402

from ..appid import BASE_APP_ID, is_devel  # noqa: E402

# Schema ID, icon name and data-file prefix. The *application* ID may differ
# in dev runs (``appid.application_id()``); these never do.
APP_ID = BASE_APP_ID
PACKAGE_DIR = Path(__file__).resolve().parent.parent
UI_DIR = Path(os.environ.get("VIBEMAPP_UI_DIR") or Path(__file__).resolve().parent)


def ui_file(name: str) -> str:
    return str(UI_DIR / name)


def source_data_dir() -> Path | None:
    """``<repo>/data`` when running from a source checkout."""
    candidate = PACKAGE_DIR.parent.parent / "data"
    if (candidate / f"{APP_ID}.gschema.xml").is_file():
        return candidate
    return None


def app_title() -> str:
    """Window and tray title; dev runs are marked so they're easy to tell apart."""
    from .i18n import _
    return _("Vibemapp (Development)") if is_devel() else _("Vibemapp")


def version() -> str:
    return os.environ.get("VIBEMAPP_VERSION") or "1.3.0-dev"


_css_loaded = False


def load_css() -> None:
    global _css_loaded
    display = Gdk.Display.get_default()
    if _css_loaded or display is None:
        return
    provider = Gtk.CssProvider()
    provider.load_from_path(ui_file("style.css"))
    Gtk.StyleContext.add_provider_for_display(
        display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    data = source_data_dir()
    if data is not None:
        Gtk.IconTheme.get_for_display(display).add_search_path(str(data / "icons"))
    _css_loaded = True
