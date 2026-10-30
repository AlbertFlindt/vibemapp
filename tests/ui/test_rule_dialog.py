"""RuleDialog: the per-rule on-screen key switches."""

from __future__ import annotations

import unittest

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk  # noqa: E402

from vibemapp.models import AppRule, Layer, Layout, RuleMatch  # noqa: E402

HAVE_DISPLAY = Gdk.Display.get_default() is not None
if HAVE_DISPLAY:
    Adw.init()


@unittest.skipUnless(HAVE_DISPLAY, "needs a display")
class OnScreenKeysTests(unittest.TestCase):
    def test_switches_load_and_save(self) -> None:
        from vibemapp.ui.rule_dialog import RuleDialog
        layout = Layout("x", "X", (), (Layer(0, "Base", None, ()), Layer(1, "Game", None, ())))
        rule = AppRule("", "All Steam Games", 1, match=RuleMatch.STEAM_GAME,
                       show_held_modifiers=False)
        dialog = RuleDialog(layout=layout, running=[], existing=[rule],
                            on_done=lambda _r: None, rule=rule)
        self.assertTrue(dialog.hold_actions_row.get_active())
        self.assertFalse(dialog.held_modifiers_row.get_active())
        dialog.hold_actions_row.set_active(False)
        dialog.held_modifiers_row.set_active(True)
        built = dialog.build_rule()
        self.assertEqual((built.show_hold_actions, built.show_held_modifiers), (False, True))
        self.assertEqual((built.match, built.layer_index), (RuleMatch.STEAM_GAME, 1))


@unittest.skipUnless(HAVE_DISPLAY, "needs a display")
class PrefilledAppTests(unittest.TestCase):
    def test_opens_on_details_for_the_app(self) -> None:
        from gi.repository import Gio
        from vibemapp.ui.apps import AppEntry
        from vibemapp.ui.rule_dialog import RuleDialog
        layout = Layout("x", "X", (), (Layer(0, "Base", None, ()), Layer(1, "Code", None, ())))
        entry = AppEntry("kitty", "Kitty", Gio.ThemedIcon.new("kitty"))
        dialog = RuleDialog(layout=layout, running=[], existing=[],
                            on_done=lambda _r: None, app=entry)
        self.assertIs(dialog.nav.get_visible_page(), dialog.details_page)
        self.assertIs(dialog.nav.get_previous_page(dialog.details_page), dialog.kind_page)
        self.assertFalse(dialog.remove_group.get_visible())
        built = dialog.build_rule()
        self.assertEqual((built.match, built.app_class, built.display_name, built.layer_index),
                         (RuleMatch.APP, "kitty", "Kitty", 1))


if __name__ == "__main__":
    unittest.main()
