"""On-screen overlay: the current layer drawn over everything, with live presses.

A click-through, never-focused surface that shows the keyboard's active layer
(outlines and tap labels only, see ``KeyboardView.set_overlay_style``) while
the overlay settings say so: typically while a momentary layer key is held.

Surfaces
    Each surface is a gtk4-layer-shell surface on the OVERLAY layer, namespace
    ``vibemapp-overlay``, keyboard mode NONE and exclusive zone -1, so it sits
    above fullscreen windows and panels and is never a window to the
    compositor (no focus, no Smart Layers trigger, not in running-app lists).
    Every surface gets an empty input region, so clicks go through it.
    With a half spacing above 0 each keyboard half gets its own surface, so the
    space between them stays free (placement rule: ``overlay_state``).
    With ``overlay-pointer`` on, the surface of each half with a Navigator in
    the layout (``Layout.navigators``; without them the right half, when a
    device is known) also draws it (``PointerView``), live, beside that half's
    inner edge above the thumb cluster (``overlay_state.pointer_pad_bounds``).

    gtk4-layer-shell is an optional package but required for the overlay, and
    it must be loaded before GTK (``vibemapp._preload``). Without it the
    controller never creates a surface. ``VIBEMAPP_NO_LAYER_SHELL=1``
    simulates that.

The windows are not added to the Gtk.Application, so they never keep the app
alive or count as its main window. ``OverlayController`` is owned by the
Application and destroyed in ``do_shutdown``.
"""

from __future__ import annotations

import logging
import os

import cairo
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, GObject, Gtk  # noqa: E402

from ..models import KeyGeometry, Layout, Navigator, PointerActivity  # noqa: E402
from ..service import KeyboardService  # noqa: E402
from .i18n import _  # noqa: E402
from .keyboard_view import KeyboardView  # noqa: E402
from .overlay_state import (Bounds, MonitorInfo, OverlayConfig, PartPlacement,  # noqa: E402
                            anchor_edges, fade_durations, geometry_bounds,
                            place_parts, pointer_pad_bounds, should_show,
                            split_geometry, union_bounds, unit_px)
from .pointer_activity import want_pointer  # noqa: E402
from .pointer_view import PointerView  # noqa: E402
from .rules_model import layer_label  # noqa: E402
from .view_binder import KeyboardViewBinder  # noqa: E402

log = logging.getLogger(__name__)

NAMESPACE = "vibemapp-overlay"
PILL_GAP_PX = 6
NO_LAYER_SHELL_ENV = "VIBEMAPP_NO_LAYER_SHELL"

_SETTING_KEYS = ("overlay-mode", "overlay-layers", "overlay-anchor", "overlay-size",
                 "overlay-opacity", "overlay-half-spacing", "overlay-monitor",
                 "overlay-idle-seconds", "overlay-fade-ms", "overlay-pointer")

_layer_shell = None
_layer_shell_checked = False


def _layer_shell_module():
    """The Gtk4LayerShell module when it works on this display, else None."""
    global _layer_shell, _layer_shell_checked
    if os.environ.get(NO_LAYER_SHELL_ENV) == "1":
        return None
    if _layer_shell_checked:
        return _layer_shell
    _layer_shell_checked = True
    try:
        gi.require_version("Gtk4LayerShell", "1.0")
        from gi.repository import Gtk4LayerShell
    except (ImportError, ValueError):
        return None
    try:
        if Gdk.Display.get_default() is None or not Gtk4LayerShell.is_supported():
            return None
    except Exception:  # noqa: BLE001 — a broken install must not break the app
        log.exception("gtk4-layer-shell check failed")
        return None
    _layer_shell = Gtk4LayerShell
    return _layer_shell


def layer_shell_available() -> bool:
    """True when the overlay can work: gtk4-layer-shell installed, loaded
    before GTK, and the compositor supports wlr-layer-shell."""
    return _layer_shell_module() is not None


def _monitor_list(display: Gdk.Display | None) -> list[Gdk.Monitor]:
    if display is None:
        return []
    model = display.get_monitors()
    return [model.get_item(i) for i in range(model.get_n_items())]


def find_monitor(display: Gdk.Display | None, connector: str | None) -> Gdk.Monitor | None:
    if not connector:
        return None
    return next((m for m in _monitor_list(display) if m.get_connector() == connector), None)


