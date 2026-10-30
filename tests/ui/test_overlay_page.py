from __future__ import annotations

import unittest

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, GObject, Gtk  # noqa: E402

from vibemapp.models import KeyBinding, KeyGeometry, Layer, Layout  # noqa: E402
from vibemapp.ui import overlay_page as op  # noqa: E402
from vibemapp.ui.settings import MemorySettings  # noqa: E402


class ChoiceTests(unittest.TestCase):
    def test_round_trip_and_unknown_fallback(self) -> None:
        for choices in (op.MODES, op.ANCHORS):
            for i, value in enumerate(choices):
                self.assertEqual(op.choice_index(choices, value), i)
                self.assertEqual(op.choice_value(choices, i), value)
        self.assertEqual(op.choice_index(op.ANCHORS, "middle"), 0)
        self.assertEqual(op.choice_value(op.MODES, Gtk.INVALID_LIST_POSITION), "off")

    def test_choices_match_schema(self) -> None:
        import xml.etree.ElementTree as ET
        from pathlib import Path
        schema = ET.parse(Path(__file__).resolve().parents[2] / "data"
                          / "io.github.albertflindt.Vibemapp.gschema.xml").getroot()
        for key, choices in (("overlay-mode", op.MODES), ("overlay-anchor", op.ANCHORS)):
            node = next(k for k in schema.iter("key") if k.get("name") == key)
            self.assertEqual(tuple(c.get("value") for c in node.iter("choice")), choices)


class LayerListTests(unittest.TestCase):
    def test_toggle_keeps_unknown_layers_sorted_unique(self) -> None:
        self.assertEqual(op.toggle_layer([7, 2], 1, True), [1, 2, 7])
        self.assertEqual(op.toggle_layer([1, 2, 7], 2, False), [1, 7])
        self.assertEqual(op.toggle_layer([1], 1, True), [1])
        self.assertEqual(op.toggle_layer([], 3, False), [])

    def test_hint(self) -> None:
        self.assertTrue(op.needs_layer_hint("selected", [], None))
        self.assertFalse(op.needs_layer_hint("selected", [5], None))
        self.assertTrue(op.needs_layer_hint("selected", [5], [0, 1, 2]))
        self.assertFalse(op.needs_layer_hint("selected", [1, 5], [0, 1, 2]))
        self.assertFalse(op.needs_layer_hint("always", [], [0, 1]))
        self.assertFalse(op.needs_layer_hint("off", [], [0, 1]))


class HalvesTests(unittest.TestCase):
    def test_has_halves(self) -> None:
        self.assertTrue(op.has_halves(None))
        self.assertTrue(op.has_halves(Layout("v", "V", SPLIT, ())))
        self.assertFalse(op.has_halves(Layout("p", "P", ONE_PIECE, ())))


class MonitorTests(unittest.TestCase):
    def test_label(self) -> None:
        self.assertEqual(op.monitor_label("DP-1", "Dell U2720Q", None), "Dell U2720Q (DP-1)")
        self.assertEqual(op.monitor_label("DP-1", "Dell U2720Q (DP-1)", None),
                         "Dell U2720Q (DP-1)")
        self.assertEqual(op.monitor_label("HDMI-A-1", "", "U2720Q", "Dell"),
                         "Dell U2720Q (HDMI-A-1)")
        self.assertEqual(op.monitor_label("eDP-1", None, None), "eDP-1")

    def test_entries(self) -> None:
        entries = op.monitor_entries([("DP-1", "A (DP-1)"), ("DP-1", "dup"), ("", "x")], "")
        self.assertEqual([v for v, _l in entries], ["", "DP-1"])
        unplugged = op.monitor_entries([("DP-1", "A (DP-1)")], "HDMI-A-1")
        self.assertEqual([v for v, _l in unplugged], ["", "DP-1", "HDMI-A-1"])
        self.assertIn("HDMI-A-1", unplugged[-1][1])


class FakeService(GObject.Object):
    __gsignals__ = {
        "layout-loaded": (GObject.SignalFlags.RUN_LAST, None, (object,)),
        "disconnected": (GObject.SignalFlags.RUN_LAST, None, ()),
    }

    def __init__(self) -> None:
        super().__init__()
        self.layout: Layout | None = None

    def get_layout(self) -> Layout | None:
        return self.layout


class FakeController(GObject.Object):
    state = GObject.Property(type=str, default="empty")


