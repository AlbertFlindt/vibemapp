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


if __name__ == "__main__":
    unittest.main()
