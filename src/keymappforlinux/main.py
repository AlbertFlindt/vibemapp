"""Application entry point: KeyMapp for Linux."""

from __future__ import annotations

import logging
import os
import signal
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from .service import KeyboardService  # noqa: E402
from .ui.i18n import _  # noqa: E402
from .ui.resources import APP_ID, load_css, ui_file, version  # noqa: E402

log = logging.getLogger("keymappforlinux")


def create_service() -> tuple[KeyboardService, bool]:
    """Return ``(service, is_fallback_mock)``."""
    if os.environ.get("KEYMAPP_MOCK") == "1":
        from .mock_service import MockKeyboardService
        return MockKeyboardService(), False
    try:
        from .backend.zsa_service import ZsaKeyboardService
    except ImportError as exc:
        log.warning("Keyboard backend unavailable (%s); using a simulated keyboard", exc)
        from .mock_service import MockKeyboardService
        return MockKeyboardService(), True
    return ZsaKeyboardService(), False


class Application(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.service: KeyboardService | None = None
        self.controller = None
        self.settings = None
        self.window = None
        self._held = False
        self._background_requested = False
        self._using_fallback_mock = False
        self._notified_background = False
        self._quitting = False

        self.add_main_option("background", ord("b"), GLib.OptionFlags.NONE,
                             GLib.OptionArg.NONE,
                             _("Start without a window and keep running in the background"),
                             None)
        self.connect("window-removed", self._on_window_removed)
        self.add_main_option("version", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             _("Show the version and exit"), None)

    # --- lifecycle -------------------------------------------------------

    def do_handle_local_options(self, options: GLib.VariantDict) -> int:
        if options.contains("version"):
            print(f"keymappforlinux {version()}")
            return 0
        return -1

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        GLib.set_application_name(_("KeyMapp for Linux"))
        Gtk.Window.set_default_icon_name(APP_ID)
        load_css()

        from .ui.controller import DeviceController
        from .ui.settings import create_settings

        self.settings = create_settings()
        self.service, self._using_fallback_mock = create_service()
        self.settings.bind("auto-connect", self.service, "auto-connect",
                           Gio.SettingsBindFlags.GET)
        self.service.props.smart_layers_enabled = self.settings.get_boolean(
            "smart-layers-on-startup")
        self.controller = DeviceController(self.service, self.settings)
        self.controller.connect("error", self._on_error)
        self.settings.connect("changed::run-in-background",
                              lambda *_a: self._sync_hold())
        self._setup_actions()
        self._sync_hold()

    def do_command_line(self, command_line: Gio.ApplicationCommandLine) -> int:
        options = command_line.get_options_dict()
        if options.contains("background"):
            # Only meaningful for the first instance; a running app ignores it.
            if self.window is None:
                self._background_requested = True
                self._sync_hold()
            return 0
        self.activate()
        return 0

    def do_activate(self) -> None:
        from .ui.window import Window

        if self.window is None:
            assert self.service is not None and self.settings is not None
            self.window = Window(self, self.service, self.controller, self.settings)
            if self._using_fallback_mock:
                self.window.add_toast(Adw.Toast(
                    title=_("Keyboard backend unavailable — showing a simulated keyboard"),
                    timeout=0))
        self.window.present()

    def do_shutdown(self) -> None:
        if self.window is not None:
            self.window.teardown()
        if self.service is not None:
            try:
                self.service.shutdown()
            except Exception:  # noqa: BLE001 — never block shutdown
                log.exception("Service shutdown failed")
        Adw.Application.do_shutdown(self)

    # --- background ------------------------------------------------------

    def _wants_hold(self) -> bool:
        assert self.settings is not None
        return self._background_requested or self.settings.get_boolean("run-in-background")

    def _sync_hold(self) -> None:
        want = self._wants_hold()
        if want and not self._held:
            self.hold()
            self._held = True
        elif not want and self._held:
            self._held = False
            self.release()

    def _on_window_removed(self, _app: Adw.Application, window: Gtk.Window) -> None:
        # GTK 4 only emits "destroy" on dispose, which our reference would
        # prevent, so track removal from the application instead.
        if window is not self.window:
            return
        self.window = None
        if not self._held or self._quitting or self._notified_background:
            return
        self._notified_background = True
        notification = Gio.Notification.new(_("KeyMapp for Linux Is Running"))
        notification.set_body(_("Smart Layers keeps working in the background"))
        self.send_notification("background", notification)

    def _on_error(self, _controller: object, message: str) -> None:
        if self.window is None or not self.window.get_visible():
            log.warning("%s", message)

    # --- actions ---------------------------------------------------------

    def _setup_actions(self) -> None:
        for name, callback, accels in (
                ("preferences", self._show_preferences, ["<Control>comma"]),
                ("shortcuts", self._show_shortcuts, ["<Control>question"]),
                ("about", self._show_about, []),
                ("quit", self._quit, ["<Control>q"])):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, cb=callback: cb())
            self.add_action(action)
            if accels:
                self.set_accels_for_action(f"app.{name}", accels)
        for action, accels in (
                ("window.close", ["<Control>w"]),
                ("win.page::layout", ["<Control>1"]),
                ("win.page::smart-layers", ["<Control>2"]),
                ("win.previous-layer", ["<Control>Page_Up"]),
                ("win.next-layer", ["<Control>Page_Down"]),
                ("win.request-layer", ["<Control>Return"]),
                ("win.add-rule", ["<Control>n"]),
                ("win.device-info", ["<Control>i"])):
            self.set_accels_for_action(action, accels)

    def _quit(self) -> None:
        self._quitting = True
        if self.window is not None:
            self.window.close()
        self._background_requested = False
        if self._held:
            self._held = False
            self.release()
        self.quit()

    def _show_preferences(self) -> None:
        from .ui.preferences import PreferencesDialog

        self.activate()
        PreferencesDialog(self.settings).present(self.window)

    def _show_shortcuts(self) -> None:
        self.activate()
        builder = Gtk.Builder.new_from_file(ui_file("shortcuts_dialog.ui"))
        builder.get_object("shortcuts_dialog").present(self.window)

    def _show_about(self) -> None:
        self.activate()
        about = Adw.AboutDialog(
            application_name=_("KeyMapp for Linux"),
            application_icon=APP_ID,
            developer_name="Albert Flindt",
            version=version(),
            website="https://github.com/albertflindt/keyMappForLinux",
            issue_url="https://github.com/albertflindt/keyMappForLinux/issues",
            license_type=Gtk.License.GPL_3_0,
            copyright="© 2026 Albert Flindt",
            comments=_("A companion app for ZSA keyboards. Smart Layers switches your "
                       "keyboard's layer automatically when you focus an app.\n\n"
                       "Not affiliated with ZSA Technology Labs."),
            developers=["Albert Flindt"],
            translator_credits=_("translator-credits"),
        )
        about.add_link(_("Oryx Configurator"), "https://configure.zsa.io")
        about.present(self.window)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO if os.environ.get("KEYMAPP_DEBUG") else
                        logging.WARNING, format="%(name)s: %(levelname)s: %(message)s")
    app = Application()
    # Quit cleanly on SIGINT/SIGTERM (e.g. session logout) so do_shutdown runs
    # and the service can restore the keyboard's base layer.
    for signum in (signal.SIGINT, signal.SIGTERM):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signum, _quit_on_signal, app)
    return app.run(argv if argv is not None else sys.argv)


def _quit_on_signal(app: Application) -> bool:
    app.quit()
    return GLib.SOURCE_REMOVE
