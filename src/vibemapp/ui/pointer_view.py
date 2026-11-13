"""PointerView: the keyboard's trackpad or trackball, drawn live.

A round trackpad (the Navigator's is round) with a dot per finger that
follows it, a fading trail behind moving fingers, a ripple where a tap
happened and a flash (the pad presses in) on a click. A trackball is a shaded
ball whose surface marks roll with its motion; buttons flash it. The state is
``PadModel`` (``ui/pointer_activity.py``, pure); this widget only draws it.

Two looks, like ``KeyboardView``: neutral theme colours (the window
foreground at various strengths on the card, like the key outlines; the
Trackpad page), or the overlay style (light outline with a dark halo and the
overlay keys' pressed fill, ``set_overlay_style``), used by the pointer pill
and the overlay. The
frame clock runs only while something fades (``PadModel.animating``); a still
pad is never redrawn.
"""

from __future__ import annotations

import math
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, Graphene, Gsk, Gtk  # noqa: E402

from ..models import PointerActivity, PointerKind  # noqa: E402
from .i18n import _  # noqa: E402
from .pointer_activity import (TRAIL_MS, PadModel, ball_marks,  # noqa: E402
                               device_title, live_label)

_PROBES = ("fg", "card", "border", "accent-bg", "overlay-fg", "overlay-halo")
FINGER_RADIUS = 0.13      # of the pad's radius
RIPPLE_FROM, RIPPLE_TO = 0.08, 0.42
PRESS_SCALE = 0.94        # the pad presses in on a click
# Theme look: window-foreground strengths on the card.
FINGER_FG = 0.8           # finger dots, trails and ripples
OUTLINE_LIT_FG = 0.45     # the rim while touched (at rest: the border probe)
TOUCH_FG = 0.04           # the disc while touched
FLASH_FG = 0.1            # the disc on a click
MARK_FG, MARK_LIT_FG = 0.45, 0.8   # trackball marks at rest and rolling
LINE_PX = 1.0
HALO_PX = 1.5


def _alpha(color: Gdk.RGBA, alpha: float) -> Gdk.RGBA:
    out = color.copy()
    out.alpha = color.alpha * max(0.0, min(1.0, alpha))
    return out


def _circle(cx: float, cy: float, r: float) -> Gsk.Path:
    builder = Gsk.PathBuilder.new()
    builder.add_circle(Graphene.Point().init(cx, cy), max(r, 0.1))
    return builder.to_path()


