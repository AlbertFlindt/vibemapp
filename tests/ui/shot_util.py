"""Shared bits for the screenshot dev tools in this directory (not tests).

Imported as a top-level module by ``tooltip_shot.py`` and ``tray_menu_shot.py``
(running a script puts its directory on ``sys.path``). Import it after
``gi.require_version`` has been called for Gtk 4.
"""

from __future__ import annotations

from gi.repository import Graphene, Gtk


class TooltipBubble(Gtk.Widget):
    """Looks like GtkTooltipWindow: CSS node ``tooltip.background`` > box > label,
    so the libadwaita tooltip style applies. GTK's own tooltip window is private
    and only shows under a real pointer, so the tools draw this instead."""

    __gtype_name__ = "VmShotTooltipBubble"

    def __init__(self, text: str | None = None, markup: str | None = None) -> None:
        super().__init__(css_classes=["background"])
        self.set_layout_manager(Gtk.BinLayout())
        box = Gtk.Box(spacing=6)
        label = Gtk.Label(wrap=True, max_width_chars=70, xalign=0)
        if markup is not None:
            label.set_markup(markup)
        else:
            label.set_text(text or "")
        box.append(label)
        box.set_parent(self)

    def do_dispose(self) -> None:
        child = self.get_first_child()
        if child is not None:
            child.unparent()


TooltipBubble.set_css_name("tooltip")


def save_widget_png(widget: Gtk.Widget, path: str) -> tuple[int, int]:
    """Render a mapped, drawn widget (at its allocated size) to a PNG."""
    # Not get_width()/get_height(): those are the content box. The paintable's
    # intrinsic size includes CSS padding and border, and snapshotting it at a
    # smaller size scales the whole widget down.
    paintable = Gtk.WidgetPaintable.new(widget)
    w, h = paintable.get_intrinsic_width(), paintable.get_intrinsic_height()
    snapshot = Gtk.Snapshot()
    paintable.snapshot(snapshot, w, h)
    node = snapshot.to_node()
    if node is None:
        raise RuntimeError("widget rendered nothing (not mapped or not drawn yet)")
    renderer = widget.get_native().get_renderer()
    texture = renderer.render_texture(node, Graphene.Rect().init(0, 0, w, h))
    texture.save_to_png(path)
    return w, h
