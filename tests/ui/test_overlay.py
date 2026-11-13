"""OverlayController with the mock keyboard. Surfaces are built with a fake
gtk4-layer-shell and never put on screen: fade_in/fade_out are replaced by flags."""

from __future__ import annotations

import unittest

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402,F401

from vibemapp.mock_service import MockKeyboardService  # noqa: E402
from vibemapp.ui.settings import MemorySettings  # noqa: E402

from .shot_util import FakeLayerShell  # noqa: E402
from .test_mock_service import spin  # noqa: E402

# Importing Gtk already tried to open the display; Adw.init() raises without one.
HAVE_DISPLAY = Gdk.Display.get_default() is not None
if HAVE_DISPLAY:
    Adw.init()


@unittest.skipUnless(HAVE_DISPLAY, "needs a display")
class OverlayControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        from vibemapp.ui import overlay

        self.overlay = overlay
        self._orig = (overlay.OverlaySurface.fade_in, overlay.OverlaySurface.fade_out)

        def fade_in(surface):
            surface._showing = True

        def fade_out(surface):
            surface._showing = False

        overlay.OverlaySurface.fade_in = fade_in
        overlay.OverlaySurface.fade_out = fade_out
        self.svc = MockKeyboardService(speed=50, autostart=False)
        self.settings = MemorySettings()
        self.ls = FakeLayerShell()
        self.ctl = overlay.OverlayController(self.svc, self.settings, layer_shell=self.ls)
        self.ctl.resolve_monitor = lambda: (None, overlay.MonitorInfo("DP-1", 3440, 1440, 800))

    def tearDown(self) -> None:
        self.ctl.destroy()
        self.overlay.OverlaySurface.fade_in, self.overlay.OverlaySurface.fade_out = self._orig
        self.svc.shutdown()

    def connect_keyboard(self) -> None:
        self.svc.simulate_plug()
        self.assertTrue(spin(lambda: self.svc.get_connected_device() is not None))
        self.svc.load_layout_async(self.svc.get_connected_device(), None, lambda *_a: None)
        self.assertTrue(spin(lambda: self.svc.get_layout() is not None))

    def showing(self) -> list[bool]:
        return [s.showing for s in self.ctl.surfaces]

    def test_selected_layer_shows_while_active(self) -> None:
        self.settings.set_value("overlay-layers", GLib.Variant("ai", [1]))
        self.connect_keyboard()
        self.assertFalse(self.ctl.shown)
        self.svc.simulate_key(48, True)           # hold the layer key...
        self.svc.simulate_layer(1)
        self.assertTrue(self.ctl.shown)
        self.assertEqual(self.showing(), [True])
        surface = self.ctl.surfaces[0]
        self.assertEqual(surface.view.get_layer().title, "Symbols")
        self.assertIn("Symbols", surface.pill.get_label())
        self.assertEqual(surface.view._press.keys(), {48})   # held key is marked
        self.svc.simulate_key(48, False)
        self.svc.simulate_layer(0)
        self.assertFalse(self.ctl.shown)
        self.assertEqual(self.showing(), [False])

    def test_mode_half_spacing_opacity_and_disconnect(self) -> None:
        self.connect_keyboard()
        self.settings.set_string("overlay-mode", "always")
        self.assertTrue(self.ctl.shown)
        self.assertEqual(len(self.ctl.surfaces), 1)
        self.settings.set_int("overlay-half-spacing", 100)
        self.assertEqual(len(self.ctl.surfaces), 2)
        self.assertEqual(self.showing(), [True, True])
        self.assertTrue(self.ctl.surfaces[0].pill.get_visible())
        self.assertFalse(self.ctl.surfaces[1].pill.get_visible())
        left, right = (self.ls.state(s) for s in self.ctl.surfaces)
        self.assertEqual(left["margin"]["left"], 24)
        self.assertEqual(right["margin"]["left"] + self.ctl.surfaces[1].view._fixed_size[0],
                         3440 - 24)
        self.settings.set_int("overlay-opacity", 40)
        self.assertAlmostEqual(self.ctl.surfaces[1].view._rest_opacity, 0.4)
        self.svc.simulate_unplug()
        self.assertFalse(self.ctl.shown)
        self.settings.set_string("overlay-mode", "off")
        self.assertFalse(self.ctl.shown)

    def test_layer_shell_setup_and_physical_size(self) -> None:
        self.connect_keyboard()
        self.settings.set_string("overlay-mode", "always")
        state = self.ls.state(self.ctl.surfaces[0])
        self.assertEqual(state["namespace"], "vibemapp-overlay")
        self.assertEqual(state["layer"], FakeLayerShell.Layer.OVERLAY)
        self.assertEqual(state["keyboard_mode"], FakeLayerShell.KeyboardMode.NONE)
        self.assertEqual(state["exclusive_zone"], -1)
        self.assertEqual(state["anchor"], {"top": False, "bottom": True, "left": True,
                                           "right": False})
        width = self.ctl.surfaces[0].view._fixed_size[0]
        # 80 % of the Voyager's real width (16 u × 19.05 mm) at 4.3 px/mm.
        self.assertAlmostEqual(width, 0.8 * 16 * 19.05 * 4.3, delta=2)
        self.assertAlmostEqual(state["margin"]["left"], (3440 - width) / 2, delta=1)

    def test_preview_without_keyboard(self) -> None:
        self.ctl.props.preview = True
        self.assertTrue(self.ctl.shown)
        self.assertEqual(self.ctl.surfaces[0].pill.get_label(), "No Keyboard Connected")
        self.ctl.props.preview = False
        self.assertFalse(self.ctl.shown)

    def test_idle_timeout_hides_until_next_key(self) -> None:
        self.connect_keyboard()
        self.settings.set_int("overlay-idle-seconds", 1)
        self.settings.set_string("overlay-mode", "always")
        self.assertTrue(self.ctl.shown)
        self.assertTrue(spin(lambda: not self.ctl.shown, timeout=3.0))
        self.svc.simulate_key(3, True)
        self.assertTrue(self.ctl.shown)

    def test_surfaces_are_click_through_and_unfocusable(self) -> None:
        self.connect_keyboard()
        self.settings.set_string("overlay-mode", "always")
        surface = self.ctl.surfaces[0]
        self.assertFalse(surface.get_focusable())
        self.assertFalse(surface.get_can_target())
        self.assertIsNone(surface.get_application())
        self.assertFalse(surface.view.get_has_tooltip())
        self.assertTrue(surface.view.is_overlay)


    def test_hiding_parks_the_surface_instead_of_unmapping(self) -> None:
        # Unmapping a layer surface makes Hyprland move keyboard focus away from
        # another app's panel, so hiding only fades out and drops to BACKGROUND.
        self.connect_keyboard()
        self.settings.set_string("overlay-mode", "always")
        surface = self.ctl.surfaces[0]
        real_in, real_out = self._orig
        hidden: list[bool] = []
        surface.set_visible = lambda visible: hidden.append(not visible)  # type: ignore[method-assign]
        surface.present = lambda: None                                   # type: ignore[method-assign]
        surface._showing = True
        real_out(surface)
        self.assertEqual(self.ls.state(surface)["layer"], FakeLayerShell.Layer.BACKGROUND)
        self.assertEqual(surface.box.get_opacity(), 0.0)
        real_in(surface)
        self.assertEqual(self.ls.state(surface)["layer"], FakeLayerShell.Layer.OVERLAY)
        self.assertNotIn(True, hidden)            # never set_visible(False)

    def real_surface(self):
        """The first surface with the real fade_in/fade_out, never presented."""
        surface = self.ctl.surfaces[0]
        hidden: list[bool] = []
        surface.set_visible = lambda visible: hidden.append(not visible)  # type: ignore[method-assign]
        surface.present = lambda: None                                   # type: ignore[method-assign]
        surface.get_mapped = lambda: True                                # type: ignore[method-assign]
        surface._showing = False
        # Adw finishes animations on unmapped widgets at once; record instead.
        surface.animations = []
        surface._animate = lambda target, ms: surface.animations.append((target, ms))
        return surface, hidden

    def test_fade_setting_applies_live(self) -> None:
        self.connect_keyboard()
        self.settings.set_string("overlay-mode", "always")
        surface = self.ctl.surfaces[0]
        self.assertEqual((surface._fade_in_ms, surface._fade_out_ms), (200, 150))
        self.settings.set_int("overlay-fade-ms", 600)
        self.assertEqual((surface._fade_in_ms, surface._fade_out_ms), (600, 450))
        self.settings.set_int("overlay-half-spacing", 100)     # rebuilt surfaces too
        self.assertEqual({(s._fade_in_ms, s._fade_out_ms) for s in self.ctl.surfaces},
                         {(600, 450)})

    def test_animated_fade_uses_the_durations(self) -> None:
        self.connect_keyboard()
        self.settings.set_int("overlay-fade-ms", 400)
        self.settings.set_string("overlay-mode", "always")
        surface, _hidden = self.real_surface()
        real_in, real_out = self._orig
        real_in(surface)
        self.assertEqual(self.ls.state(surface)["layer"], FakeLayerShell.Layer.OVERLAY)
        real_out(surface)
        self.assertEqual(surface.animations, [(1.0, 400), (0.0, 300)])

    def test_zero_fade_shows_and_hides_at_once_but_still_parks(self) -> None:
        self.connect_keyboard()
        self.settings.set_int("overlay-fade-ms", 0)
        self.settings.set_string("overlay-mode", "always")
        surface, hidden = self.real_surface()
        real_in, real_out = self._orig
        surface.box.set_opacity(0.0)
        real_in(surface)
        self.assertEqual(surface.box.get_opacity(), 1.0)
        self.assertEqual(self.ls.state(surface)["layer"], FakeLayerShell.Layer.OVERLAY)
        real_out(surface)
        self.assertEqual(surface.animations, [])              # no animation either way
        self.assertEqual(surface.box.get_opacity(), 0.0)
        self.assertEqual(self.ls.state(surface)["layer"], FakeLayerShell.Layer.BACKGROUND)
        self.assertNotIn(True, hidden)                        # never unmapped

    def test_hidden_surface_switches_layer_without_crossfade(self) -> None:
        # Hidden surfaces stay mapped; crossfading on show would flash the old layer.
        self.settings.set_value("overlay-layers", GLib.Variant("ai", [1, 2]))
        self.connect_keyboard()
        self.svc.simulate_layer(1)
        self.svc.simulate_layer(0)                # hidden, still holds layer 1
        surface = self.ctl.surfaces[0]
        calls: list[bool] = []
        real = surface.view.set_layer

        def spy(layer, animate=True, layers=None):
            calls.append(animate)
            real(layer, animate=animate, layers=layers)
        surface.view.set_layer = spy              # type: ignore[method-assign]
        self.svc.simulate_layer(2)
        self.assertEqual(surface.view.get_layer().title, self.svc.get_layout().layers[2].title)
        self.assertEqual(calls, [False])
        self.svc.simulate_layer(1)                # visible: layer 2 → 1 may crossfade
        self.assertEqual(calls[-1], True)


