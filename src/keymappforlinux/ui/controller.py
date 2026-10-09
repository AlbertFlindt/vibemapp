"""DeviceController — turns KeyboardService signals into one UI state.

Lives on the application (not the window) so auto-connect and layout loading
keep working while the app runs in the background without a window.

``state`` is one of:

``empty``       nothing plugged in
``available``   a keyboard is plugged in but not connected (auto-connect off)
``connecting``  connect_device() in progress
``failed``      opening the keyboard failed (``message`` has the reason)
``loading``     connected, layout being fetched
``no-layout``   connected, but no layout (no Oryx, offline, …)
``ready``       connected with a layout
"""

from __future__ import annotations

from gi.repository import Gio, GLib, GObject

from ..models import Device, Layout
from ..service import KeyboardService
from .i18n import _

CONNECT_TIMEOUT_S = 15


class DeviceController(GObject.Object):
    __gtype_name__ = "KmlDeviceController"

    __gsignals__ = {
        # A user-presentable error that is not already shown by a state page.
        "error": (GObject.SignalFlags.RUN_LAST, None, (str,)),
    }

    state = GObject.Property(type=str, default="empty")
    message = GObject.Property(type=str, default="")

    def __init__(self, service: KeyboardService, settings: Gio.Settings) -> None:
        super().__init__()
        self.service = service
        self.settings = settings
        self._attempted: set[str] = set()
        self._connecting_id: str | None = None
        self._connect_timeout = 0
        self._loading = False
        self._layout_error: str | None = None
        self._connect_error: str | None = None
        self._cancellable: Gio.Cancellable | None = None

        service.connect("devices-changed", self._on_devices_changed)
        service.connect("connected", self._on_connected)
        service.connect("disconnected", self._on_disconnected)
        service.connect("layout-loaded", self._on_layout_loaded)
        service.connect("error", self._on_error)
        service.connect("notify::auto-connect", lambda *_a: self._update())

        GLib.idle_add(self._initial_sync)

    # --- public ----------------------------------------------------------

    @property
    def device(self) -> Device | None:
        return self.service.get_connected_device()

    @property
    def layout(self) -> Layout | None:
        return self.service.get_layout()

    def available_device(self) -> Device | None:
        devices = sorted(self.service.list_devices(),
                         key=lambda d: (not d.oryx_available, not d.hidraw_path))
        return devices[0] if devices else None

    def connect_device(self, device_id: str | None = None) -> None:
        if device_id is None:
            device = self.available_device()
            if device is None:
                return
            device_id = device.id
        self._attempted.add(device_id)
        self._connecting_id = device_id
        self._connect_error = None
        self._clear_timeout()
        self._connect_timeout = GLib.timeout_add_seconds(
            CONNECT_TIMEOUT_S, self._on_connect_timeout)
        self._update()
        self.service.connect_device(device_id)

    def disconnect_device(self) -> None:
        if self.device is not None:
            # Don't immediately auto-connect again after an explicit disconnect.
            self._attempted.add(self.device.id)
            self.service.disconnect_device()

    def reload_layout(self) -> None:
        device = self.device
        if device is None:
            return
        if self._cancellable is not None:
            self._cancellable.cancel()
        self._cancellable = Gio.Cancellable()
        self._loading = True
        self._layout_error = None
        self._update()
        cancellable = self._cancellable

        def done(layout: Layout | None, error: str | None) -> None:
            if cancellable.is_cancelled():
                return
            self._loading = False
            if layout is None:
                self._layout_error = error or _("The layout could not be loaded")
            self._update()

        self.service.load_layout_async(device, cancellable, done)

    # --- internals -------------------------------------------------------

    def _initial_sync(self) -> bool:
        if self.device is not None and self.layout is None:
            self.reload_layout()
        self._update()
        return GLib.SOURCE_REMOVE

    def _clear_timeout(self) -> None:
        if self._connect_timeout:
            GLib.source_remove(self._connect_timeout)
            self._connect_timeout = 0

    def _on_connect_timeout(self) -> bool:
        self._connect_timeout = 0
        if self.device is None and self.state == "connecting":
            self._connecting_id = None
            self._connect_error = _("The keyboard did not respond")
            self._update()
        return GLib.SOURCE_REMOVE

    def _auto_connecting(self) -> bool:
        """The service connects by itself (``auto-connect``); show that as
        "connecting" until it connects or reports an error."""
        if not self.service.props.auto_connect or self._connect_error:
            return False
        return any(d.id not in self._attempted and not d.access_denied
                   for d in self.service.list_devices())

    def _on_devices_changed(self, _service: KeyboardService) -> None:
        present = {d.id for d in self.service.list_devices()}
        # Forget devices that went away, so a replug is retried.
        self._attempted &= present
        if self._connecting_id is not None and self._connecting_id not in present:
            self._connecting_id = None
            self._clear_timeout()
        if not present:
            self._connect_error = None
        self._update()

    def _on_connected(self, _service: KeyboardService, device: Device) -> None:
        self._connecting_id = None
        self._connect_error = None
        self._clear_timeout()
        layout = self.layout
        if layout is None or (device.layout_id and layout.id != device.layout_id):
            self.reload_layout()
        self._update()

    def _on_disconnected(self, _service: KeyboardService) -> None:
        if self._cancellable is not None:
            self._cancellable.cancel()
            self._cancellable = None
        self._loading = False
        self._layout_error = None
        self._update()

    def _on_layout_loaded(self, _service: KeyboardService, _layout: Layout) -> None:
        self._loading = False
        self._layout_error = None
        self._update()

    def _on_error(self, _service: KeyboardService, message: str) -> None:
        if self._connecting_id is not None or self.state == "connecting":
            # Shown on the "failed" status page instead of a toast.
            self._connecting_id = None
            self._clear_timeout()
            self._connect_error = message
            self._update()
            return
        self.emit("error", message)

    def _compute_state(self) -> tuple[str, str]:
        device = self.device
        if device is None:
            if self._connecting_id is not None or self._auto_connecting():
                return "connecting", ""
            devices = self.service.list_devices()
            if not devices:
                return "empty", ""
            if all(d.access_denied for d in devices):
                return "failed", _("{name} was found, but this user isn't allowed to open "
                                   "it. Install the udev rules, then replug the "
                                   "keyboard.").format(name=devices[0].name)
            if self._connect_error:
                return "failed", self._connect_error
            return "available", ""
        if self.layout is not None:
            return "ready", ""
        if self._loading:
            return "loading", ""
        if not device.oryx_available:
            return "no-layout", _("This keyboard's firmware doesn't support Oryx, "
                                  "so its layout and live layer can't be shown.")
        if self._layout_error:
            return "no-layout", self._layout_error
        return "loading", ""

    def _update(self) -> None:
        state, message = self._compute_state()
        if state == "connecting" and not self._connect_timeout:
            self._connect_timeout = GLib.timeout_add_seconds(
                CONNECT_TIMEOUT_S, self._on_connect_timeout)
        elif state != "connecting":
            self._clear_timeout()
        if self.message != message:
            self.message = message
        if self.state != state:
            self.state = state
