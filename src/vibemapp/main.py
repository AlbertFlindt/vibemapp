"""Application entry point: Vibemapp."""

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

log = logging.getLogger("vibemapp")


def create_service() -> tuple[KeyboardService, bool]:
    """Return ``(service, is_fallback_mock)``."""
    if os.environ.get("VIBEMAPP_MOCK") == "1":
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
        self.tray = None

        self.add_main_option("background", ord("b"), GLib.OptionFlags.NONE,
                             GLib.OptionArg.NONE,
                             _("Start in the tray (or in the background) without a window"),
                             None)
        self.connect("window-removed", self._on_window_removed)
        self.add_main_option("version", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             _("Show the version and exit"), None)

    # --- lifecycle -------------------------------------------------------

    def do_handle_local_options(self, options: GLib.VariantDict) -> int:
        if options.contains("version"):
            print(f"vibemapp {version()}")
            return 0
        return -1

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        GLib.set_application_name(_("Vibemapp"))
        Gtk.Window.set_default_icon_name(APP_ID)
        load_css()

        from .ui.controller import DeviceController
        from .ui.settings import create_settings

        self.settings = create_settings()
        self.service, self._using_fallback_mock = create_service()
        self.settings.bind("auto-connect", self.service, "auto-connect",
                           Gio.SettingsBindFlags.GET)
        # Recording is toggled from the Heatmap page through the service
        # property; persist it both ways.
        self.settings.bind("heatmap-recording", self.service, "heatmap-recording",
                           Gio.SettingsBindFlags.DEFAULT)
        self.service.props.smart_layers_enabled = self.settings.get_boolean(
            "smart-layers-on-startup")
        self.controller = DeviceController(self.service, self.settings)
        self.controller.connect("error", self._on_error)
        self.settings.connect("changed::run-in-background",
                              lambda *_a: self._sync_hold())
        self._setup_actions()
        self._setup_tray()
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
        if self.tray is not None:
            self.tray.shutdown()
        if self.service is not None:
            try:
                self.service.shutdown()
            except Exception:  # noqa: BLE001 — never block shutdown
                log.exception("Service shutdown failed")
        Adw.Application.do_shutdown(self)

    # --- background ------------------------------------------------------

    def _setup_tray(self) -> None:
        from .ui.tray_controller import TrayController

        if not TrayController.supported():
            return
        self.tray = TrayController(
            self.service, self.controller, self.settings,
            on_toggle_window=self._toggle_window, on_show_window=self.activate,
            on_quit=self._quit)
        self.tray.connect("notify::active", self._on_tray_active_changed)

    @property
    def tray_active(self) -> bool:
        return self.tray is not None and self.tray.active

    def _on_tray_active_changed(self, *_args: object) -> None:
        if self._quitting:
            return
        if not self.tray_active and self.window is None and not (
                self._background_requested or self.settings.get_boolean("run-in-background")):
            # The tray host went away (or the tray was turned off) while we
            # were only living in the tray: show the window rather than
            # silently quitting or becoming unreachable.
            self.activate()
        self._sync_hold()

    def _toggle_window(self) -> None:
        if self.window is not None and self.window.is_active():
            self.window.close()
        else:
            self.activate()

    def _wants_hold(self) -> bool:
        assert self.settings is not None
        return (self._background_requested or self.tray_active
                or self.settings.get_boolean("run-in-background"))

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
        # With a tray icon the app is visibly still running; only notify when
        # it lives on invisibly.
        if (not self._held or self._quitting or self._notified_background
                or self.tray_active):
            return
        self._notified_background = True
        notification = Gio.Notification.new(_("Vibemapp Is Running"))
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
                ("win.page::heatmap", ["<Control>3"]),
                ("win.previous-layer", ["<Control>Page_Up"]),
                ("win.next-layer", ["<Control>Page_Down"]),
                ("win.request-layer", ["<Control>Return"]),
                ("win.add-rule", ["<Control>n"]),
                ("win.device-info", ["<Control>i"])):
            self.set_accels_for_action(action, accels)

    def _quit(self) -> None:
        self._quitting = True
        if self.tray is not None:
            self.tray.shutdown()
            self.tray = None
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
        PreferencesDialog(self.settings, tray=self.tray).present(self.window)

    def _show_shortcuts(self) -> None:
        self.activate()
        builder = Gtk.Builder.new_from_file(ui_file("shortcuts_dialog.ui"))
        builder.get_object("shortcuts_dialog").present(self.window)

    def _show_about(self) -> None:
        self.activate()
        about = Adw.AboutDialog(
            application_name=_("Vibemapp"),
            application_icon=APP_ID,
            developer_name="Albert Flindt",
            version=version(),
            website="https://github.com/albertflindt/vibemapp",
            issue_url="https://github.com/albertflindt/vibemapp/issues",
            license_type=Gtk.License.GPL_3_0,
            copyright="© 2026 Albert Flindt",
            comments=_("Vibemapp is a companion for ZSA keyboards. Smart Layers switches "
                       "your keyboard's layer when you focus an app, the layout view "
                       "follows your keyboard live, and an optional heatmap shows which "
                       "keys you use most.\n\nNot affiliated with ZSA Technology Labs."),
            developers=["Albert Flindt"],
            translator_credits=_("translator-credits"),
        )
        about.add_link(_("Oryx Configurator"), "https://configure.zsa.io")
        about.present(self.window)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO if os.environ.get("VIBEMAPP_DEBUG") else
                        logging.WARNING, format="%(name)s: %(levelname)s: %(message)s")
    # Must run before anything (e.g. the dev schema compile) writes into
    # ~/.cache/vibemapp or ~/.config/vibemapp (SPEC 6.1).
    try:
        from .backend.migration import migrate_legacy_dirs
    except ImportError:
        pass
    else:
        try:
            migrate_legacy_dirs()
        except Exception:  # noqa: BLE001 — never block startup on migration
            log.exception("Migrating old KeyMapp for Linux directories failed")
    app = Application()
    # Quit cleanly on SIGINT/SIGTERM (e.g. session logout) so do_shutdown runs
    # and the service can restore the keyboard's base layer.
    for signum in (signal.SIGINT, signal.SIGTERM):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signum, _quit_on_signal, app)
    return app.run(argv if argv is not None else sys.argv)


def _quit_on_signal(app: Application) -> bool:
    app.quit()
    return GLib.SOURCE_REMOVE
