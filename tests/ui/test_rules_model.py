from __future__ import annotations

import unittest

from vibemapp.models import AppRule, Layer, RuleMatch
from vibemapp.ui import rules_model as rm


def rule(cls: str, layer: int, pattern: str | None = None, enabled: bool = True) -> AppRule:
    return AppRule(cls, cls.title(), layer, title_pattern=pattern, enabled=enabled)


class MatchingTests(unittest.TestCase):
    def test_first_enabled_match_wins(self) -> None:
        rules = [rule("firefox", 2, "YouTube"), rule("firefox", 1), rule("kitty", 3)]
        self.assertEqual(rm.target_layer(rules, 0, "firefox", "YouTube — Firefox")[0], 2)
        self.assertEqual(rm.target_layer(rules, 0, "firefox", "Docs — Firefox")[0], 1)
        self.assertEqual(rm.target_layer(rules, 0, "kitty", "fish")[0], 3)

    def test_disabled_rules_are_skipped(self) -> None:
        rules = [rule("kitty", 3, enabled=False), rule("kitty", 1)]
        self.assertEqual(rm.target_layer(rules, None, "kitty", "")[0], 1)

    def test_class_match_is_exact_and_case_sensitive(self) -> None:
        rules = [rule("firefox", 2)]
        self.assertEqual(rm.target_layer(rules, 0, "Firefox", "")[0], 0)
        self.assertEqual(rm.target_layer(rules, 0, "firefox-esr", "")[0], 0)

    def test_fallback_and_keep(self) -> None:
        self.assertEqual(rm.target_layer([], 2, "x", ""), (2, None))
        self.assertEqual(rm.target_layer([], None, "x", ""), (None, None))

    def test_invalid_pattern_never_matches(self) -> None:
        self.assertFalse(rm.rule_matches(rule("a", 1, "("), "a", "("))

    def test_validate_pattern(self) -> None:
        self.assertIsNone(rm.validate_pattern(None))
        self.assertIsNone(rm.validate_pattern(""))
        self.assertIsNone(rm.validate_pattern("YouTube|Twitch"))
        self.assertIsNotNone(rm.validate_pattern("Downloads("))


class EditingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rules = [rule("a", 0), rule("b", 1), rule("c", 2)]

    def classes(self, rules: list[AppRule]) -> list[str]:
        return [r.app_class for r in rules]

    def test_move(self) -> None:
        self.assertEqual(self.classes(rm.move_rule(self.rules, 0, 1)), ["b", "a", "c"])
        self.assertEqual(self.classes(rm.move_rule(self.rules, 2, -1)), ["a", "c", "b"])
        self.assertEqual(self.classes(rm.move_rule(self.rules, 0, -1)), ["a", "b", "c"])
        self.assertEqual(self.classes(rm.move_rule_to(self.rules, 0, 99)), ["b", "c", "a"])
        self.assertEqual(self.classes(rm.move_rule_to(self.rules, 5, 0)), ["a", "b", "c"])

    def test_inputs_are_not_mutated(self) -> None:
        before = list(self.rules)
        rm.move_rule(self.rules, 0, 1)
        rm.remove_rule(self.rules, 0)
        rm.update_rule(self.rules, 0, enabled=False)
        rm.add_rule(self.rules, rule("d", 0))
        self.assertEqual(self.rules, before)

    def test_remove_and_update(self) -> None:
        self.assertEqual(self.classes(rm.remove_rule(self.rules, 1)), ["a", "c"])
        updated = rm.update_rule(self.rules, 1, layer_index=3, enabled=False)
        self.assertEqual((updated[1].layer_index, updated[1].enabled), (3, False))
        self.assertEqual(rm.update_rule(self.rules, 9, enabled=False), self.rules)

    def test_add_puts_specific_rule_before_catch_all(self) -> None:
        rules = rm.add_rule(self.rules, rule("b", 3, "YouTube"))
        self.assertEqual([(r.app_class, r.title_pattern) for r in rules],
                         [("a", None), ("b", "YouTube"), ("b", None), ("c", None)])
        self.assertEqual(self.classes(rm.add_rule(self.rules, rule("d", 0))),
                         ["a", "b", "c", "d"])

    def test_shadowed(self) -> None:
        rules = [rule("a", 0), rule("a", 1, "x"), rule("b", 0, enabled=False), rule("b", 1)]
        self.assertEqual(rm.shadowed_rules(rules), {1})

    def test_clamp(self) -> None:
        clamped = rm.clamp_rules_to_layers([rule("a", 7), rule("b", 1)], 4)
        self.assertEqual([r.layer_index for r in clamped], [3, 1])


