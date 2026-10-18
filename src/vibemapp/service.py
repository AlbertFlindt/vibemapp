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

from .models import (AppRule, CoachHint, Device, ErgoReport, LayoutComparison, FocusedWindow, HeatmapApp, HeatmapPeriod, Layout,
                     RunningApp, SteamGame)


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
        # The compositor's focused monitor changed (connector name, e.g.
        # "DP-1"). Additive (1.1): the overlay follows it. Never emitted
        # without a focus source.
        "focused-monitor-changed": (GObject.SignalFlags.RUN_LAST, None, (str,)),
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
    # a calendar day. Since 1.2 each count is also kept per focused app (window
    # class, or ``steam:<app id>`` for Steam games; never window titles).
    # Recording continues while the window is closed.
    heatmap_recording = GObject.Property(type=bool, default=False,
                                         nick="heatmap-recording")
    # Key-pair recording is a separate opt-in (additive, 1.2) and only records
    # while heatmap_recording is also on. Each pair is "key B pressed within
    # about a second after key A", by (layer, key index), counted per day and
    # app. Never sequences longer than two keys, never characters.
    pair_recording = GObject.Property(type=bool, default=False,
                                      nick="pair-recording")

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

    def get_pressed_keys(self) -> set[int]:
        """Key indices held down right now on the connected keyboard (from
        ``key-event``), so a view that appears mid-hold is correct. Empty when
        disconnected. Additive (1.1): the default knows of no pressed keys."""
        return set()

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

    def get_focused_window(self) -> FocusedWindow | None:
        """Last known focused window with Steam details, or None. Up to date
        whenever ``focus-changed`` is emitted."""
        raise NotImplementedError

    def get_focused_monitor(self) -> str | None:
        """Connector name of the compositor's focused monitor ("DP-1"), or
        None when unknown. Additive (1.1); see ``focused-monitor-changed``."""
        return None

    def list_steam_games(self) -> list[SteamGame]:
        """Installed Steam games (all Steam libraries), sorted by name, without
        Proton/runtime/redistributable tools. [] when Steam isn't installed."""
        raise NotImplementedError

    # --- heatmap -------------------------------------------------------

    def get_key_counts(self, period: HeatmapPeriod,
                       layer: int | None = None,
                       app: str | None = None) -> dict[int, int]:
        """Press counts per key index for the current layout.

        ``layer`` None sums all layers; otherwise only presses made while that
        layer was the active layer. ``app`` None means all apps (including
        presses recorded before 1.2 or with no focused app); otherwise a
        ``HeatmapApp.key`` (additive, 1.2). Keys with no presses may be
        omitted. Returns {} when no layout is loaded."""
        raise NotImplementedError

    def list_heatmap_apps(self, period: HeatmapPeriod) -> list[HeatmapApp]:
        """Apps with presses in ``period`` for the current layout, most presses
        first. Additive (1.2): the default knows of no apps."""
        return []

    def get_ergo_report(self, period: HeatmapPeriod,
                        app: str | None = None) -> ErgoReport | None:
        """Ergo score of the current layout's recorded presses and pairs, or
        None when no layout is loaded or the keyboard model has no finger map.
        Additive (1.2): the default has no report."""
        return None

    def get_coach_hints(self, period: HeatmapPeriod,
                        app: str | None = None) -> list[CoachHint]:
        """Layout-coach hints for the current layout's recorded presses and
        pairs, most useful first; [] below ``COACH_MIN_PRESSES`` presses, without
        a layout or without a finger map. Pure suggestions: never writes to the
        keyboard. Additive (1.2): the default has no hints."""
        return []

    def get_layout_comparisons(self, period: HeatmapPeriod,
                               app: str | None = None) -> list[LayoutComparison]:
        """The recorded presses and pairs scored on well-known letter layouts
        (QWERTY, Colemak, Colemak-DH, Workman, Dvorak), best score first; [] below
        ``COACH_MIN_PRESSES`` presses, without a layout or finger map, or when the
        base layer has no recognisable letter block. Pure suggestions: never
        writes to the keyboard. Additive (1.2): the default has none."""
        return []

    def reset_key_counts(self) -> None:
        """Delete all recorded counts for the current layout: per-app counts and key pairs included."""
        raise NotImplementedError

    def shutdown(self) -> None:
        """Release devices, sockets and threads. Called on app shutdown."""
        raise NotImplementedError
