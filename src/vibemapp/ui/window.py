"""Main window: sidebar navigation between Layout, Smart Layers, Heatmap, Overlay and Insights."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from ..service import KeyboardService  # noqa: E402
from .controller import DeviceController  # noqa: E402
from .device_dialog import DeviceDialog  # noqa: E402
from . import instance_check  # noqa: E402
from .i18n import _  # noqa: E402
from .layout_page import LayoutPage  # noqa: E402
from ..appid import is_devel  # noqa: E402
from .resources import app_title, ui_file  # noqa: E402
from .rules_model import layer_label  # noqa: E402
from .heatmap_page import HeatmapPage  # noqa: E402
from .insights_page import InsightsPage  # noqa: E402
from .overlay_page import OverlayPage  # noqa: E402
from .smart_layers_page import SmartLayersPage  # noqa: E402

PAGES = ("layout", "smart-layers", "heatmap", "overlay", "insights")


def page_title(name: str) -> str:
    return {"layout": _("Layout"), "smart-layers": _("Smart Layers"),
            "heatmap": _("Heatmap"), "overlay": _("Overlay"),
            "insights": _("Insights")}[name]


@Gtk.Template(filename=ui_file("window.ui"))
class Window(Adw.ApplicationWindow):
    __gtype_name__ = "KmlWindow"

    toast_overlay: Adw.ToastOverlay = Gtk.Template.Child()
    split_view: Adw.NavigationSplitView = Gtk.Template.Child()
    sidebar: Adw.Sidebar = Gtk.Template.Child()
    content_page: Adw.NavigationPage = Gtk.Template.Child()
    content_stack: Gtk.Stack = Gtk.Template.Child()
    layout_page: LayoutPage = Gtk.Template.Child()
    smart_layers_page: SmartLayersPage = Gtk.Template.Child()
    heatmap_page: HeatmapPage = Gtk.Template.Child()
    overlay_page: OverlayPage = Gtk.Template.Child()
    insights_page: InsightsPage = Gtk.Template.Child()
    device_status_icon: Gtk.Image = Gtk.Template.Child()
    device_status_title: Gtk.Label = Gtk.Template.Child()
    device_status_subtitle: Gtk.Label = Gtk.Template.Child()

    def __init__(self, application: Adw.Application, service: KeyboardService,
                 controller: DeviceController, settings: Gio.Settings) -> None:
        super().__init__(application=application)
        self.service = service
        self.controller = controller
        self.settings = settings
        if is_devel():
            # GNOME convention for development builds: striped header bars.
            # The window title (task bars, Hyprland) says so too; the sidebar
            # heading stays "Vibemapp" because the longer one is truncated.
            self.add_css_class("devel")
            self.set_title(app_title())

        self.set_default_size(settings.get_int("window-width"),
                              settings.get_int("window-height"))
        if settings.get_boolean("window-maximized"):
            self.maximize()

        self._setup_actions()
        self.layout_page.setup(service, controller)
        self.smart_layers_page.setup(service, controller, self.add_toast)
        self.heatmap_page.setup(service, controller)
        self.overlay_page.setup(service, controller, settings, self.add_toast)
        self.insights_page.setup(service, controller)
        self.layout_page.connect("layers-changed", lambda *_a: self._sync_actions())

        self._handlers = [
            (controller, controller.connect("notify::state", lambda *_a: self._sync())),
            (controller, controller.connect("error", self._on_error)),
            (service, service.connect("layer-changed", lambda *_a: self._sync_device_status())),
        ]

        self._setup_instance_check()

        # Always open on Layout (also when re-shown from the tray).
        self.show_page("layout", reveal=False)
        self._sync()

    # --- installed app also running (dev runs) ---------------------------

    def _setup_instance_check(self) -> None:
        from ..mock_service import MockKeyboardService

        self._instance_watcher: instance_check.InstalledAppWatcher | None = None
        self._instance_banners: list[Adw.Banner] = []
        if not instance_check.should_watch(is_devel(),
                                           isinstance(self.service, MockKeyboardService)):
            return
        # One banner under each page's header bar (HIG placement); each page
        # has its own toolbar view.
        for page in (self.layout_page, self.smart_layers_page, self.heatmap_page,
                     self.overlay_page, self.insights_page):
            toolbar = page.get_child()
            if not isinstance(toolbar, Adw.ToolbarView):
                continue
            banner = Adw.Banner(
                title=_("The installed Vibemapp is also running. Both will switch "
                        "keyboard layers."))
            banner.connect("button-clicked", lambda _b: self._quit_installed_app())
            toolbar.add_top_bar(banner)
            self._instance_banners.append(banner)
        watcher = instance_check.InstalledAppWatcher()
        self._handlers += [
            (watcher, watcher.connect("notify::running", lambda *_a: self._sync_instance_banner())),
            (watcher, watcher.connect("notify::can-quit",
                                      lambda *_a: self._sync_instance_banner())),
        ]
        self._instance_watcher = watcher
        watcher.start()

    def _sync_instance_banner(self) -> None:
        watcher = self._instance_watcher
        running = watcher is not None and watcher.props.running
        label = _("Quit Installed App") if running and watcher.props.can_quit else None
        for banner in self._instance_banners:
            banner.set_button_label(label)
            banner.set_revealed(running)

    def _quit_installed_app(self) -> None:
        def done(error: GLib.Error | None) -> None:
            if error is not None:
                self.add_toast(Adw.Toast(title=_("Couldn't quit the installed Vibemapp"),
                                         timeout=6))

        if self._instance_watcher is not None:
            self._instance_watcher.request_quit(done)

    # --- actions ---------------------------------------------------------

    def _setup_actions(self) -> None:
        page_action = Gio.SimpleAction.new_stateful(
            "page", GLib.VariantType.new("s"), GLib.Variant("s", "layout"))
        page_action.connect("activate", lambda _a, p: self.show_page(p.get_string()))
        self.add_action(page_action)
        self._page_action = page_action

        self._actions: dict[str, Gio.SimpleAction] = {}
        for name, callback in (
                ("previous-layer", lambda: self._step_layer(-1)),
                ("next-layer", lambda: self._step_layer(1)),
                ("request-layer", self._request_layer),
                ("add-rule", self._add_rule),
                ("device-info", self._show_device_info),
                ("reload-layout", self.controller.reload_layout),
                ("connect", lambda: self.controller.connect_device()),
                ("disconnect", self.controller.disconnect_device),
                ("toggle-heatmap-recording", self.heatmap_page.toggle_recording),
                ("reset-heatmap", self.heatmap_page.confirm_reset)):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, cb=callback: cb())
            self.add_action(action)
            self._actions[name] = action

    def _step_layer(self, delta: int) -> None:
        if self.content_stack.get_visible_child_name() != "layout":
            self.show_page("layout")
        self.layout_page.step_layer(delta)

    def _request_layer(self) -> None:
        self.service.request_layer(self.layout_page.view_layer)

    def _add_rule(self) -> None:
        self.show_page("smart-layers")
        self.smart_layers_page.add_rule()

    def _show_device_info(self) -> None:
        DeviceDialog(self.controller).present(self)

    def _sync_actions(self) -> None:
        state = self.controller.state
        ready = state == "ready"
        connected = self.controller.device is not None
        page = self.layout_page
        self._actions["previous-layer"].set_enabled(ready)
        self._actions["next-layer"].set_enabled(ready)
        self._actions["request-layer"].set_enabled(
            ready and page.view_layer != page.active_layer
            and bool(self.controller.device and self.controller.device.oryx_available))
        self._actions["add-rule"].set_enabled(ready)
        self._actions["reload-layout"].set_enabled(
            connected and bool(self.controller.device and self.controller.device.oryx_available))
        self._actions["connect"].set_enabled(state in ("available", "failed"))
        self._actions["disconnect"].set_enabled(connected)
        self._actions["device-info"].set_enabled(True)

    # --- navigation ------------------------------------------------------

    def show_page(self, name: str, reveal: bool = True) -> None:
        if name not in PAGES:
            return
        self.content_stack.set_visible_child_name(name)
        self.content_page.set_title(page_title(name))
        self.sidebar.set_selected(PAGES.index(name))
        self._page_action.set_state(GLib.Variant("s", name))
        if reveal:
            self.split_view.set_show_content(True)

    @Gtk.Template.Callback()
    def on_breakpoint_apply(self, _breakpoint: Adw.Breakpoint) -> None:
        self.smart_layers_page.set_compact(True)

    @Gtk.Template.Callback()
    def on_breakpoint_unapply(self, _breakpoint: Adw.Breakpoint) -> None:
        self.smart_layers_page.set_compact(False)

    @Gtk.Template.Callback()
    def on_sidebar_activated(self, _sidebar: Adw.Sidebar, index: int) -> None:
        if 0 <= index < len(PAGES):
            self.show_page(PAGES[index])

    # --- state -----------------------------------------------------------

    def _sync(self) -> None:
        self._sync_actions()
        self._sync_device_status()

    def _sync_device_status(self) -> None:
        state = self.controller.state
        device = self.controller.device
        layout = self.controller.layout
        title = device.name if device else _("No Keyboard")
        subtitle = {
            "empty": _("Not connected"),
            "available": _("Ready to connect"),
            "connecting": _("Connecting…"),
            "failed": _("Can't open keyboard"),
            "loading": _("Loading layout…"),
            "no-layout": _("Connected · no layout"),
        }.get(state, "")
        if state == "ready" and layout is not None:
            index = self.service.get_active_layer()
            layer = layout.layers[index] if 0 <= index < len(layout.layers) else None
            subtitle = layer_label(layer, index)
        if state in ("available", "failed"):
            available = self.controller.available_device()
            if available is not None:
                title = available.name
        self.device_status_title.set_label(title)
        self.device_status_subtitle.set_label(subtitle)
        self.device_status_icon.set_from_icon_name(
            "dialog-warning-symbolic" if state == "failed" else "input-keyboard-symbolic")
        self.device_status_icon.set_opacity(1.0 if device is not None else 0.55)

    def _on_error(self, _controller: DeviceController, message: str) -> None:
        self.add_toast(Adw.Toast(title=message, timeout=6))

    def add_toast(self, toast: Adw.Toast) -> None:
        self.toast_overlay.add_toast(toast)

    # --- closing ---------------------------------------------------------

    def do_close_request(self) -> bool:
        width, height = self.get_default_size()
        if not self.is_maximized():
            self.settings.set_int("window-width", width)
            self.settings.set_int("window-height", height)
        self.settings.set_boolean("window-maximized", self.is_maximized())
        # The window is destroyed even when running in the background; the
        # application holds itself and builds a fresh window on activation.
        self.teardown()
        return Adw.ApplicationWindow.do_close_request(self)

    def teardown(self) -> None:
        if not self._handlers:
            return
        for obj, handler in self._handlers:
            obj.disconnect(handler)
        self._handlers = []
        if self._instance_watcher is not None:
            self._instance_watcher.stop()
            self._instance_watcher = None
        self.layout_page.teardown()
        self.smart_layers_page.teardown()
        self.heatmap_page.teardown()
        self.overlay_page.teardown()
        self.insights_page.teardown()
