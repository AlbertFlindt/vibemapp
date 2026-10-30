"""HoldPillController with the mock keyboard and a fake gtk4-layer-shell. The
pill surface is never put on screen: ``flash`` is replaced by a recorder."""

from __future__ import annotations

import unittest

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib  # noqa: E402

from vibemapp.mock_service import MockKeyboardService  # noqa: E402
from vibemapp.ui.settings import MemorySettings  # noqa: E402

from .shot_util import FakeLayerShell  # noqa: E402
from .test_mock_service import spin  # noqa: E402

HAVE_DISPLAY = Gdk.Display.get_default() is not None
if HAVE_DISPLAY:
    Adw.init()


class SizeTests(unittest.TestCase):
    def test_key_width(self) -> None:
        from vibemapp.ui.hold_pill import key_width_units
        self.assertEqual(key_width_units("C"), 1.0)
        self.assertEqual(key_width_units("Esc"), 1.25)
        self.assertEqual(key_width_units("Ctrl+C"), 1.75)


class MarginTests(unittest.TestCase):
    def test_edge_margin(self) -> None:
        from vibemapp.ui.hold_pill import GAP_PX, edge_margin
        self.assertEqual(edge_margin(1440, 0), 144)
        self.assertEqual(edge_margin(1440, 100), 144)        # overlay is nearer the edge
        self.assertEqual(edge_margin(1440, 400), 400 + GAP_PX)


