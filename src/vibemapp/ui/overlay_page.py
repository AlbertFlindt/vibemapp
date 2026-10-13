"""Overlay page: when and how the see-through layout overlay is shown.

Everything here is a GSettings front end; the overlay window itself
(``ui/overlay.py``) reads the same keys. Preview drives the stateful
``app.overlay-preview`` action.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Sequence

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from ..models import Layout  # noqa: E402
from ..service import KeyboardService  # noqa: E402
from .controller import DeviceController  # noqa: E402
from .i18n import _  # noqa: E402
from .resources import ui_file  # noqa: E402
from .rules_model import layer_label  # noqa: E402

log = logging.getLogger(__name__)

# Combo row positions <-> GSettings choices (same order as overlay_page.ui).
MODES = ("off", "selected", "always")
ANCHORS = ("bottom", "bottom-left", "bottom-right", "top", "top-left", "top-right")
PREVIEW_ACTION = "overlay-preview"

SHORTCUT_SNIPPET = 'hl.bind("SUPER + O", hl.dsp.exec_cmd("vibemapp --toggle-overlay"))'
INSTALL_COMMAND = "sudo pacman -S gtk4-layer-shell"


# --- pure helpers (unit tested) -------------------------------------------

def choice_index(choices: Sequence[str], value: str) -> int:
    """Combo position of a GSettings choice; unknown values fall back to 0."""
    return choices.index(value) if value in choices else 0


def choice_value(choices: Sequence[str], index: int) -> str:
    return choices[index] if 0 <= index < len(choices) else choices[0]


def toggle_layer(layers: Iterable[int], index: int, on: bool) -> list[int]:
    """Sorted, de-duplicated layer list with ``index`` added or removed.

    Indices the current layout doesn't have are kept: they belong to
    another layout (or a future one) and the user didn't touch them."""
    result = {int(i) for i in layers}
    if on:
        result.add(index)
    else:
        result.discard(index)
    return sorted(result)


def needs_layer_hint(mode: str, layers: Iterable[int],
                     available: Iterable[int] | None) -> bool:
    """True when "On Selected Layers" can never show the overlay.

    ``available`` are the current layout's layer indices, or None if no
    layout is loaded (then any selection counts)."""
    if mode != "selected":
        return False
    chosen = set(layers)
    if available is None:
        return not chosen
    return not chosen & set(available)


def monitor_label(connector: str, description: str | None, model: str | None,
                  manufacturer: str | None = None) -> str:
    """``"Dell U2720Q (DP-1)"``; the connector alone if nothing else is known."""
    name = (description or "").strip()
    if not name:
        name = " ".join(p for p in ((manufacturer or "").strip(), (model or "").strip()) if p)
    if not name:
        return connector or _("Unknown Monitor")
    if connector and connector not in name:
        # Translators: monitor name, then its connector, e.g. "Dell U2720Q (DP-1)"
        return _("{name} ({connector})").format(name=name, connector=connector)
    return name


def monitor_entries(monitors: Iterable[tuple[str, str]], saved: str
                    ) -> list[tuple[str, str]]:
    """``(value, label)`` rows for the Monitor combo.

    ``monitors`` are ``(connector, label)`` pairs. First comes "Focused
    Monitor" (value ""); a saved monitor that isn't connected stays listed so
    the setting isn't lost while it's unplugged."""
    entries = [("", _("Focused Monitor"))]
    seen = {""}
    for connector, label in monitors:
        if not connector or connector in seen:
            continue
        seen.add(connector)
        entries.append((connector, label))
    if saved and saved not in seen:
        # Translators: a monitor connector name such as "DP-2"
        entries.append((saved, _("{connector} (Not Connected)").format(connector=saved)))
    return entries


def get_layers(settings: Gio.Settings) -> list[int]:
    return [int(i) for i in settings.get_value("overlay-layers").unpack()]


def set_layers(settings: Gio.Settings, layers: Iterable[int]) -> None:
    value = sorted({int(i) for i in layers})
    if value != get_layers(settings):
        settings.set_value("overlay-layers", GLib.Variant("ai", value))


def has_halves(layout: Layout | None) -> bool:
    """Whether the board is split, so Half Spacing applies. Unknown (no
    layout, or the overlay module is missing) counts as split."""
    if layout is None or not layout.geometry:
        return True
    try:
        from .overlay_state import split_geometry
    except ImportError:
        return True
    return split_geometry(layout.geometry) is not None


