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
        # 80 % of the mock board's real width (14.4 u × 19.05 mm) at 4.3 px/mm.
        self.assertAlmostEqual(width, 0.8 * 14.4 * 19.05 * 4.3, delta=2)
        self.assertEqual(state["margin"]["left"], (3440 - width) // 2)

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
