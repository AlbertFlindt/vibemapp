"""Smart Layers page: master switch, live status, rules and fallback layer."""

from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango  # noqa: E402

from ..models import AppRule, FocusedWindow, Layout  # noqa: E402
from ..service import KeyboardService  # noqa: E402
from . import rules_model as rm  # noqa: E402
from .apps import AppIndex  # noqa: E402
from .controller import DeviceController  # noqa: E402
from .i18n import _  # noqa: E402
from .resources import ui_file  # noqa: E402
from .rule_dialog import present_rule_dialog, rule_presentation, steam_game_icon  # noqa: E402
from .widgets import LayerDot  # noqa: E402

DRAG_PREFIX = "kml-rule:"


def _layer_dropdown(layout: Layout, selected: int) -> Gtk.DropDown:
    """Dropdown of layers: colour dot + title on the button, full label in the list."""
    model = Gtk.StringList.new([str(layer.index) for layer in layout.layers])

    def make_factory(full: bool) -> Gtk.SignalListItemFactory:
        factory = Gtk.SignalListItemFactory()

        def setup(_f: object, item: Gtk.ListItem) -> None:
            box = Gtk.Box(spacing=8)
            box.append(LayerDot())
            box.append(Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END))
            item.set_child(box)

        def bind(_f: object, item: Gtk.ListItem) -> None:
            index = int(item.get_item().get_string())
            layer = layout.layers[index]
            box = item.get_child()
            dot = box.get_first_child()
            label = dot.get_next_sibling()
            dot.set_color(layer.color)
            label.set_label(rm.layer_label(layer, index) if full
                            else (layer.title or rm.layer_label(None, index)))

        factory.connect("setup", setup)
        factory.connect("bind", bind)
        return factory

    dropdown = Gtk.DropDown(model=model, valign=Gtk.Align.CENTER,
                            factory=make_factory(False), list_factory=make_factory(True))
    dropdown.set_selected(min(selected, len(layout.layers) - 1))
    return dropdown


