"""Add/Edit Rule dialog: pick an app, then its layer and an optional title pattern."""

from __future__ import annotations

import re
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, Gtk  # noqa: E402

from ..models import AppRule, Layout, RunningApp  # noqa: E402
from .apps import AppEntry, AppIndex  # noqa: E402
from .i18n import _  # noqa: E402
from .resources import ui_file  # noqa: E402
from .rules_model import OWN_APP_CLASS, layer_label, validate_pattern  # noqa: E402


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


@Gtk.Template(filename=ui_file("rule_dialog.ui"))
class RuleDialog(Adw.Dialog):
    __gtype_name__ = "KmlRuleDialog"

    nav: Adw.NavigationView = Gtk.Template.Child()
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
    app_row: Adw.ActionRow = Gtk.Template.Child()
    app_icon: Gtk.Image = Gtk.Template.Child()
    layer_row: Adw.ComboRow = Gtk.Template.Child()
    pattern_row: Adw.EntryRow = Gtk.Template.Child()
    pattern_hint: Gtk.Label = Gtk.Template.Child()
    remove_group: Adw.PreferencesGroup = Gtk.Template.Child()

    def __init__(self, *, layout: Layout, running: list[RunningApp],
                 existing: list[AppRule], on_done: Callable[[AppRule], None],
                 rule: AppRule | None = None,
                 on_remove: Callable[[], None] | None = None) -> None:
        super().__init__()
        self._on_remove = on_remove
        self.remove_group.set_visible(rule is not None and on_remove is not None)
        self._layout = layout
        self._running = [r for r in running if r.app_class and r.app_class != OWN_APP_CLASS]
        self._existing_classes = {r.app_class for r in existing}
        self._on_done = on_done
        self._editing = rule
        self._entry: AppEntry | None = None
        self._index = AppIndex.get()

        self.layer_row.set_model(Gtk.StringList.new(
            [layer_label(layer, layer.index) for layer in layout.layers]))
        self.search_entry.set_key_capture_widget(self)

        if rule is None:
            self._index.refresh()
            self._populate()
            self.layer_row.set_selected(min(1, len(layout.layers) - 1))
        else:
            self.set_title(_("Edit Rule"))
            self.details_page.set_title(_("Edit Rule"))
            self.save_button.set_label(_("_Save"))
            entry = self._index.entry_for(rule.app_class, icon_name=rule.icon_name,
                                          fallback_name=rule.display_name)
            self._show_details(AppEntry(rule.app_class, rule.display_name or entry.name,
                                        entry.icon), push=False)
            self.layer_row.set_selected(min(rule.layer_index, len(layout.layers) - 1))
            self.pattern_row.set_text(rule.title_pattern or "")
            self.nav.replace([self.details_page])
            self.set_focus(self.layer_row)
        self._validate()

    # --- app lists -------------------------------------------------------

    def _populate(self) -> None:
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
            listbox.set_filter_func(self._filter)
        self._update_visibility()

    def _filter(self, row: Gtk.ListBoxRow) -> bool:
        text = self.search_entry.get_text().strip().casefold()
        return not text or (isinstance(row, AppRow) and text in row.search_text)

    def _has_visible_rows(self, listbox: Gtk.ListBox) -> bool:
        child = listbox.get_first_child()
        while child is not None:
            if isinstance(child, AppRow) and child.get_child_visible():
                return True
            child = child.get_next_sibling()
        return False

    def _update_visibility(self) -> None:
        self.running_box.set_visible(self._has_visible_rows(self.running_list))
        self.installed_box.set_visible(self._has_visible_rows(self.installed_list))
        text = self.search_entry.get_text().strip()
        exact = any(isinstance(r, AppRow) and r.entry.app_class == text
                    for lb in (self.running_list, self.installed_list)
                    for r in self._rows(lb))
        show_custom = bool(text) and not exact and " " not in text
        self.custom_list.set_visible(show_custom)
        if show_custom:
            self.custom_row.set_title(_("Use “{cls}”").format(cls=text))

    @staticmethod
    def _rows(listbox: Gtk.ListBox) -> list[Gtk.Widget]:
        rows = []
        child = listbox.get_first_child()
        while child is not None:
            rows.append(child)
            child = child.get_next_sibling()
        return rows

    @Gtk.Template.Callback()
    def on_search_changed(self, _entry: Gtk.SearchEntry) -> None:
        self.running_list.invalidate_filter()
        self.installed_list.invalidate_filter()
        self._update_visibility()

    @Gtk.Template.Callback()
    def on_search_activate(self, _entry: Gtk.SearchEntry) -> None:
        for listbox in (self.running_list, self.installed_list):
            for row in self._rows(listbox):
                if isinstance(row, AppRow) and row.get_child_visible():
                    self._show_details(row.entry)
                    return
        if self.custom_list.get_visible():
            self.on_custom_activated(self.custom_list, self.custom_row)

    @Gtk.Template.Callback()
    def on_app_activated(self, _listbox: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        if isinstance(row, AppRow):
            self._show_details(row.entry)

    @Gtk.Template.Callback()
    def on_custom_activated(self, _listbox: Gtk.ListBox, _row: Gtk.ListBoxRow) -> None:
        app_class = self.search_entry.get_text().strip()
        if app_class:
            self._show_details(self._index.entry_for(app_class))

    # --- details ---------------------------------------------------------

    def _show_details(self, entry: AppEntry, push: bool = True) -> None:
        self._entry = entry
        self.app_row.set_title(entry.name)
        self.app_row.set_subtitle(entry.app_class)
        self.app_icon.set_from_gicon(entry.icon)
        self._validate()
        if push:
            self.nav.push(self.details_page)
            self.set_focus(self.layer_row)

    def _open_titles(self) -> list[str]:
        if self._entry is None:
            return []
        return [app.title for app in self._running
                if app.app_class == self._entry.app_class and app.title]

    def _validate(self) -> bool:
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
        valid = error is None and self._entry is not None
        self.save_button.set_sensitive(valid)
        return valid

    @Gtk.Template.Callback()
    def on_remove_activated(self, _row: Adw.ButtonRow) -> None:
        if self._on_remove is not None:
            self._on_remove()
        self.close()

    @Gtk.Template.Callback()
    def on_pattern_changed(self, _row: Adw.EntryRow) -> None:
        self._validate()

    @Gtk.Template.Callback()
    def on_save_clicked(self, *_args: object) -> None:
        if not self._validate() or self._entry is None:
            return
        pattern = self.pattern_row.get_text().strip() or None
        layer = self.layer_row.get_selected()
        if layer == Gtk.INVALID_LIST_POSITION:
            layer = 0
        icon_name = None
        icon = self._entry.icon
        if isinstance(icon, Gio.ThemedIcon):
            names = icon.get_names()
            icon_name = names[0] if names else None
        rule = AppRule(
            app_class=self._entry.app_class,
            display_name=self._entry.name,
            layer_index=int(layer),
            title_pattern=pattern,
            enabled=self._editing.enabled if self._editing else True,
            icon_name=icon_name,
        )
        self._on_done(rule)
        self.close()


def present_rule_dialog(parent: Gtk.Widget, *, layout: Layout,
                        running: list[RunningApp], existing: list[AppRule],
                        on_done: Callable[[AppRule], None],
                        rule: AppRule | None = None,
                        on_remove: Callable[[], None] | None = None) -> RuleDialog:
    dialog = RuleDialog(layout=layout, running=running, existing=existing,
                        on_done=on_done, rule=rule, on_remove=on_remove)
    dialog.present(parent)
    if rule is None:
        dialog.search_entry.grab_focus()
    return dialog
