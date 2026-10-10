"""System tray (StatusNotifierItem + dbusmenu). GLib/Gio/GObject only, no GTK.

See ``vibemapp.tray.sni`` for the full API documentation.
"""

from __future__ import annotations

from .sni import (APP_ICON, ITEM_PATH, MENU_PATH, WATCHER_NAME, TrayIcon, TrayMenuItem,
                  default_icon_theme_path)

__all__ = ["TrayIcon", "TrayMenuItem", "default_icon_theme_path", "APP_ICON",
           "WATCHER_NAME", "ITEM_PATH", "MENU_PATH"]