ONE_PIECE = tuple(KeyGeometry(i, float(i % 12), float(i // 12)) for i in range(48))
SPLIT = tuple(KeyGeometry(i, float(i % 6) + (10.0 if i >= 24 else 0.0), float(i % 24 // 6))
              for i in range(48))


def layout(*titles: str) -> Layout:
    return Layout("abc", "Test", (), tuple(Layer(i, t) for i, t in enumerate(titles)))


@unittest.skipUnless(Gtk.init_check(), "needs a display")
class PageTests(unittest.TestCase):
    def setUp(self) -> None:
        Adw.init()
        self.settings = MemorySettings()
        self.service = FakeService()
        self.controller = FakeController()
        self.actions = Gio.SimpleActionGroup()
        self.preview = Gio.SimpleAction.new_stateful(op.PREVIEW_ACTION, None,
                                                     GLib.Variant("b", False))
        self.preview.connect("change-state", lambda a, v: a.set_state(v))
        # The page's settings view needs layer-shell; don't depend on the host.
        self._available = op.layer_shell_available
        op.layer_shell_available = lambda: True
        self.page = op.OverlayPage()
        self.page.setup(self.service, self.controller, self.settings, lambda _t: None,
                        self.actions)

    def tearDown(self) -> None:
        self.page.teardown()
        op.layer_shell_available = self._available

    def load(self, *titles: str) -> None:
        self.service.layout = layout(*titles)
        self.controller.state = "ready"

    def test_empty_state_then_layer_rows(self) -> None:
        self.assertEqual(self.page._layer_rows, {})
        self.assertIsNotNone(self.page.layers_list.get_row_at_index(0))
        self.load("Base", "Symbols", "Numpad")
        self.assertEqual(sorted(self.page._layer_rows), [0, 1, 2])
        self.assertEqual(self.page._layer_rows[1].get_title(), "Layer 1 · Symbols")
        self.controller.state = "empty"
        self.assertEqual(self.page._layer_rows, {})

    def test_layer_switches_write_settings_and_keep_other_layers(self) -> None:
        self.settings.set_value("overlay-layers", GLib.Variant("ai", [9]))
        self.load("Base", "Symbols", "Numpad")
        self.assertNotEqual(self.page.mode_row.get_subtitle(), "")   # hint: none usable
        self.page._layer_rows[2].set_active(True)
        self.assertEqual(op.get_layers(self.settings), [2, 9])
        self.assertEqual(self.page.mode_row.get_subtitle(), "")
        self.settings.set_value("overlay-layers", GLib.Variant("ai", [1]))
        self.assertTrue(self.page._layer_rows[1].get_active())
        self.assertFalse(self.page._layer_rows[2].get_active())

    def test_mode_and_anchor_combos(self) -> None:
        self.assertEqual(self.page.mode_row.get_selected(), 1)      # default: selected
        self.page.mode_row.set_selected(2)
        self.assertEqual(self.settings.get_string("overlay-mode"), "always")
        self.assertFalse(self.page.layers_group.get_visible())
        self.settings.set_string("overlay-mode", "off")
        self.assertEqual(self.page.mode_row.get_selected(), 0)
        self.assertFalse(self.page.idle_row.get_sensitive())
        self.page.anchor_row.set_selected(4)
        self.assertEqual(self.settings.get_string("overlay-anchor"), "top-left")

    def test_hold_actions(self) -> None:
        children = list(self.page._hold_children)
        self.assertEqual(len(children), 1)                       # hint without a layout
        self.assertFalse(self.page.hold_keys_row.get_sensitive())
        self.page.hold_pill_row.set_active(True)
        self.assertTrue(self.settings.get_boolean("hold-pill"))
        self.assertTrue(self.page.hold_keys_row.get_sensitive())
        self.settings.set_value("hold-pill-excluded", GLib.Variant("as", ["7:7"]))
        keys = (KeyBinding(0, tap_label="C", hold_combo="Ctrl+C"), KeyBinding(1, tap_label="D"))
        self.service.layout = Layout("abc", "Test", (), (Layer(0, "Base", None, keys),))
        self.controller.state = "ready"
        self.assertEqual(list(self.page._hold_rows), ["0:0"])
        row = self.page._hold_rows["0:0"]
        self.assertEqual((row.get_title(), row.get_active()), ("Ctrl+C", True))
        row.set_active(False)
        self.assertEqual(op.get_hold_excluded(self.settings), ["0:0", "7:7"])
        self.settings.set_value("hold-pill-excluded", GLib.Variant("as", []))
        self.assertTrue(row.get_active())

    def test_held_modifiers(self) -> None:
        self.assertFalse(self.page.modifier_pill_row.get_active())
        self.page.modifier_pill_row.set_active(True)
        self.assertTrue(self.settings.get_boolean("modifier-pill"))
        self.settings.set_boolean("modifier-pill", False)
        self.assertFalse(self.page.modifier_pill_row.get_active())

    def test_spins(self) -> None:
        self.assertEqual(int(self.page.size_row.get_value()),
                         self.settings.get_int("overlay-size"))
        self.page.size_row.set_value(120)
        self.assertEqual(self.settings.get_int("overlay-size"), 120)
        self.page.opacity_row.set_value(85)
        self.assertEqual(self.settings.get_int("overlay-opacity"), 85)
        self.assertEqual(int(self.page.fade_row.get_value()),
                         self.settings.get_int("overlay-fade-ms"))
        self.page.fade_row.set_value(0)
        self.assertEqual(self.settings.get_int("overlay-fade-ms"), 0)
        self.settings.set_int("overlay-fade-ms", 450)
        self.assertEqual(int(self.page.fade_row.get_value()), 450)
        self.settings.set_int("overlay-idle-seconds", 4)
        self.assertEqual(int(self.page.idle_row.get_value()), 4)
        self.page.half_spacing_row.set_value(100)
        self.assertEqual(self.settings.get_int("overlay-half-spacing"), 100)
        self.settings.set_int("overlay-half-spacing", 25)
        self.assertEqual(int(self.page.half_spacing_row.get_value()), 25)

    def test_half_spacing_hidden_for_one_piece_board(self) -> None:
        self.assertTrue(self.page.half_spacing_row.get_visible())    # unknown board
        self.service.layout = Layout("p", "Planck", ONE_PIECE, (Layer(0, "Base"),))
        self.controller.state = "ready"
        self.assertFalse(self.page.half_spacing_row.get_visible())
        self.service.layout = Layout("v", "Voyager", SPLIT, (Layer(0, "Base"),))
        self.service.emit("layout-loaded", self.service.layout)
        self.assertTrue(self.page.half_spacing_row.get_visible())

    def test_stack_shows_settings_when_layer_shell_works(self) -> None:
        self.assertEqual(self.page.stack.get_visible_child_name(), "settings")



    def test_saved_unplugged_monitor_stays_selected(self) -> None:
        self.settings.set_string("overlay-monitor", "HDMI-Z-9")
        self.assertEqual(self.page._monitor_values[self.page.monitor_row.get_selected()],
                         "HDMI-Z-9")
        self.page.monitor_row.set_selected(0)
        self.assertEqual(self.settings.get_string("overlay-monitor"), "")
        self.assertIn("HDMI-Z-9", self.page._monitor_values)   # still offered

    def test_preview_row_follows_action(self) -> None:
        self.assertFalse(self.page.preview_row.get_visible())        # no action yet
        self.actions.add_action(self.preview)
        self.assertTrue(self.page.preview_row.get_visible())
        self.page.preview_row.set_active(True)
        self.assertTrue(self.preview.get_state().get_boolean())
        self.preview.set_state(GLib.Variant("b", False))
        self.assertFalse(self.page.preview_row.get_active())
        self.preview.set_state(GLib.Variant("b", True))
        self.page.stop_preview()
        self.assertFalse(self.preview.get_state().get_boolean())



@unittest.skipUnless(Gtk.init_check(), "needs a display")
class UnavailableTests(unittest.TestCase):
    def test_no_layer_shell_shows_install_state(self) -> None:
        original = op.layer_shell_available
        op.layer_shell_available = lambda: False
        try:
            page = op.OverlayPage()
            page.setup(FakeService(), FakeController(), MemorySettings(), lambda _t: None,
                       Gio.SimpleActionGroup())
        finally:
            op.layer_shell_available = original
        self.assertEqual(page.stack.get_visible_child_name(), "unavailable")
        self.assertEqual(page.install_label.get_label(), op.INSTALL_COMMAND)
        page.teardown()

if __name__ == "__main__":
    unittest.main()