class PointerView(Gtk.Widget):
    __gtype_name__ = "KmlPointerView"

    def __init__(self, kind: PointerKind | None = None) -> None:
        super().__init__()
        self.model = PadModel(kind or PointerKind.TRACKPAD)
        self._overlay = False
        self._rest_opacity = 1.0
        self._size: int | None = None
        self._tick_id = 0
        self._now = lambda: time.monotonic() * 1000
        self._colors: dict[str, Gdk.RGBA] = {}
        self._probes: list[Gtk.Widget] = []
        for name in _PROBES:
            probe = Gtk.Label()
            probe.add_css_class(f"probe-{name}")
            probe.set_parent(self)
            probe.set_child_visible(False)
            self._probes.append(probe)
        style = Adw.StyleManager.get_default()
        self._style_handlers = [style.connect(f"notify::{prop}", self._on_style_changed)
                                for prop in ("dark", "accent-color-rgba", "high-contrast")]
        self._update_label(None)

    # --- public ----------------------------------------------------------

    def set_kind(self, kind: PointerKind | None) -> None:
        kind = kind or PointerKind.TRACKPAD
        if kind != self.model.kind:
            self.model = PadModel(kind)
            self._update_label(None)
            self.queue_draw()

    def set_overlay_style(self, overlay: bool) -> None:
        self._overlay = overlay
        if overlay:
            self.add_css_class("overlay")
        else:
            self.remove_css_class("overlay")
        self._colors.clear()
        self.queue_draw()

    def set_rest_opacity(self, opacity: float) -> None:
        """Overlay style: strength while nothing touches it (full while lit)."""
        self._rest_opacity = max(0.0, min(1.0, opacity))
        self.queue_draw()

    def set_diameter(self, px: int | None) -> None:
        """Measure exactly ``px`` square (None: fill what the parent gives)."""
        self._size = px
        self.queue_resize()

    def feed(self, activity: PointerActivity) -> None:
        self.model.feed(activity, self._now())
        self._update_label(activity)
        self.queue_draw()
        self._sync_tick()

    def reset(self) -> None:
        self.model.reset()
        self.queue_draw()

    # --- animation -------------------------------------------------------

    def _sync_tick(self) -> None:
        if self.model.animating(self._now()):
            if not self._tick_id:
                self._tick_id = self.add_tick_callback(self._on_tick)
        elif self._tick_id:
            self.remove_tick_callback(self._tick_id)
            self._tick_id = 0

    def _on_tick(self, _widget: Gtk.Widget, _clock: Gdk.FrameClock) -> bool:
        now = self._now()
        self.model.prune(now)
        self.queue_draw()
        if self.model.animating(now):
            return True
        self._tick_id = 0
        return False

    # --- accessibility ---------------------------------------------------

    def _update_label(self, activity: PointerActivity | None) -> None:
        text = live_label(activity) if activity is not None else None
        name = device_title(self.model.kind)
        # Translators: e.g. "Trackpad: Two-Finger Scroll ↓"
        label = _("{device}: {activity}").format(device=name, activity=text) if text else name
        self.update_property([Gtk.AccessibleProperty.LABEL], [label])

    # --- style -----------------------------------------------------------

    def _on_style_changed(self, *_args: object) -> None:
        self._colors.clear()
        self.queue_draw()

    def do_css_changed(self, change: Gtk.CssStyleChange) -> None:
        Gtk.Widget.do_css_changed(self, change)
        self._colors.clear()
        self.queue_draw()

    def _color(self, name: str) -> Gdk.RGBA:
        if not self._colors:
            for probe_name, probe in zip(_PROBES, self._probes):
                self._colors[probe_name] = probe.get_color()
        return self._colors[name]

    def do_dispose(self) -> None:
        if self._tick_id:
            self.remove_tick_callback(self._tick_id)
            self._tick_id = 0
        style = Adw.StyleManager.get_default()
        for handler in getattr(self, "_style_handlers", []):
            style.disconnect(handler)
        self._style_handlers = []
        for probe in getattr(self, "_probes", []):
            probe.unparent()
        self._probes = []
        Gtk.Widget.do_dispose(self)

    # --- size ------------------------------------------------------------

    def do_get_request_mode(self) -> Gtk.SizeRequestMode:
        return Gtk.SizeRequestMode.CONSTANT_SIZE

    def do_measure(self, orientation: Gtk.Orientation, for_size: int
                   ) -> tuple[int, int, int, int]:
        if self._size is not None:
            return self._size, self._size, -1, -1
        return 48, 160, -1, -1

    # --- drawing ---------------------------------------------------------

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        width, height = self.get_width(), self.get_height()
        pad = HALO_PX * 2 + 1 if self._overlay else 1
        radius = min(width, height) / 2 - pad
        if radius <= 2:
            return
        now = self._now()
        cx, cy = width / 2, height / 2
        if self._overlay:
            self._push_halo(snapshot)
        if self.model.kind == PointerKind.TRACKBALL:
            self._draw_ball(snapshot, cx, cy, radius, now)
        else:
            self._draw_pad(snapshot, cx, cy, radius, now)
        if self._overlay:
            snapshot.pop()

    def _push_halo(self, snapshot: Gtk.Snapshot) -> None:
        """A crisp dark rim and soft glow behind everything, like the keys."""
        halo = self._color("overlay-halo")
        shadows = []
        for dx, dy, blur in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, HALO_PX * 2)):
            shadow = Gsk.Shadow()
            shadow.color = halo
            shadow.dx, shadow.dy, shadow.radius = float(dx), float(dy), float(blur)
            shadows.append(shadow)
        snapshot.push_shadow(shadows)

    def _palette(self, lit: float) -> tuple[Gdk.RGBA, Gdk.RGBA, Gdk.RGBA, Gdk.RGBA]:
        """(outline, touch tint, click flash, finger) colours for a lit level
        0..1; the tint and flash at full strength (scaled by lit and flash)."""
        fg = self._color("fg")
        if self._overlay:
            ofg = self._color("overlay-fg")
            strength = self._rest_opacity + (1 - self._rest_opacity) * lit
            press = self._color("accent-bg")     # as the overlay's pressed keys
            return (_alpha(ofg, strength), _alpha(press, 0.3), _alpha(press, 0.85 * 0.55),
                    _alpha(ofg, 1.0))
        outline = self._mix(self._color("border"), _alpha(fg, OUTLINE_LIT_FG), lit)
        return outline, _alpha(fg, TOUCH_FG), _alpha(fg, FLASH_FG), _alpha(fg, FINGER_FG)

    def _draw_pad(self, snapshot: Gtk.Snapshot, cx: float, cy: float, radius: float,
                  now: float) -> None:
        model = self.model
        lit = model.lit(now)
        flash = model.flash(now)
        outline, tint, flash_color, finger = self._palette(lit)
        snapshot.save()
        snapshot.translate(Graphene.Point().init(cx, cy))
        scale = 1.0 - (1.0 - PRESS_SCALE) * flash
        snapshot.scale(scale, scale)
        disc = _circle(0, 0, radius)
        if not self._overlay:
            snapshot.append_fill(disc, Gsk.FillRule.WINDING, self._color("card"))
        if lit > 0:                 # touched: a faint fill
            snapshot.append_fill(disc, Gsk.FillRule.WINDING, _alpha(tint, lit))
        if flash > 0:
            snapshot.append_fill(disc, Gsk.FillRule.WINDING, _alpha(flash_color, flash))
        line = LINE_PX if self._overlay else 1.5
        snapshot.append_stroke(_circle(0, 0, radius - line / 2), Gsk.Stroke.new(line), outline)

        dot = radius * FINGER_RADIUS
        for trail in model.trails:
            self._draw_trail(snapshot, trail, radius, dot, finger, now)
        for ripple in model.ripples:
            p = ripple.progress(now)
            r = radius * (RIPPLE_FROM + (RIPPLE_TO - RIPPLE_FROM) * (1 - (1 - p) ** 3))
            snapshot.append_stroke(_circle(ripple.x * radius * 0.86, ripple.y * radius * 0.86, r),
                                   Gsk.Stroke.new(max(dot * 0.3 * (1 - p), 1.0)),
                                   _alpha(finger, 1 - p))
            if p < 0.35:
                snapshot.append_fill(_circle(ripple.x * radius * 0.86, ripple.y * radius * 0.86,
                                             dot * (1 - p)),
                                     Gsk.FillRule.WINDING, _alpha(finger, 1 - p / 0.35))
        fade = model.lifted_alpha(now)
        for x, y in model.lifted:
            snapshot.append_fill(_circle(x * radius * 0.86, y * radius * 0.86, dot),
                                 Gsk.FillRule.WINDING, _alpha(finger, fade))
        for x, y in model.contacts:
            snapshot.append_fill(_circle(x * radius * 0.86, y * radius * 0.86, dot),
                                 Gsk.FillRule.WINDING, finger)
        snapshot.restore()

    def _draw_trail(self, snapshot: Gtk.Snapshot, trail, radius: float, dot: float,
                    color: Gdk.RGBA, now: float) -> None:
        for (x0, y0, t0), (x1, y1, t1) in zip(trail, trail[1:]):
            age = 1 - min(1.0, (now - t1) / TRAIL_MS)
            if age <= 0:
                continue
            builder = Gsk.PathBuilder.new()
            builder.move_to(x0 * radius * 0.86, y0 * radius * 0.86)
            builder.line_to(x1 * radius * 0.86, y1 * radius * 0.86)
            stroke = Gsk.Stroke.new(max(dot * 1.4 * age, 1.0))
            stroke.set_line_cap(Gsk.LineCap.ROUND)
            snapshot.append_stroke(builder.to_path(), stroke, _alpha(color, 0.6 * age))

    def _draw_ball(self, snapshot: Gtk.Snapshot, cx: float, cy: float, radius: float,
                   now: float) -> None:
        model = self.model
        flash = model.flash(now)
        lit = max(model.lit(now), flash)
        outline, tint, flash_color, mark = self._palette(lit)
        snapshot.save()
        snapshot.translate(Graphene.Point().init(cx, cy))
        disc = _circle(0, 0, radius)
        if not self._overlay:
            # Lit from the top left: a radial gradient from the card colour.
            bounds = Graphene.Rect().init(-radius, -radius, radius * 2, radius * 2)
            black = Gdk.RGBA()
            black.parse("black")
            light = self._mix(self._color("card"), self._color("fg"), 0.1)
            dark = self._mix(self._color("card"), black, 0.3)
            stops = []
            for offset, color in ((0.0, light), (1.0, dark)):
                stop = Gsk.ColorStop()
                stop.offset, stop.color = offset, color
                stops.append(stop)
            snapshot.push_clip(bounds)
            snapshot.push_fill(disc, Gsk.FillRule.WINDING)
            snapshot.append_radial_gradient(
                bounds, Graphene.Point().init(-radius * 0.35, -radius * 0.4),
                radius * 1.5, radius * 1.5, 0.0, 1.0, stops)
            snapshot.pop()
            snapshot.pop()
        if lit > 0:
            snapshot.append_fill(disc, Gsk.FillRule.WINDING, _alpha(tint, lit))
        if flash > 0:
            snapshot.append_fill(disc, Gsk.FillRule.WINDING, _alpha(flash_color, flash))
        line = LINE_PX if self._overlay else 1.5
        snapshot.append_stroke(_circle(0, 0, radius - line / 2), Gsk.Stroke.new(line), outline)
        rolling = model.rolling(now)
        step_x, step_y = model.last_roll
        before = ball_marks(model.rot_x - 4 * step_x, model.rot_y - 4 * step_y)
        for (x, y, z), (bx, by, bz) in zip(ball_marks(model.rot_x, model.rot_y), before):
            if z <= 0.05:
                continue
            color = mark if self._overlay else self._mix(
                self._color("card"), self._color("fg"),
                MARK_FG + (MARK_LIT_FG - MARK_FG) * rolling)
            r = radius * 0.09 * (0.45 + 0.55 * z)
            if rolling > 0 and bz > 0:      # a streak behind each mark while it rolls
                builder = Gsk.PathBuilder.new()
                builder.move_to(bx * radius * 0.9, by * radius * 0.9)
                builder.line_to(x * radius * 0.9, y * radius * 0.9)
                stroke = Gsk.Stroke.new(r * 1.2)
                stroke.set_line_cap(Gsk.LineCap.ROUND)
                snapshot.append_stroke(builder.to_path(), stroke,
                                       _alpha(color, 0.35 * rolling * z))
            snapshot.append_fill(_circle(x * radius * 0.9, y * radius * 0.9, r),
                                 Gsk.FillRule.WINDING, _alpha(color, 0.3 + 0.7 * z))
        snapshot.restore()

    @staticmethod
    def _mix(a: Gdk.RGBA, b: Gdk.RGBA, t: float) -> Gdk.RGBA:
        out = Gdk.RGBA()
        t = max(0.0, min(1.0, t))
        out.red = a.red + (b.red - a.red) * t
        out.green = a.green + (b.green - a.green) * t
        out.blue = a.blue + (b.blue - a.blue) * t
        out.alpha = a.alpha + (b.alpha - a.alpha) * t
        return out


PointerView.set_css_name("pointerview")
PointerView.set_accessible_role(Gtk.AccessibleRole.IMG)

__all__ = ["PointerView"]
