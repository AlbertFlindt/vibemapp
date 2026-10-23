from __future__ import annotations

import unittest

from vibemapp.mock_service import MockKeyboardService
from vibemapp.models import AppRule, FocusedWindow, RuleMatch
from gi.repository import GLib

from vibemapp.ui import layer_notifier as ln
from vibemapp.ui.settings import MemorySettings

from .test_mock_service import spin


class LayerNoticeTests(unittest.TestCase):
    """The pure decision."""

    def notice(self, **kwargs: object):
        args = dict(enabled=True, window_active=False, target=2, active_layer=0,
                    layer_title="Layer 2 · Media", app_name="Firefox", fallback=False)
        args.update(kwargs)
        return ln.layer_notice(**args)

    def test_shown_on_a_real_change(self) -> None:
        self.assertEqual(self.notice(), ("Layer 2 · Media", "Switched for Firefox"))

    def test_skipped(self) -> None:
        self.assertIsNone(self.notice(enabled=False))
        self.assertIsNone(self.notice(window_active=True))
        self.assertIsNone(self.notice(active_layer=2))

    def test_fallback_and_unknown_app(self) -> None:
        self.assertEqual(self.notice(fallback=True)[1],
                         "No rule for Firefox, using the fallback layer")
        self.assertEqual(self.notice(fallback=True, app_name=None)[1],
                         "No rule matched, using the fallback layer")
        self.assertEqual(self.notice(app_name=None)[1], "Switched by a Smart Layers rule")

    def test_excluded_target_is_silent(self) -> None:
        self.assertIsNone(self.notice(excluded=frozenset({2})))
        self.assertIsNotNone(self.notice(excluded=frozenset({3})))

    def test_excluded_active_layer_compares_with_previous(self) -> None:
        mouse = frozenset({3})
        # Base -> (firmware) Mouse -> a rule for Base: back where we were.
        self.assertIsNone(self.notice(target=0, active_layer=3, excluded=mouse,
                                      previous_layer=0))
        # Base -> Mouse -> a rule for Media: a real change.
        self.assertEqual(self.notice(target=2, active_layer=3, excluded=mouse,
                                     previous_layer=0)[0], "Layer 2 · Media")
        # Nothing known before the excluded layer: notify, the switch is real.
        self.assertIsNotNone(self.notice(target=0, active_layer=3, excluded=mouse,
                                         previous_layer=None))
        # previous_layer only counts while the active layer is excluded.
        self.assertIsNotNone(self.notice(target=2, active_layer=0, excluded=mouse,
                                         previous_layer=2))

    def test_focused_app_name(self) -> None:
        names = {"kitty": "Kitty Terminal"}.get
        self.assertEqual(ln.focused_app_name(FocusedWindow("kitty", "fish"), names),
                         "Kitty Terminal")
        game = FocusedWindow("steam_app_570", "Dota 2", steam_app_id="570",
                             steam_game_name="Dota 2")
        self.assertEqual(ln.focused_app_name(game, names), "Dota 2")
        unnamed = FocusedWindow("steam_app_99", "", steam_app_id="99")
        self.assertEqual(ln.focused_app_name(unnamed, names), "Steam Game 99")
        # The real app index prettifies classes it doesn't know.
        self.assertTrue(ln.focused_app_name(FocusedWindow("zz-no-such-app", "")))


