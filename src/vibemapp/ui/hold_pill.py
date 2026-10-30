"""Hold-action pill: a short on-screen "Ctrl+C" when a key's hold action fires,
and the held-modifier pill on the same surface: "Ctrl+Shift+T" while modifiers are held.

It looks like a key of the overlay: a ``KeyboardView`` in overlay style
(outline and label, light with a dark halo) showing one key, one key unit
tall and as wide as its label needs. It follows the overlay's appearance
settings (``PillStyle``): its key size, opacity, monitor and the edge it's
anchored to. Each feature has its own fade and its own time to stay visible.

``HoldDetector`` (``ui/hold_actions.py``) decides that a hold fired;
``HoldPillController`` feeds it the service's key events and shows a
``HoldPillSurface`` while the key stays down, then for ``hold-pill-linger-ms``.
Off unless ``hold-pill`` is on; single keys can be left out with
``hold-pill-excluded``. ``ModifierTracker`` (``ui/held_modifiers.py``) keeps the
pill up while modifiers are held, then for ``modifier-pill-linger-ms``; off
unless ``modifier-pill`` is on.

Surface
    One gtk4-layer-shell surface, namespace ``vibemapp-hold-pill``, made the
    same way as the overlay's (``ui/overlay.py``): OVERLAY layer, keyboard mode
    NONE, exclusive zone -1, empty input region, not added to the
    Gtk.Application. It is anchored to the overlay's top or bottom edge only,
    so the compositor centres it, ``EDGE_FRACTION`` of the monitor height in
    from that edge, or just past the overlay while that is shown there. It
    goes on the overlay's monitor (the fixed one, else the focused one). Like the overlay
    it never unmaps once shown: hidden, it's transparent and parked on the
    BACKGROUND layer, so it never takes keyboard focus from another panel.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, replace

import cairo
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, GObject, Gtk  # noqa: E402

from ..backend.smart_layers import match_rule  # noqa: E402
from ..models import AppRule, KeyBinding, KeyGeometry, Layer  # noqa: E402
from ..service import KeyboardService  # noqa: E402
from .held_modifiers import HeldKey, ModifierTracker, held_key  # noqa: E402
from .hold_actions import HoldAction, HoldDetector, hold_action, key_id  # noqa: E402
from .keyboard_view import KeyboardView  # noqa: E402

log = logging.getLogger(__name__)

NAMESPACE = "vibemapp-hold-pill"
ENABLED_KEY = "hold-pill"
EXCLUDED_KEY = "hold-pill-excluded"
MODIFIER_KEY = "modifier-pill"
# Per feature: (time it stays after release, fade duration) settings, in ms.
HOLD_TIMING_KEYS = ("hold-pill-linger-ms", "hold-pill-fade-ms")
MODIFIER_TIMING_KEYS = ("modifier-pill-linger-ms", "modifier-pill-fade-ms")
EDGE_FRACTION = 0.1
GAP_PX = 12
# Room around the key for the overlay's halo, which is drawn outside it.
HALO_PAD_PX = 4
# Key size when the overlay can't say (no monitor or layout yet).
DEFAULT_UNIT_PX = 48.0



@dataclass(frozen=True)
class PillStyle:
    """The overlay's appearance, as the pill uses it."""
    edge: str = "bottom"            # "top" or "bottom", the overlay's anchor edge
    unit: float = DEFAULT_UNIT_PX   # logical px per key unit (overlay Size)
    opacity: float = 0.6            # overlay Opacity, 0..1
    fade_in_ms: int = 200           # the feature's Fade Duration
    fade_out_ms: int = 150


def fade_pair(fade_ms: int) -> tuple[int, int]:
    """(fade-in, fade-out): out takes three quarters of in, as the overlay's."""
    fade_in = max(0, fade_ms)
    return fade_in, (fade_in * 3 + 2) // 4


