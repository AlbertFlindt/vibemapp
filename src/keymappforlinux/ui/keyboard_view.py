"""KeyboardView — draws a keyboard layer from KeyGeometry with GtkSnapshot.

Colours come from libadwaita CSS variables (resolved through invisible probe
children styled in ``style.css``), so the view follows light/dark and the
accent colour. The only literal colours are Oryx-supplied key/layer tints.
"""

from __future__ import annotations

import math

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, GLib, Graphene, Gsk, Gtk, Pango  # noqa: E402

from ..models import KeyBinding, KeyGeometry, Layer  # noqa: E402
from .i18n import _  # noqa: E402

KEY_GAP = 0.08          # key units between neighbouring keys
KEY_RADIUS = 0.16       # corner radius, key units
MAX_UNIT_PX = 72.0      # don't blow keys up beyond this on huge windows
PRESS_IN_MS = 60
PRESS_OUT_MS = 280
FADE_MS = 220

_PROBES = ("fg", "key", "border", "shade", "accent-bg", "accent-fg")


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


def geometry_bounds(geometry: tuple[KeyGeometry, ...]) -> tuple[float, float, float, float]:
    """Bounding box (x0, y0, x1, y1) in key units, honouring rotation."""
    if not geometry:
        return 0.0, 0.0, 1.0, 1.0
    xs: list[float] = []
    ys: list[float] = []
    for key in geometry:
        cx, cy = key.x + key.w / 2, key.y + key.h / 2
        rad = math.radians(key.rotation)
        cos, sin = math.cos(rad), math.sin(rad)
        for dx, dy in ((-key.w / 2, -key.h / 2), (key.w / 2, -key.h / 2),
                       (key.w / 2, key.h / 2), (-key.w / 2, key.h / 2)):
            xs.append(cx + dx * cos - dy * sin)
            ys.append(cy + dx * sin + dy * cos)
    return min(xs), min(ys), max(xs), max(ys)


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
        self._fade = 1.0
        self._press: dict[int, float] = {}
        self._press_anims: dict[int, Adw.TimedAnimation] = {}
        self._layout_cache: dict[tuple[str, int, bool], Pango.Layout] = {}
        self._colors: dict[str, Gdk.RGBA] = {}

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

    def set_layer(self, layer: Layer | None, animate: bool = True) -> None:
        if layer is self._layer or (layer is not None and self._layer is not None
                                    and layer == self._layer):
            return
        self._prev_layer = self._layer
        self._layer = layer
        if animate and self._prev_layer is not None and self.get_mapped():
            self._fade_anim.reset()
            self._fade_anim.play()
        else:
            self._fade = 1.0
            self.queue_draw()
        self._update_accessible_label()

    def get_layer(self) -> Layer | None:
        return self._layer

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
        if self._layer is None:
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
        x0, _y0, x1, _y1 = self._bounds
        width_units = max(x1 - x0, 1.0)
        if orientation == Gtk.Orientation.HORIZONTAL:
            return 200, max(200, int(width_units * 52)), -1, -1
        natural_w = for_size if for_size > 0 else width_units * 52
        natural = int(min(natural_w, width_units * MAX_UNIT_PX) * self._aspect())
        return 100, max(natural, 100), -1, -1

    def do_size_allocate(self, width: int, height: int, baseline: int) -> None:
        self._layout_cache.clear()

    def _transform(self) -> tuple[float, float, float]:
        """(unit_px, offset_x, offset_y) mapping key units to widget pixels."""
        x0, y0, x1, y1 = self._bounds
        width, height = self.get_width(), self.get_height()
        unit = min(width / max(x1 - x0, 0.1), height / max(y1 - y0, 0.1), MAX_UNIT_PX)
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
        for geo in self._geometry:
            self._draw_key(snapshot, geo, unit, off_x, off_y,
                           bindings_new.get(geo.index),
                           bindings_old.get(geo.index) if bindings_old is not None else None)

    def _draw_key(self, snapshot: Gtk.Snapshot, geo: KeyGeometry, unit: float,
                  off_x: float, off_y: float, binding: KeyBinding | None,
                  old: KeyBinding | None) -> None:
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
        # without any binding are drawn fainter.
        def cap_alpha(b: KeyBinding | None) -> float:
            blank = b is None or not (b.tap_label or b.hold_label or b.glyph)
            return 0.55 if blank else 1.0

        blank_alpha = cap_alpha(binding)
        if transition:
            blank_alpha = cap_alpha(old) * (1 - fade) + blank_alpha * fade

        snapshot.append_outset_shadow(rounded, _with_alpha(self._color("shade"), blank_alpha),
                                      0, max(unit * 0.03, 1.0), 0, max(unit * 0.06, 1.0))
        snapshot.push_rounded_clip(rounded)
        snapshot.append_color(_with_alpha(self._color("key"), blank_alpha), rect)

        # Oryx key colour tint, crossfaded between layers.
        tint_alpha = 0.32 if Adw.StyleManager.get_default().get_dark() else 0.22
        for b, alpha in ((old if transition else None, 1 - fade), (binding, fade)):
            tint = _rgba(b.color) if b is not None else None
            if tint is not None and alpha > 0:
                snapshot.append_color(_with_alpha(tint, tint_alpha * alpha), rect)
        if press > 0:
            snapshot.append_color(_with_alpha(self._color("accent-bg"), press), rect)
        snapshot.pop()

        border = _with_alpha(self._color("border"), blank_alpha)
        snapshot.append_border(rounded, [1, 1, 1, 1], [border] * 4)

        fg = _mix(self._color("fg"), self._color("accent-fg"), press)
        if transition:
            self._draw_labels(snapshot, old, w, h, unit, _with_alpha(fg, 1 - self._fade))
            self._draw_labels(snapshot, binding, w, h, unit, _with_alpha(fg, self._fade))
        else:
            self._draw_labels(snapshot, binding, w, h, unit, fg)
        snapshot.restore()

    def _draw_labels(self, snapshot: Gtk.Snapshot, binding: KeyBinding | None,
                     w: float, h: float, unit: float, fg: Gdk.RGBA) -> None:
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
            layout = self._fitted_layout(binding.hold_label, unit * 0.17, max_w, bold=False)
            lw, lh = layout.get_pixel_size()
            snapshot.save()
            snapshot.translate(Graphene.Point().init(-lw / 2, h / 2 - lh - h * 0.06))
            snapshot.append_layout(layout, _with_alpha(fg, 0.6))
            snapshot.restore()

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

    def do_query_tooltip(self, x: int, y: int, keyboard_mode: bool,
                         tooltip: Gtk.Tooltip) -> bool:
        if keyboard_mode or self._layer is None:
            return False
        geo = self.key_at(x, y)
        if geo is None:
            return False
        binding = next((k for k in self._layer.keys if k.index == geo.index), None)
        if binding is None or not (binding.tap_label or binding.hold_label):
            return False
        text = GLib.markup_escape_text(binding.tap_label or "")
        if binding.hold_label:
            hold = GLib.markup_escape_text(binding.hold_label)
            text = (_("{tap} · hold for {hold}").format(tap=text, hold=hold)
                    if text else _("Hold for {hold}").format(hold=hold))
        tooltip.set_markup(text)
        return True


KeyboardView.set_css_name("keyboardview")
KeyboardView.set_accessible_role(Gtk.AccessibleRole.IMG)
