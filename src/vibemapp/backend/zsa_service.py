"""Production ``KeyboardService``: hidraw + Oryx + Hyprland + Smart Layers.

Threading: device I/O runs on the ``OryxConnection`` worker thread and layout
fetches on a short-lived thread. Everything that touches service state or
emits a signal is marshalled to the GLib main loop with ``GLib.idle_add``.

Behaviour notes:
* Auto-connect: when no keyboard is connected, the first Oryx-capable device
  is opened. A device we can't open (missing udev rules) gets one ``error``
  per plug-in.
* On a deliberate disconnect or shutdown, if Smart Layers moved the keyboard
  off layer 0 and it's still on that layer, we send ``layer_move(0)`` so the
  keyboard isn't left on an app layer. The firmware keeps its layer when the
  host goes away.
* Focus events for our own window are neither forwarded as ``focus-changed``
  nor used by Smart Layers, so the UI keeps showing the app the user came from.
* Firmware keycodes TOGGLE_SMART_LAYER and TRIGGER_SMART_LAYER (Oryx events
  0x09/0x0A) toggle ``smart-layers-enabled`` and force a re-evaluation.
"""

from __future__ import annotations

import datetime as dt
import errno
import logging
import time
import os
import threading
from dataclasses import replace
from typing import Any, Callable

from gi.repository import Gio, GLib

from ..models import (AppRule, Device, FocusedWindow, HeatmapPeriod, Layout, RunningApp,
                      SteamGame)
from ..service import KeyboardService
from . import devices as devmod
from . import geometries
from ._i18n import _
from .focus import FocusWatcher, create_focus_watcher
from .heatmap import HeatmapStore
from .steam import SteamLibrary
from .migration import migrate_legacy_dirs
from .oryx import Evt, HandshakeResult, OryxConnection, OryxError, OryxEvent
from .oryx_api import LayoutCache, LayoutRepository
from ..appid import is_own_app_class
from .smart_layers import DEFAULT_LAYOUT_KEY, RulesStore, SmartLayersEngine

log = logging.getLogger(__name__)

ConnectionFactory = Callable[[str, Callable[[OryxEvent], None], Callable[[str | None], None]],
                             Any]


def _idle(fn: Callable[..., object], *args: object) -> None:
    def run() -> bool:
        fn(*args)
        return GLib.SOURCE_REMOVE
    GLib.idle_add(run)