# (monitor, its logical height) to show on; the px to keep free at the edge
# (the overlay's extent while it's shown there, else 0); the style.
MonitorFn = Callable[[], tuple[Gdk.Monitor | None, int]]
ClearanceFn = Callable[[], int]
StyleFn = Callable[[], PillStyle]


def edge_margin(monitor_height: int, clearance: int) -> int:
    """Distance of the pill from its edge, in logical px."""
    base = int(monitor_height * EDGE_FRACTION)
    return max(base, clearance + GAP_PX) if clearance > 0 else base


def key_width_units(text: str) -> float:
    """Width of the pill key, in key units: 1u for up to two characters (the
    overlay's large label size), wider in quarter units like real keys."""
    extra = max(len(text) - 2, 0) * 0.17
    return 1.0 + -(-extra * 4 // 1) / 4 if extra else 1.0


class HoldPillSurface(Gtk.Window):
    """The pill. ``flash(text)`` shows it, restarting the timer if it's on."""

    __gtype_name__ = "KmlHoldPillSurface"

    def __init__(self, layer_shell) -> None:
        super().__init__(decorated=False, resizable=False, focusable=False,
                         can_focus=False, can_target=False, deletable=False)
        self.remove_css_class("background")
        self.add_css_class("vibemapp-overlay")
        self._ls = layer_shell
        self._showing = False
        self._fade: Adw.TimedAnimation | None = None
        self._hide_id = 0
        self._fade_in_ms, self._fade_out_ms = PillStyle().fade_in_ms, PillStyle().fade_out_ms
        self.view = KeyboardView()
        self.view.set_overlay_style(True)
        self.view.set_can_target(False)
        for side in ("top", "bottom", "start", "end"):
            getattr(self.view, f"set_margin_{side}")(HALO_PAD_PX)
        self.view.set_opacity(0.0)
        self.set_child(self.view)

        ls = layer_shell
        ls.init_for_window(self)
        ls.set_layer(self, ls.Layer.OVERLAY)
        ls.set_namespace(self, NAMESPACE)
        ls.set_keyboard_mode(self, ls.KeyboardMode.NONE)
        ls.set_exclusive_zone(self, -1)
        for edge in (ls.Edge.LEFT, ls.Edge.RIGHT):
            ls.set_anchor(self, edge, False)
        self.connect("realize", lambda *_a: self._click_through())
        self.connect("map", lambda *_a: self._click_through())

    @property
    def showing(self) -> bool:
        return self._showing

    def place(self, monitor: Gdk.Monitor | None, edge: str, margin: int) -> None:
        ls = self._ls
        bottom = edge != "top"
        ls.set_anchor(self, ls.Edge.BOTTOM, bottom)
        ls.set_anchor(self, ls.Edge.TOP, not bottom)
        ls.set_margin(self, ls.Edge.BOTTOM, margin if bottom else 0)
        ls.set_margin(self, ls.Edge.TOP, 0 if bottom else margin)
        ls.set_monitor(self, monitor)

    def set_style(self, style: PillStyle) -> None:
        """Opacity at once; fade durations from the next fade."""
        self.view.set_rest_opacity(style.opacity)
        self._fade_in_ms, self._fade_out_ms = max(0, style.fade_in_ms), max(0, style.fade_out_ms)

    def set_key(self, text: str, unit: float) -> None:
        width = key_width_units(text)
        self.view.set_geometry((KeyGeometry(0, 0.0, 0.0, w=width),))
        self.view.set_layer(Layer(0, "", None, (KeyBinding(0, tap_label=text),)),
                            animate=False)
        self.view.set_fixed_size(round(width * unit), round(unit))

    def flash(self, text: str, unit: float = DEFAULT_UNIT_PX) -> None:
        """Show ``text`` until ``release()``."""
        self.set_key(text, unit)
        if self._hide_id:
            GLib.source_remove(self._hide_id)
            self._hide_id = 0
        if self._showing:
            return
        self._showing = True
        self._ls.set_layer(self, self._ls.Layer.OVERLAY)
        if not self.get_mapped():
            self.present()
        self._animate(1.0, self._fade_in_ms)

    def release(self, linger_ms: int) -> None:
        """Fade out after ``linger_ms`` (0: now), restarting a hide on its way."""
        if self._hide_id:
            GLib.source_remove(self._hide_id)
            self._hide_id = 0
        if not self._showing:
            return
        if linger_ms <= 0:
            self._on_hide_timeout()
        else:
            self._hide_id = GLib.timeout_add(linger_ms, self._on_hide_timeout)

    def hide_now(self) -> None:
        if self._hide_id:
            GLib.source_remove(self._hide_id)
            self._hide_id = 0
        self._stop_fade()
        self._showing = False
        self.view.set_opacity(0.0)
        self._ls.set_layer(self, self._ls.Layer.BACKGROUND)

    def _on_hide_timeout(self) -> bool:
        self._hide_id = 0
        self._showing = False
        self._animate(0.0, self._fade_out_ms)
        return GLib.SOURCE_REMOVE

    def _animate(self, target: float, duration: int) -> None:
        self._stop_fade()
        if duration <= 0:           # Fade Duration 0: show and hide at once
            self.view.set_opacity(target)
            if target == 0.0:
                self._ls.set_layer(self, self._ls.Layer.BACKGROUND)
            return
        anim = Adw.TimedAnimation.new(self.view, self.view.get_opacity(), target, duration,
                                      Adw.PropertyAnimationTarget.new(self.view, "opacity"))
        anim.set_easing(Adw.Easing.EASE_OUT_CUBIC)
        anim.connect("done", self._on_fade_done)
        self._fade = anim
        anim.play()

    def _on_fade_done(self, anim: Adw.TimedAnimation) -> None:
        if anim is not self._fade:
            return
        self._fade = None
        if not self._showing:
            self._ls.set_layer(self, self._ls.Layer.BACKGROUND)

    def _stop_fade(self) -> None:
        if self._fade is not None:
            anim, self._fade = self._fade, None
            anim.pause()

    def _click_through(self) -> None:
        surface = self.get_surface()
        if surface is not None:
            surface.set_input_region(cairo.Region())


class HoldPillController(GObject.Object):
    """Wires key events to the detector and the detector to the pill.

    ``layer_shell`` is the Gtk4LayerShell module (None: never shows a pill).
    ``monitor`` and ``clearance`` come from the overlay controller."""

    __gtype_name__ = "KmlHoldPillController"

    def __init__(self, service: KeyboardService, settings, layer_shell, *,
                 monitor: MonitorFn, clearance: ClearanceFn = lambda: 0,
                 style: StyleFn = PillStyle,
                 now: Callable[[], float] | None = None) -> None:
        super().__init__()
        self.service = service
        self.settings = settings
        self._ls = layer_shell
        self._monitor = monitor
        self._clearance = clearance
        self._style = style
        self._now = now or (lambda: time.monotonic() * 1000)
        self._surface: HoldPillSurface | None = None
        self._hold_index: int | None = None     # key whose hold action is shown
        self._shown_timing = HOLD_TIMING_KEYS
        self.detector = HoldDetector(self._lookup, self._on_fired, self._schedule,
                                     GLib.source_remove)
        self.modifiers = ModifierTracker(self._lookup_held, self._on_modifiers, self._schedule,
                                         GLib.source_remove)
        self._handlers: list[tuple[object, int]] = []
        if layer_shell is None:
            return
        self._handlers = [
            (service, service.connect("key-event", self._on_key_event)),
            (service, service.connect("disconnected", lambda *_a: self._reset())),
            (service, service.connect("layout-loaded", lambda *_a: self._forget_keys())),
            (settings, settings.connect(f"changed::{ENABLED_KEY}", lambda *_a: self._reset())),
            (settings, settings.connect(f"changed::{MODIFIER_KEY}", lambda *_a: self._reset())),
        ]

    @property
    def available(self) -> bool:
        return self._ls is not None

    @property
    def surface(self) -> HoldPillSurface | None:
        return self._surface

    def destroy(self) -> None:
        for obj, handler in self._handlers:
            obj.disconnect(handler)
        self._handlers = []
        self._forget_keys()
        if self._surface is not None:
            self._surface.hide_now()
            self._surface.destroy()
            self._surface = None

    @staticmethod
    def _schedule(ms: int, cb: Callable[[], None]) -> int:
        def fire() -> bool:
            cb()
            return GLib.SOURCE_REMOVE
        return GLib.timeout_add(ms, fire)

    def _forget_keys(self) -> None:
        self.detector.reset()
        self.modifiers.reset()
        self._hold_index = None

    def _reset(self) -> None:
        self._forget_keys()
        if self._surface is not None and self._surface.showing:
            self._surface.hide_now()

    def _on_key_event(self, _service: object, index: int, pressed: bool) -> None:
        self.detector.key(index, pressed, self._now())
        self.modifiers.key(index, pressed)
        if not pressed and index == self._hold_index:
            self._hold_index = None
            self._after_release()

    def _focused_rule(self) -> AppRule | None:
        """The Smart Layers rule matching the focused window: its switches can
        turn the pills off there (e.g. in games)."""
        window = self.service.get_focused_window()
        if window is None:
            return None
        return match_rule(self.service.get_rules(), window.app_class, window.title,
                          window.steam_app_id)

    def _lookup(self, index: int) -> HoldAction | None:
        if not self.settings.get_boolean(ENABLED_KEY):
            return None
        rule = self._focused_rule()
        if rule is not None and not rule.show_hold_actions:
            return None
        action = hold_action(self.service.get_layout(), self.service.get_active_layer(), index)
        if action is None:
            return None
        excluded = set(self.settings.get_value(EXCLUDED_KEY).unpack())
        return None if key_id(action.layer, action.index) in excluded else action

    def _lookup_held(self, index: int) -> HeldKey | None:
        if not self.settings.get_boolean(MODIFIER_KEY):
            return None
        rule = self._focused_rule()
        if rule is not None and not rule.show_held_modifiers:
            return None
        return held_key(self.service.get_layout(), self.service.get_active_layer(), index)

    def _on_fired(self, action: HoldAction) -> None:
        self._hold_index = action.index
        self.show(action.combo, HOLD_TIMING_KEYS)

    def _on_modifiers(self, text: str | None) -> None:
        if text:
            self.show(text, MODIFIER_TIMING_KEYS)
        elif self._hold_index is None:
            self._after_release()

    def _after_release(self) -> None:
        """A shown key was let go: back to the held modifiers, else fade out."""
        if self.modifiers.text:
            self.show(self.modifiers.text, MODIFIER_TIMING_KEYS)
        elif self._surface is not None:
            linger_key = self._shown_timing[0]
            self._surface.release(self.settings.get_int(linger_key))

    def show(self, text: str, timing: tuple[str, str] = HOLD_TIMING_KEYS) -> None:
        """Show ``text`` until a release (also used by the screenshot tool), with
        the fade of the feature whose ``timing`` settings keys are given."""
        if self._ls is None:
            return
        if self._surface is None:
            self._surface = HoldPillSurface(self._ls)
        self._shown_timing = timing
        monitor, height = self._monitor()
        fade_in, fade_out = fade_pair(self.settings.get_int(timing[1]))
        style = replace(self._style(), fade_in_ms=fade_in, fade_out_ms=fade_out)
        self._surface.set_style(style)
        self._surface.place(monitor, style.edge, edge_margin(height, self._clearance()))
        self._surface.flash(text, style.unit)


__all__ = ["ENABLED_KEY", "EXCLUDED_KEY", "HOLD_TIMING_KEYS", "MODIFIER_KEY",
           "MODIFIER_TIMING_KEYS", "HoldPillController", "HoldPillSurface", "NAMESPACE",
           "PillStyle", "edge_margin", "fade_pair", "key_width_units"]
