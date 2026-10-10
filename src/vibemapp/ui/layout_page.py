"""Layout page: the keyboard drawing, layer selector and empty states."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, GObject, Gtk, Pango  # noqa: E402

from ..models import Layout  # noqa: E402
from ..service import KeyboardService  # noqa: E402
from .controller import DeviceController  # noqa: E402
from .i18n import _  # noqa: E402
from .keyboard_view import KeyboardView  # noqa: E402,F401  (registers the type)
from .resources import ui_file  # noqa: E402
from .rules_model import layer_label  # noqa: E402
from .widgets import LayerDot  # noqa: E402

MAX_TOGGLES = 6


@Gtk.Template(filename=ui_file("layout_page.ui"))
class LayoutPage(Adw.Bin):
    __gtype_name__ = "KmlLayoutPage"

    window_title: Adw.WindowTitle = Gtk.Template.Child()
    device_button: Gtk.MenuButton = Gtk.Template.Child()
    stack: Gtk.Stack = Gtk.Template.Child()
    status_page: Adw.StatusPage = Gtk.Template.Child()
    status_button: Gtk.Button = Gtk.Template.Child()
    loading_page: Adw.StatusPage = Gtk.Template.Child()
    selector_bin: Adw.Bin = Gtk.Template.Child()
    keyboard_view: KeyboardView = Gtk.Template.Child()
    active_dot: LayerDot = Gtk.Template.Child()
    active_label: Gtk.Label = Gtk.Template.Child()
    request_revealer: Gtk.Revealer = Gtk.Template.Child()

    # Emitted when the viewed or active layer changes, so the window can
    # update action sensitivity.
    __gsignals__ = {"layers-changed": (GObject.SignalFlags.RUN_LAST, None, ())}

    def __init__(self) -> None:
        super().__init__()
        self.service: KeyboardService | None = None
        self.controller: DeviceController | None = None
        self._layout: Layout | None = None
        self._view_layer = 0
        self._selector: Gtk.Widget | None = None
        self._selector_handler = 0
        self._status_action: str | None = None

    def setup(self, service: KeyboardService, controller: DeviceController) -> None:
        self.service = service
        self.controller = controller
        self._handlers = [
            (service, service.connect("layer-changed", self._on_layer_changed)),
            (service, service.connect("key-event", self._on_key_event)),
            (service, service.connect("layout-loaded", lambda *_a: self._sync())),
            (service, service.connect("disconnected", lambda *_a: self.keyboard_view.clear_pressed())),
            (controller, controller.connect("notify::state", lambda *_a: self._sync())),
            (controller, controller.connect("notify::message", lambda *_a: self._sync())),
        ]
        self._sync()

    def teardown(self) -> None:
        for obj, handler in getattr(self, "_handlers", []):
            obj.disconnect(handler)
        self._handlers = []

    # --- state -----------------------------------------------------------

    @property
    def view_layer(self) -> int:
        return self._view_layer

    @property
    def active_layer(self) -> int:
        return self.service.get_active_layer() if self.service else 0

    @property
    def is_ready(self) -> bool:
        return self.controller is not None and self.controller.state == "ready"

    def n_layers(self) -> int:
        return len(self._layout.layers) if self._layout else 0

    def _sync(self) -> None:
        assert self.controller is not None and self.service is not None
        state = self.controller.state
        device = self.controller.device
        self.window_title.set_subtitle(device.name if device else "")
        if state == "ready":
            layout = self.service.get_layout()
            if layout is not None and layout is not self._layout:
                self._set_layout(layout)
            if self._layout is not None:
                self.window_title.set_subtitle(self._layout.title)
            self.stack.set_visible_child_name("layout")
        elif state in ("connecting", "loading"):
            self._layout = None
            self.loading_page.set_title(_("Connecting…") if state == "connecting"
                                        else _("Loading Layout…"))
            self.loading_page.set_description(
                _("Fetching the layout from Oryx") if state == "loading" else None)
            self.stack.set_visible_child_name("loading")
        else:
            self._layout = None
            self._show_status(state)
        self.emit("layers-changed")

    def _show_status(self, state: str) -> None:
        assert self.controller is not None
        page = self.status_page
        message = GLib.markup_escape_text(self.controller.message or "")
        udev_hint = _("If your keyboard is plugged in but not found, install the "
                      "udev rules and replug it.")
        action: tuple[str, str] | None = None
        if state == "available":
            device = self.controller.available_device()
            page.set_icon_name("input-keyboard-symbolic")
            page.set_title(_("Keyboard Found"))
            page.set_description(_("{name} is plugged in and ready to connect.").format(
                name=GLib.markup_escape_text(device.name if device else _("Your keyboard"))))
            action = ("connect", _("_Connect"))
        elif state == "failed":
            page.set_icon_name("dialog-warning-symbolic")
            page.set_title(_("Can't Open Keyboard"))
            hint = _("Make sure the udev rules are installed, then replug the keyboard.")
            device = self.controller.available_device()
            if device is not None and device.access_denied:
                page.set_title(_("Permission Needed"))
            page.set_description(message if "udev" in message.lower()
                                 else f"{message}\n\n{hint}" if message else hint)
            action = ("connect", _("_Try Again"))
        elif state == "no-layout":
            page.set_icon_name("dialog-warning-symbolic")
            page.set_title(_("Layout Unavailable"))
            page.set_description(message)
            device = self.controller.device
            if device is not None and device.oryx_available:
                action = ("reload", _("_Try Again"))
        else:
            page.set_icon_name("input-keyboard-symbolic")
            page.set_title(_("Connect Your Keyboard"))
            page.set_description(
                _("Plug in a ZSA keyboard running Oryx firmware, such as the Voyager "
                  "or Moonlander.") + "\n\n" + udev_hint)
        self._status_action = action[0] if action else None
        self.status_button.set_visible(action is not None)
        if action is not None:
            self.status_button.set_label(action[1])
            self.status_button.set_use_underline(True)
        self.stack.set_visible_child_name("status")

    @Gtk.Template.Callback()
    def on_status_button_clicked(self, _button: Gtk.Button) -> None:
        if self.controller is None:
            return
        if self._status_action == "connect":
            self.controller.connect_device()
        elif self._status_action == "reload":
            self.controller.reload_layout()

    # --- layout & layers -------------------------------------------------

    def _set_layout(self, layout: Layout) -> None:
        self._layout = layout
        self.window_title.set_subtitle(layout.title)
        self.keyboard_view.set_geometry(layout.geometry)
        self._view_layer = min(self.active_layer, max(len(layout.layers) - 1, 0))
        self._build_selector(layout)
        self._show_layer(animate=False)

    def _build_selector(self, layout: Layout) -> None:
        if len(layout.layers) <= MAX_TOGGLES:
            group = Adw.ToggleGroup(can_shrink=True, homogeneous=False)
            group.update_property([Gtk.AccessibleProperty.LABEL], [_("Layer")])
            for layer in layout.layers:
                box = Gtk.Box(spacing=6, margin_start=6, margin_end=6)
                box.append(LayerDot(layer.color))
                box.append(Gtk.Label(label=layer.title or layer_label(None, layer.index),
                                     ellipsize=Pango.EllipsizeMode.END))
                toggle = Adw.Toggle(name=str(layer.index), child=box,
                                    label=layer.title,
                                    tooltip=layer_label(layer, layer.index))
                group.add(toggle)
            group.set_active(self._view_layer)
            self._selector_handler = group.connect("notify::active", self._on_toggle_active)
            self._selector = group
        else:
            model = Gtk.StringList.new([layer_label(layer, layer.index)
                                        for layer in layout.layers])
            dropdown = Gtk.DropDown(model=model, selected=self._view_layer)
            dropdown.update_property([Gtk.AccessibleProperty.LABEL], [_("Layer")])
            self._selector_handler = dropdown.connect("notify::selected",
                                                      self._on_dropdown_selected)
            self._selector = dropdown
        self.selector_bin.set_child(self._selector)

    def _set_selector(self, index: int) -> None:
        selector = self._selector
        if selector is None:
            return
        selector.handler_block(self._selector_handler)
        if isinstance(selector, Adw.ToggleGroup):
            selector.set_active(index)
        elif isinstance(selector, Gtk.DropDown):
            selector.set_selected(index)
        selector.handler_unblock(self._selector_handler)

    def _on_toggle_active(self, group: Adw.ToggleGroup, _pspec: object) -> None:
        self.view_layer_index(group.get_active())

    def _on_dropdown_selected(self, dropdown: Gtk.DropDown, _pspec: object) -> None:
        self.view_layer_index(dropdown.get_selected())

    def view_layer_index(self, index: int, animate: bool = True) -> None:
        """Show ``index`` without touching the keyboard."""
        if self._layout is None or not 0 <= index < len(self._layout.layers):
            return
        self._view_layer = index
        self._set_selector(index)
        self._show_layer(animate)

    def step_layer(self, delta: int) -> None:
        if self._layout is not None:
            self.view_layer_index((self._view_layer + delta) % len(self._layout.layers))

    def _show_layer(self, animate: bool) -> None:
        if self._layout is None:
            return
        layer = self._layout.layers[self._view_layer]
        self.keyboard_view.set_layer(layer, animate=animate, layers=self._layout.layers)
        self._update_active_label()
        self.emit("layers-changed")

    def _update_active_label(self) -> None:
        if self._layout is None:
            return
        active = self.active_layer
        layers = self._layout.layers
        active_layer = layers[active] if 0 <= active < len(layers) else None
        self.active_dot.set_color(active_layer.color if active_layer else None)
        self.active_label.set_label(_("Keyboard is on {layer}").format(
            layer=layer_label(active_layer, active)))
        self.request_revealer.set_reveal_child(active != self._view_layer)

    def _on_layer_changed(self, _service: KeyboardService, index: int) -> None:
        # Follow the keyboard: the view jumps to whatever layer it reports.
        if self._layout is not None and 0 <= index < len(self._layout.layers):
            self.view_layer_index(index)
        self._update_active_label()
        self.emit("layers-changed")

    def _on_key_event(self, _service: KeyboardService, index: int, pressed: bool) -> None:
        if self._layout is not None:
            self.keyboard_view.set_key_pressed(index, pressed)