class LayerNotifierTests(unittest.TestCase):
    """Wired to the mock service's rule-applied signal."""

    def setUp(self) -> None:
        self.svc = MockKeyboardService(speed=50, autostart=False)
        self.settings = MemorySettings()
        self.settings.set_boolean(ln.SETTINGS_KEY, True)
        self.sent: list[tuple[str, str, str]] = []
        self.window_active = False
        self.notifier = ln.LayerNotifier(
            self.svc, self.settings, lambda *args: self.sent.append(args),
            lambda: self.window_active, app_name=lambda cls: f"App {cls}")
        self.svc.simulate_plug()
        self.svc.connect_device(self.svc.list_devices()[0].id)
        self.assertTrue(spin(lambda: self.svc.get_connected_device() is not None))
        done: list = []
        self.svc.load_layout_async(self.svc.get_connected_device(), None,
                                   lambda layout, err: done.append(layout))
        self.assertTrue(spin(lambda: bool(done)))
        self.svc.set_rules([
            AppRule("kitty", "kitty", 1),
            AppRule("fire.*", "Firefox", 2, match=RuleMatch.CLASS_PATTERN),
            AppRule("", "All Steam Games", 3, match=RuleMatch.STEAM_GAME)])
        self.svc.set_fallback_layer(0)
        self.svc.props.smart_layers_enabled = True

    def tearDown(self) -> None:
        self.notifier.shutdown()
        self.svc.shutdown()

    def test_setting_off_sends_nothing(self) -> None:
        self.settings.set_boolean(ln.SETTINGS_KEY, False)
        self.svc.simulate_focus("kitty", "fish")
        self.assertEqual(self.svc.get_active_layer(), 1)
        self.assertEqual(self.sent, [])

    def test_real_change_uses_one_fixed_id(self) -> None:
        self.svc.simulate_focus("kitty", "fish")
        self.svc.simulate_focus("firefox", "Docs")     # pattern rule: named by window
        self.assertEqual(self.sent, [
            ("layer-changed", "Layer 1 · Symbols", "Switched for App kitty"),
            ("layer-changed", "Layer 2 · Media", "Switched for App firefox")])

    def test_same_layer_resent_sends_nothing(self) -> None:
        self.svc.simulate_layer(1)                     # layer key pressed by hand
        self.svc.simulate_focus("kitty", "fish")       # re-sends layer 1
        self.assertEqual(self.svc.get_active_layer(), 1)
        self.assertEqual(self.sent, [])

    def test_window_active_sends_nothing(self) -> None:
        self.window_active = True
        self.svc.simulate_focus("kitty", "fish")
        self.assertEqual(self.svc.get_active_layer(), 1)
        self.assertEqual(self.sent, [])

    def test_fallback(self) -> None:
        self.svc.simulate_focus("kitty", "fish")
        self.svc.simulate_focus("blender", "scene")
        self.assertEqual(self.sent[-1], ("layer-changed", "Layer 0 · Base",
                                         "No rule for App blender, using the fallback layer"))

    def test_steam_games(self) -> None:
        # "All Steam Games" is reported with app_class "" like the fallback.
        self.svc.simulate_focus("steam_app_1245620", "ELDEN RING")
        self.assertEqual(self.sent, [("layer-changed", "Layer 3 · Navigation",
                                      "Switched for ELDEN RING")])

    def exclude(self, *layers: int) -> None:
        self.settings.set_value(ln.EXCLUDED_KEY, GLib.Variant("ai", list(layers)))

    def test_switch_to_excluded_layer_is_silent(self) -> None:
        self.exclude(1)
        self.svc.simulate_focus("kitty", "fish")
        self.assertEqual(self.svc.get_active_layer(), 1)
        self.assertEqual(self.sent, [])

    def test_back_from_excluded_layer_to_previous_is_silent(self) -> None:
        self.exclude(3)
        self.assertEqual(self.svc.get_active_layer(), 0)
        self.svc.simulate_layer(3)                     # firmware: trackpad touched
        self.svc.simulate_focus("blender", "scene")    # fallback: Base again
        self.assertEqual(self.svc.get_active_layer(), 0)
        self.assertEqual(self.sent, [])

    def test_from_excluded_layer_to_another_notifies(self) -> None:
        self.exclude(3)
        self.svc.simulate_layer(3)
        self.svc.simulate_focus("kitty", "fish")
        self.assertEqual(self.sent, [("layer-changed", "Layer 1 · Symbols",
                                      "Switched for App kitty")])

    def test_previous_layer_follows_layer_changes(self) -> None:
        self.exclude(3)
        self.svc.simulate_focus("kitty", "fish")       # Base -> Symbols, notifies
        self.svc.simulate_layer(3)                     # Symbols -> Mouse
        self.svc.simulate_focus("kitty", "vim")        # back to Symbols: silent
        self.assertEqual(self.svc.get_active_layer(), 1)
        self.svc.simulate_layer(3)
        self.svc.simulate_focus("blender", "scene")    # to Base: notifies
        self.assertEqual([title for _id, title, _body in self.sent],
                         ["Layer 1 · Symbols", "Layer 0 · Base"])

    def test_shutdown_disconnects(self) -> None:
        self.notifier.shutdown()
        self.svc.simulate_focus("kitty", "fish")
        self.svc.simulate_layer(3)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.notifier._handlers, [])


if __name__ == "__main__":
    unittest.main()