@unittest.skipUnless(HAVE_DISPLAY, "needs a display")
class PointerPadTests(unittest.TestCase):
    """The layout's Navigators beside their halves' inner edges (``overlay-pointer``)."""

    setUp = OverlayControllerTests.setUp
    tearDown = OverlayControllerTests.tearDown
    connect_keyboard = OverlayControllerTests.connect_keyboard

    def use_navigators(self, *navigators, known: bool = True) -> None:
        """The layout as Oryx describes it, whatever the mock fills in."""
        from dataclasses import replace
        layout = replace(self.svc.get_layout(), navigators=tuple(navigators),
                         navigators_known=known)
        self.svc.get_layout = lambda: layout
        self.svc.emit("layout-loaded", layout)

    def test_pad_only_with_the_setting_and_a_device(self) -> None:
        from vibemapp.models import PointerAction, PointerActivity, PointerKind
        self.connect_keyboard()
        self.svc.simulate_pointer_kind(PointerKind.TRACKPAD)
        self.use_navigators(known=False)          # a layout cached before 1.10
        self.settings.set_string("overlay-mode", "always")
        self.assertTrue(self.ctl.shown)
        self.assertFalse(any(s.pads for s in self.ctl.surfaces))
        self.assertFalse(self.svc.props.pointer_tracking)
        self.settings.set_boolean("overlay-pointer", True)
        pads = [p for s in self.ctl.surfaces for p in s.pads]
        self.assertEqual(len(pads), 1)            # the right half, by default
        self.assertTrue(self.svc.props.pointer_tracking)       # shown and on
        self.svc.emit("pointer-activity", PointerActivity(
            PointerKind.TRACKPAD, PointerAction.TOUCH, fingers=1, contacts=((0.5, 0.5),)))
        self.assertEqual(pads[0].model.contacts, ((0.0, 0.0),))
        self.settings.set_string("overlay-mode", "off")         # never shows it by itself
        self.assertFalse(self.ctl.shown)
        self.assertFalse(self.svc.props.pointer_tracking)
        self.settings.set_string("overlay-mode", "always")
        self.svc.simulate_pointer_kind(None)
        self.assertFalse(any(s.pads for s in self.ctl.surfaces))

    def test_no_pad_when_the_layout_says_there_is_none(self) -> None:
        from vibemapp.models import PointerKind
        self.connect_keyboard()
        self.svc.simulate_pointer_kind(PointerKind.TRACKPAD)
        self.use_navigators()                     # Oryx: no Navigator
        self.settings.set_boolean("overlay-pointer", True)
        self.settings.set_string("overlay-mode", "always")
        self.assertFalse(any(s.pads for s in self.ctl.surfaces))

    def test_each_navigator_on_its_half_lit_by_its_own_kind(self) -> None:
        from vibemapp.models import Navigator, PointerAction, PointerActivity, PointerKind
        self.connect_keyboard()
        self.use_navigators(Navigator("left", PointerKind.TRACKBALL),
                            Navigator("right", PointerKind.TRACKPAD))
        self.settings.set_boolean("overlay-pointer", True)
        self.settings.set_int("overlay-half-spacing", 50)
        self.settings.set_string("overlay-mode", "always")
        surfaces = self.ctl.surfaces
        self.assertEqual(len(surfaces), 2)
        self.assertEqual([[p.model.kind for p in s.pads] for s in surfaces],
                         [[PointerKind.TRACKBALL], [PointerKind.TRACKPAD]])
        self.assert_pad_inside(surfaces[0], "left")
        self.assert_pad_inside(surfaces[1], "right")
        ball, pad = surfaces[0].pads[0], surfaces[1].pads[0]
        self.svc.emit("pointer-activity", PointerActivity(
            PointerKind.TRACKPAD, PointerAction.TOUCH, fingers=1, contacts=((0.5, 0.5),)))
        self.assertEqual(len(pad.model.contacts), 1)
        self.assertEqual(ball.model.lit(0), 0.0)           # not the trackball's
        self.assertEqual(ball.model.contacts, ())

    def test_joined_board_carries_both_pads_between_the_halves(self) -> None:
        from vibemapp.models import Navigator, PointerKind
        self.connect_keyboard()
        self.use_navigators(Navigator("left", PointerKind.TRACKBALL),
                            Navigator("right", PointerKind.TRACKPAD))
        self.settings.set_boolean("overlay-pointer", True)
        self.settings.set_string("overlay-mode", "always")
        surfaces = self.ctl.surfaces
        self.assertEqual(len(surfaces), 1)
        left, right = surfaces[0].pads
        view = surfaces[0].view
        # Both in the gap between the halves, side by side, inside the board.
        self.assertGreater(left.get_margin_start(), view.get_margin_start())
        self.assertLessEqual(left.get_margin_start() + left._size, right.get_margin_start())
        self.assertLess(right.get_margin_start() + right._size,
                        view.get_margin_start() + view._fixed_size[0])

    def assert_pad_inside(self, surface, side: str) -> None:
        """The pad sits on the half's inner side (the surface's edge facing the
        other half), in the lower part, inside the surface."""
        view, pad = surface.view, surface.pads[0]
        size = pad._size
        board_w, board_h = view._fixed_size
        surface_w = view.get_margin_start() + board_w + view.get_margin_end()
        surface_h = view.get_margin_top() + board_h + view.get_margin_bottom()
        self.assertGreaterEqual(pad.get_margin_top(), 0)
        self.assertLessEqual(pad.get_margin_top() + size, surface_h)
        if side == "right":                  # inner edge: the surface's left
            self.assertEqual(pad.get_margin_start(), 0)
            self.assertGreater(view.get_margin_start(), 0)
            # The layer pill lines up with the keys, not the pad.
            self.assertEqual(surface.pill.get_margin_start(), view.get_margin_start())
        else:
            self.assertEqual(pad.get_margin_start() + size, surface_w)
            self.assertEqual(view.get_margin_start(), 0)
            self.assertGreater(view.get_margin_end(), 0)
        # Above the thumb cluster: its bottom in the board's middle part.
        bottom = pad.get_margin_top() + size - view.get_margin_top()
        self.assertGreater(bottom, board_h * 0.5)
        self.assertLess(bottom, board_h * 0.8)


