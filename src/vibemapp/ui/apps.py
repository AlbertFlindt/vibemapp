"""Lookup of installed applications by Hyprland window class."""

from __future__ import annotations

from dataclasses import dataclass

from gi.repository import Gio

from .rules_model import app_class_from_desktop_id, prettify_class

FALLBACK_ICON = "application-x-executable-symbolic"


@dataclass(frozen=True)
class AppEntry:
    app_class: str
    name: str
    icon: Gio.Icon
    description: str = ""
    keywords: str = ""


def _icon_from_name(name: str | None) -> Gio.Icon:
    names = [name, FALLBACK_ICON] if name else [FALLBACK_ICON]
    return Gio.ThemedIcon.new_from_names(names)


class AppIndex:
    """Index of desktop entries, keyed by every plausible window class."""

    _instance: AppIndex | None = None

    @classmethod
    def get(cls) -> AppIndex:
        if cls._instance is None:
            cls._instance = AppIndex()
        return cls._instance

    def __init__(self) -> None:
        self._by_key: dict[str, Gio.AppInfo] = {}
        self._installed: list[AppEntry] = []
        self.refresh()

    def refresh(self) -> None:
        by_key: dict[str, Gio.AppInfo] = {}
        installed: dict[str, AppEntry] = {}
        for info in Gio.AppInfo.get_all():
            desktop_id = info.get_id() or ""
            wm_class = None
            if isinstance(info, Gio.DesktopAppInfo):
                wm_class = info.get_startup_wm_class()
            app_class = app_class_from_desktop_id(desktop_id, wm_class)
            if not app_class:
                continue
            base = desktop_id.removesuffix(".desktop")
            for key in {app_class, base, base.rsplit(".", 1)[-1]}:
                by_key.setdefault(key.lower(), info)
            if info.should_show() and app_class not in installed:
                keywords = ""
                if isinstance(info, Gio.DesktopAppInfo):
                    keywords = " ".join(info.get_keywords() or [])
                installed[app_class] = AppEntry(
                    app_class, info.get_display_name() or info.get_name() or app_class,
                    info.get_icon() or _icon_from_name(None),
                    info.get_description() or "", keywords)
        self._by_key = by_key
        self._installed = sorted(installed.values(), key=lambda e: e.name.casefold())

    def lookup(self, app_class: str) -> Gio.AppInfo | None:
        if not app_class:
            return None
        key = app_class.lower()
        return self._by_key.get(key) or self._by_key.get(key.rsplit(".", 1)[-1])

    def installed(self) -> list[AppEntry]:
        return list(self._installed)

    def entry_for(self, app_class: str, *, icon_name: str | None = None,
                  fallback_name: str | None = None) -> AppEntry:
        info = self.lookup(app_class)
        if info is not None:
            return AppEntry(app_class,
                            info.get_display_name() or info.get_name() or app_class,
                            info.get_icon() or _icon_from_name(icon_name),
                            info.get_description() or "")
        return AppEntry(app_class, fallback_name or prettify_class(app_class),
                        _icon_from_name(icon_name))

    def display_name(self, app_class: str) -> str:
        return self.entry_for(app_class).name