def monitor_info(monitor: Gdk.Monitor) -> MonitorInfo:
    geometry = monitor.get_geometry()      # logical px: compositor scale applied
    return MonitorInfo(monitor.get_connector(), geometry.width, geometry.height,
                       monitor.get_width_mm())


class OverlaySurface(Gtk.Window):
    """One layer-shell surface: an optional layer pill and a KeyboardView.

    Hiding never unmaps the surface. On Hyprland, unmapping a layer surface makes
    the compositor recompute keyboard focus, which takes it away from another
    app's focused layer-shell panel (e.g. a shell's session menu opened while a
    layer key was held). A hidden surface is fully transparent and parked on the
    BACKGROUND layer instead, below every window, so it doesn't sit over
    fullscreen windows. Changing the layer keeps it mapped.
    """

    __gtype_name__ = "KmlOverlaySurface"

    def __init__(self, layer_shell) -> None:
        super().__init__(decorated=False, resizable=False, focusable=False,
                         can_focus=False, can_target=False, deletable=False)
        self.remove_css_class("background")
        self.add_css_class("vibemapp-overlay")
        self._ls = layer_shell
        self._showing = False
        self._fade: Adw.TimedAnimation | None = None
        self._fade_in_ms, self._fade_out_ms = fade_durations(OverlayConfig())
        self.vertical = "bottom"
        self.edge_margin = 0

        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=PILL_GAP_PX,
                           can_target=False)
        self.pill = Gtk.Label(css_classes=["overlay-pill"], can_target=False,
                              visible=False)
        self.view = KeyboardView()
        self.view.set_overlay_style(True)
        self.view.set_can_target(False)
        self.view.set_hexpand(False)
        self.view.set_vexpand(False)
        # The board and, on a half with a Navigator, its trackpad or trackball.
        self.stage = Gtk.Overlay(can_target=False, halign=Gtk.Align.START)
        self.stage.set_child(self.view)
        self.pads: list[PointerView] = []
        self._rest_opacity = 1.0
        self.box.append(self.pill)
        self.box.append(self.stage)
        self.box.set_opacity(0.0)
        self.set_child(self.box)

        ls = layer_shell
        ls.init_for_window(self)
        ls.set_layer(self, ls.Layer.OVERLAY)
        ls.set_namespace(self, NAMESPACE)
        ls.set_keyboard_mode(self, ls.KeyboardMode.NONE)
        ls.set_exclusive_zone(self, -1)
        self.connect("realize", lambda *_a: self._click_through())
        self.connect("map", self._on_map)

    @property
    def showing(self) -> bool:
        return self._showing

    # --- placement -------------------------------------------------------

    def place(self, monitor: Gdk.Monitor | None, vertical: str,
              placement: PartPlacement, pill_halign: Gtk.Align) -> None:
        """Anchor to the ``vertical`` edge and the left edge, so the surface's
        position is exactly ``placement`` on ``monitor``."""
        # The pill sits on the board's inner side, away from the screen edge.
        if vertical == "bottom":
            self.box.reorder_child_after(self.stage, self.pill)
        else:
            self.box.reorder_child_after(self.pill, self.stage)
        self.pill.set_halign(pill_halign)
        self.vertical, self.edge_margin = vertical, placement.edge_margin
        ls = self._ls
        margins = {"top": 0, "bottom": 0, "right": 0, "left": placement.x}
        margins[vertical] = placement.edge_margin
        for name, edge in (("top", ls.Edge.TOP), ("bottom", ls.Edge.BOTTOM),
                           ("left", ls.Edge.LEFT), ("right", ls.Edge.RIGHT)):
            ls.set_anchor(self, edge, name in (vertical, "left"))
            ls.set_margin(self, edge, margins[name])
        ls.set_monitor(self, monitor)

    def set_board(self, geometry: tuple[KeyGeometry, ...], placement: PartPlacement,
                  bounds: Bounds | None = None,
                  pads: tuple[tuple[Bounds, Navigator], ...] = ()) -> None:
        """``bounds`` are the part's in key units, the ``pads``' included (each
        drawn as its Navigator's kind); without them the board fills ``placement``."""
        self.view.set_geometry(geometry)
        self.view.set_visible(bool(geometry))
        board = geometry_bounds(geometry) if geometry else (0.0, 0.0, 1.0, 1.0)
        bounds = bounds or board
        unit = placement.width / max(bounds[2] - bounds[0], 0.1)
        start, top = (board[0] - bounds[0]) * unit, (board[1] - bounds[1]) * unit
        end, bottom = (bounds[2] - board[2]) * unit, (bounds[3] - board[3]) * unit
        self.view.set_fixed_size(max(placement.width - round(start) - round(end), 1),
                                 max(placement.height - round(top) - round(bottom), 1))
        for side, value in (("start", start), ("top", top), ("end", end), ("bottom", bottom)):
            getattr(self.view, f"set_margin_{side}")(round(value))
        # The layer pill lines up with the keys, not the pad beside them.
        self.pill.set_margin_start(round(start))
        self.pill.set_margin_end(round(end))
        for view in self.pads:
            self.stage.remove_overlay(view)
        self.pads = []
        for pad, navigator in pads:
            view = PointerView(navigator.kind)
            view.set_overlay_style(True)
            view.set_can_target(False)
            view.set_halign(Gtk.Align.START)
            view.set_valign(Gtk.Align.START)
            view.set_rest_opacity(self._rest_opacity)
            view.set_diameter(round((pad[2] - pad[0]) * unit))
            view.set_margin_start(round((pad[0] - bounds[0]) * unit))
            view.set_margin_top(round((pad[1] - bounds[1]) * unit))
            self.stage.add_overlay(view)
            self.pads.append(view)

    def extent(self) -> int:
        """Height from its anchored edge outwards, in logical px: the same
        whether it's shown or hidden (its natural height, pill included)."""
        return self.edge_margin + self.box.measure(Gtk.Orientation.VERTICAL, -1)[1]

    def set_pill(self, text: str | None) -> None:
        self.pill.set_visible(bool(text))
        self.pill.set_label(text or "")

    def set_rest_opacity(self, opacity: float) -> None:
        self._rest_opacity = opacity
        self.view.set_rest_opacity(opacity)
        for view in self.pads:
            view.set_rest_opacity(opacity)
        self.pill.set_opacity(opacity)

    # --- showing ---------------------------------------------------------

    def set_fade_durations(self, fade_in_ms: int, fade_out_ms: int) -> None:
        """Takes effect from the next fade; 0 shows or hides at once."""
        self._fade_in_ms, self._fade_out_ms = max(0, fade_in_ms), max(0, fade_out_ms)

    def fade_in(self) -> None:
        if self._showing:
            return
        self._showing = True
        self._stop_fade()
        self._ls.set_layer(self, self._ls.Layer.OVERLAY)
        if not self.get_mapped():
            self.box.set_opacity(1.0 if self._fade_in_ms == 0 else 0.0)
            self.present()          # fades in on "map"
        elif self._fade_in_ms == 0:
            self.box.set_opacity(1.0)
        else:
            self._animate(1.0, self._fade_in_ms)

    def fade_out(self) -> None:
        if not self._showing:
            return
        self._showing = False
        self._stop_fade()
        if not self.get_mapped() or self._fade_out_ms == 0:
            self.box.set_opacity(0.0)
            self._park()
            return
        self._animate(0.0, self._fade_out_ms)

    def _park(self) -> None:
        """Hidden: stay mapped (see the class docstring), below all windows."""
        self._ls.set_layer(self, self._ls.Layer.BACKGROUND)

    def _on_map(self, *_args: object) -> None:
        self._click_through()
        if self._showing and self._fade_in_ms > 0:
            self._animate(1.0, self._fade_in_ms)

    def _animate(self, target: float, duration: int) -> None:
        anim = Adw.TimedAnimation.new(self.box, self.box.get_opacity(), target, duration,
                                      Adw.PropertyAnimationTarget.new(self.box, "opacity"))
        anim.set_easing(Adw.Easing.EASE_OUT_CUBIC)
        anim.connect("done", self._on_fade_done)
        self._fade = anim
        anim.play()

    def _on_fade_done(self, anim: Adw.TimedAnimation) -> None:
        if anim is not self._fade:
            return
        self._fade = None
        if not self._showing:
            self._park()

    def _stop_fade(self) -> None:
        if self._fade is not None:
            anim, self._fade = self._fade, None
            anim.pause()

    def _click_through(self) -> None:
        surface = self.get_surface()
        if surface is not None:
            surface.set_input_region(cairo.Region())