class RuleRow(Adw.ActionRow):
    """One Smart Layers rule. Reports user intent back to the page through
    callbacks; the page owns the rule list."""

    __gtype_name__ = "KmlRuleRow"

    def __init__(self, page: SmartLayersPage, position: int, rule: AppRule,
                 layout: Layout, *, n_rules: int, shadowed: bool,
                 compact: bool = False) -> None:
        super().__init__(activatable=True, use_markup=False, subtitle_lines=2)
        self.page = page
        self.position = position
        self.rule = rule
        self._shadowed = shadowed
        self._matching = False

        title, self._target_text, gicon = rule_presentation(rule)
        self.set_title(title)
        self.set_tooltip_text(_("Edit rule"))
        if rm.is_catch_all(rule):
            self.add_css_class("group-rule")

        # add_prefix() prepends, so the drag handle (added last) ends up first.
        icon = Gtk.Image(gicon=gicon, pixel_size=32,
                         accessible_role=Gtk.AccessibleRole.PRESENTATION)
        icon.add_css_class("icon-dropshadow")
        self.add_prefix(icon)
        handle = Gtk.Image(icon_name="list-drag-handle-symbolic",
                           accessible_role=Gtk.AccessibleRole.PRESENTATION)
        handle.add_css_class("drag-handle")
        self.add_prefix(handle)
        self._handle = handle

        name = self.get_title()
        self.layer_dropdown = _layer_dropdown(layout, rule.layer_index)
        self.layer_dropdown.set_tooltip_text(_("Layer"))
        self.layer_dropdown.update_property(
            [Gtk.AccessibleProperty.LABEL], [_("Layer for {app}").format(app=name)])
        self.layer_dropdown.connect("notify::selected", self._on_layer_selected)
        self.add_suffix(self.layer_dropdown)

        self.switch = Gtk.Switch(active=rule.enabled, valign=Gtk.Align.CENTER)
        self.switch.update_property(
            [Gtk.AccessibleProperty.LABEL], [_("Enable rule for {app}").format(app=name)])
        self.switch.connect("notify::active", self._on_enabled)
        self.add_suffix(self.switch)

        remove = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER,
                            tooltip_text=_("Remove Rule"))
        remove.add_css_class("flat")
        remove.update_property(
            [Gtk.AccessibleProperty.LABEL], [_("Remove rule for {app}").format(app=name)])
        remove.connect("clicked", lambda *_a: self.page.remove_rule(self.position))
        self.add_suffix(remove)
        self._remove_button = remove
        self.set_compact(compact)

        self.connect("activated", lambda *_a: self.page.edit_rule(self.position))
        self._setup_actions(n_rules)
        self._setup_dnd()
        self._update_subtitle()

    # --- presentation ----------------------------------------------------

    def set_compact(self, compact: bool) -> None:
        """Narrow layout: drop the handle and remove button (both stay
        reachable via the context menu, Delete and the Edit dialog)."""
        self._handle.set_visible(not compact)
        self._remove_button.set_visible(not compact)

    def set_matching(self, matching: bool) -> None:
        if matching != self._matching:
            self._matching = matching
            self._update_subtitle()

    def _update_subtitle(self) -> None:
        parts = [self._target_text]
        if self.rule.title_pattern:
            parts.append(_("title matches “{pattern}”").format(pattern=self.rule.title_pattern))
        line = " · ".join(parts)
        if self._shadowed:
            line += "\n" + _("Never used: an earlier rule always matches first")
        elif self._matching and self.rule.enabled:
            line += "\n" + _("Matches the focused window")
        self.set_subtitle(line)

    # --- editing ---------------------------------------------------------

    def _on_layer_selected(self, dropdown: Gtk.DropDown, _pspec: object) -> None:
        index = dropdown.get_selected()
        if index != Gtk.INVALID_LIST_POSITION and index != self.rule.layer_index:
            self.page.update_rule(self.position, layer_index=int(index))

    def _on_enabled(self, switch: Gtk.Switch, _pspec: object) -> None:
        if switch.get_active() != self.rule.enabled:
            self.page.update_rule(self.position, enabled=switch.get_active())

    def _setup_actions(self, n_rules: int) -> None:
        group = Gio.SimpleActionGroup()
        for name, callback, enabled in (
                ("move-up", lambda *_a: self.page.move_rule(self.position, -1),
                 self.position > 0),
                ("move-down", lambda *_a: self.page.move_rule(self.position, 1),
                 self.position < n_rules - 1),
                ("edit", lambda *_a: self.page.edit_rule(self.position), True),
                ("remove", lambda *_a: self.page.remove_rule(self.position), True)):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            action.set_enabled(enabled)
            group.add_action(action)
        self.insert_action_group("rule", group)

        shortcuts = Gtk.ShortcutController(scope=Gtk.ShortcutScope.LOCAL)
        for accel, action_name in (("<Alt>Up", "rule.move-up"),
                                   ("<Alt>Down", "rule.move-down"),
                                   ("Delete", "rule.remove"),
                                   ("Menu", "rule.context-menu"),
                                   ("<Shift>F10", "rule.context-menu")):
            if action_name == "rule.context-menu":
                action = Gtk.CallbackAction.new(lambda *_a: self._popup_menu(None) or True)
            else:
                action = Gtk.NamedAction.new(action_name)
            shortcuts.add_shortcut(Gtk.Shortcut.new(
                Gtk.ShortcutTrigger.parse_string(accel), action))
        self.add_controller(shortcuts)

        menu = Gio.Menu()
        section = Gio.Menu()
        section.append(_("Move _Up"), "rule.move-up")
        section.append(_("Move _Down"), "rule.move-down")
        menu.append_section(None, section)
        section = Gio.Menu()
        section.append(_("_Edit…"), "rule.edit")
        section.append(_("_Remove"), "rule.remove")
        menu.append_section(None, section)
        self._menu = Gtk.PopoverMenu.new_from_model(menu)
        self._menu.set_parent(self)
        self._menu.set_has_arrow(False)
        self._menu.set_halign(Gtk.Align.START)

        click = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        click.connect("pressed", lambda _g, _n, x, y: self._popup_menu((x, y)))
        self.add_controller(click)
        long_press = Gtk.GestureLongPress(touch_only=True)
        long_press.connect("pressed", lambda _g, x, y: self._popup_menu((x, y)))
        self.add_controller(long_press)

    def _popup_menu(self, point: tuple[float, float] | None) -> None:
        if point is not None:
            rect = Gdk.Rectangle()
            rect.x, rect.y, rect.width, rect.height = int(point[0]), int(point[1]), 1, 1
            self._menu.set_pointing_to(rect)
        else:
            self._menu.set_pointing_to(None)
        self._menu.popup()

    def do_dispose(self) -> None:
        menu = getattr(self, "_menu", None)
        if menu is not None:
            menu.unparent()
            self._menu = None
        Adw.ActionRow.do_dispose(self)

    # --- drag and drop ---------------------------------------------------

    def _setup_dnd(self) -> None:
        source = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
        source.connect("prepare", self._on_drag_prepare)
        source.connect("drag-begin", self._on_drag_begin)
        source.connect("drag-end", lambda *_a: self.remove_css_class("dragging"))
        self.add_controller(source)

        target = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        target.connect("motion", self._on_drop_motion)
        target.connect("leave", lambda *_a: self._clear_drop_hint())
        target.connect("drop", self._on_drop)
        self.add_controller(target)

    def _on_drag_prepare(self, _source: Gtk.DragSource, x: float, y: float) -> Gdk.ContentProvider:
        self._drag_offset = (x, y)
        return Gdk.ContentProvider.new_for_value(f"{DRAG_PREFIX}{self.position}")

    def _on_drag_begin(self, source: Gtk.DragSource, _drag: Gdk.Drag) -> None:
        paintable = Gtk.WidgetPaintable(widget=self)
        x, y = getattr(self, "_drag_offset", (0, 0))
        source.set_icon(paintable.get_current_image(), int(x), int(y))
        self.add_css_class("dragging")

    def _drop_below(self, y: float) -> bool:
        return y > self.get_height() / 2

    def _clear_drop_hint(self) -> None:
        self.remove_css_class("drop-above")
        self.remove_css_class("drop-below")

    def _on_drop_motion(self, _target: Gtk.DropTarget, _x: float, y: float) -> Gdk.DragAction:
        below = self._drop_below(y)
        self.add_css_class("drop-below" if below else "drop-above")
        self.remove_css_class("drop-above" if below else "drop-below")
        return Gdk.DragAction.MOVE

    def _on_drop(self, _target: Gtk.DropTarget, value: str, _x: float, y: float) -> bool:
        self._clear_drop_hint()
        if not isinstance(value, str) or not value.startswith(DRAG_PREFIX):
            return False
        try:
            src = int(value[len(DRAG_PREFIX):])
        except ValueError:
            return False
        dest = self.position + (1 if self._drop_below(y) else 0)
        if src < dest:
            dest -= 1
        GLib.idle_add(lambda: self.page.move_rule_to(src, dest) and False)
        return True


