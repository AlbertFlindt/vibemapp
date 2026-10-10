"""Small reusable widgets."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, Graphene, Gsk, Gtk  # noqa: E402


class LayerDot(Gtk.Widget):
    """A small circle filled with an Oryx layer colour."""

    __gtype_name__ = "KmlLayerDot"

    def __init__(self, color: str | None = None) -> None:
        super().__init__()
        self.set_valign(Gtk.Align.CENTER)
        self.set_halign(Gtk.Align.CENTER)
        self.add_css_class("layer-dot")
        self._rgba: Gdk.RGBA | None = None
        self.set_color(color)

    def set_color(self, color: str | None) -> None:
        rgba = Gdk.RGBA()
        self._rgba = rgba if color and rgba.parse(color) else None
        self.set_visible(self._rgba is not None)
        self.queue_draw()

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        if self._rgba is None:
            return
        w, h = self.get_width(), self.get_height()
        rect = Graphene.Rect().init(0, 0, w, h)
        rounded = Gsk.RoundedRect()
        rounded.init_from_rect(rect, min(w, h) / 2)
        snapshot.push_rounded_clip(rounded)
        snapshot.append_color(self._rgba, rect)
        snapshot.pop()
        # Let CSS draw the outline on top.
        Gtk.Widget.do_snapshot(self, snapshot)


LayerDot.set_css_name("layerdot")
LayerDot.set_accessible_role(Gtk.AccessibleRole.PRESENTATION)


class _HeatBar(Gtk.Widget):
    """Gradient swatch of the heatmap ramp (follows light/dark)."""

    __gtype_name__ = "KmlHeatBar"

    def __init__(self) -> None:
        super().__init__()
        self.set_size_request(160, 10)
        self.set_valign(Gtk.Align.CENTER)

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        from gi.repository import Adw

        from . import heat

        w, h = self.get_width(), self.get_height()
        stops_rgb = heat.ramp(Adw.StyleManager.get_default().get_dark())
        stops = []
        for i, (r, g, b) in enumerate(stops_rgb):
            color = Gdk.RGBA()
            color.red, color.green, color.blue, color.alpha = r, g, b, 1.0
            stops.append(_color_stop(i / (len(stops_rgb) - 1), color))
        rect = Graphene.Rect().init(0, 0, w, h)
        rounded = Gsk.RoundedRect()
        rounded.init_from_rect(rect, h / 2)
        snapshot.push_rounded_clip(rounded)
        snapshot.append_linear_gradient(rect, Graphene.Point().init(0, 0),
                                        Graphene.Point().init(w, 0), stops)
        snapshot.pop()


def _color_stop(offset: float, color: Gdk.RGBA) -> Gsk.ColorStop:
    stop = Gsk.ColorStop()
    stop.offset = offset
    stop.color = color
    return stop


class HeatLegend(Gtk.Box):
    """``Presses per key  1 ▕gradient▏ 1,234``"""

    __gtype_name__ = "KmlHeatLegend"

    def __init__(self) -> None:
        from .i18n import _

        super().__init__()
        self.set_spacing(8)
        caption = Gtk.Label(label=_("Presses per key"))
        caption.add_css_class("dim-label")
        caption.add_css_class("caption")
        caption.set_margin_end(4)
        self._low = Gtk.Label(label="1")
        self._high = Gtk.Label(label="")
        for label in (self._low, self._high):
            label.add_css_class("caption")
            label.add_css_class("numeric")
        self.append(caption)
        self.append(self._low)
        bar = _HeatBar()
        bar.update_property([Gtk.AccessibleProperty.LABEL], [_("Colour scale")])
        self.append(bar)
        self.append(self._high)
        self._bar = bar

    def set_maximum(self, maximum: int) -> None:
        from . import heat

        self._high.set_label(heat.format_count(maximum))
        self.set_visible(maximum > 0)
        self._bar.queue_draw()
