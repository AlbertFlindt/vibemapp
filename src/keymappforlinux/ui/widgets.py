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