@Gtk.Template(filename=ui_file("smart_layers_page.ui"))
class SmartLayersPage(Adw.Bin):
    __gtype_name__ = "KmlSmartLayersPage"

    banner: Adw.Banner = Gtk.Template.Child()
    prefs_page: Adw.PreferencesPage = Gtk.Template.Child()
    enable_row: Adw.SwitchRow = Gtk.Template.Child()
    status_row: Adw.ActionRow = Gtk.Template.Child()
    status_icon: Gtk.Image = Gtk.Template.Child()
    status_target: Gtk.Label = Gtk.Template.Child()
    rules_group: Adw.PreferencesGroup = Gtk.Template.Child()
    rules_list: Gtk.ListBox = Gtk.Template.Child()
    fallback_group: Adw.PreferencesGroup = Gtk.Template.Child()
    fallback_row: Adw.ComboRow = Gtk.Template.Child()

    def __init__(self) -> None:
        super().__init__()
        self.service: KeyboardService | None = None
        self.controller: DeviceController | None = None
        self._toast: Callable[[Adw.Toast], None] = lambda _t: None
        self._rules: list[AppRule] = []
        self._layout: Layout | None = None
        self._focus: FocusedWindow | None = None
        self._fallback_handler = self.fallback_row.connect(
            "notify::selected", self._on_fallback_selected)
        self._placeholder_title = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER)
        self._placeholder_title.add_css_class("heading")
        self._placeholder_body = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER)
        self._placeholder_body.add_css_class("dim-label")
        placeholder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        placeholder.add_css_class("rules-placeholder")
        placeholder.append(self._placeholder_title)
        placeholder.append(self._placeholder_body)
        self.rules_list.set_placeholder(placeholder)
        self._default_description = self.rules_group.get_description()
        self._compact = False

    def set_compact(self, compact: bool) -> None:
        self._compact = compact
        for row in self._rows():
            row.set_compact(compact)

    def setup(self, service: KeyboardService, controller: DeviceController,
              show_toast: Callable[[Adw.Toast], None]) -> None:
        self.service = service
        self.controller = controller
        self._toast = show_toast
        self._binding = service.bind_property(
            "smart-layers-enabled", self.enable_row, "active",
            GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE)
        self._handlers = [
            (service, service.connect("rules-changed", lambda *_a: self._reload())),
            (service, service.connect("layout-loaded", lambda *_a: self._reload(force=True))),
            (service, service.connect("focus-changed", self._on_focus_changed)),
            (service, service.connect("focus-source-changed", self._on_focus_source_changed)),
            (service, service.connect("layer-changed", lambda *_a: self._update_status())),
            (service, service.connect("rule-applied", lambda *_a: self._update_status())),
            (service, service.connect("notify::smart-layers-enabled",
                                      lambda *_a: self._update_status())),
            (controller, controller.connect("notify::state",
                                            lambda *_a: self._reload(force=True))),
        ]
        focused = service.get_focused_window()
        if focused and focused.app_class and focused.app_class != rm.OWN_APP_CLASS:
            self._focus = focused
        self._reload(force=True)

    def teardown(self) -> None:
        for obj, handler in getattr(self, "_handlers", []):
            obj.disconnect(handler)
        self._handlers = []
        if getattr(self, "_binding", None) is not None:
            self._binding.unbind()
            self._binding = None

    # --- state -----------------------------------------------------------

    @property
    def editable(self) -> bool:
        return (self.controller is not None and self.controller.state == "ready"
                and self._layout is not None)

    def _reload(self, force: bool = False) -> None:
        assert self.service is not None and self.controller is not None
        self.banner.set_revealed(not self.service.focus_source_available())
        layout = self.service.get_layout() if self.controller.state == "ready" else None
        layout_changed = layout is not self._layout
        self._layout = layout
        rules = self.service.get_rules() if layout is not None else []
        if force or layout_changed or rules != self._rules:
            self._rules = rules
            self._rebuild_rules()
        self._sync_fallback()
        self._sync_sensitivity()
        self._update_status()

    def _sync_sensitivity(self) -> None:
        editable = self.editable
        self.rules_group.set_sensitive(editable)
        self.fallback_group.set_sensitive(editable)
        if editable:
            self.rules_group.set_description(self._default_description)
            self._placeholder_title.set_label(_("No Rules"))
            self._placeholder_body.set_label(
                _("Add a rule to switch layers when an app is focused"))
        else:
            reason = (_("Rules are saved per layout. Connect a keyboard to see and "
                        "edit the rules for its layout.")
                      if self.controller is None or self.controller.device is None
                      else _("Rules become available once the keyboard's layout is loaded."))
            self.rules_group.set_description(reason)
            self._placeholder_title.set_label(_("No Keyboard Layout"))
            self._placeholder_body.set_label(_("Connect your keyboard to manage rules"))

    def _rebuild_rules(self) -> None:
        listbox = self.rules_list
        focus_pos = None
        focus = self.get_root().get_focus() if self.get_root() else None
        if isinstance(focus, Gtk.Widget):
            row = focus if isinstance(focus, RuleRow) else focus.get_ancestor(RuleRow.__gtype__)
            if isinstance(row, RuleRow):
                focus_pos = row.position
        # Not remove_all(): that also drops the placeholder.
        for row in self._rows():
            listbox.remove(row)
        if self._layout is None:
            return
        shadowed = rm.shadowed_rules(self._rules)
        for pos, rule in enumerate(self._rules):
            listbox.append(RuleRow(self, pos, rule, self._layout, n_rules=len(self._rules),
                                   shadowed=pos in shadowed, compact=self._compact))
        if focus_pos is not None:
            row = listbox.get_row_at_index(min(focus_pos, len(self._rules) - 1))
            if row is not None:
                row.grab_focus()

    def _rows(self) -> list[RuleRow]:
        rows: list[RuleRow] = []
        child = self.rules_list.get_first_child()
        while child is not None:
            if isinstance(child, RuleRow):
                rows.append(child)
            child = child.get_next_sibling()
        return rows

    def _sync_fallback(self) -> None:
        assert self.service is not None
        layout = self._layout
        labels = [_("Don't Change")]
        if layout is not None:
            labels += [rm.layer_label(layer, layer.index) for layer in layout.layers]
        self.fallback_row.handler_block(self._fallback_handler)
        model = self.fallback_row.get_model()
        current = ([model.get_string(i) for i in range(model.get_n_items())]
                   if isinstance(model, Gtk.StringList) else None)
        if current != labels:
            self.fallback_row.set_model(Gtk.StringList.new(labels))
        fallback = self.service.get_fallback_layer() if layout is not None else None
        selected = 0 if fallback is None else min(fallback + 1, len(labels) - 1)
        self.fallback_row.set_selected(selected)
        self.fallback_row.handler_unblock(self._fallback_handler)

    def _on_fallback_selected(self, row: Adw.ComboRow, _pspec: object) -> None:
        if self.service is None or not self.editable:
            return
        selected = row.get_selected()
        if selected == Gtk.INVALID_LIST_POSITION:
            return
        value = None if selected == 0 else int(selected) - 1
        if value != self.service.get_fallback_layer():
            self.service.set_fallback_layer(value)

    # --- status ----------------------------------------------------------

    def _on_focus_source_changed(self, _service: KeyboardService, available: bool) -> None:
        self.banner.set_revealed(not available)
        if not available:
            self._focus = None
        self._update_status()

    def _on_focus_changed(self, _service: KeyboardService, app_class: str, title: str) -> None:
        # Our own window is ignored by the engine; keep showing the last app.
        if app_class == rm.OWN_APP_CLASS:
            return
        window = self.service.get_focused_window() if self.service is not None else None
        if window is None or window.app_class != app_class:
            # Shouldn't happen (the contract keeps them in sync); degrade gracefully.
            window = FocusedWindow(app_class, title)
        self._focus = window if app_class else None
        if self.service is not None:
            self.banner.set_revealed(not self.service.focus_source_available())
        self._update_status()

    def _update_status(self) -> None:
        if self.service is None:
            return
        row = self.status_row
        target_label = self.status_target
        target_label.remove_css_class("dim-label")
        focus = self._focus
        if focus is None:
            row.set_title(_("No Focused App"))
            row.set_subtitle(_("Waiting for a window to be focused")
                             if self.service.focus_source_available()
                             else _("Focus tracking is unavailable"))
            self.status_icon.set_from_icon_name("focus-windows-symbolic")
            target_label.set_label("")
            for rule_row in self._rows():
                rule_row.set_matching(False)
            return

        app_class, title, steam_id = focus.app_class, focus.title, focus.steam_app_id
        if steam_id:
            name = focus.steam_game_name or _("Steam Game {id}").format(id=steam_id)
            row.set_title(_("Focused: {game} (Steam)").format(game=name))
            self.status_icon.set_from_gicon(steam_game_icon(steam_id))
        else:
            entry = AppIndex.get().entry_for(app_class)
            row.set_title(_("Focused: {app}").format(app=entry.name))
            self.status_icon.set_from_gicon(entry.icon)
        row.set_subtitle(title or app_class)

        match_pos = None
        if self._layout is None:
            target_label.set_label(_("No layout loaded"))
            target_label.add_css_class("dim-label")
        else:
            fallback = self.service.get_fallback_layer()
            target, rule = rm.target_layer(self._rules, fallback, app_class, title, steam_id)
            layers = self._layout.layers
            layer = layers[target] if target is not None and 0 <= target < len(layers) else None
            if rule is not None:
                match_pos = self._rules.index(rule)
                target_label.set_label("→ " + rm.layer_label(layer, target))
            elif fallback is None:
                target_label.set_label(_("No rule — layer unchanged"))
            else:
                target_label.set_label(_("No rule — fallback to {layer}").format(
                    layer=rm.layer_label(layer, target)))
            if not self.service.props.smart_layers_enabled:
                target_label.add_css_class("dim-label")
                target_label.set_tooltip_text(_("Smart Layers is off"))
            else:
                target_label.set_tooltip_text(None)
        for rule_row in self._rows():
            rule_row.set_matching(rule_row.position == match_pos)

    # --- rule editing (called by rows / window actions) ------------------

    def _commit(self, rules: list[AppRule], *, rebuild: bool = True) -> None:
        assert self.service is not None
        self._rules = list(rules)
        if rebuild:
            self._rebuild_rules()
        self.service.set_rules(list(rules))
        self._update_status()

    def update_rule(self, position: int, **changes: object) -> None:
        if not self.editable:
            return
        rules = rm.update_rule(self._rules, position, **changes)
        # Only the shadowing hint may change, the row already shows the edit.
        needs_rebuild = rm.shadowed_rules(rules) != rm.shadowed_rules(self._rules)
        for row in self._rows():
            if row.position == position:
                row.rule = rules[position]
                row._update_subtitle()
        self._commit(rules, rebuild=needs_rebuild)

    def move_rule(self, position: int, delta: int) -> None:
        self.move_rule_to(position, position + delta)

    def move_rule_to(self, src: int, dest: int) -> None:
        if not self.editable:
            return
        rules = rm.move_rule_to(self._rules, src, dest)
        if rules == self._rules:
            return
        self._commit(rules)
        row = self.rules_list.get_row_at_index(max(0, min(dest, len(rules) - 1)))
        if row is not None:
            row.grab_focus()

    def remove_rule(self, position: int) -> None:
        if not self.editable or not 0 <= position < len(self._rules):
            return
        before = list(self._rules)
        removed = before[position]
        self._commit(rm.remove_rule(before, position))
        toast = Adw.Toast(title=_("Rule for {app} removed").format(app=removed.display_name),
                          button_label=_("_Undo"), priority=Adw.ToastPriority.HIGH)

        def undo(_toast: Adw.Toast) -> None:
            if self.editable:
                current = list(self._rules)
                current.insert(min(position, len(current)), removed)
                self._commit(current)

        toast.connect("button-clicked", undo)
        self._toast(toast)

    def add_rule(self) -> None:
        if not self.editable or self._layout is None or self.service is None:
            if self.controller is not None:
                self._toast(Adw.Toast(title=_("Connect your keyboard to add rules")))
            return

        def done(rule: AppRule) -> None:
            if self.editable:
                self._commit(rm.add_rule(self._rules, rule))

        present_rule_dialog(self, layout=self._layout,
                            running=self.service.list_running_apps(),
                            existing=self._rules, on_done=done)

    def edit_rule(self, position: int) -> None:
        if not self.editable or self._layout is None or self.service is None:
            return
        if not 0 <= position < len(self._rules):
            return
        original = self._rules[position]

        def done(rule: AppRule) -> None:
            if self.editable and position < len(self._rules) and \
                    self._rules[position] == original:
                rules = list(self._rules)
                rules[position] = rule
                self._commit(rules)

        def remove() -> None:
            if position < len(self._rules) and self._rules[position] == original:
                self.remove_rule(position)

        present_rule_dialog(self, layout=self._layout,
                            running=self.service.list_running_apps(),
                            existing=self._rules, on_done=done, rule=original,
                            on_remove=remove)
