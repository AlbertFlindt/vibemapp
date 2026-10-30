from __future__ import annotations

import json
import os
import tempfile
import unittest
from typing import Callable

from vibemapp.appid import DEVEL_APP_ID
from vibemapp.backend.smart_layers import (APP_ID, RulesStore, SmartLayersEngine,
                                                  match_rule)
from vibemapp.models import AppRule, RuleMatch


class ManualScheduler:
    def __init__(self) -> None:
        self.now = 0
        self._tasks: dict[int, tuple[int, Callable[[], None]]] = {}
        self._next = 1

    def schedule(self, delay_ms: int, fn: Callable[[], None]) -> object:
        handle = self._next
        self._next += 1
        self._tasks[handle] = (self.now + delay_ms, fn)
        return handle

    def cancel(self, handle: object) -> None:
        self._tasks.pop(handle, None)  # type: ignore[arg-type]

    def advance(self, ms: int) -> None:
        self.now += ms
        for handle, (due, fn) in sorted(self._tasks.items(), key=lambda kv: kv[1][0]):
            if due <= self.now and handle in self._tasks:
                del self._tasks[handle]
                fn()


RULES = [
    AppRule("kitty", "Kitty", 1),
    AppRule("firefox", "Firefox (YouTube)", 3, title_pattern=r"YouTube"),
    AppRule("firefox", "Firefox", 2),
    AppRule("blender", "Blender", 4, enabled=False),
]


class MatchTests(unittest.TestCase):
    def test_exact_class_case_sensitive(self) -> None:
        self.assertEqual(match_rule(RULES, "kitty", "x").layer_index, 1)
        self.assertIsNone(match_rule(RULES, "Kitty", "x"))

    def test_first_match_and_title_regex(self) -> None:
        self.assertEqual(match_rule(RULES, "firefox", "Lo-fi — YouTube — Firefox").layer_index, 3)
        self.assertEqual(match_rule(RULES, "firefox", "GitHub").layer_index, 2)

    def test_disabled_and_invalid_regex(self) -> None:
        self.assertIsNone(match_rule(RULES, "blender", ""))
        bad = [AppRule("x", "X", 1, title_pattern="(")]
        self.assertIsNone(match_rule(bad, "x", "anything"))


class EngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "sub", "smart-layers.json")
        self.store = RulesStore(self.path)
        self.store.set("L1", RULES, 0)
        self.sched = ManualScheduler()
        self.sent: list[int] = []
        self.applied: list[tuple[str, int]] = []
        self.rules_changed = 0
        self.engine = SmartLayersEngine(
            self.store, self.sent.append,
            lambda c, l: self.applied.append((c, l)),
            self._on_rules, scheduler=self.sched, debounce_ms=50)
        self.engine.set_layout("L1")

    def _on_rules(self) -> None:
        self.rules_changed += 1

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _activate(self) -> None:
        self.engine.set_connected(True)
        self.engine.set_enabled(True)

    def test_paused_until_enabled_and_connected(self) -> None:
        self.engine.focus_changed("kitty", "")
        self.sched.advance(100)
        self.assertEqual(self.sent, [])
        self.engine.set_connected(True)
        self.assertEqual(self.sent, [])
        self.engine.set_enabled(True)       # re-evaluates current focus immediately
        self.assertEqual(self.sent, [1])
        self.assertEqual(self.applied, [("kitty", 1)])

    def test_reconnect_reevaluates(self) -> None:
        self._activate()
        self.engine.focus_changed("kitty", "")
        self.sched.advance(60)
        self.engine.keyboard_layer_changed(1)
        self.engine.set_connected(False)
        self.engine.set_connected(True)
        self.assertEqual(self.sent, [1, 1])

    def test_debounce_coalesces_rapid_flips(self) -> None:
        self._activate()
        self.engine.focus_changed("kitty", "")
        self.sched.advance(20)
        self.engine.focus_changed("firefox", "GitHub")
        self.sched.advance(20)
        self.engine.focus_changed("kitty", "")
        self.sched.advance(49)
        self.assertEqual(self.sent, [])
        self.sched.advance(1)
        self.assertEqual(self.sent, [1])

    def test_held_keys_defer_switch_until_release(self) -> None:
        # Holding a momentary layer key, Super+arrow moves focus to another window:
        # layer_move(fallback) would drop the held layer, so wait for the release.
        self._activate()
        self.engine.keyboard_layer_changed(3)
        self.engine.key_activity(True)      # layer key down
        self.engine.key_activity(True)      # Super down
        self.engine.focus_changed("nautilus", "")
        self.sched.advance(100)
        self.engine.focus_changed("kitty", "")
        self.sched.advance(100)
        self.assertEqual(self.sent, [])
        self.engine.key_activity(True)      # Super up, layer key still held
        self.sched.advance(100)
        self.assertEqual(self.sent, [])
        self.engine.key_activity(False)     # layer key up
        self.sched.advance(49)
        self.assertEqual(self.sent, [])
        self.sched.advance(1)
        self.assertEqual(self.sent, [1])    # the window focused at release time

    def test_release_without_focus_change_sends_nothing(self) -> None:
        self._activate()
        self.engine.key_activity(True)
        self.engine.key_activity(False)
        self.sched.advance(100)
        self.assertEqual(self.sent, [])

    def test_held_keys_time_out(self) -> None:
        self._activate()
        self.engine.key_activity(True)      # its KEYUP never arrives
        self.engine.focus_changed("kitty", "")
        self.sched.advance(9_999)
        self.assertEqual(self.sent, [])
        self.sched.advance(1)               # timeout: keys count as released
        self.assertEqual(self.sent, [])
        self.sched.advance(50)              # then the usual debounce
        self.assertEqual(self.sent, [1])

    def test_disconnect_clears_held_keys(self) -> None:
        self._activate()
        self.engine.key_activity(True)
        self.engine.focus_changed("kitty", "")
        self.sched.advance(100)
        self.engine.set_connected(False)
        self.engine.set_connected(True)     # nothing held on a fresh connection
        self.assertEqual(self.sent, [1])

    def test_fallback_and_keep(self) -> None:
        self._activate()
        self.engine.focus_changed("nautilus", "")
        self.sched.advance(50)
        self.assertEqual(self.sent, [0])
        self.assertEqual(self.applied, [("", 0)])
        self.engine.set_fallback(None)
        self.engine.keyboard_layer_changed(0)
        self.engine.focus_changed("kitty", "")
        self.sched.advance(50)
        self.engine.focus_changed("gimp", "")
        self.sched.advance(50)
        self.assertEqual(self.sent, [0, 1])   # None fallback leaves the layer alone

    def test_dedupe_against_last_sent_and_reported(self) -> None:
        self._activate()
        self.engine.focus_changed("firefox", "GitHub")
        self.sched.advance(50)
        self.engine.keyboard_layer_changed(2)
        self.engine.focus_changed("firefox", "MDN")      # same target, keyboard agrees
        self.sched.advance(50)
        self.assertEqual(self.sent, [2])
        self.engine.keyboard_layer_changed(0)            # user changed layer by hand
        self.engine.focus_changed("kitty", "shell")
        self.sched.advance(50)
        self.engine.keyboard_layer_changed(1)
        self.engine.focus_changed("firefox", "Docs")     # back to firefox: re-assert
        self.sched.advance(50)
        self.assertEqual(self.sent, [2, 1, 2])

    def test_title_change_keeps_manual_layer(self) -> None:
        # Shells set the title on every command; that mustn't undo a toggled layer.
        self._activate()
        self.engine.focus_changed("kitty", "~")
        self.sched.advance(50)
        self.assertEqual(self.sent, [1])
        self.engine.keyboard_layer_changed(4)            # user toggled a layer (TG)
        self.engine.focus_changed("kitty", "vim notes.md")
        self.engine.focus_changed("kitty", "~")
        self.sched.advance(50)
        self.assertEqual(self.sent, [1])

    def test_title_change_that_changes_target_sends(self) -> None:
        self._activate()
        self.engine.focus_changed("firefox", "GitHub")
        self.sched.advance(50)
        self.engine.keyboard_layer_changed(2)
        self.engine.focus_changed("firefox", "Cats - YouTube")
        self.sched.advance(50)
        self.engine.keyboard_layer_changed(3)
        self.engine.focus_changed("firefox", "GitHub")
        self.sched.advance(50)
        self.assertEqual(self.sent, [2, 3, 2])

    def test_reevaluate_reasserts_unchanged_target(self) -> None:
        self._activate()
        self.engine.focus_changed("kitty", "~")
        self.sched.advance(50)
        self.engine.keyboard_layer_changed(4)
        self.engine.reevaluate()                         # e.g. TRIGGER_SMART_LAYER
        self.assertEqual(self.sent, [1, 1])

    def test_own_window_ignored(self) -> None:
        self._activate()
        self.engine.focus_changed("kitty", "")
        self.sched.advance(50)
        self.engine.keyboard_layer_changed(1)
        self.engine.focus_changed(APP_ID, "Vibemapp")
        self.sched.advance(50)
        self.assertEqual(self.engine.focus, ("kitty", ""))
        # A dev run's window (run-dev.sh) is ours too.
        self.engine.focus_changed(DEVEL_APP_ID, "Vibemapp (Development)")
        self.sched.advance(50)
        self.assertEqual(self.engine.focus, ("kitty", ""))
        # Editing rules while our window is focused evaluates the *previous* app.
        self.engine.set_rules([AppRule("kitty", "Kitty", 3)])
        self.assertEqual(self.sent, [1, 3])

    def test_invalid_target_layer_skipped(self) -> None:
        self.engine.layer_count = 2
        self._activate()
        self.engine.focus_changed("firefox", "YouTube")   # layer 3 doesn't exist
        self.sched.advance(50)
        self.assertEqual(self.sent, [])

    def test_disable_cancels_pending(self) -> None:
        self._activate()
        self.engine.focus_changed("kitty", "")
        self.engine.set_enabled(False)
        self.sched.advance(100)
        self.assertEqual(self.sent, [])

    def test_rules_changed_callbacks(self) -> None:
        before = self.rules_changed
        self.engine.set_rules([])
        self.engine.set_fallback(2)
        self.engine.set_layout("other")
        self.assertEqual(self.rules_changed - before, 3)
        self.assertEqual(self.engine.rules, [])
        self.assertIsNone(self.engine.fallback)    # unknown layout -> keep


