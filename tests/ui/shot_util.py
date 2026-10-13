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


class FakeLayerShell:
    """Stand-in for the ``Gtk4LayerShell`` module: records what the overlay
    asks of each window instead of turning it into a layer surface. Lets tests
    and ``screenshot.py --overlay`` build real overlay surfaces off screen."""

    class Edge:
        LEFT, RIGHT, TOP, BOTTOM = "left", "right", "top", "bottom"

    class Layer:
        BACKGROUND, BOTTOM, TOP, OVERLAY = range(4)

    class KeyboardMode:
        NONE, EXCLUSIVE, ON_DEMAND = range(3)

    def __init__(self) -> None:
        self.windows: dict[int, dict] = {}

    def _state(self, window) -> dict:
        return self.windows.setdefault(id(window), {"anchor": {}, "margin": {}})

    def init_for_window(self, window) -> None:
        self._state(window)["init"] = True

    def set_layer(self, window, layer) -> None:
        self._state(window)["layer"] = layer

    def set_namespace(self, window, namespace) -> None:
        self._state(window)["namespace"] = namespace

    def set_keyboard_mode(self, window, mode) -> None:
        self._state(window)["keyboard_mode"] = mode

    def set_exclusive_zone(self, window, zone) -> None:
        self._state(window)["exclusive_zone"] = zone

    def set_anchor(self, window, edge, anchored) -> None:
        self._state(window)["anchor"][edge] = bool(anchored)

    def set_margin(self, window, edge, margin) -> None:
        self._state(window)["margin"][edge] = int(margin)

    def set_monitor(self, window, monitor) -> None:
        self._state(window)["monitor"] = monitor

    def state(self, window) -> dict:
        return self.windows[id(window)]
