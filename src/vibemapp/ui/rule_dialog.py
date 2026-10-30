"""Add/Edit Rule dialog.

Step 1 picks what the rule matches (an app, all Steam games or a window class
pattern); the details page then sets the layer and optional
title pattern. Editing opens straight on the details page.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, Gtk  # noqa: E402

from ..models import AppRule, Layout, RuleMatch, RunningApp  # noqa: E402
from .apps import AppEntry, AppIndex  # noqa: E402
from .i18n import _  # noqa: E402
from .resources import ui_file  # noqa: E402
from .rules_model import (classes_matching, is_own_app_class, layer_label,  # noqa: E402
                          validate_pattern)

GAMING_ICON = "input-gaming-symbolic"
PATTERN_ICON = "edit-find-symbolic"
PATTERN_EXAMPLE = "steam_app_.*"
PREVIEW_LIMIT = 4


def steam_game_icon(app_id: str | None) -> Gio.Icon:
    """Steam's own ``steam_icon_<id>`` (installed with desktop shortcuts),
    falling back to a gamepad."""
    # Pick one name explicitly: with a mixed list GTK prefers the symbolic one.
    display = Gdk.Display.get_default()
    if app_id and display is not None:
        name = f"steam_icon_{app_id}"
        if Gtk.IconTheme.get_for_display(display).has_icon(name):
            return Gio.ThemedIcon.new(name)
    return Gio.ThemedIcon.new(GAMING_ICON)


def steam_client_icon() -> Gio.Icon:
    """The installed Steam app icon, for "All Steam Games"; a gamepad if the
    theme doesn't have it."""
    display = Gdk.Display.get_default()
    if display is not None and Gtk.IconTheme.get_for_display(display).has_icon("steam"):
        return Gio.ThemedIcon.new("steam")
    steam = AppIndex.get().lookup("steam")
    if steam is not None and steam.get_icon() is not None:
        return steam.get_icon()
    return Gio.ThemedIcon.new(GAMING_ICON)


def rule_presentation(rule: AppRule) -> tuple[str, str, Gio.Icon]:
    """``(title, subtitle, icon)`` describing what a rule matches."""
    if rule.match == RuleMatch.STEAM_GAME and not rule.app_class:
        return (rule.display_name or _("All Steam Games"), _("Any Steam game"),
                steam_client_icon())
    if rule.match == RuleMatch.STEAM_GAME:
        return (rule.display_name or _("Steam Game {id}").format(id=rule.app_class),
                _("Steam game · {id}").format(id=rule.app_class),
                steam_game_icon(rule.app_class))
    if rule.match == RuleMatch.CLASS_PATTERN:
        return (rule.display_name or _("Window Class Pattern"),
                _("Pattern: {pattern}").format(pattern=rule.app_class),
                Gio.ThemedIcon.new(PATTERN_ICON))
    entry = AppIndex.get().entry_for(rule.app_class, icon_name=rule.icon_name,
                                     fallback_name=rule.display_name)
    return rule.display_name or entry.name, rule.app_class, entry.icon


class AppRow(Adw.ActionRow):
    __gtype_name__ = "KmlAppRow"

    def __init__(self, entry: AppEntry, *, subtitle: str, has_rule: bool) -> None:
        super().__init__(activatable=True, use_markup=False, title=entry.name,
                         subtitle=subtitle, subtitle_lines=1)
        self.entry = entry
        self.search_text = " ".join((entry.name, entry.app_class, entry.keywords,
                                     subtitle)).casefold()
        icon = Gtk.Image(gicon=entry.icon, pixel_size=32,
                         accessible_role=Gtk.AccessibleRole.PRESENTATION)
        icon.add_css_class("icon-dropshadow")
        self.add_prefix(icon)
        if has_rule:
            badge = Gtk.Label(label=_("Has a rule"), valign=Gtk.Align.CENTER)
            badge.add_css_class("dim-label")
            badge.add_css_class("caption")
            self.add_suffix(badge)
        self.add_suffix(Gtk.Image(icon_name="go-next-symbolic",
                                  accessible_role=Gtk.AccessibleRole.PRESENTATION))