class StoreTests(unittest.TestCase):
    def test_roundtrip_and_format(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "smart-layers.json")
            store = RulesStore(path)
            store.set("abc", RULES, 0)
            store.set("xyz", [], None)
            with open(path) as f:
                data = json.load(f)
            self.assertEqual(data["version"], 1)
            self.assertEqual(data["layouts"]["abc"]["fallback"], 0)
            self.assertIsNone(data["layouts"]["xyz"]["fallback"])
            self.assertEqual(data["layouts"]["abc"]["rules"][1]["title_pattern"], "YouTube")
            again = RulesStore(path)
            self.assertEqual(again.get("abc"), (RULES, 0))
            self.assertEqual(again.get("missing"), ([], None))
            self.assertEqual([n for n in os.listdir(d) if n.endswith(".tmp")], [])

    def test_pill_switches_roundtrip_and_default_on(self) -> None:
        from vibemapp.backend.smart_layers import rule_from_json, rule_to_json
        rule = AppRule("", "All Steam Games", 2, match=RuleMatch.STEAM_GAME,
                       show_hold_actions=False, show_held_modifiers=False)
        data = rule_to_json(rule)
        self.assertEqual((data["show_hold_actions"], data["show_held_modifiers"]), (False, False))
        self.assertEqual(rule_from_json(data), rule)
        old = rule_from_json({"app_class": "kitty", "layer_index": 1})   # pre-1.8 file
        self.assertTrue(old.show_hold_actions and old.show_held_modifiers)

    def test_tolerates_bad_entries_and_corrupt_file(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "smart-layers.json")
            with open(path, "w") as f:
                json.dump({"version": 1, "layouts": {"a": {"fallback": "x", "rules": [
                    {"app_class": "kitty", "layer_index": 1},
                    {"app_class": "", "layer_index": 1},
                    {"app_class": "x", "layer_index": -1},
                    "garbage"]}}}, f)
            rules, fb = RulesStore(path).get("a")
            self.assertEqual(rules, [AppRule("kitty", "kitty", 1)])
            self.assertIsNone(fb)
            with open(path, "w") as f:
                f.write("{not json")
            self.assertEqual(RulesStore(path).layout_ids(), [])
            self.assertTrue(os.path.exists(path + ".corrupt"))


if __name__ == "__main__":
    unittest.main()