_AUTO = object()


class OverlayController(GObject.Object):
    """Decides when the overlay shows and keeps its surfaces up to date.

    Without gtk4-layer-shell (``available`` False) it never creates a surface.
    ``layer_shell`` is for tests and the screenshot tool: a stand-in module."""

    __gtype_name__ = "KmlOverlayController"

    # True while the overlay is (being) shown.
    shown = GObject.Property(type=bool, default=False)
    # Force-show on the overlay's monitor, e.g. while adjusting its settings.
    preview = GObject.Property(type=bool, default=False)

    def __init__(self, service: KeyboardService, settings, *, layer_shell=_AUTO) -> None:
        super().__init__()
        self.service = service
        self.settings = settings
        self._ls = _layer_shell_module() if layer_shell is _AUTO else layer_shell
        self.config = OverlayConfig.from_settings(settings)
        self._surfaces: list[OverlaySurface] = []
        self._placement_key: tuple | None = None
        self._idle = False
        self._idle_id = 0
        self._destroyed = False
        self._handlers: list = []
        self._monitors = None
        self._monitors_handler = 0
        self._binder: KeyboardViewBinder | None = None
        if self._ls is None:
            log.info("gtk4-layer-shell unavailable: the overlay is off")
            return
        self._binder = KeyboardViewBinder(service, on_layer_changed=self._on_layer_changed,
                                          reset_on_layer_change=True)
        self._handlers = [
            (service, service.connect("key-event", self._on_key_event)),
            (service, service.connect("layout-loaded", lambda *_a: self._update())),
            (service, service.connect("disconnected", lambda *_a: self._update())),
            (service, service.connect("focused-monitor-changed",
                                      lambda *_a: self._update())),
            (service, service.connect("pointer-changed", lambda *_a: self._update())),
            (service, service.connect("pointer-activity", self._on_pointer_activity)),
            (self, self.connect("notify::preview", lambda *_a: self._update())),
        ]
        for key in _SETTING_KEYS:
            self._handlers.append((settings, settings.connect(f"changed::{key}",
                                                              self._on_setting_changed)))
        display = Gdk.Display.get_default()
        self._monitors = display.get_monitors() if display is not None else None
        if self._monitors is not None:
            self._monitors_handler = self._monitors.connect("items-changed",
                                                            lambda *_a: self._update())
        self._update()

    # --- public ----------------------------------------------------------

    @property
    def available(self) -> bool:
        return self._ls is not None

    @property
    def surfaces(self) -> list[OverlaySurface]:
        return list(self._surfaces)

    @property
    def layer_shell(self):
        """The layer-shell module the overlay uses, or None."""
        return self._ls

    def pill_monitor(self) -> tuple[Gdk.Monitor | None, int]:
        """Where the hold-action pill goes: the overlay's monitor and its
        logical height."""
        monitor, info = self.resolve_monitor()
        return monitor, info.height

    def key_unit_px(self) -> float:
        """Logical px per key unit the overlay uses on its monitor now."""
        layout = self.service.get_layout()
        geometry = layout.geometry if layout is not None else ()
        if not geometry:
            return 48.0
        board = geometry_bounds(geometry)
        _monitor, info = self.resolve_monitor()
        return unit_px(info, self.config.size, board[2] - board[0])

    def can_show(self) -> bool:
        """Whether the overlay may appear at all: available, and previewed or
        a mode that can show it (not Off, and some layer selected)."""
        if not self.available or self._destroyed:
            return False
        if self.props.preview:
            return True
        if self.config.mode == "off" or self.service.get_layout() is None:
            return False
        return self.config.mode == "always" or bool(self.config.layers)

    def edge_clearance(self) -> int:
        """Logical px the overlay takes up from its anchored edge when shown,
        whether it's shown now or not, as long as it can show at all (else 0).
        So the pills keep one position while the overlay comes and goes (e.g.
        a Mouse layer the firmware turns on while the trackpad moves)."""
        if not self.can_show():
            return 0
        self._ensure_surfaces()
        return max((s.extent() for s in self._surfaces), default=0)

    def pill_style(self):
        """The overlay's appearance settings for the hold-action pill."""
        from .hold_pill import PillStyle
        vertical, _horizontal = anchor_edges(self.config.anchor)
        fade_in, fade_out = fade_durations(self.config)
        return PillStyle(edge=vertical, unit=self.key_unit_px(),
                         opacity=self.config.opacity / 100,
                         fade_in_ms=fade_in, fade_out_ms=fade_out)

    def destroy(self) -> None:
        if self._destroyed:
            return
        self._destroyed = True
        if self._binder is not None:
            self._binder.disconnect()
        for obj, handler in self._handlers:
            obj.disconnect(handler)
        self._handlers = []
        if self._monitors is not None and self._monitors_handler:
            self._monitors.disconnect(self._monitors_handler)
        self._monitors_handler = 0
        self._cancel_idle()
        self._destroy_surfaces()
        if self._ls is not None:
            want_pointer(self.service, "overlay", False)

    # --- state -----------------------------------------------------------

    def _on_setting_changed(self, _settings: object, _key: str) -> None:
        self.config = OverlayConfig.from_settings(self.settings)
        if self.config.idle_seconds <= 0:
            self._cancel_idle()
            self._idle = False
        fades = fade_durations(self.config)
        for surface in self._surfaces:
            surface.set_rest_opacity(self.config.opacity / 100)
            surface.set_fade_durations(*fades)
        self._update()

    def _on_layer_changed(self, _index: int) -> None:
        self._touch()
        self._update()

    def _on_key_event(self, _service: object, _index: int, _pressed: bool) -> None:
        self._touch()
        if self._idle is False and self.shown:
            return
        self._update()

    def _touch(self) -> None:
        """Activity: restart the idle timeout."""
        self._idle = False
        self._cancel_idle()
        if self.config.idle_seconds > 0:
            self._idle_id = GLib.timeout_add_seconds(self.config.idle_seconds, self._on_idle)

    def _cancel_idle(self) -> None:
        if self._idle_id:
            GLib.source_remove(self._idle_id)
            self._idle_id = 0

    def _on_idle(self) -> bool:
        self._idle_id = 0
        self._idle = True
        self._update()
        return GLib.SOURCE_REMOVE

    def wants_shown(self) -> bool:
        return self.available and should_show(
            self.config, self.service.get_active_layer(),
            has_layout=self.service.get_layout() is not None,
            idle=self._idle, preview=self.props.preview)

    def _update(self) -> None:
        if self._destroyed:
            return
        want = self.wants_shown()
        if want:
            if not self.shown and self.config.idle_seconds > 0 and not self._idle_id:
                self._touch()
            self._show()
        else:
            self._hide()
        if want != self.shown:
            self.shown = want
        # Pointer reports are parsed only while a pad can be seen.
        want_pointer(self.service, "overlay", want and bool(self.navigators()))

    def navigators(self) -> tuple[Navigator, ...]:
        """The pads the overlay draws: the layout's Navigators when Oryx said
        (``navigators_known``; maybe none), else (a layout cached before 1.10)
        the known device on the right half; () when ``overlay-pointer`` is off."""
        if not self.config.pointer:
            return ()
        layout = self.service.get_layout()
        if layout is not None and (layout.navigators_known or layout.navigators):
            return tuple(layout.navigators)
        kind = self.service.get_pointer_kind()
        return (Navigator("right", kind),) if kind is not None else ()

    def _on_pointer_activity(self, _service: object, activity: PointerActivity) -> None:
        for surface in self._surfaces:
            for view in surface.pads:
                if view.model.kind == activity.kind:
                    view.feed(activity)

    # --- surfaces --------------------------------------------------------

    def resolve_monitor(self) -> tuple[Gdk.Monitor | None, MonitorInfo]:
        """The monitor to use: the fixed one, else the focused one, else the
        first. Always explicit, so the size is computed for the right one."""
        display = Gdk.Display.get_default()
        name = self.config.monitor or self.service.get_focused_monitor()
        monitor = find_monitor(display, name)
        if monitor is None:
            monitors = _monitor_list(display)
            monitor = monitors[0] if monitors else None
        if monitor is None:
            return None, MonitorInfo(None, 1920, 1080, 0)
        return monitor, monitor_info(monitor)

    def _ensure_surfaces(self) -> None:
        """Build the surfaces for the current placement (they stay hidden
        until shown); rebuilt when it changes."""
        layout = self.service.get_layout()
        monitor, info = self.resolve_monitor()
        geometry = layout.geometry if layout is not None else ()
        key = (self.config.anchor, self.config.size, self.config.half_spacing, info, geometry,
               self.navigators())
        if key == self._placement_key:
            return
        was_shown = any(s.showing for s in self._surfaces)
        self._destroy_surfaces()
        self._build_surfaces(geometry, monitor, info)
        self._placement_key = key
        self._refresh_content(layout)
        if was_shown:
            for surface in self._surfaces:
                surface.box.set_opacity(1.0)
                surface.fade_in()

    def _show(self) -> None:
        self._ensure_surfaces()
        self._refresh_content(self.service.get_layout())
        if self._binder is not None:
            self._binder.sync_pressed()
        for surface in self._surfaces:
            surface.fade_in()

    def _hide(self) -> None:
        for surface in self._surfaces:
            surface.fade_out()

    def layout_parts(self, geometry: tuple[KeyGeometry, ...], info: MonitorInfo,
                     navigators: tuple[Navigator, ...] = ()
                     ) -> list[tuple[tuple[KeyGeometry, ...], PartPlacement, Bounds,
                                     tuple[tuple[Bounds, Navigator], ...]]]:
        """The board parts (whole board, or left and right half), where they go
        on ``info``'s monitor, their bounds and the pads of the ``navigators``
        each carries (a pad goes on the part of its half: the whole board when
        it isn't spaced apart)."""
        halves = split_geometry(geometry) if self.config.half_spacing > 0 else None
        parts = list(halves) if halves is not None else [geometry]
        bounds = [geometry_bounds(p) for p in parts]
        pads: list[list[tuple[Bounds, Navigator]]] = [[] for _ in parts]
        board = geometry_bounds(geometry)
        whole = split_geometry(geometry) if geometry else None
        placed: list[Bounds] = []
        for navigator in navigators if geometry else ():
            n = 1 if navigator.side == "right" else 0
            if whole is None:
                pad = pointer_pad_bounds(geometry, navigator.side)
            else:
                pad = pointer_pad_bounds(whole[n], navigator.side, whole[1 - n],
                                         tuple(placed))
            placed.append(pad)
            n = min(n, len(parts) - 1)
            pads[n].append((pad, navigator))
            bounds[n] = union_bounds(bounds[n], pad)
            board = union_bounds(board, pad)
        unit = unit_px(info, self.config.size, board[2] - board[0])
        placements = place_parts(info, self.config.anchor, unit, board, bounds,
                                 self.config.half_spacing)
        return list(zip(parts, placements, bounds, (tuple(p) for p in pads)))

    def _build_surfaces(self, geometry: tuple[KeyGeometry, ...],
                        monitor: Gdk.Monitor | None, info: MonitorInfo) -> None:
        vertical, _horizontal = anchor_edges(self.config.anchor)
        parts = self.layout_parts(geometry, info, self.navigators())
        for part, placement, bounds, pads in parts:
            surface = OverlaySurface(self._ls)
            # Joined board: pill centred on it; halves: with the left half.
            halign = Gtk.Align.CENTER if len(parts) == 1 else Gtk.Align.START
            surface.place(monitor, vertical, placement, halign)
            surface.set_board(part, placement, bounds, pads)
            surface.set_rest_opacity(self.config.opacity / 100)
            surface.set_fade_durations(*fade_durations(self.config))
            self._surfaces.append(surface)
        if self._binder is not None:
            self._binder.set_views(*(s.view for s in self._surfaces))

    def _destroy_surfaces(self) -> None:
        if self._binder is not None:
            self._binder.set_views()
        for surface in self._surfaces:
            surface.destroy()
        self._surfaces = []
        self._placement_key = None

    def _refresh_content(self, layout: Layout | None) -> None:
        index = self.service.get_active_layer()
        layer = None
        if layout is not None and 0 <= index < len(layout.layers):
            layer = layout.layers[index]
        text = _("No Keyboard Connected") if layout is None else layer_label(layer, index)
        for n, surface in enumerate(self._surfaces):
            # Crossfade only while it's on screen. A hidden surface stays mapped
            # (see OverlaySurface), so animating would briefly show the old layer
            # as it fades in.
            surface.view.set_layer(layer, animate=surface.showing,
                                   layers=layout.layers if layout else None)
            surface.set_pill(text if n == 0 else None)


__all__ = ["NAMESPACE", "NO_LAYER_SHELL_ENV", "OverlayController", "OverlaySurface",
           "find_monitor", "layer_shell_available", "monitor_info"]
