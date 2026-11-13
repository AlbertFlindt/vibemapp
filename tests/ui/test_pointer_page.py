"""Trackpad page and its sidebar item: title, grayed out without a device,
settings both ways, the live view and pointer_tracking."""

from __future__ import annotations

import unittest

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, GObject, Gtk  # noqa: E402

from vibemapp.models import PointerAction, PointerActivity, PointerKind  # noqa: E402
from vibemapp.ui.settings import MemorySettings  # noqa: E402

from .test_mock_service import spin  # noqa: E402

HAVE_DISPLAY = Gdk.Display.get_default() is not None
if HAVE_DISPLAY:
    Adw.init()


class FakeService(GObject.Object):
    __gsignals__ = {
        "pointer-changed": (GObject.SignalFlags.RUN_LAST, None, ()),
        "pointer-activity": (GObject.SignalFlags.RUN_LAST, None, (object,)),
    }
    pointer_tracking = GObject.Property(type=bool, default=False, nick="pointer-tracking")

    def __init__(self) -> None:
        super().__init__()
        self.kind: PointerKind | None = None

    def get_pointer_kind(self) -> PointerKind | None:
        return self.kind

    def plug(self, kind: PointerKind | None) -> None:
        self.kind = kind
        self.emit("pointer-changed")


class ToggleTests(unittest.TestCase):
    def test_toggle_action_keeps_order_and_unknown(self) -> None:
        from vibemapp.ui.pointer_page import toggle_action
        self.assertEqual(toggle_action(["scroll", "click"], "tap", True), ["click", "tap", "scroll"])
        self.assertEqual(toggle_action(["click", "x"], "click", False), ["x"])


