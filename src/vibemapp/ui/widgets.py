"""Small reusable widgets."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, Graphene, Gsk, Gtk, Pango  # noqa: E402


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


class FingerLoadChart(Gtk.Widget):
    """Ten bars, left pinky to right pinky: each finger's share of the key presses.

    A short line across each bar marks the finger's comfortable share; bars
    above it use the warning colour. Colours come from libadwaita through
    invisible probe labels (accent, warning, foreground), so it follows the
    theme, dark mode and the accent colour. Values are always printed above
    the bars, so colour is never the only channel.
    """

    __gtype_name__ = "KmlFingerLoadChart"

    MIN_WIDTH = 260
    HEIGHT = 200
    GAP = 0.7                       # extra gap between the thumbs, in columns

    def __init__(self) -> None:
        super().__init__()
        self._loads: list[float] = [0.0] * 10
        self._capacities: list[float] = [0.0] * 10
        self._names: list[str] = [""] * 10      # full names, e.g. "Pinky"
        self._initials: list[str] = [""] * 10
        self._hands = ("", "")
        self._colors: dict[str, Gdk.RGBA] = {}
        self._probes: list[tuple[str, Gtk.Widget]] = []
        for name in ("accent", "warning"):
            probe = Gtk.Label()
            probe.add_css_class(name)
            probe.set_parent(self)
            probe.set_child_visible(False)
            self._probes.append((name, probe))
        from gi.repository import Adw

        style = Adw.StyleManager.get_default()
        self._style_handlers = [
            style.connect("notify::dark", self._on_style_changed),
            style.connect("notify::accent-color-rgba", self._on_style_changed),
            style.connect("notify::high-contrast", self._on_style_changed),
        ]

    def set_data(self, loads: list[float], capacities: list[float], names: list[str],
                 initials: list[str], hands: tuple[str, str], description: str) -> None:
        """``loads`` and ``capacities`` are shares (0..1) in finger order."""
        self._loads, self._capacities = list(loads), list(capacities)
        self._names, self._initials, self._hands = list(names), list(initials), hands
        self.update_property([Gtk.AccessibleProperty.DESCRIPTION], [description])
        self.queue_draw()

    # --- widget plumbing -------------------------------------------------

    def do_dispose(self) -> None:
        from gi.repository import Adw

        style = Adw.StyleManager.get_default()
        for handler in getattr(self, "_style_handlers", []):
            style.disconnect(handler)
        self._style_handlers = []
        for _name, probe in getattr(self, "_probes", []):
            probe.unparent()
        self._probes = []
        Gtk.Widget.do_dispose(self)

    def _on_style_changed(self, *_args: object) -> None:
        self._colors.clear()
        self.queue_draw()

    def do_css_changed(self, change: Gtk.CssStyleChange) -> None:
        self._colors.clear()
        Gtk.Widget.do_css_changed(self, change)

    def do_measure(self, orientation: Gtk.Orientation, for_size: int) -> tuple[int, int, int, int]:
        if orientation == Gtk.Orientation.HORIZONTAL:
            return self.MIN_WIDTH, 460, -1, -1
        return self.HEIGHT, self.HEIGHT, -1, -1

    def _color(self, name: str) -> Gdk.RGBA:
        if not self._colors:
            for probe_name, probe in self._probes:
                self._colors[probe_name] = probe.get_color()
            self._colors["fg"] = self.get_color()
        return self._colors[name]

    # --- drawing ---------------------------------------------------------

    def _text(self, snapshot: Gtk.Snapshot, text: str, cx: float, y: float, scale: float,
              color: Gdk.RGBA, bold: bool = False) -> float:
        """Draw ``text`` centred on ``cx`` with its top at ``y``; returns its height."""
        layout = self.create_pango_layout(text)
        attrs = Pango.AttrList()
        attrs.insert(Pango.attr_scale_new(scale))
        if bold:
            attrs.insert(Pango.attr_weight_new(Pango.Weight.SEMIBOLD))
        layout.set_attributes(attrs)
        width, height = layout.get_pixel_size()
        snapshot.save()
        snapshot.translate(Graphene.Point().init(round(cx - width / 2), round(y)))
        snapshot.append_layout(layout, color)
        snapshot.restore()
        return height

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        width, height = self.get_width(), self.get_height()
        if width <= 0 or height <= 0:
            return
        fg = self._color("fg")
        dim = Gdk.RGBA()
        dim.red, dim.green, dim.blue, dim.alpha = fg.red, fg.green, fg.blue, 0.65
        faint = Gdk.RGBA()
        faint.red, faint.green, faint.blue, faint.alpha = fg.red, fg.green, fg.blue, 0.18

        columns = 10 + self.GAP
        column = width / columns
        names = self._names if column >= 46 else self._initials
        row_h = 17
        base = height - 3 * row_h - 6                 # y of the bars' bottom edge
        room = base - 8
        scale = max(max(self._loads, default=0.0), max(self._capacities, default=0.0), 0.05) * 1.1
        bar_w = min(column * 0.62, 30)

        def column_x(i: int) -> float:
            return (i + (self.GAP if i >= 5 else 0) + 0.5) * column

        snapshot.append_color(faint, Graphene.Rect().init(0, base, width, 1))
        for i, load in enumerate(self._loads):
            cx = column_x(i)
            over = load > self._capacities[i] + 1e-9
            color = self._color("warning" if over else "accent")
            bar_h = max(2.0, load / scale * room) if load > 0 else 0.0
            if bar_h:
                rect = Graphene.Rect().init(cx - bar_w / 2, base - bar_h, bar_w, bar_h)
                rounded = Gsk.RoundedRect()
                rounded.init_from_rect(rect, 4)
                snapshot.push_rounded_clip(rounded)
                snapshot.append_color(color, rect)
                snapshot.pop()
            marker_y = base - self._capacities[i] / scale * room
            snapshot.append_color(
                fg, Graphene.Rect().init(cx - bar_w / 2 - 3, round(marker_y) - 1, bar_w + 6, 2))
            self._text(snapshot, f"{round(load * 100)}%", cx, base + 5, 0.85, fg, bold=True)
            self._text(snapshot, names[i], cx, base + 5 + row_h, 0.85, dim)
        for group, label in enumerate(self._hands):
            first = group * 5
            cx = (column_x(first) + column_x(first + 4)) / 2
            self._text(snapshot, label, cx, base + 5 + 2 * row_h, 0.85, dim, bold=True)


FingerLoadChart.set_css_name("fingerloadchart")
