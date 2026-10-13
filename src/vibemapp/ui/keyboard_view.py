"""KeyboardView — draws a keyboard layer from KeyGeometry with GtkSnapshot.

Colours come from libadwaita CSS variables (resolved through invisible probe
children styled in ``style.css``), so the view follows light/dark and the
accent colour. The only literal colours are Oryx-supplied key/layer tints.
"""

from __future__ import annotations

import math
from dataclasses import replace

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, Graphene, Gsk, Gtk, Pango  # noqa: E402

from ..models import KeyBinding, KeyGeometry, Layer  # noqa: E402
from . import heat  # noqa: E402
from .transparency import (BASE_ONLY, TRANSPARENT_MARK, FallThrough,  # noqa: E402
                           binding_label, fallthrough_map, is_transparent, transparent_tooltip)
from .i18n import _, ngettext  # noqa: E402
from .overlay_state import geometry_bounds  # noqa: E402,F401  (re-exported)

KEY_GAP = 0.08          # key units between neighbouring keys
KEY_RADIUS = 0.16       # corner radius, key units
MAX_UNIT_PX = 72.0      # don't blow keys up beyond this on huge windows
PRESS_IN_MS = 60
PRESS_OUT_MS = 280
FADE_MS = 220

_PROBES = ("fg", "key", "border", "dash", "shade", "accent-bg", "accent-fg",
           "overlay-fg", "overlay-halo")

# Transparent keys: a faint, flat face with a dashed outline, the ▽ marker and
# the fall-through binding in dimmed text.
TRANSPARENT_FACE_ALPHA = 0.3
TRANSPARENT_LABEL_ALPHA = 0.45
TRANSPARENT_MARK_ALPHA = 0.7
BLANK_FACE_ALPHA = 0.55

# Overlay style (see set_overlay_style): outlines and tap labels only, light
# with a dark halo so they read over any content.
OVERLAY_PRESS_ALPHA = 0.85        # accent fill of a pressed key
OVERLAY_TRANSPARENT_ALPHA = 0.5   # fall-through label of a transparent key
OVERLAY_BLANK_ALPHA = 0.6         # outline of a key without a binding
OVERLAY_LINE_PX = 1.0
OVERLAY_HALO_PX = 1.5


def _rgba(spec: str | None) -> Gdk.RGBA | None:
    if not spec:
        return None
    color = Gdk.RGBA()
    return color if color.parse(spec) else None


def _with_alpha(color: Gdk.RGBA, alpha: float) -> Gdk.RGBA:
    out = color.copy()
    out.alpha = color.alpha * max(0.0, min(1.0, alpha))
    return out


def _mix(a: Gdk.RGBA, b: Gdk.RGBA, t: float) -> Gdk.RGBA:
    out = Gdk.RGBA()
    out.red = a.red + (b.red - a.red) * t
    out.green = a.green + (b.green - a.green) * t
    out.blue = a.blue + (b.blue - a.blue) * t
    out.alpha = a.alpha + (b.alpha - a.alpha) * t
    return out