@unittest.skipUnless(HAVE_DISPLAY, "needs a display")
class PageTests(unittest.TestCase):
    def setUp(self) -> None:
        from vibemapp.ui import pointer_page
        self.mod = pointer_page
        self._available = pointer_page.layer_shell_available
        pointer_page.layer_shell_available = lambda: True
        self.service = FakeService()
        self.settings = MemorySettings()
        self.page = pointer_page.PointerPage()
        self.page.setup(self.service, self.settings, lambda _t: None)

    def tearDown(self) -> None:
        self.page.teardown()
        self.mod.layer_shell_available = self._available

    def test_title_and_state_follow_the_device(self) -> None:
        self.assertEqual(self.page.stack.get_visible_child_name(), "none")
        self.assertEqual(self.page.window_title.get_title(), "Trackpad & Trackball")
        self.service.plug(PointerKind.TRACKBALL)
        self.assertEqual(self.page.stack.get_visible_child_name(), "settings")
        self.assertEqual(self.page.window_title.get_title(), "Trackball")
        self.assertEqual(self.page.live_label.get_label(), "Move the trackball to try it")

    def test_settings_both_ways(self) -> None:
        page, settings = self.page, self.settings
        page.pill_row.set_active(True)
        self.assertTrue(settings.get_boolean("pointer-pill"))
        page.scroll_row.set_active(False)
        self.assertEqual(settings.get_value("pointer-pill-actions").unpack(),
                         ["click", "tap", "move"])
        settings.set_value("pointer-pill-actions", GLib.Variant("as", ["scroll"]))
        self.assertEqual([r.get_active() for r in (page.click_row, page.tap_row,
                                                   page.scroll_row, page.move_row)],
                         [False, False, True, False])
        page.linger_row.set_value(1200)
        self.assertEqual(settings.get_int("pointer-pill-linger-ms"), 1200)
        settings.set_int("pointer-pill-fade-ms", 0)
        self.assertEqual(int(page.fade_row.get_value()), 0)
        page.overlay_row.set_active(True)
        self.assertTrue(settings.get_boolean("overlay-pointer"))
        self.assertEqual(page.side_row.get_selected(), 0)            # right
        page.side_row.set_selected(1)
        self.assertEqual(settings.get_string("pointer-side"), "left")
        settings.set_string("pointer-side", "right")
        self.assertEqual(page.side_row.get_selected(), 0)

    def test_live_view_only_while_shown(self) -> None:
        self.service.plug(PointerKind.TRACKPAD)
        self.assertFalse(self.service.props.pointer_tracking)   # page not on screen
        self.page.get_mapped = lambda: True
        self.page._sync_tracking()
        self.assertTrue(self.service.props.pointer_tracking)
        self.service.emit("pointer-activity", PointerActivity(
            PointerKind.TRACKPAD, PointerAction.SCROLL, fingers=2, direction="down",
            contacts=((0.4, 0.5), (0.6, 0.5))))
        self.assertEqual(self.page.live_label.get_label(), "Two-Finger Scroll ↓")
        self.assertEqual(len(self.page.live_view.model.contacts), 2)
        self.service.emit("pointer-activity", PointerActivity(PointerKind.TRACKPAD,
                                                               PointerAction.IDLE))
        self.assertEqual(self.page.live_label.get_label(), "Touch the trackpad to try it")
        self.service.plug(None)
        self.assertFalse(self.service.props.pointer_tracking)

    def test_live_card_height_never_changes(self) -> None:
        """Arrows come from a taller fallback font and long texts could wrap;
        the card below the drawing must not move with the gesture."""
        from vibemapp.ui.pointer_activity import idle_hint, live_label
        P, K = PointerAction, PointerKind
        texts = {idle_hint(K.TRACKPAD), idle_hint(K.TRACKBALL)}
        for fingers in range(5):
            for action in (P.TOUCH, P.MOVE, P.TAP):
                texts.add(live_label(PointerActivity(K.TRACKPAD, action, fingers=fingers)))
            for direction in ("", "up", "down", "left", "right"):
                for kind in K:
                    texts.add(live_label(PointerActivity(kind, P.SCROLL, fingers=fingers,
                                                         direction=direction)))
        for button in (1, 2, 3):
            texts.add(live_label(PointerActivity(K.TRACKPAD, P.CLICK, button=button)))
        texts.add(live_label(PointerActivity(K.TRACKBALL, P.MOVE)))
        texts.discard(None)
        self.assertIn("Two-Finger Scroll ↓", texts)
        self.service.plug(K.TRACKPAD)
        card = self.page.live_caption.get_parent()
        for width in (400, 260, 200):
            heights = {}
            for text in sorted(texts):
                self.page.live_label.set_label(text)
                heights[text] = (self.page.live_caption.measure(
                    Gtk.Orientation.VERTICAL, width - 24)[1],
                    card.measure(Gtk.Orientation.VERTICAL, width)[1])
            with self.subTest(width=width):
                self.assertEqual(len(set(heights.values())), 1, heights)

    def test_unavailable_without_layer_shell(self) -> None:
        self.mod.layer_shell_available = lambda: False
        page = self.mod.PointerPage()
        service = FakeService()
        service.kind = PointerKind.TRACKPAD
        page.setup(service, MemorySettings(), lambda _t: None)
        self.assertEqual(page.stack.get_visible_child_name(), "unavailable")
        page.teardown()


@unittest.skipUnless(HAVE_DISPLAY, "needs a display")
class SidebarTests(unittest.TestCase):
    def test_item_grayed_out_and_page_left_when_device_goes(self) -> None:
        from vibemapp.mock_service import MockKeyboardService
        from vibemapp.ui.controller import DeviceController
        from vibemapp.ui.window import Window
        svc = MockKeyboardService(autostart=False)
        svc.simulate_pointer_kind(None)
        settings = MemorySettings()
        app = Adw.Application(application_id="io.github.albertflindt.Vibemapp.Test")
        app.register(None)
        controller = DeviceController(svc, settings)
        win = Window(app, svc, controller, settings)
        try:
            item = win.pointer_item
            self.assertFalse(item.get_enabled())
            self.assertIn("trackball", item.get_tooltip())
            win.show_page("pointer")
            self.assertEqual(win.content_stack.get_visible_child_name(), "layout")
            svc.simulate_plug()
            self.assertTrue(spin(lambda: svc.get_connected_device() is not None))
            svc.simulate_pointer_kind(PointerKind.TRACKPAD)
            self.assertTrue(item.get_enabled())
            self.assertEqual(item.get_title(), "Trackpad")
            win.show_page("pointer")
            self.assertEqual(win.content_page.get_title(), "Trackpad")
            svc.simulate_pointer_kind(None)
            self.assertEqual(win.content_stack.get_visible_child_name(), "layout")
        finally:
            win.teardown()
            win.destroy()
            svc.shutdown()


if __name__ == "__main__":
    unittest.main()