class ZsaKeyboardService(KeyboardService):
    __gtype_name__ = "KmlZsaKeyboardService"

    # Auto-connect backoff after repeated failures (reset on hotplug).
    BACKOFF_BASE_MS = 1000
    BACKOFF_MAX_MS = 30_000
    STABLE_AFTER_MS = 10_000     # a connection this old clears the failure count
    HEATMAP_THROTTLE_MS = 1000   # key-counts-changed at most this often
    HEATMAP_FLUSH_MS = 30_000    # heatmap written to disk at most this often

    def __init__(self, *, sysfs_root: str = "/sys", dev_root: str = "/dev",
                 rules_path: str | None = None, cache_dir: str | None = None,
                 focus_watcher: FocusWatcher | None = None,
                 repository: LayoutRepository | None = None,
                 connection_factory: ConnectionFactory | None = None,
                 auto_connect: bool = True, autostart: bool = True,
                 poll_interval_ms: int = 3000,
                 heatmap_dir: str | None = None,
                 clock: Callable[[], dt.date] | None = None,
                 migrate: bool | None = None,
                 steam_roots: list[str] | None = None,
                 proc_root: str = "/proc") -> None:
        super().__init__()
        # Legacy keymappforlinux -> vibemapp dirs. By default this only runs
        # when using the real default paths (not in tests with injected dirs).
        if migrate is None:
            migrate = rules_path is None and cache_dir is None and heatmap_dir is None
        if migrate:
            migrate_legacy_dirs()
        # Constructor kwarg kept as an alias for the "auto-connect" property.
        self.props.auto_connect = auto_connect
        self._monitor = devmod.DeviceMonitor(sysfs_root, dev_root, poll_interval_ms=poll_interval_ms)
        self._monitor.connect("changed", self._on_devices_changed)
        self._watcher = focus_watcher if focus_watcher is not None else create_focus_watcher()
        self._watcher.connect("focus-changed", self._on_focus_changed)
        self._watcher.connect("availability-changed", self._on_focus_source_changed)
        self._watcher.connect("monitor-changed", self._on_monitor_changed)
        self._repo = repository or LayoutRepository(LayoutCache(cache_dir))
        self._connection_factory = connection_factory or (
            lambda path, on_event, on_closed: OryxConnection(path, on_event, on_closed))

        self._devices: list[Device] = []
        self._device: Device | None = None
        self._conn: Any = None
        self._generation = 0              # bumps on every connect/disconnect
        self._connecting: str | None = None
        self._notified_denied: set[str] = set()
        self._layout: Layout | None = None
        self._layout_waiters: dict[tuple[str, str | None], list[Callable]] = {}
        self._active_layer = 0
        self._pressed: set[int] = set()
        self._focused: tuple[str, str] | None = None
        self._shut_down = False
        self._started = False
        self._failures = 0
        self._retry_id = 0
        self._stable_id = 0
        self._heatmap = HeatmapStore(heatmap_dir, clock or dt.date.today)
        self._steam = SteamLibrary(steam_roots, proc_root)
        self._focused_window: FocusedWindow | None = None
        self._counts_timer = 0
        self._counts_last_emit = 0.0
        self._flush_id = 0

        self._engine = SmartLayersEngine(
            RulesStore(rules_path), send_layer=self._engine_send_layer,
            on_applied=lambda cls, layer: self.emit("rule-applied", cls, layer),
            on_rules_changed=lambda: self.emit("rules-changed"))
        self.connect("notify::smart-layers-enabled", self._on_enabled_changed)
        self.connect("notify::auto-connect", self._on_auto_connect_changed)
        self.connect("notify::heatmap-recording", self._on_heatmap_recording_changed)

        if autostart:
            self.start()

    # --- lifecycle --------------------------------------------------------------

    def start(self) -> None:
        self._monitor.start()
        self._devices = self._monitor.devices
        self._started = True
        if not self._flush_id:
            self._flush_id = GLib.timeout_add(self.HEATMAP_FLUSH_MS, self._on_flush_timer)
        self._watcher.start()
        current = self._watcher.current()
        if current and not is_own_app_class(current[0]):
            self._update_focus(*current)
        # Warm the Steam manifest cache off the focus path.
        GLib.idle_add(lambda: (self._steam.games(), GLib.SOURCE_REMOVE)[1])
        # Let the UI connect its handlers before the first auto-connect.
        GLib.idle_add(self._maybe_auto_connect)

    def shutdown(self) -> None:
        if self._shut_down:
            return
        self._shut_down = True
        self._cancel_backoff_timers()
        self._monitor.stop()
        self._watcher.stop()
        self._engine.set_enabled(False)
        self._close_connection(restore_base=True)
        self._device = None
        for attr in ("_flush_id", "_counts_timer"):
            sid = getattr(self, attr)
            if sid:
                GLib.source_remove(sid)
                setattr(self, attr, 0)
        self._flush_heatmap()

    # --- devices ---------------------------------------------------------------

    def list_devices(self) -> list[Device]:
        if self._device is None:
            return list(self._devices)
        return [self._device if d.id == self._device.id else d for d in self._devices]

    def get_connected_device(self) -> Device | None:
        return self._device

    def connect_device(self, device_id: str) -> None:
        device = next((d for d in self._devices if d.id == device_id), None)
        if device is None:
            self._emit_later("error", _("The keyboard is no longer connected"))
            return
        if self._device is not None and self._device.id == device_id:
            return
        if self._connecting == device_id:
            return
        if self._device is not None:
            self.disconnect_device()
        self._start_connect(device, user_initiated=True)

    def disconnect_device(self) -> None:
        self._generation += 1
        self._connecting = None
        had_device = self._device is not None
        self._close_connection(restore_base=True)
        self._teardown_state()
        if had_device:
            self.emit("disconnected")

    def _start_connect(self, device: Device, user_initiated: bool) -> None:
        if not device.hidraw_path:
            # No raw HID interface: firmware without Oryx support.
            self._generation += 1
            self._device = replace(device, oryx_available=False)
            self._set_heatmap_layout(device.layout_id or DEFAULT_LAYOUT_KEY)
            self._active_layer = 0
            self._engine.set_layout(device.layout_id or DEFAULT_LAYOUT_KEY)
            self.emit("connected", self._device)
            return
        if not os.access(device.hidraw_path, os.R_OK | os.W_OK):
            self._mark_access_denied(device.id)
            if user_initiated or device.id not in self._notified_denied:
                self._notified_denied.add(device.id)
                self._emit_later("error", self._permission_message(device))
            return

        self._generation += 1
        generation = self._generation
        self._connecting = device.id

        def on_event(ev: OryxEvent, gen: int = generation) -> None:
            if gen == self._generation:
                self._on_oryx_event(ev)

        def on_closed(reason: str | None, gen: int = generation) -> None:
            if gen == self._generation and reason is not None:
                self._on_connection_lost(reason)

        conn = self._connection_factory(device.hidraw_path, on_event, on_closed)

        def work() -> None:
            try:
                conn.open()
                result = conn.handshake()
            except (OryxError, OSError) as exc:
                try:
                    conn.close()
                except Exception:   # noqa: BLE001
                    pass
                _idle(self._finish_connect, generation, device, None, None, str(exc),
                      getattr(exc, "errno", None))
                return
            _idle(self._finish_connect, generation, device, conn, result, None)

        threading.Thread(target=work, name="oryx-connect", daemon=True).start()

    def _finish_connect(self, generation: int, device: Device, conn: Any,
                        result: HandshakeResult | None, error: str | None,
                        err_no: int | None = None) -> None:
        if generation != self._generation or self._shut_down:
            if conn is not None:
                conn.close()
            return
        self._connecting = None
        if error is not None or result is None:
            if err_no in (errno.EACCES, errno.EPERM):
                # Needs udev rules + replug; hotplug/poll will retry, no backoff loop.
                self._notified_denied.add(device.id)
                self._mark_access_denied(device.id)
                self.emit("error", self._permission_message(device))
            else:
                self.emit("error", _("Couldn't open the keyboard: {error}").format(error=error))
                self._register_failure()
            return
        dev = devmod.with_firmware(device, result.firmware_version)
        dev = replace(dev, oryx_available=result.paired,
                      geometry=dev.geometry or geometries.geometry_for(dev.model))
        if not result.paired:
            conn.close()
            conn = None
        self._conn = conn
        self._device = dev
        self._set_heatmap_layout(dev.layout_id or DEFAULT_LAYOUT_KEY)
        self._active_layer = result.layer if result.layer is not None else 0
        self._layout = None
        self._engine.layer_count = None
        self._engine.set_layout(dev.layout_id or DEFAULT_LAYOUT_KEY)
        self.emit("connected", dev)
        if not result.paired:
            if result.legacy_pairing:
                self.emit("error", _("This keyboard's firmware is too old for live Oryx "
                                     "features. Update it in Oryx to use Smart Layers."))
            else:
                self.emit("error", _("The keyboard didn't answer the Oryx handshake"))
            return
        self._engine.keyboard_layer_changed(self._active_layer)
        self.emit("layer-changed", self._active_layer)
        self._engine.set_connected(True)
        self._arm_stable_timer()
        if dev.layout_id:
            self._load_layout(dev, None, None)

    def _close_connection(self, restore_base: bool) -> None:
        conn, self._conn = self._conn, None
        if conn is None:
            return
        if restore_base:
            sent = self._engine.last_sent
            if sent not in (None, 0) and self._active_layer == sent:
                try:
                    conn.set_layer(0)
                except (OryxError, ValueError, OSError):
                    pass
        self._engine.set_connected(False)
        try:
            conn.close()
        except Exception:   # noqa: BLE001
            log.exception("closing connection failed")

    def _teardown_state(self) -> None:
        self._set_heatmap_layout(None)
        self._device = None
        self._layout = None
        self._active_layer = 0
        self._pressed.clear()
        self._engine.set_connected(False)
        self._engine.layer_count = None
        self._engine.set_layout(None)      # emits rules-changed (no rules without a keyboard)

    def _on_connection_lost(self, reason: str) -> None:
        log.info("Keyboard connection lost: %s", reason)
        self._generation += 1
        self._conn = None
        had = self._device is not None
        self._teardown_state()
        if had:
            self.emit("disconnected")
        self._register_failure()
        self._monitor.rescan()      # if the device is really gone this is a hotplug

    def _on_devices_changed(self, _monitor: devmod.DeviceMonitor) -> None:
        self._reset_backoff()           # hotplug (or ACL change): try again right away
        self._devices = self._monitor.devices
        present = {d.id for d in self._devices}
        # Forget the "denied" state for devices that went away or became
        # accessible (udev rules installed), so they are tried again.
        self._notified_denied &= {d.id for d in self._devices if not d.oryx_available}
        if self._device is not None and self._device.id not in present:
            self._generation += 1
            self._close_connection(restore_base=False)
            self._teardown_state()
            self.emit("devices-changed")
            self.emit("disconnected")
        else:
            self.emit("devices-changed")
        if self._started:
            self._maybe_auto_connect()

    def _maybe_auto_connect(self) -> bool:
        if (not self.props.auto_connect or self._shut_down or self._device is not None
                or self._connecting is not None or self._retry_id):
            return GLib.SOURCE_REMOVE
        candidates = [d for d in self._devices
                      if d.hidraw_path and d.id not in self._notified_denied]
        target = next((d for d in candidates if d.oryx_available), None)
        if target is not None:
            self._start_connect(target, user_initiated=False)
        elif candidates:
            self._start_connect(candidates[0], user_initiated=False)  # emits udev hint once
        return GLib.SOURCE_REMOVE

    # --- auto-connect backoff -----------------------------------------------------

    def backoff_delay_ms(self, failures: int) -> int:
        return min(self.BACKOFF_BASE_MS * 2 ** max(0, failures - 1), self.BACKOFF_MAX_MS)

    def _register_failure(self) -> None:
        if self._stable_id:
            GLib.source_remove(self._stable_id)
            self._stable_id = 0
        if not self.props.auto_connect or self._shut_down:
            return
        self._failures += 1
        if self._retry_id:
            GLib.source_remove(self._retry_id)
        delay = self.backoff_delay_ms(self._failures)
        log.info("Auto-connect retry %d in %d ms", self._failures, delay)
        self._retry_id = GLib.timeout_add(delay, self._on_retry_timer)

    def _on_retry_timer(self) -> bool:
        self._retry_id = 0
        self._maybe_auto_connect()
        return GLib.SOURCE_REMOVE

    def _arm_stable_timer(self) -> None:
        if self._stable_id:
            GLib.source_remove(self._stable_id)

        def stable() -> bool:
            self._stable_id = 0
            self._failures = 0
            return GLib.SOURCE_REMOVE
        self._stable_id = GLib.timeout_add(self.STABLE_AFTER_MS, stable)

    def _reset_backoff(self) -> None:
        self._failures = 0
        if self._retry_id:
            GLib.source_remove(self._retry_id)
            self._retry_id = 0

    def _cancel_backoff_timers(self) -> None:
        self._reset_backoff()
        if self._stable_id:
            GLib.source_remove(self._stable_id)
            self._stable_id = 0

    def _mark_access_denied(self, device_id: str) -> None:
        changed = False
        out = []
        for d in self._devices:
            if d.id == device_id and not d.access_denied:
                d = replace(d, access_denied=True, oryx_available=False)
                changed = True
            out.append(d)
        if changed:
            self._devices = out
            self._emit_later("devices-changed")

    def _on_focus_source_changed(self, _watcher: FocusWatcher, available: bool) -> None:
        # Watchers emit from GLib sources, i.e. already on the main loop.
        self.emit("focus-source-changed", bool(available))

    @staticmethod
    def _permission_message(device: Device) -> str:
        return _("Permission denied opening {path}. Install the udev rules and "
                 "replug the keyboard.").format(path=device.hidraw_path)

    def _emit_later(self, signal: str, *args: object) -> None:
        _idle(self.emit, signal, *args)

    # --- oryx events --------------------------------------------------------------

    def _on_oryx_event(self, ev: OryxEvent) -> None:
        if self._device is None:
            return
        if ev.code == Evt.LAYER and ev.layer is not None:
            self._engine.keyboard_layer_changed(ev.layer)
            if ev.layer != self._active_layer:
                self._active_layer = ev.layer
                self.emit("layer-changed", ev.layer)
        elif ev.code in (Evt.KEYDOWN, Evt.KEYUP) and ev.row is not None and ev.col is not None:
            index = geometries.matrix_to_index(self._device.model, ev.row, ev.col)
            if index is not None:
                pressed = ev.code == Evt.KEYDOWN
                if pressed:
                    self._pressed.add(index)
                else:
                    self._pressed.discard(index)
                if pressed and self.props.heatmap_recording:
                    self._heatmap.record(self._active_layer, index)
                    self._schedule_counts_changed()
                self.emit("key-event", index, pressed)
        elif ev.code == Evt.TOGGLE_SMART_LAYER:
            self.props.smart_layers_enabled = not self.props.smart_layers_enabled
        elif ev.code == Evt.TRIGGER_SMART_LAYER:
            self._engine.set_connected(True)    # resets dedupe state, re-evaluates
        elif ev.code == Evt.ERROR:
            log.debug("Oryx error event 0x%02x", ev.value or 0)

    def _engine_send_layer(self, layer: int) -> None:
        if self._conn is None:
            return
        try:
            self._conn.set_layer(layer)
        except (OryxError, ValueError, OSError) as exc:
            self.emit("error", _("Couldn't switch layer: {error}").format(error=exc))

    # --- layout -------------------------------------------------------------------

    def load_layout_async(self, device: Device, cancellable: Gio.Cancellable | None,
                          callback: Callable[[Layout | None, str | None], None]) -> None:
        if self._device is None or self._device.id != device.id:
            _idle(callback, None, _("The keyboard is not connected"))
            return
        dev = self._device
        if not dev.layout_id:
            _idle(callback, None, _("This firmware does not report an Oryx layout"))
            return
        if self._layout is not None and self._layout.id == dev.layout_id and (
                dev.revision_id is None or self._layout.revision_id == dev.revision_id):
            layout = self._layout

            def done() -> None:
                if cancellable is not None and cancellable.is_cancelled():
                    callback(None, _("Cancelled"))
                else:
                    callback(layout, None)
            _idle(done)
            return
        self._load_layout(dev, cancellable, callback)

    def _load_layout(self, dev: Device, cancellable: Gio.Cancellable | None,
                     callback: Callable[[Layout | None, str | None], None] | None) -> None:
        assert dev.layout_id
        key = (dev.layout_id, dev.revision_id)
        waiters = self._layout_waiters.get(key)
        entry = (callback, cancellable)
        if waiters is not None:
            waiters.append(entry)
            return
        self._layout_waiters[key] = [entry]
        generation = self._generation

        def finish(layout: Layout | None, error: str | None) -> None:
            entries = self._layout_waiters.pop(key, [])
            current = generation == self._generation and self._device is not None
            if layout is not None and current:
                self._layout = layout
                self._engine.layer_count = len(layout.layers) or None
                self.emit("layout-loaded", layout)
            elif layout is None and current and all(cb is None for cb, _c in entries):
                self.emit("error", error or _("Couldn't load the layout"))
            for cb, canc in entries:
                if cb is None:
                    continue
                if canc is not None and canc.is_cancelled():
                    cb(None, _("Cancelled"))
                elif not current:
                    cb(None, _("The keyboard is not connected"))
                else:
                    cb(layout, error)

        self._repo.load_async(dev.layout_id, dev.revision_id, dev.model, finish)

    def get_layout(self) -> Layout | None:
        return self._layout

    def get_active_layer(self) -> int:
        return self._active_layer

    def get_pressed_keys(self) -> set[int]:
        return set(self._pressed)

    def request_layer(self, index: int) -> None:
        if self._device is None:
            self.emit("error", _("No keyboard is connected"))
            return
        if not self._device.oryx_available or self._conn is None:
            self.emit("error", _("This keyboard's firmware does not support Oryx"))
            return
        n_layers = len(self._layout.layers) if self._layout else 32
        if not 0 <= index < n_layers:
            self.emit("error", _("Layer {index} does not exist").format(index=index))
            return
        try:
            self._conn.set_layer(index)
        except (OryxError, ValueError, OSError) as exc:
            self.emit("error", _("Couldn't switch layer: {error}").format(error=exc))

    # --- smart layers -------------------------------------------------------------

    def get_rules(self) -> list[AppRule]:
        return self._engine.rules if self._device is not None else []

    def set_rules(self, rules: list[AppRule]) -> None:
        if self._device is None:
            self.emit("error", _("Connect a keyboard to edit its rules"))
            return
        try:
            self._engine.set_rules(list(rules))
        except OSError as exc:
            self.emit("error", _("Couldn't save rules: {error}").format(error=exc.strerror or exc))

    def get_fallback_layer(self) -> int | None:
        return self._engine.fallback if self._device is not None else None

    def set_fallback_layer(self, index: int | None) -> None:
        if self._device is None:
            self.emit("error", _("Connect a keyboard to edit its rules"))
            return
        try:
            self._engine.set_fallback(index)
        except OSError as exc:
            self.emit("error", _("Couldn't save rules: {error}").format(error=exc.strerror or exc))

    def list_running_apps(self) -> list[RunningApp]:
        try:
            return self._watcher.list_running_apps()
        except Exception:   # noqa: BLE001
            log.exception("list_running_apps failed")
            return []

    def focus_source_available(self) -> bool:
        return self._watcher.available()

    def get_focused_app(self) -> tuple[str, str] | None:
        return self._focused

    def get_focused_window(self) -> FocusedWindow | None:
        return self._focused_window

    def list_steam_games(self) -> list[SteamGame]:
        return self._steam.games()

    def get_focused_monitor(self) -> str | None:
        return self._watcher.current_monitor()

    def _on_monitor_changed(self, _watcher: FocusWatcher, monitor: str) -> None:
        # Watchers emit from GLib sources, i.e. already on the main loop.
        self.emit("focused-monitor-changed", monitor)

    def _update_focus(self, app_class: str, title: str) -> FocusedWindow:
        try:
            window = self._steam.describe(app_class, title, self._watcher.current_pid())
        except Exception:   # noqa: BLE001 - Steam detection must never break focus
            log.exception("Steam detection failed")
            window = FocusedWindow(app_class, title, self._watcher.current_pid())
        self._focused = (app_class, title)
        self._focused_window = window
        self._engine.focus_changed(app_class, title, window.steam_app_id)
        return window

    def _on_focus_changed(self, _watcher: FocusWatcher, app_class: str, title: str) -> None:
        if is_own_app_class(app_class):
            return
        self._update_focus(app_class, title)       # get_focused_window() is current...
        self.emit("focus-changed", app_class, title)   # ...before listeners run

    # --- heatmap ------------------------------------------------------------------

    def get_key_counts(self, period: HeatmapPeriod,
                       layer: int | None = None) -> dict[int, int]:
        return self._heatmap.counts(period, layer)

    def reset_key_counts(self) -> None:
        try:
            self._heatmap.reset()
        except OSError as exc:
            self.emit("error", _("Couldn't reset the heatmap: {error}").format(
                error=exc.strerror or exc))
        self._emit_counts_changed_now()

    def _set_heatmap_layout(self, layout_id: str | None) -> None:
        if layout_id == self._heatmap.layout_id:
            return
        self._heatmap.set_layout(layout_id)
        self._emit_counts_changed_now()

    def _schedule_counts_changed(self) -> None:
        if self._counts_timer:
            return
        elapsed_ms = (time.monotonic() - self._counts_last_emit) * 1000
        delay = max(0, int(self.HEATMAP_THROTTLE_MS - elapsed_ms))

        def fire() -> bool:
            self._counts_timer = 0
            self._counts_last_emit = time.monotonic()
            self.emit("key-counts-changed")
            return GLib.SOURCE_REMOVE
        self._counts_timer = GLib.timeout_add(delay, fire)

    def _emit_counts_changed_now(self) -> None:
        if self._counts_timer:
            GLib.source_remove(self._counts_timer)
            self._counts_timer = 0
        self._counts_last_emit = time.monotonic()
        self.emit("key-counts-changed")

    def _flush_heatmap(self) -> None:
        try:
            self._heatmap.flush()
        except OSError as exc:
            log.warning("Can't save heatmap: %s", exc)

    def _on_flush_timer(self) -> bool:
        self._flush_heatmap()
        return GLib.SOURCE_CONTINUE

    def _on_heatmap_recording_changed(self, *_args: object) -> None:
        if not self.props.heatmap_recording:
            self._flush_heatmap()

    def _on_auto_connect_changed(self, *_args: object) -> None:
        if self.props.auto_connect:
            self._reset_backoff()
            if self._started:
                self._maybe_auto_connect()
        else:
            self._reset_backoff()       # stop pending retries; keep any live connection

    def _on_enabled_changed(self, *_args: object) -> None:
        self._engine.set_enabled(bool(self.props.smart_layers_enabled))


__all__ = ["ZsaKeyboardService"]