class LabelTests(unittest.TestCase):
    def test_layer_label(self) -> None:
        self.assertEqual(rm.layer_label(Layer(2, "Media"), 2), "Layer 2 · Media")
        self.assertEqual(rm.layer_label(Layer(2, "Layer 2"), 2), "Layer 2")
        self.assertEqual(rm.layer_label(None, 1), "Layer 1")
        self.assertEqual(rm.layer_label(None, None), "Don't Change")

    def test_app_class_from_desktop(self) -> None:
        self.assertEqual(rm.app_class_from_desktop_id("org.gnome.Nautilus.desktop", None),
                         "org.gnome.Nautilus")
        self.assertEqual(rm.app_class_from_desktop_id("code.desktop", "Code"), "Code")
        self.assertEqual(rm.app_class_from_desktop_id(None, None), "")

    def test_prettify(self) -> None:
        self.assertEqual(rm.prettify_class("org.gnome.Nautilus"), "Nautilus")
        self.assertEqual(rm.prettify_class("kitty"), "Kitty")


if __name__ == "__main__":
    unittest.main()


class GroupRuleTests(unittest.TestCase):
    """Steam / class-pattern rules (SPEC 7); mirrors the engine's semantics."""

    def steam(self, app_id: str = "", layer: int = 3, **kw) -> AppRule:
        return AppRule(app_id, "Steam", layer, match=RuleMatch.STEAM_GAME, **kw)

    def pattern(self, regex: str, layer: int = 2, **kw) -> AppRule:
        return AppRule(regex, "Pattern", layer, match=RuleMatch.CLASS_PATTERN, **kw)

    def test_steam_matching(self) -> None:
        any_game = self.steam()
        elden = self.steam("1245620", 1)
        self.assertTrue(rm.rule_matches(any_game, "steam_app_1245620", "", "1245620"))
        self.assertTrue(rm.rule_matches(any_game, "dota2", "Dota 2", "570"))
        self.assertFalse(rm.rule_matches(any_game, "steam", "Steam", None), "Steam client")
        self.assertTrue(rm.rule_matches(elden, "steam_app_1245620", "", "1245620"))
        self.assertFalse(rm.rule_matches(elden, "dota2", "", "570"))
        self.assertTrue(rm.rule_matches(self.steam(title_pattern="Dota"), "dota2", "Dota 2", "570"))
        self.assertFalse(rm.rule_matches(self.steam(title_pattern="x"), "dota2", "Dota 2", "570"))

    def test_class_pattern_uses_fullmatch(self) -> None:
        rule = self.pattern("steam_app_.*")
        self.assertTrue(rm.rule_matches(rule, "steam_app_570", ""))
        self.assertFalse(rm.rule_matches(rule, "not_steam_app_570", ""), "fullmatch, not search")
        self.assertFalse(rm.rule_matches(self.pattern("kit"), "kitty", ""))
        self.assertTrue(rm.rule_matches(self.pattern("kit.*"), "kitty", ""))
        self.assertFalse(rm.rule_matches(self.pattern("("), "(", ""))

    def test_first_match_wins_across_kinds(self) -> None:
        rules = [self.steam("1245620", 1), self.steam(), rule("kitty", 2)]
        self.assertEqual(rm.target_layer(rules, 0, "steam_app_1245620", "", "1245620")[0], 1)
        self.assertEqual(rm.target_layer(rules, 0, "dota2", "", "570")[0], 3)
        self.assertEqual(rm.target_layer(rules, 0, "kitty", "")[0], 2)
        self.assertEqual(rm.target_layer(rules, 0, "steam", "")[0], 0)

    def test_specific_rules_go_above_catch_alls(self) -> None:
        rules = [rule("kitty", 1), self.steam(), self.pattern("foo.*")]
        added = rm.add_rule(rules, self.steam("570", 2))
        self.assertEqual(added[1].app_class, "570", "single game above All Steam Games")
        added = rm.add_rule(rules, rule("firefox", 2))
        self.assertEqual([r.app_class for r in added], ["kitty", "firefox", "", "foo.*"])
        added = rm.add_rule(rules, self.pattern("bar.*"))
        self.assertEqual(added[-1].app_class, "bar.*", "catch-alls are appended")

    def test_shadowing_by_catch_alls(self) -> None:
        rules = [self.steam(), self.steam("570"), self.pattern("kit.*"), rule("kitty", 1),
                 rule("firefox", 1), self.pattern("kit.*"), self.steam("1", title_pattern="x")]
        self.assertEqual(rm.shadowed_rules(rules), {1, 3, 5, 6})
        # A title-pattern catch-all doesn't shadow anything.
        rules = [self.steam(title_pattern="Dota"), self.steam("570")]
        self.assertEqual(rm.shadowed_rules(rules), set())
        # Disabled blockers don't count.
        rules = [self.steam(enabled=False), self.steam("570")]
        self.assertEqual(rm.shadowed_rules(rules), set())

    def test_classes_matching(self) -> None:
        classes = ["kitty", "steam_app_1", "steam_app_2", "steam_app_1", "firefox"]
        self.assertEqual(rm.classes_matching("steam_app_.*", classes),
                         ["steam_app_1", "steam_app_2"])
        self.assertEqual(rm.classes_matching("(", classes), [])
        self.assertEqual(rm.classes_matching("", classes), [])