def layer_shell_available() -> bool:
    """Ask the overlay module; if it can't be imported, assume it works."""
    try:
        from .overlay import layer_shell_available as available
    except ImportError:
        return True
    try:
        return bool(available())
    except Exception:  # noqa: BLE001 — a probe must never break the page
        log.exception("Couldn't check for gtk4-layer-shell")
        return True


# --- page -------------------------------------------------------------------

@Gtk.Template(filename=ui_file("overlay_page.ui"))
class OverlayPage(Adw.Bin):
    __gtype_name__ = "KmlOverlayPage"

    stack: Gtk.Stack = Gtk.Template.Child()
    install_label: Gtk.Label = Gtk.Template.Child()
    mode_row: Adw.ComboRow = Gtk.Template.Child()
    idle_row: Adw.SpinRow = Gtk.Template.Child()
    layers_group: Adw.PreferencesGroup = Gtk.Template.Child()
    layers_list: Gtk.ListBox = Gtk.Template.Child()
    preview_row: Adw.SwitchRow = Gtk.Template.Child()
    anchor_row: Adw.ComboRow = Gtk.Template.Child()
    monitor_row: Adw.ComboRow = Gtk.Template.Child()
    size_row: Adw.SpinRow = Gtk.Template.Child()
    half_spacing_row: Adw.SpinRow = Gtk.Template.Child()
    opacity_row: Adw.SpinRow = Gtk.Template.Child()
    snippet_label: Gtk.Label = Gtk.Template.Child()

    def __init__(self) -> None:
        super().__init__()
        self.service: KeyboardService | None = None
        self.controller: DeviceController | None = None
        self.settings: Gio.Settings | None = None
        self._add_toast: Callable[[Adw.Toast], None] | None = None
        self._handlers: list[tuple[object, int]] = []
        self._layout: Layout | None = None
        self._built = False
        self._layer_rows: dict[int, Adw.SwitchRow] = {}
        self._monitor_values: list[str] = []
        self._syncing = False
        self._writing_monitor = False
        self._app: Gio.ActionGroup | None = None
        self.snippet_label.set_label(SHORTCUT_SNIPPET)
        self.install_label.set_label(INSTALL_COMMAND)
        # Leaving the page or hiding the window unmaps it: stop previewing.
        self.connect("unmap", lambda *_a: self.stop_preview())

    def setup(self, service: KeyboardService, controller: DeviceController,
              settings: Gio.Settings, add_toast: Callable[[Adw.Toast], None],
              actions: Gio.ActionGroup | None = None) -> None:
        """``actions`` holds ``overlay-preview``; defaults to the window's app."""
        self.service = service
        self.controller = controller
        self.settings = settings
        self._add_toast = add_toast
        # The overlay can't work without layer-shell: no settings then.
        self.stack.set_visible_child_name(
            "settings" if layer_shell_available() else "unavailable")

        connect = self._handlers.append
        for key, refresh in (("overlay-mode", self._sync_mode),
                             ("overlay-layers", self._sync_layers),
                             ("overlay-anchor", self._sync_anchor),
                             ("overlay-monitor", self._sync_monitors),
                             ("overlay-idle-seconds", self._sync_spins),
                             ("overlay-size", self._sync_spins),
                             ("overlay-half-spacing", self._sync_spins),
                             ("overlay-opacity", self._sync_spins)):
            connect((settings, settings.connect(f"changed::{key}",
                                                lambda *_a, cb=refresh: cb())))
        connect((service, service.connect("layout-loaded", lambda *_a: self._sync_layers())))
        connect((service, service.connect("disconnected", lambda *_a: self._sync_layers())))
        connect((controller, controller.connect("notify::state",
                                                lambda *_a: self._sync_layers())))
        display = Gdk.Display.get_default()
        if display is not None:
            monitors = display.get_monitors()
            connect((monitors, monitors.connect("items-changed",
                                                lambda *_a: self._sync_monitors())))

        self.mode_row.connect("notify::selected", self._on_mode_selected)
        self.anchor_row.connect("notify::selected", self._on_anchor_selected)
        self.monitor_row.connect("notify::selected", self._on_monitor_selected)
        for row, key in ((self.idle_row, "overlay-idle-seconds"),
                         (self.size_row, "overlay-size"),
                         (self.half_spacing_row, "overlay-half-spacing"),
                         (self.opacity_row, "overlay-opacity")):
            row.connect("notify::value", self._on_spin_value, key)
        self.preview_row.connect("notify::active", self._on_preview_active)

        self._sync_mode()
        self._sync_layers()
        self._sync_anchor()
        self._sync_monitors()
        self._sync_spins()
        self._watch_preview_action(actions)

    def teardown(self) -> None:
        self.stop_preview()
        for obj, handler in self._handlers:
            obj.disconnect(handler)
        self._handlers = []
        self._app = None

    # --- settings -> widgets ---------------------------------------------

    def _guarded(self, fn: Callable[[], None]) -> None:
        self._syncing = True
        try:
            fn()
        finally:
            self._syncing = False

    def _sync_mode(self) -> None:
        mode = self.settings.get_string("overlay-mode")
        self._guarded(lambda: self.mode_row.set_selected(choice_index(MODES, mode)))
        self.layers_group.set_visible(mode == "selected")
        self.idle_row.set_sensitive(mode != "off")
        self._sync_hint()

    def _sync_hint(self) -> None:
        mode = self.settings.get_string("overlay-mode")
        layout = self._current_layout()
        available = [layer.index for layer in layout.layers] if layout else None
        if needs_layer_hint(mode, get_layers(self.settings), available):
            self.mode_row.set_subtitle(_("Choose the layers below"))
        elif mode == "always":
            self.mode_row.set_subtitle(_("Shows on every layer, also while you type"))
        else:
            self.mode_row.set_subtitle("")

    def _current_layout(self) -> Layout | None:
        if self.service is None or self.controller is None:
            return None
        if self.controller.state != "ready":
            return None
        return self.service.get_layout()

    def _sync_layers(self) -> None:
        layout = self._current_layout()
        chosen = set(get_layers(self.settings))
        if not self._built or layout is not self._layout:
            self._rebuild_layer_rows(layout)
        self.half_spacing_row.set_visible(has_halves(layout))
        for index, row in self._layer_rows.items():
            self._guarded(lambda r=row, i=index: r.set_active(i in chosen))
        self._sync_hint()

    def _rebuild_layer_rows(self, layout: Layout | None) -> None:
        self._built = True
        self._layout = layout
        self._layer_rows = {}
        self.layers_list.remove_all()
        if layout is None or not layout.layers:
            empty = Adw.ActionRow(title=_("No Keyboard Layout"),
                                  subtitle=_("Connect your keyboard to choose its layers"),
                                  use_markup=False)
            empty.add_css_class("dim-label")
            self.layers_list.append(empty)
            return
        for layer in layout.layers:
            row = Adw.SwitchRow(title=layer_label(layer, layer.index), use_markup=False)
            if layer.index == 0:
                row.set_subtitle(_("Shows the overlay whenever no other layer is active"))
            row.connect("notify::active", self._on_layer_active, layer.index)
            self.layers_list.append(row)
            self._layer_rows[layer.index] = row

    def _sync_anchor(self) -> None:
        anchor = self.settings.get_string("overlay-anchor")
        self._guarded(lambda: self.anchor_row.set_selected(choice_index(ANCHORS, anchor)))

    def _sync_monitors(self) -> None:
        if self._writing_monitor:
            return
        saved = self.settings.get_string("overlay-monitor")
        monitors = []
        display = Gdk.Display.get_default()
        if display is not None:
            model = display.get_monitors()
            for i in range(model.get_n_items()):
                monitor = model.get_item(i)
                connector = monitor.get_connector() or ""
                monitors.append((connector, monitor_label(
                    connector, monitor.get_description(), monitor.get_model(),
                    monitor.get_manufacturer())))
        entries = monitor_entries(monitors, saved)
        values = [value for value, _label in entries]

        def apply() -> None:
            if values != self._monitor_values or self.monitor_row.get_model() is None:
                self.monitor_row.set_model(Gtk.StringList.new([lbl for _v, lbl in entries]))
                self._monitor_values = values
            self.monitor_row.set_selected(values.index(saved) if saved in values else 0)

        self._guarded(apply)

    def _sync_spins(self) -> None:
        def apply() -> None:
            for row, key in ((self.idle_row, "overlay-idle-seconds"),
                             (self.size_row, "overlay-size"),
                             (self.half_spacing_row, "overlay-half-spacing"),
                             (self.opacity_row, "overlay-opacity")):
                value = self.settings.get_int(key)
                if int(row.get_value()) != value:
                    row.set_value(value)

        self._guarded(apply)

    # --- widgets -> settings ---------------------------------------------

    def _on_mode_selected(self, row: Adw.ComboRow, _pspec: object) -> None:
        if not self._syncing:
            self.settings.set_string("overlay-mode", choice_value(MODES, row.get_selected()))

    def _on_anchor_selected(self, row: Adw.ComboRow, _pspec: object) -> None:
        if not self._syncing:
            self.settings.set_string("overlay-anchor",
                                     choice_value(ANCHORS, row.get_selected()))

    def _on_monitor_selected(self, row: Adw.ComboRow, _pspec: object) -> None:
        index = row.get_selected()
        if not self._syncing and 0 <= index < len(self._monitor_values):
            # The row already shows the choice. Rebuilding its model from the
            # changed:: handler, inside this notify, crashes GTK; an unplugged
            # monitor's entry simply stays until the monitor list changes.
            self._writing_monitor = True
            try:
                self.settings.set_string("overlay-monitor", self._monitor_values[index])
            finally:
                self._writing_monitor = False

    def _on_spin_value(self, row: Adw.SpinRow, _pspec: object, key: str) -> None:
        if not self._syncing and int(row.get_value()) != self.settings.get_int(key):
            self.settings.set_int(key, int(row.get_value()))

    def _on_layer_active(self, row: Adw.SwitchRow, _pspec: object, index: int) -> None:
        if not self._syncing:
            set_layers(self.settings,
                       toggle_layer(get_layers(self.settings), index, row.get_active()))

    # --- preview ---------------------------------------------------------

    def _watch_preview_action(self, app: Gio.ActionGroup | None) -> None:
        if app is None:
            root = self.get_root()
            app = root.get_application() if isinstance(root, Gtk.Window) else None
        if app is None:
            app = Gio.Application.get_default()
        if app is None:
            self.preview_row.set_visible(False)
            return
        self._app = app
        connect = self._handlers.append
        connect((app, app.connect(f"action-added::{PREVIEW_ACTION}",
                                  lambda *_a: self._sync_preview())))
        connect((app, app.connect(f"action-removed::{PREVIEW_ACTION}",
                                  lambda *_a: self._sync_preview())))
        connect((app, app.connect(f"action-enabled-changed::{PREVIEW_ACTION}",
                                  lambda *_a: self._sync_preview())))
        connect((app, app.connect(f"action-state-changed::{PREVIEW_ACTION}",
                                  lambda *_a: self._sync_preview())))
        self._sync_preview()

    def _preview_state(self) -> bool | None:
        """Current preview state, or None if the action doesn't exist."""
        app = self._app
        if app is None or not app.has_action(PREVIEW_ACTION):
            return None
        state = app.get_action_state(PREVIEW_ACTION)
        if state is None or not state.is_of_type(GLib.VariantType.new("b")):
            return None
        return state.get_boolean()

    def _sync_preview(self) -> None:
        state = self._preview_state()
        self.preview_row.set_visible(state is not None)
        if state is None:
            return
        self.preview_row.set_sensitive(self._app.get_action_enabled(PREVIEW_ACTION))
        self._guarded(lambda: self.preview_row.set_active(state))

    def _on_preview_active(self, row: Adw.SwitchRow, _pspec: object) -> None:
        if self._syncing:
            return
        if self._preview_state() is None:
            return
        self._app.change_action_state(PREVIEW_ACTION, GLib.Variant("b", row.get_active()))

    def stop_preview(self) -> None:
        if self._preview_state():
            self._app.change_action_state(PREVIEW_ACTION, GLib.Variant("b", False))

    # --- shortcut --------------------------------------------------------

    @Gtk.Template.Callback()
    def on_copy_clicked(self, button: Gtk.Button) -> None:
        self._copy(button, SHORTCUT_SNIPPET)

    @Gtk.Template.Callback()
    def on_copy_install_clicked(self, button: Gtk.Button) -> None:
        self._copy(button, INSTALL_COMMAND)

    def _copy(self, button: Gtk.Button, text: str) -> None:
        button.get_clipboard().set(text)
        if self._add_toast is not None:
            self._add_toast(Adw.Toast(title=_("Copied to clipboard")))