class KeyboardView(Gtk.Widget):
    __gtype_name__ = "KmlKeyboardView"

    def __init__(self) -> None:
        super().__init__()
        # Set explicitly: constructor kwargs are ignored for GtkBuilder-created
        # instances.
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_has_tooltip(True)
        self._geometry: tuple[KeyGeometry, ...] = ()
        self._bounds = (0.0, 0.0, 1.0, 1.0)
        self._layer: Layer | None = None
        self._prev_layer: Layer | None = None
        self._fall: dict[int, FallThrough] = {}
        self._prev_fall: dict[int, FallThrough] = {}
        self._fade = 1.0
        self._press: dict[int, float] = {}
        self._press_anims: dict[int, Adw.TimedAnimation] = {}
        self._layout_cache: dict[tuple[str, int, bool], Pango.Layout] = {}
        self._colors: dict[str, Gdk.RGBA] = {}
        self._heat: dict[int, int] | None = None
        self._heat_max = 0
        self._overlay = False
        self._rest_opacity = 1.0
        self.max_unit_px = MAX_UNIT_PX
        self._fixed_size: tuple[int, int] | None = None

        self._probes: list[Gtk.Widget] = []
        for name in _PROBES:
            probe = Gtk.Label()
            probe.add_css_class(f"probe-{name}")
            probe.set_parent(self)
            probe.set_child_visible(False)
            self._probes.append(probe)

        self._fade_anim = Adw.TimedAnimation.new(
            self, 0.0, 1.0, FADE_MS, Adw.CallbackAnimationTarget.new(self._on_fade))
        self._fade_anim.set_easing(Adw.Easing.EASE_OUT_CUBIC)

        style = Adw.StyleManager.get_default()
        self._style_handlers = [
            style.connect("notify::dark", self._on_style_changed),
            style.connect("notify::accent-color-rgba", self._on_style_changed),
            style.connect("notify::high-contrast", self._on_style_changed),
        ]
        self._update_accessible_label()

    # --- public API ------------------------------------------------------

    def set_geometry(self, geometry: tuple[KeyGeometry, ...]) -> None:
        self._geometry = tuple(geometry)
        self._bounds = geometry_bounds(self._geometry)
        self._press.clear()
        self.queue_resize()

    def set_layer(self, layer: Layer | None, animate: bool = True,
                  layers: tuple[Layer, ...] | None = None) -> None:
        """Show ``layer``. Pass the layout's whole layer stack as ``layers`` to
        show what transparent keys fall through to (the base layer's key)."""
        fall = fallthrough_map(layers or (), layer, BASE_ONLY)
        if layer is self._layer or (layer is not None and self._layer is not None
                                    and layer == self._layer):
            if fall != self._fall:
                self._fall = fall
                self.queue_draw()
            return
        self._prev_layer = self._layer
        self._prev_fall = self._fall
        self._layer = layer
        self._fall = fall
        if animate and self._prev_layer is not None and self.get_mapped():
            self._fade_anim.reset()
            self._fade_anim.play()
        else:
            self._fade = 1.0
            self.queue_draw()
        self._update_accessible_label()

    def get_layer(self) -> Layer | None:
        return self._layer

    def set_heat(self, counts: dict[int, int] | None) -> None:
        """Heat mode: ``counts`` per key index colours the keys on a sequential
        ramp and prints the count; ``None`` returns to the normal layout view."""
        self._heat = dict(counts) if counts is not None else None
        self._heat_max = max(self._heat.values(), default=0) if self._heat else 0
        self._update_accessible_label()
        self.queue_draw()

    def set_overlay_style(self, overlay: bool) -> None:
        """Overlay drawing: no key faces at rest, light 1px outlines and tap
        labels with a dark halo, accent fill on pressed keys, no hold labels
        and no tooltips. Used by the on-screen overlay, never the Layout page."""
        self._overlay = overlay
        self.set_has_tooltip(not overlay)
        if overlay:
            self.add_css_class("overlay")
        else:
            self.remove_css_class("overlay")
        self._colors.clear()
        self.queue_draw()

    def set_rest_opacity(self, opacity: float) -> None:
        """Overlay style: opacity of keys that aren't pressed (pressed keys are
        drawn at full strength)."""
        opacity = max(0.0, min(1.0, opacity))
        if opacity != self._rest_opacity:
            self._rest_opacity = opacity
            self.queue_draw()

    def set_fixed_size(self, width: int, height: int) -> None:
        """Measure exactly ``width`` x ``height`` (the overlay sizes itself
        from the monitor, not from its window)."""
        self._fixed_size = (width, height)
        self.max_unit_px = float("inf")
        self.queue_resize()

    @property
    def is_overlay(self) -> bool:
        return self._overlay

    @property
    def heat_max(self) -> int:
        return self._heat_max

    def set_key_pressed(self, index: int, pressed: bool) -> None:
        anim = self._press_anims.get(index)
        if anim is not None:
            anim.pause()
        current = self._press.get(index, 0.0)
        target = 1.0 if pressed else 0.0
        if not self.get_mapped():
            self._set_press(index, target)
            return

        def update(value: float) -> None:
            self._set_press(index, value)

        anim = Adw.TimedAnimation.new(self, current, target,
                                      PRESS_IN_MS if pressed else PRESS_OUT_MS,
                                      Adw.CallbackAnimationTarget.new(update))
        anim.set_easing(Adw.Easing.EASE_OUT_QUAD if pressed else Adw.Easing.EASE_OUT_CUBIC)
        self._press_anims[index] = anim
        anim.play()

    def clear_pressed(self) -> None:
        for anim in self._press_anims.values():
            anim.pause()
        self._press_anims.clear()
        self._press.clear()
        self.queue_draw()

    # --- callbacks -------------------------------------------------------

    def _set_press(self, index: int, value: float) -> None:
        if value <= 0.001:
            self._press.pop(index, None)
        else:
            self._press[index] = value
        self.queue_draw()

    def _on_fade(self, value: float) -> None:
        self._fade = value
        if value >= 1.0:
            self._prev_layer = None
        self.queue_draw()

    def _on_style_changed(self, *_args: object) -> None:
        self._colors.clear()
        self.queue_draw()

    def do_css_changed(self, change: Gtk.CssStyleChange) -> None:
        Gtk.Widget.do_css_changed(self, change)
        self._colors.clear()
        self._layout_cache.clear()
        self.queue_draw()

    def do_unrealize(self) -> None:
        self._layout_cache.clear()
        Gtk.Widget.do_unrealize(self)

    def do_dispose(self) -> None:
        style = Adw.StyleManager.get_default()
        for handler in getattr(self, "_style_handlers", []):
            style.disconnect(handler)
        self._style_handlers = []
        for probe in getattr(self, "_probes", []):
            probe.unparent()
        self._probes = []
        Gtk.Widget.do_dispose(self)

    def _update_accessible_label(self) -> None:
        if self._overlay and self._layer is not None:
            label = _("Keyboard overlay showing {layer}").format(layer=self._layer.title)
        elif self._heat is not None:
            label = _("Key press heatmap")
        elif self._layer is None:
            label = _("Keyboard layout")
        else:
            label = _("Keyboard layout showing {layer}").format(layer=self._layer.title)
        self.update_property([Gtk.AccessibleProperty.LABEL], [label])

    # --- sizing ----------------------------------------------------------

    def _aspect(self) -> float:
        x0, y0, x1, y1 = self._bounds
        return max(y1 - y0, 0.1) / max(x1 - x0, 0.1)

    def do_get_request_mode(self) -> Gtk.SizeRequestMode:
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def do_measure(self, orientation: Gtk.Orientation, for_size: int) -> tuple[int, int, int, int]:
        if self._fixed_size is not None:
            size = self._fixed_size[0 if orientation == Gtk.Orientation.HORIZONTAL else 1]
            return size, size, -1, -1
        x0, _y0, x1, _y1 = self._bounds
        width_units = max(x1 - x0, 1.0)
        if orientation == Gtk.Orientation.HORIZONTAL:
            return 200, max(200, int(width_units * 52)), -1, -1
        natural_w = for_size if for_size > 0 else width_units * 52
        natural = int(min(natural_w, width_units * self.max_unit_px) * self._aspect())
        return 100, max(natural, 100), -1, -1

    def do_size_allocate(self, width: int, height: int, baseline: int) -> None:
        self._layout_cache.clear()

    def _transform(self) -> tuple[float, float, float]:
        """(unit_px, offset_x, offset_y) mapping key units to widget pixels."""
        x0, y0, x1, y1 = self._bounds
        width, height = self.get_width(), self.get_height()
        unit = min(width / max(x1 - x0, 0.1), height / max(y1 - y0, 0.1), self.max_unit_px)
        off_x = (width - (x1 - x0) * unit) / 2 - x0 * unit
        off_y = (height - (y1 - y0) * unit) / 2 - y0 * unit
        return unit, off_x, off_y

    # --- colours & text --------------------------------------------------

    def _color(self, name: str) -> Gdk.RGBA:
        if not self._colors:
            for probe_name, probe in zip(_PROBES, self._probes):
                self._colors[probe_name] = probe.get_color()
        return self._colors[name]

    def _text_layout(self, text: str, px: int, bold: bool) -> Pango.Layout:
        key = (text, px, bold)
        layout = self._layout_cache.get(key)
        if layout is None:
            layout = self.create_pango_layout(text)
            desc = (self.get_pango_context().get_font_description()
                    or Pango.FontDescription()).copy()
            desc.set_absolute_size(px * Pango.SCALE)
            desc.set_weight(Pango.Weight.SEMIBOLD if bold else Pango.Weight.NORMAL)
            layout.set_font_description(desc)
            layout.set_alignment(Pango.Alignment.CENTER)
            if len(self._layout_cache) > 600:
                self._layout_cache.clear()
            self._layout_cache[key] = layout
        return layout

    def _fitted_layout(self, text: str, px: float, max_w: float, bold: bool) -> Pango.Layout:
        size = max(int(px), 6)
        layout = self._text_layout(text, size, bold)
        width = layout.get_pixel_size()[0]
        if width > max_w and width > 0:
            size = max(int(size * max_w / width), 6)
            layout = self._text_layout(text, size, bold)
        return layout

    # --- drawing ---------------------------------------------------------

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        if not self._geometry:
            return
        unit, off_x, off_y = self._transform()
        if unit <= 0:
            return
        bindings_new = {k.index: k for k in self._layer.keys} if self._layer else {}
        bindings_old = ({k.index: k for k in self._prev_layer.keys}
                        if self._prev_layer is not None and self._fade < 1.0 else None)
        if self._overlay:
            self._snapshot_overlay(snapshot, unit, off_x, off_y, bindings_new, bindings_old)
            return
        for geo in self._geometry:
            self._draw_key(snapshot, geo, unit, off_x, off_y,
                           bindings_new.get(geo.index),
                           bindings_old.get(geo.index) if bindings_old is not None else None,
                           self._fall.get(geo.index), self._prev_fall.get(geo.index))

    def _draw_key(self, snapshot: Gtk.Snapshot, geo: KeyGeometry, unit: float,
                  off_x: float, off_y: float, binding: KeyBinding | None,
                  old: KeyBinding | None, fall: FallThrough | None = None,
                  old_fall: FallThrough | None = None) -> None:
        transition = self._prev_layer is not None and self._fade < 1.0
        fade = self._fade if transition else 1.0
        press = self._press.get(geo.index, 0.0)
        cx = off_x + (geo.x + geo.w / 2) * unit
        cy = off_y + (geo.y + geo.h / 2) * unit
        w = geo.w * unit - KEY_GAP * unit
        h = geo.h * unit - KEY_GAP * unit
        radius = KEY_RADIUS * unit

        snapshot.save()
        snapshot.translate(Graphene.Point().init(cx, cy))
        if geo.rotation:
            snapshot.rotate(geo.rotation)
        if press > 0:
            scale = 1.0 - 0.05 * press
            snapshot.scale(scale, scale)

        rect = Graphene.Rect().init(-w / 2, -h / 2, w, h)
        rounded = Gsk.RoundedRect()
        rounded.init_from_rect(rect, radius)

        # Base cap: card colour with a soft drop shade for depth. Keys
        # without any binding are drawn fainter; transparent keys are flat and
        # fainter still, with a dashed outline.
        def cap_alpha(b: KeyBinding | None) -> float:
            if is_transparent(b):
                return TRANSPARENT_FACE_ALPHA
            blank = b is None or not (b.tap_label or b.hold_label or b.glyph)
            return BLANK_FACE_ALPHA if blank else 1.0

        def crossfade(value) -> float:  # value: Callable[[KeyBinding | None], float]
            new = value(binding)
            return value(old) * (1 - fade) + new * fade if transition else new

        if self._heat is not None:
            self._draw_heat_key(snapshot, geo, rect, rounded, unit, w, h, binding)
            snapshot.restore()
            return

        blank_alpha = crossfade(cap_alpha)
        dashed = crossfade(lambda b: 1.0 if is_transparent(b) else 0.0)

        if dashed < 1.0:
            snapshot.append_outset_shadow(
                rounded, _with_alpha(self._color("shade"), blank_alpha * (1 - dashed)),
                0, max(unit * 0.03, 1.0), 0, max(unit * 0.06, 1.0))
        snapshot.push_rounded_clip(rounded)
        snapshot.append_color(_with_alpha(self._color("key"), blank_alpha), rect)

        # Oryx key colour tint, crossfaded between layers.
        tint_alpha = 0.32 if Adw.StyleManager.get_default().get_dark() else 0.22
        for b, alpha in ((old if transition else None, 1 - fade), (binding, fade)):
            tint = _rgba(b.color) if b is not None else None
            if tint is not None and alpha > 0:
                if is_transparent(b):
                    alpha *= 0.5
                snapshot.append_color(_with_alpha(tint, tint_alpha * alpha), rect)
        if press > 0:
            snapshot.append_color(_with_alpha(self._color("accent-bg"), press), rect)
        snapshot.pop()

        if dashed < 1.0:
            border = _with_alpha(self._color("border"), blank_alpha * (1 - dashed))
            snapshot.append_border(rounded, [1, 1, 1, 1], [border] * 4)
        if dashed > 0.0:
            self._draw_dashed_outline(snapshot, rect, radius, unit, dashed)

        fg = _mix(self._color("fg"), self._color("accent-fg"), press)
        if transition:
            self._draw_key_face(snapshot, old, old_fall, w, h, unit, fg, 1 - self._fade, press)
            self._draw_key_face(snapshot, binding, fall, w, h, unit, fg, self._fade, press)
        else:
            self._draw_key_face(snapshot, binding, fall, w, h, unit, fg, 1.0, press)
        snapshot.restore()

    # --- overlay style ---------------------------------------------------

    def _snapshot_overlay(self, snapshot: Gtk.Snapshot, unit: float, off_x: float,
                          off_y: float, bindings_new: dict[int, KeyBinding],
                          bindings_old: dict[int, KeyBinding] | None) -> None:
        # One shadow node for the whole board: a crisp 1px dark rim around
        # every outline and label (four offset copies) plus a soft glow.
        halo = self._color("overlay-halo")
        shadows = []
        for dx, dy, radius in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0),
                               (0, 0, OVERLAY_HALO_PX * 2)):
            shadow = Gsk.Shadow()
            shadow.color = halo
            shadow.dx, shadow.dy = float(dx), float(dy)
            shadow.radius = float(radius)
            shadows.append(shadow)
        snapshot.push_shadow(shadows)
        for geo in self._geometry:
            self._draw_overlay_key(
                snapshot, geo, unit, off_x, off_y, bindings_new.get(geo.index),
                bindings_old.get(geo.index) if bindings_old is not None else None,
                self._fall.get(geo.index), self._prev_fall.get(geo.index))
        snapshot.pop()

    def _draw_overlay_key(self, snapshot: Gtk.Snapshot, geo: KeyGeometry, unit: float,
                          off_x: float, off_y: float, binding: KeyBinding | None,
                          old: KeyBinding | None, fall: FallThrough | None,
                          old_fall: FallThrough | None) -> None:
        transition = self._prev_layer is not None and self._fade < 1.0
        press = self._press.get(geo.index, 0.0)
        # Pressed keys come up to full strength whatever the rest opacity.
        strength = self._rest_opacity + (1.0 - self._rest_opacity) * press
        cx = off_x + (geo.x + geo.w / 2) * unit
        cy = off_y + (geo.y + geo.h / 2) * unit
        w = geo.w * unit - KEY_GAP * unit
        h = geo.h * unit - KEY_GAP * unit
        radius = KEY_RADIUS * unit

        snapshot.save()
        snapshot.translate(Graphene.Point().init(cx, cy))
        if geo.rotation:
            snapshot.rotate(geo.rotation)
        rect = Graphene.Rect().init(-w / 2, -h / 2, w, h)
        rounded = Gsk.RoundedRect()
        rounded.init_from_rect(rect, radius)

        if press > 0:
            snapshot.push_rounded_clip(rounded)
            snapshot.append_color(
                _with_alpha(self._color("accent-bg"), OVERLAY_PRESS_ALPHA * press), rect)
            snapshot.pop()

        def outline_alpha(b: KeyBinding | None) -> float:
            if is_transparent(b):
                return OVERLAY_TRANSPARENT_ALPHA
            blank = b is None or not (b.tap_label or b.hold_label or b.glyph)
            return OVERLAY_BLANK_ALPHA if blank else 1.0

        line_alpha = outline_alpha(binding)
        if transition:
            line_alpha = outline_alpha(old) * (1 - self._fade) + line_alpha * self._fade
        fg = self._color("overlay-fg")
        line = _with_alpha(fg, strength * (line_alpha + (1 - line_alpha) * press))
        snapshot.append_border(rounded, [OVERLAY_LINE_PX] * 4, [line] * 4)

        label_fg = _mix(fg, self._color("accent-fg"), press)
        if transition:
            self._draw_overlay_label(snapshot, old, old_fall, w, h, unit, label_fg,
                                     strength * (1 - self._fade), press)
            self._draw_overlay_label(snapshot, binding, fall, w, h, unit, label_fg,
                                     strength * self._fade, press)
        else:
            self._draw_overlay_label(snapshot, binding, fall, w, h, unit, label_fg,
                                     strength, press)
        snapshot.restore()

    def _draw_overlay_label(self, snapshot: Gtk.Snapshot, binding: KeyBinding | None,
                            fall: FallThrough | None, w: float, h: float, unit: float,
                            fg: Gdk.RGBA, alpha: float, press: float) -> None:
        """Tap label only (the hold label when there's no tap action)."""
        if alpha <= 0.01 or binding is None:
            return
        if is_transparent(binding):
            dim = OVERLAY_TRANSPARENT_ALPHA + (1 - OVERLAY_TRANSPARENT_ALPHA) * press
            shown = fall.binding if fall is not None else None
            if shown is None or not (binding_label(shown) or shown.glyph):
                shown = KeyBinding(binding.index, tap_label=TRANSPARENT_MARK)
            binding, alpha = shown, alpha * dim
        color = _with_alpha(fg, alpha)
        if binding.glyph and self._draw_glyph(snapshot, binding.glyph, unit * 0.36, 0.0, color):
            return
        text = binding_label(binding)
        if not text:
            return
        px = unit * (0.34 if len(text) <= 2 else 0.24)
        layout = self._fitted_layout(text, px, w * 0.84, bold=True)
        lw, lh = layout.get_pixel_size()
        snapshot.save()
        snapshot.translate(Graphene.Point().init(-lw / 2, -lh / 2))
        snapshot.append_layout(layout, color)
        snapshot.restore()

    def _draw_dashed_outline(self, snapshot: Gtk.Snapshot, rect: Graphene.Rect,
                             radius: float, unit: float, alpha: float) -> None:
        # Inset by half the line width so the stroke stays inside the cap.
        line = max(unit * 0.022, 1.0)
        inner = Graphene.Rect().init(rect.get_x() + line / 2, rect.get_y() + line / 2,
                                     rect.get_width() - line, rect.get_height() - line)
        rounded = Gsk.RoundedRect()
        rounded.init_from_rect(inner, max(radius - line / 2, 0.0))
        builder = Gsk.PathBuilder.new()
        builder.add_rounded_rect(rounded)
        stroke = Gsk.Stroke.new(line)
        dash = max(unit * 0.07, 2.0)
        stroke.set_dash([dash, dash * 0.8])
        snapshot.append_stroke(builder.to_path(), stroke,
                               _with_alpha(self._color("dash"), alpha))

    def _draw_key_face(self, snapshot: Gtk.Snapshot, binding: KeyBinding | None,
                       fall: FallThrough | None, w: float, h: float, unit: float,
                       fg: Gdk.RGBA, alpha: float, press: float) -> None:
        """Labels for one layer's binding; ``alpha`` is the crossfade weight."""
        if alpha <= 0.01:
            return
        if not is_transparent(binding):
            self._draw_labels(snapshot, binding, w, h, unit, _with_alpha(fg, alpha))
            return
        # Pressing a transparent key brings its text up to full strength, like
        # any other key under the accent highlight.
        def dimmed(level: float) -> Gdk.RGBA:
            return _with_alpha(fg, alpha * (level + (1 - level) * press))

        # LT(n, KC_TRNS): only the tap falls through; the key's own hold
        # action is drawn like any other hold label.
        own_hold = binding.hold_label
        hold_fg = _with_alpha(fg, alpha * 0.6) if own_hold else None
        if fall is not None and (binding_label(fall.binding) or fall.binding.glyph):
            shown = (replace(fall.binding, hold_label=own_hold) if own_hold
                     else fall.binding)
            self._draw_labels(snapshot, shown, w, h, unit,
                              dimmed(TRANSPARENT_LABEL_ALPHA), hold_fg=hold_fg)
            # Marker tucked into the top corner so it doesn't fight the label.
            layout = self._text_layout(TRANSPARENT_MARK, max(int(unit * 0.16), 6), False)
            lw, lh = layout.get_pixel_size()
            inset = min(w, h) * 0.07
            snapshot.save()
            snapshot.translate(Graphene.Point().init(w / 2 - inset - lw, -h / 2 + inset * 0.6))
            snapshot.append_layout(layout, dimmed(TRANSPARENT_MARK_ALPHA))
            snapshot.restore()
        else:
            layout = self._fitted_layout(TRANSPARENT_MARK, unit * 0.30, w * 0.84, bold=False)
            lw, lh = layout.get_pixel_size()
            centre_y = -h * 0.08 if own_hold else 0.0
            snapshot.save()
            snapshot.translate(Graphene.Point().init(-lw / 2, centre_y - lh / 2))
            snapshot.append_layout(layout, dimmed(TRANSPARENT_MARK_ALPHA))
            snapshot.restore()
            if own_hold:
                self._draw_hold(snapshot, own_hold, w, h, unit, hold_fg)

    def _draw_heat_key(self, snapshot: Gtk.Snapshot, geo: KeyGeometry, rect: Graphene.Rect,
                       rounded: Gsk.RoundedRect, unit: float, w: float, h: float,
                       binding: KeyBinding | None) -> None:
        count = self._heat.get(geo.index, 0) if self._heat else 0
        snapshot.append_outset_shadow(rounded, self._color("shade"),
                                      0, max(unit * 0.03, 1.0), 0, max(unit * 0.06, 1.0))
        snapshot.push_rounded_clip(rounded)
        snapshot.append_color(self._color("key"), rect)
        fg = self._color("fg")
        if count > 0:
            dark = Adw.StyleManager.get_default().get_dark()
            r, g, b = heat.color_at(heat.intensity(count, self._heat_max), dark)
            fill = Gdk.RGBA()
            fill.red, fill.green, fill.blue, fill.alpha = r, g, b, 1.0
            snapshot.append_color(fill, rect)
            fg = Gdk.RGBA()
            fg.parse("#ffffff" if heat.text_is_light((r, g, b)) else "#1a1a1a")
        snapshot.pop()
        snapshot.append_border(rounded, [1, 1, 1, 1], [self._color("border")] * 4)

        max_w = w * 0.84
        label = TRANSPARENT_MARK if is_transparent(binding) else binding_label(binding)
        if count > 0:
            text = heat.compact_count(count)
            layout = self._fitted_layout(text, unit * 0.30, max_w, bold=True)
            lw, lh = layout.get_pixel_size()
            snapshot.save()
            snapshot.translate(Graphene.Point().init(-lw / 2, h * 0.08 - lh / 2))
            snapshot.append_layout(layout, fg)
            snapshot.restore()
        if label:
            layout = self._fitted_layout(label, unit * 0.17, max_w, bold=False)
            lw, lh = layout.get_pixel_size()
            y = -h / 2 + h * 0.08 if count > 0 else -lh / 2
            snapshot.save()
            snapshot.translate(Graphene.Point().init(-lw / 2, y))
            snapshot.append_layout(layout, _with_alpha(fg, 0.75 if count > 0 else 0.5))
            snapshot.restore()

    def _draw_hold(self, snapshot: Gtk.Snapshot, text: str, w: float, h: float,
                   unit: float, fg: Gdk.RGBA) -> None:
        layout = self._fitted_layout(text, unit * 0.17, w * 0.84, bold=False)
        lw, lh = layout.get_pixel_size()
        snapshot.save()
        snapshot.translate(Graphene.Point().init(-lw / 2, h / 2 - lh - h * 0.06))
        snapshot.append_layout(layout, fg)
        snapshot.restore()

    def _draw_labels(self, snapshot: Gtk.Snapshot, binding: KeyBinding | None,
                     w: float, h: float, unit: float, fg: Gdk.RGBA,
                     hold_fg: Gdk.RGBA | None = None) -> None:
        if binding is None or fg.alpha <= 0.01:
            return
        has_hold = bool(binding.hold_label)
        centre_y = -h * 0.08 if has_hold else 0.0
        max_w = w * 0.84

        drew_glyph = False
        if binding.glyph:
            drew_glyph = self._draw_glyph(snapshot, binding.glyph, unit * 0.36, centre_y, fg)
        if not drew_glyph and binding.tap_label:
            text = binding.tap_label
            px = unit * (0.34 if len(text) <= 2 else 0.24)
            layout = self._fitted_layout(text, px, max_w, bold=len(text) <= 2)
            lw, lh = layout.get_pixel_size()
            snapshot.save()
            snapshot.translate(Graphene.Point().init(-lw / 2, centre_y - lh / 2))
            snapshot.append_layout(layout, fg)
            snapshot.restore()
        if has_hold:
            self._draw_hold(snapshot, binding.hold_label, w, h, unit,
                            hold_fg if hold_fg is not None else _with_alpha(fg, 0.6))

    def _draw_glyph(self, snapshot: Gtk.Snapshot, icon_name: str, size: float,
                    centre_y: float, fg: Gdk.RGBA) -> bool:
        display = self.get_display()
        theme = Gtk.IconTheme.get_for_display(display)
        if not theme.has_icon(icon_name):
            return False
        px = max(int(size), 8)
        paintable = theme.lookup_icon(icon_name, None, px, self.get_scale_factor(),
                                      self.get_direction(), 0)
        snapshot.save()
        snapshot.translate(Graphene.Point().init(-px / 2, centre_y - px / 2))
        paintable.snapshot_symbolic(snapshot, px, px, [fg, fg, fg, fg])
        snapshot.restore()
        return True

    # --- tooltips --------------------------------------------------------

    def key_at(self, x: float, y: float) -> KeyGeometry | None:
        unit, off_x, off_y = self._transform()
        if unit <= 0:
            return None
        for geo in self._geometry:
            cx = off_x + (geo.x + geo.w / 2) * unit
            cy = off_y + (geo.y + geo.h / 2) * unit
            rad = math.radians(-geo.rotation)
            dx, dy = x - cx, y - cy
            lx = dx * math.cos(rad) - dy * math.sin(rad)
            ly = dx * math.sin(rad) + dy * math.cos(rad)
            if abs(lx) <= geo.w * unit / 2 and abs(ly) <= geo.h * unit / 2:
                return geo
        return None

    def key_rect(self, geo: KeyGeometry) -> Gdk.Rectangle:
        """Axis-aligned pixel bounds of a key (rotated keys included)."""
        unit, off_x, off_y = self._transform()
        x0, y0, x1, y1 = geometry_bounds((geo,))
        rect = Gdk.Rectangle()
        rect.x = int(math.floor(off_x + x0 * unit))
        rect.y = int(math.floor(off_y + y0 * unit))
        rect.width = max(1, int(math.ceil(off_x + x1 * unit)) - rect.x)
        rect.height = max(1, int(math.ceil(off_y + y1 * unit)) - rect.y)
        return rect

    def tooltip_text_for(self, geo: KeyGeometry) -> str | None:
        """Plain-text tooltip for a key, or None when there's nothing to say."""
        if self._layer is None:
            return None
        binding = next((k for k in self._layer.keys if k.index == geo.index), None)
        if self._heat is not None:
            count = self._heat.get(geo.index, 0)
            name = _("Transparent") if is_transparent(binding) else binding_label(binding)
            presses = ngettext("{n} press", "{n} presses", count).format(
                n=heat.format_count(count))
            return f"{name} · {presses}" if name else presses
        if is_transparent(binding):
            return transparent_tooltip(self._fall.get(geo.index), binding.hold_label)
        if binding is None or not (binding.tap_label or binding.hold_label):
            return None
        if not binding.hold_label:
            return binding.tap_label
        if not binding.tap_label:
            return _("Hold for {hold}").format(hold=binding.hold_label)
        return _("{tap} · hold for {hold}").format(tap=binding.tap_label,
                                                   hold=binding.hold_label)

    def do_query_tooltip(self, x: int, y: int, keyboard_mode: bool,
                         tooltip: Gtk.Tooltip) -> bool:
        if keyboard_mode:
            return False
        geo = self.key_at(x, y)
        if geo is None:
            return False
        text = self.tooltip_text_for(geo)
        if text is None:
            return False
        # Without a tip area GTK treats the whole view as one tooltip region,
        # so the tooltip neither moves nor updates between keys (SPEC 6.3).
        tooltip.set_tip_area(self.key_rect(geo))
        tooltip.set_text(text)
        return True

KeyboardView.set_css_name("keyboardview")
KeyboardView.set_accessible_role(Gtk.AccessibleRole.IMG)