@unittest.skipUnless(HAVE_DISPLAY, "needs a display")
class ControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        from vibemapp.ui import hold_pill

        self.hold_pill = hold_pill
        self._flash = hold_pill.HoldPillSurface.flash
        self.flashes: list[str] = []
        hold_pill.HoldPillSurface.flash = (
            lambda _s, text, unit, linger=True: self.flashes.append(text if linger else f"{text}…"))
        self.svc = MockKeyboardService(speed=50, autostart=False)
        self.settings = MemorySettings()
        self.ls = FakeLayerShell()
        self.clearance = 0
        self.style = hold_pill.PillStyle(unit=40.0)
        self.ctl = hold_pill.HoldPillController(
            self.svc, self.settings, self.ls, monitor=lambda: (None, 1000),
            clearance=lambda: self.clearance, style=lambda: self.style)
        self.svc.simulate_plug()
        self.assertTrue(spin(lambda: self.svc.get_connected_device() is not None))
        self.svc.load_layout_async(self.svc.get_connected_device(), None, lambda *_a: None)
        self.assertTrue(spin(lambda: self.svc.get_layout() is not None))
        layer = self.svc.get_layout().layers[0]
        self.c_key = next(k.index for k in layer.keys if k.hold_combo == "Ctrl+C")

    def tearDown(self) -> None:
        self.ctl.destroy()
        self.hold_pill.HoldPillSurface.flash = self._flash
        self.svc.shutdown()

    @staticmethod
    def wait(ms: int) -> None:
        deadline = GLib.get_monotonic_time() + ms * 1000
        spin(lambda: GLib.get_monotonic_time() > deadline, timeout=2)

    def hold_c(self, ms: int = 300) -> None:
        self.svc.simulate_key(self.c_key, True)
        self.wait(ms)
        self.svc.simulate_key(self.c_key, False)

    def test_off_by_default(self) -> None:
        self.hold_c()
        self.assertEqual(self.flashes, [])
        self.assertIsNone(self.ctl.surface)

    def test_hold_shows_pill_tap_does_not(self) -> None:
        self.settings.set_boolean("hold-pill", True)
        self.hold_c(ms=50)
        self.assertEqual(self.flashes, [])
        self.wait(250)                       # past the tap's dance
        self.hold_c()
        self.assertEqual(self.flashes, ["Ctrl+C"])
        state = self.ls.state(self.ctl.surface)
        self.assertEqual(state["namespace"], self.hold_pill.NAMESPACE)
        self.assertEqual(state["anchor"], {"top": False, "left": False, "right": False,
                                           "bottom": True})
        self.assertEqual(state["margin"]["bottom"], 100)

    def test_excluded_key_and_clearance(self) -> None:
        self.settings.set_boolean("hold-pill", True)
        self.settings.set_value("hold-pill-excluded", GLib.Variant("as", [f"0:{self.c_key}"]))
        self.hold_c()
        self.assertEqual(self.flashes, [])
        self.settings.set_value("hold-pill-excluded", GLib.Variant("as", []))
        self.clearance = 300
        self.hold_c()
        self.assertEqual(self.flashes, ["Ctrl+C"])
        self.assertEqual(self.ls.state(self.ctl.surface)["margin"]["bottom"],
                         300 + self.hold_pill.GAP_PX)

    def test_key_looks_like_an_overlay_key(self) -> None:
        from vibemapp.ui.hold_pill import HoldPillSurface
        surface = HoldPillSurface(self.ls)
        surface.set_key("Ctrl+C", 40.0)
        self.assertTrue(surface.view.is_overlay)
        self.assertEqual(surface.view._fixed_size, (70, 40))
        self.assertEqual(surface.view._layer.keys[0].tap_label, "Ctrl+C")
        surface.destroy()

    def test_follows_overlay_appearance(self) -> None:
        self.settings.set_boolean("hold-pill", True)
        self.style = self.hold_pill.PillStyle(edge="top", unit=40.0, opacity=0.3,
                                              fade_in_ms=0, fade_out_ms=0)
        self.hold_c()
        surface = self.ctl.surface
        state = self.ls.state(surface)
        self.assertEqual((state["anchor"]["top"], state["anchor"]["bottom"]), (True, False))
        self.assertEqual((state["margin"]["top"], state["margin"]["bottom"]), (100, 0))
        self.assertEqual(surface.view._rest_opacity, 0.3)
        self.assertEqual((surface._fade_in_ms, surface._fade_out_ms), (0, 0))

    def test_held_modifier_stays_until_released(self) -> None:
        layer = self.svc.get_layout().layers[0]
        a_key = next(k.index for k in layer.keys if k.hold_modifier == "Ctrl")
        self.svc.simulate_key(a_key, True)
        self.wait(300)
        self.assertEqual(self.flashes, [])           # off by default
        self.svc.simulate_key(a_key, False)
        self.settings.set_boolean("modifier-pill", True)
        self.svc.simulate_key(a_key, True)
        self.wait(300)                               # past the mod-tap's term
        self.svc.simulate_key(self.c_key, True)
        self.svc.simulate_key(self.c_key, False)
        self.assertTrue(spin(lambda: len(self.flashes) == 2, timeout=2))
        self.assertEqual(self.flashes, ["Ctrl…", "Ctrl+C…"])   # no linger while held
        self.svc.simulate_key(a_key, False)
        self.assertTrue(spin(lambda: self.ctl.modifiers.text is None, timeout=2))

    def test_release_lingers_then_hides(self) -> None:
        from vibemapp.ui.hold_pill import HoldPillSurface
        surface = HoldPillSurface(self.ls)
        surface._showing = True                      # as after flash(linger=False)
        surface.release()
        self.assertNotEqual(surface._hide_id, 0)
        hide_id = surface._hide_id
        surface.release()                            # already on its way
        self.assertEqual(surface._hide_id, hide_id)
        surface.hide_now()
        surface.destroy()

    def test_no_layer_shell_does_nothing(self) -> None:
        ctl = self.hold_pill.HoldPillController(self.svc, self.settings, None,
                                                monitor=lambda: (None, 1000))
        self.settings.set_boolean("hold-pill", True)
        self.assertFalse(ctl.available)
        ctl.show("Ctrl+C")
        self.assertIsNone(ctl.surface)
        ctl.destroy()


if __name__ == "__main__":
    unittest.main()