def _rows(listbox: Gtk.ListBox) -> list[Gtk.Widget]:
    rows = []
    child = listbox.get_first_child()
    while child is not None:
        rows.append(child)
        child = child.get_next_sibling()
    return rows


@Gtk.Template(filename=ui_file("rule_dialog.ui"))
class RuleDialog(Adw.Dialog):
    __gtype_name__ = "KmlRuleDialog"

    nav: Adw.NavigationView = Gtk.Template.Child()
    kind_page: Adw.NavigationPage = Gtk.Template.Child()
    kind_app_row: Adw.ActionRow = Gtk.Template.Child()
    kind_steam_any_row: Adw.ActionRow = Gtk.Template.Child()
    kind_steam_any_row_icon: Gtk.Image = Gtk.Template.Child()
    kind_pattern_row: Adw.ActionRow = Gtk.Template.Child()
    apps_page: Adw.NavigationPage = Gtk.Template.Child()
    details_page: Adw.NavigationPage = Gtk.Template.Child()
    search_entry: Gtk.SearchEntry = Gtk.Template.Child()
    custom_list: Gtk.ListBox = Gtk.Template.Child()
    custom_row: Adw.ActionRow = Gtk.Template.Child()
    running_box: Gtk.Box = Gtk.Template.Child()
    running_list: Gtk.ListBox = Gtk.Template.Child()
    installed_box: Gtk.Box = Gtk.Template.Child()
    installed_list: Gtk.ListBox = Gtk.Template.Child()
    save_button: Gtk.Button = Gtk.Template.Child()
    app_group: Adw.PreferencesGroup = Gtk.Template.Child()
    app_row: Adw.ActionRow = Gtk.Template.Child()
    app_icon: Gtk.Image = Gtk.Template.Child()
    class_pattern_group: Adw.PreferencesGroup = Gtk.Template.Child()
    class_pattern_row: Adw.EntryRow = Gtk.Template.Child()
    class_pattern_hint: Gtk.Label = Gtk.Template.Child()
    layer_row: Adw.ComboRow = Gtk.Template.Child()
    pattern_row: Adw.EntryRow = Gtk.Template.Child()
    pattern_hint: Gtk.Label = Gtk.Template.Child()
    remove_group: Adw.PreferencesGroup = Gtk.Template.Child()
    hold_actions_row: Adw.SwitchRow = Gtk.Template.Child()
    held_modifiers_row: Adw.SwitchRow = Gtk.Template.Child()

    def __init__(self, *, layout: Layout, running: list[RunningApp],
                 existing: list[AppRule], on_done: Callable[[AppRule], None],
                 rule: AppRule | None = None,
                 on_remove: Callable[[], None] | None = None,
                 app: AppEntry | None = None) -> None:
        super().__init__()
        self._layout = layout
        self._running = [r for r in running if r.app_class and not is_own_app_class(r.app_class)]
        self._existing = list(existing)
        self._existing_classes = {r.app_class for r in existing if r.match == RuleMatch.APP}
        self._on_done = on_done
        self._on_remove = on_remove
        self._editing = rule
        self._kind = RuleMatch.APP
        self._entry: AppEntry | None = None     # APP and STEAM_GAME targets
        self._target = ""                       # app_class stored in the rule
        self._index = AppIndex.get()

        self.remove_group.set_visible(rule is not None and on_remove is not None)
        self.layer_row.set_model(Gtk.StringList.new(
            [layer_label(layer, layer.index) for layer in layout.layers]))
        self.kind_steam_any_row_icon.set_from_gicon(steam_client_icon())
        # Type-to-search only on the page that owns the entry.
        self.search_entry.set_key_capture_widget(self.apps_page)

        if rule is None:
            self.layer_row.set_selected(min(1, len(layout.layers) - 1))
            if app is not None:
                # Prefilled (the focused window): Back still leads to the kinds.
                self._show_details(RuleMatch.APP, app.app_class, app,
                                   subtitle=app.app_class, push=False)
                self.nav.replace([self.kind_page, self.details_page])
                self.set_focus(self.layer_row)
        else:
            self.set_title(_("Edit Rule"))
            self.details_page.set_title(_("Edit Rule"))
            self.save_button.set_label(_("_Save"))
            self._load_rule(rule)
            self.layer_row.set_selected(min(rule.layer_index, len(layout.layers) - 1))
            self.pattern_row.set_text(rule.title_pattern or "")
            self.hold_actions_row.set_active(rule.show_hold_actions)
            self.held_modifiers_row.set_active(rule.show_held_modifiers)
            self.nav.replace([self.details_page])
        self._validate()

    # --- step 1: kind ----------------------------------------------------

    @Gtk.Template.Callback()
    def on_kind_activated(self, row: Adw.ActionRow) -> None:
        if row is self.kind_app_row:
            if not _rows(self.installed_list) and not _rows(self.running_list):
                self._index.refresh()
                self._populate_apps()
            self.nav.push(self.apps_page)
            self.search_entry.grab_focus()
        elif row is self.kind_steam_any_row:
            self._show_details(RuleMatch.STEAM_GAME, "",
                               AppEntry("", _("All Steam Games"), steam_client_icon()),
                               subtitle=_("Any game launched from Steam"))
        elif row is self.kind_pattern_row:
            if not self.class_pattern_row.get_text():
                self.class_pattern_row.set_text(PATTERN_EXAMPLE)
            self._show_details(RuleMatch.CLASS_PATTERN, "", None)
            self.class_pattern_row.grab_focus()

    def _load_rule(self, rule: AppRule) -> None:
        title, subtitle, icon = rule_presentation(rule)
        if rule.match == RuleMatch.CLASS_PATTERN:
            self.class_pattern_row.set_text(rule.app_class)
            self._show_details(RuleMatch.CLASS_PATTERN, "", None, push=False)
        else:
            self._show_details(rule.match, rule.app_class,
                               AppEntry(rule.app_class, title, icon),
                               subtitle=subtitle, push=False)

    # --- step 2a: apps ---------------------------------------------------

    def _populate_apps(self) -> None:
        seen: set[str] = set()
        for app in self._running:
            if app.app_class in seen:
                continue
            seen.add(app.app_class)
            entry = self._index.entry_for(app.app_class, icon_name=app.icon_name)
            self.running_list.append(AppRow(
                entry, subtitle=app.app_class,
                has_rule=app.app_class in self._existing_classes))
        for entry in self._index.installed():
            self.installed_list.append(AppRow(
                entry, subtitle=entry.app_class,
                has_rule=entry.app_class in self._existing_classes))
        for listbox in (self.running_list, self.installed_list):
            listbox.set_filter_func(self._filter_apps)
        self._update_app_visibility()

    def _filter_apps(self, row: Gtk.ListBoxRow) -> bool:
        text = self.search_entry.get_text().strip().casefold()
        return not text or (isinstance(row, AppRow) and text in row.search_text)

    @staticmethod
    def _has_visible_rows(listbox: Gtk.ListBox) -> bool:
        return any(row.get_child_visible() for row in _rows(listbox)
                   if isinstance(row, AppRow))

    def _update_app_visibility(self) -> None:
        self.running_box.set_visible(self._has_visible_rows(self.running_list))
        self.installed_box.set_visible(self._has_visible_rows(self.installed_list))
        text = self.search_entry.get_text().strip()
        exact = any(isinstance(r, AppRow) and r.entry.app_class == text
                    for lb in (self.running_list, self.installed_list) for r in _rows(lb))
        show_custom = bool(text) and not exact and " " not in text
        self.custom_list.set_visible(show_custom)
        if show_custom:
            self.custom_row.set_title(_("Use “{cls}”").format(cls=text))

    @Gtk.Template.Callback()
    def on_search_changed(self, _entry: Gtk.SearchEntry) -> None:
        self.running_list.invalidate_filter()
        self.installed_list.invalidate_filter()
        self._update_app_visibility()

    @Gtk.Template.Callback()
    def on_search_activate(self, _entry: Gtk.SearchEntry) -> None:
        for listbox in (self.running_list, self.installed_list):
            for row in _rows(listbox):
                if isinstance(row, AppRow) and row.get_child_visible():
                    self._pick_app(row.entry)
                    return
        if self.custom_list.get_visible():
            self.on_custom_activated(self.custom_list, self.custom_row)

    @Gtk.Template.Callback()
    def on_app_activated(self, _listbox: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        if isinstance(row, AppRow):
            self._pick_app(row.entry)

    @Gtk.Template.Callback()
    def on_custom_activated(self, _listbox: Gtk.ListBox, _row: Gtk.ListBoxRow) -> None:
        app_class = self.search_entry.get_text().strip()
        if app_class:
            self._pick_app(self._index.entry_for(app_class))

    def _pick_app(self, entry: AppEntry) -> None:
        self._show_details(RuleMatch.APP, entry.app_class, entry, subtitle=entry.app_class)

    # --- step 3: details -------------------------------------------------

    def _show_details(self, kind: RuleMatch, target: str, entry: AppEntry | None, *,
                      subtitle: str = "", push: bool = True) -> None:
        self._kind = kind
        self._target = target
        self._entry = entry
        is_pattern = kind == RuleMatch.CLASS_PATTERN
        self.app_group.set_visible(not is_pattern)
        self.class_pattern_group.set_visible(is_pattern)
        if entry is not None:
            self.app_row.set_title(entry.name)
            self.app_row.set_subtitle(subtitle)
            self.app_icon.set_from_gicon(entry.icon)
        self._validate()
        if push:
            self.nav.push(self.details_page)
            if not is_pattern:
                self.set_focus(self.layer_row)

    def _open_titles(self) -> list[str]:
        if self._kind == RuleMatch.APP:
            return [a.title for a in self._running if a.app_class == self._target and a.title]
        if self._kind == RuleMatch.CLASS_PATTERN:
            matching = set(classes_matching(self.class_pattern_row.get_text(),
                                            [a.app_class for a in self._running]))
            return [a.title for a in self._running if a.app_class in matching and a.title]
        return []

    def _validate_class_pattern(self) -> bool:
        hint = self.class_pattern_hint
        for css in ("error", "dim-label"):
            hint.remove_css_class(css)
        pattern = self.class_pattern_row.get_text().strip()
        if not pattern:
            self.class_pattern_row.remove_css_class("error")
            hint.set_label(_("Enter a pattern, for example {example}").format(
                example=PATTERN_EXAMPLE))
            hint.add_css_class("dim-label")
            return False
        error = validate_pattern(pattern)
        if error is not None:
            self.class_pattern_row.add_css_class("error")
            hint.set_label(_("Invalid regular expression: {error}").format(error=error))
            hint.add_css_class("error")
            return False
        self.class_pattern_row.remove_css_class("error")
        matches = classes_matching(pattern, [a.app_class for a in self._running])
        if not matches:
            hint.set_label(_("No open window matches right now"))
        else:
            shown = ", ".join(matches[:PREVIEW_LIMIT])
            if len(matches) > PREVIEW_LIMIT:
                shown += _(", and {n} more").format(n=len(matches) - PREVIEW_LIMIT)
            hint.set_label(_("Matches: {classes}").format(classes=shown))
        hint.add_css_class("dim-label")
        return True

    def _validate(self) -> bool:
        target_ok = (self._validate_class_pattern() if self._kind == RuleMatch.CLASS_PATTERN
                     else self._entry is not None)
        pattern = self.pattern_row.get_text()
        error = validate_pattern(pattern)
        hint = self.pattern_hint
        hint.remove_css_class("error")
        hint.remove_css_class("dim-label")
        if error is not None:
            self.pattern_row.add_css_class("error")
            hint.set_label(_("Invalid regular expression: {error}").format(error=error))
            hint.add_css_class("error")
            hint.set_visible(True)
        else:
            self.pattern_row.remove_css_class("error")
            titles = self._open_titles()
            if pattern and titles:
                matching = [t for t in titles if re.search(pattern, t)]
                if matching:
                    hint.set_label(_("Matches the open window “{title}”").format(
                        title=matching[0]))
                elif len(titles) == 1:
                    hint.set_label(_("Doesn't match the open window “{title}”").format(
                        title=titles[0]))
                else:
                    hint.set_label(_("Doesn't match any open window of this app"))
                hint.add_css_class("dim-label")
                hint.set_visible(True)
            else:
                hint.set_visible(False)
        valid = error is None and target_ok
        self.save_button.set_sensitive(valid)
        return valid

    @Gtk.Template.Callback()
    def on_class_pattern_changed(self, _row: Adw.EntryRow) -> None:
        self._validate()

    @Gtk.Template.Callback()
    def on_remove_activated(self, _row: Adw.ButtonRow) -> None:
        if self._on_remove is not None:
            self._on_remove()
        self.close()

    @Gtk.Template.Callback()
    def on_pattern_changed(self, _row: Adw.EntryRow) -> None:
        self._validate()

    def build_rule(self) -> AppRule | None:
        rule = self._build_target()
        if rule is None:
            return None
        return replace(rule, show_hold_actions=self.hold_actions_row.get_active(),
                       show_held_modifiers=self.held_modifiers_row.get_active())

    def _build_target(self) -> AppRule | None:
        if not self._validate():
            return None
        title_pattern = self.pattern_row.get_text().strip() or None
        layer = self.layer_row.get_selected()
        if layer == Gtk.INVALID_LIST_POSITION:
            layer = 0
        enabled = self._editing.enabled if self._editing else True
        if self._kind == RuleMatch.CLASS_PATTERN:
            pattern = self.class_pattern_row.get_text().strip()
            name = (self._editing.display_name if self._editing
                    and self._editing.match == RuleMatch.CLASS_PATTERN
                    else _("Window Class Pattern"))
            return AppRule(pattern, name, int(layer), title_pattern, enabled,
                           PATTERN_ICON, RuleMatch.CLASS_PATTERN)
        assert self._entry is not None
        if self._kind == RuleMatch.STEAM_GAME:
            icon = GAMING_ICON if not self._target else None
            return AppRule(self._target, self._entry.name, int(layer), title_pattern,
                           enabled, icon, RuleMatch.STEAM_GAME)
        icon_name = None
        if isinstance(self._entry.icon, Gio.ThemedIcon):
            names = self._entry.icon.get_names()
            icon_name = names[0] if names else None
        return AppRule(self._target, self._entry.name, int(layer), title_pattern,
                       enabled, icon_name, RuleMatch.APP)

    @Gtk.Template.Callback()
    def on_save_clicked(self, *_args: object) -> None:
        rule = self.build_rule()
        if rule is not None:
            self._on_done(rule)
            self.close()


def present_rule_dialog(parent: Gtk.Widget, *, layout: Layout,
                        running: list[RunningApp], existing: list[AppRule],
                        on_done: Callable[[AppRule], None],
                        rule: AppRule | None = None,
                        on_remove: Callable[[], None] | None = None,
                        app: AppEntry | None = None) -> RuleDialog:
    dialog = RuleDialog(layout=layout, running=running, existing=existing,
                        on_done=on_done, rule=rule, on_remove=on_remove, app=app)
    dialog.present(parent)
    return dialog