@unittest.skipUnless(HAVE_DISPLAY, "needs a display")
class ClearanceTests(unittest.TestCase):
    """Where the pills sit: past the overlay whenever it can show."""

    setUp = OverlayControllerTests.setUp
    tearDown = OverlayControllerTests.tearDown
    connect_keyboard = OverlayControllerTests.connect_keyboard

    def test_stable_while_the_overlay_comes_and_goes(self) -> None:
        self.settings.set_value("overlay-layers", GLib.Variant("ai", [3]))
        self.connect_keyboard()
        self.assertFalse(self.ctl.shown)
        hidden = self.ctl.edge_clearance()
        self.assertGreater(hidden, 24 + 100)            # margin plus a whole board
        self.svc.simulate_layer(3)                      # e.g. the auto mouse layer
        self.assertTrue(self.ctl.shown)
        self.assertEqual(self.ctl.edge_clearance(), hidden)
        self.svc.simulate_layer(0)
        self.assertFalse(self.ctl.shown)
        self.assertEqual(self.ctl.edge_clearance(), hidden)

    def test_none_when_it_cannot_show(self) -> None:
        self.connect_keyboard()
        self.settings.set_value("overlay-layers", GLib.Variant("ai", []))
        self.assertEqual(self.ctl.edge_clearance(), 0)  # no layer selected
        self.settings.set_string("overlay-mode", "always")
        self.assertGreater(self.ctl.edge_clearance(), 0)
        self.settings.set_string("overlay-mode", "off")
        self.assertEqual(self.ctl.edge_clearance(), 0)
        self.ctl.props.preview = True                   # previewing shows it
        self.assertGreater(self.ctl.edge_clearance(), 0)


@unittest.skipUnless(HAVE_DISPLAY, "needs a display")
class UnavailableTests(unittest.TestCase):
    def test_no_layer_shell_means_no_surfaces(self) -> None:
        from vibemapp.ui import overlay

        svc = MockKeyboardService(speed=50, autostart=False)
        settings = MemorySettings()
        ctl = overlay.OverlayController(svc, settings, layer_shell=None)
        try:
            self.assertFalse(ctl.available)
            settings.set_string("overlay-mode", "always")
            ctl.props.preview = True
            self.assertFalse(ctl.shown)
            self.assertEqual(ctl.surfaces, [])
        finally:
            ctl.destroy()
            svc.shutdown()

    def test_env_override(self) -> None:
        import os
        from unittest import mock

        from vibemapp.ui import overlay

        with mock.patch.dict(os.environ, {overlay.NO_LAYER_SHELL_ENV: "1"}):
            self.assertFalse(overlay.layer_shell_available())


if __name__ == "__main__":
    unittest.main()
