"""KeyboardService — the backend/frontend contract.

Owned by the PM. The UI talks *only* to an instance of a ``KeyboardService``
subclass (``backend.zsa_service.ZsaKeyboardService`` in production,
``mock_service.MockKeyboardService`` for UI development with VIBEMAPP_MOCK=1).

Threading rule: every signal is emitted on the GLib main loop (default main
context). Implementations doing blocking I/O on worker threads must marshal
back with ``GLib.idle_add`` before emitting. Methods are called from the main
thread and must not block for more than a few milliseconds.

Signal payloads that are Python objects (Device, Layout) are passed as
``GObject.TYPE_PYOBJECT``.
"""

from __future__ import annotations

from typing import Callable

from gi.repository import Gio, GObject

from .models import AppRule, Device, HeatmapPeriod, Layout, RunningApp


class KeyboardService(GObject.Object):
    __gtype_name__ = "KmlKeyboardService"

    __gsignals__ = {
        # The set returned by list_devices() changed (hotplug).
        "devices-changed": (GObject.SignalFlags.RUN_LAST, None, ()),
        # A device was opened and the Oryx handshake finished (or failed:
        # check Device.oryx_available).
        "connected": (GObject.SignalFlags.RUN_LAST, None, (object,)),
        # The connected device went away or disconnect_device() was called.
        "disconnected": (GObject.SignalFlags.RUN_LAST, None, ()),
        # Layout for the connected device became available (fetched or cached).
        "layout-loaded": (GObject.SignalFlags.RUN_LAST, None, (object,)),
        # The keyboard reports its highest active layer changed.
        "layer-changed": (GObject.SignalFlags.RUN_LAST, None, (int,)),
        # Physical key press/release, index in Oryx key-index order.
        "key-event": (GObject.SignalFlags.RUN_LAST, None, (int, bool)),
        # Focused window changed (app_class, title). Emitted even when Smart
        # Layers is disabled, so the UI can show what is focused.
        "focus-changed": (GObject.SignalFlags.RUN_LAST, None, (str, str)),
        # Smart Layers switched the keyboard because of a focus change.
        # app_class is "" when the fallback layer was applied.
        "rule-applied": (GObject.SignalFlags.RUN_LAST, None, (str, int)),
        # Rules or fallback layer changed (by set_rules/set_fallback_layer or
        # because a different layout's rule set got loaded).
        "rules-changed": (GObject.SignalFlags.RUN_LAST, None, ()),
        # The focus source (Hyprland) appeared or went away.
        "focus-source-changed": (GObject.SignalFlags.RUN_LAST, None, (bool,)),
        # Heatmap counts for the current layout changed (throttled to at most
        # about once per second; also emitted after reset and layout change).
        "key-counts-changed": (GObject.SignalFlags.RUN_LAST, None, ()),
        # Human-readable, user-presentable error (shown as a toast).
        "error": (GObject.SignalFlags.RUN_LAST, None, (str,)),
    }

    smart_layers_enabled = GObject.Property(type=bool, default=False,
                                            nick="smart-layers-enabled")
    # When True the service connects to the first available device by itself
    # (on startup and hotplug). The UI binds this to the "auto-connect" setting.
    auto_connect = GObject.Property(type=bool, default=True, nick="auto-connect")
    # Heatmap recording is opt-in. When True, every key *press* on the connected
    # keyboard increments a per-(day, layer, key index) counter for the current
    # layout. Only counts are stored: never key order or timestamps finer than
    # a calendar day. Recording continues while the window is closed.
    heatmap_recording = GObject.Property(type=bool, default=False,
                                         nick="heatmap-recording")

    # --- devices -------------------------------------------------------

    def list_devices(self) -> list[Device]:
        raise NotImplementedError

    def get_connected_device(self) -> Device | None:
        raise NotImplementedError

    def connect_device(self, device_id: str) -> None:
        """Open the device asynchronously; result arrives via ``connected``/``error``."""
        raise NotImplementedError

    def disconnect_device(self) -> None:
        raise NotImplementedError

    # --- layout --------------------------------------------------------

    def load_layout_async(self, device: Device,
                          cancellable: Gio.Cancellable | None,
                          callback: Callable[[Layout | None, str | None], None]) -> None:
        """Fetch (or load cached) layout. ``callback(layout, error_message)``
        runs on the main loop. Also emits ``layout-loaded`` on success."""
        raise NotImplementedError

    def get_layout(self) -> Layout | None:
        raise NotImplementedError

    def get_active_layer(self) -> int:
        raise NotImplementedError

    def request_layer(self, index: int) -> None:
        """Ask the keyboard to switch to ``index`` (Oryx set-layer, RAM only)."""
        raise NotImplementedError

    # --- smart layers --------------------------------------------------

    def get_rules(self) -> list[AppRule]:
        """Rules for the currently connected layout (empty if none)."""
        raise NotImplementedError

    def set_rules(self, rules: list[AppRule]) -> None:
        """Replace and persist the rules for the current layout."""
        raise NotImplementedError

    def get_fallback_layer(self) -> int | None:
        """Layer applied when no rule matches; ``None`` = leave layer as is."""
        raise NotImplementedError

    def set_fallback_layer(self, index: int | None) -> None:
        raise NotImplementedError

    def list_running_apps(self) -> list[RunningApp]:
        raise NotImplementedError

    def focus_source_available(self) -> bool:
        """True when a supported focus source (Hyprland in v1) is reachable."""
        raise NotImplementedError

    def get_focused_app(self) -> tuple[str, str] | None:
        """Last known (app_class, title), or None."""
        raise NotImplementedError

    # --- heatmap -------------------------------------------------------

    def get_key_counts(self, period: HeatmapPeriod,
                       layer: int | None = None) -> dict[int, int]:
        """Press counts per key index for the current layout.

        ``layer`` None sums all layers; otherwise only presses made while that
        layer was the active layer. Keys with no presses may be omitted.
        Returns {} when no layout is loaded."""
        raise NotImplementedError

    def reset_key_counts(self) -> None:
        """Delete all recorded counts for the current layout."""
        raise NotImplementedError

    def shutdown(self) -> None:
        """Release devices, sockets and threads. Called on app shutdown."""
        raise NotImplementedError
