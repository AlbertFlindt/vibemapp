from __future__ import annotations

import dataclasses
import os
import unittest

os.environ.setdefault("VIBEMAPP_MEMORY_SETTINGS", "1")

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GObject, Gtk  # noqa: E402

from vibemapp.models import (ErgoReport, Finger, HeatmapApp, HeatmapPeriod,  # noqa: E402
                             KeyBinding, KeyGeometry, KeyPairCount, Layer, Layout, LayoutComparison)
from vibemapp.service import KeyboardService  # noqa: E402
from vibemapp.ui import insights_page  # noqa: E402
from vibemapp.ui.insights_page import InsightsPage  # noqa: E402

LAYOUT = Layout(
    "abc", "Test", tuple(KeyGeometry(i, float(i), 0.0) for i in range(4)),
    (Layer(0, "Base", keys=(KeyBinding(0, "A"), KeyBinding(1, "S"), KeyBinding(2, "D"),
                            KeyBinding(3, "F"))),
     Layer(1, "Symbols", keys=(KeyBinding(0, "!"), KeyBinding(1, transparent=True),
                               KeyBinding(2, hold_label="L2"), KeyBinding(3)))))
FIREFOX = HeatmapApp("firefox", "Firefox", 30)
KITTY = HeatmapApp("kitty", "kitty", 10)


def report(presses: int = 5000, pairs: int = 50) -> ErgoReport:
    load = {f: 0.1 for f in Finger}
    return ErgoReport(
        presses=presses, pairs=pairs, finger_load=load, left_share=0.5,
        same_finger_rate=0.03 if pairs else 0.0,
        worst_same_finger=(KeyPairCount((0, 0), (1, 1), 7, Finger.L_MIDDLE), KeyPairCount((0, 2), (0, 3), 3),
                           KeyPairCount((1, 1), (0, 9), 2)) if pairs else (),
        reach_cost=0.8, layer_switch_rate=0.05 if pairs else 0.0, score=82.0,
        penalties={"finger_overload": 3.2, "hand_imbalance": 0.0, "same_finger": 9.0,
                   "reach": 4.8, "layer_switch": 1.0})


class FakeService(KeyboardService):
    def __init__(self) -> None:
        super().__init__()
        self.apps: list[HeatmapApp] = []
        self.report: ErgoReport | None = report()
        self.has_layout = True
        self.counts = {0: 3}
        self.calls: list[tuple] = []
        self.comparisons: list[LayoutComparison] = []
        self.layout_calls: list[tuple] = []

    def get_layout(self) -> Layout | None:
        return LAYOUT if self.has_layout else None

    def list_heatmap_apps(self, period: HeatmapPeriod) -> list[HeatmapApp]:
        return list(self.apps)

    def get_key_counts(self, period, layer=None, app=None) -> dict[int, int]:
        return dict(self.counts)

    def get_layout_comparisons(self, period, app=None) -> list[LayoutComparison]:
        self.layout_calls.append((period, app))
        return list(self.comparisons)

    def get_ergo_report(self, period, app=None) -> ErgoReport | None:
        self.calls.append((period, app))
        return self.report


class FakeController(GObject.Object):
    state = GObject.Property(type=str, default="ready")


class HelperTests(unittest.TestCase):
    def test_number_formats(self) -> None:
        self.assertIn("5", insights_page.format_percent(0.005))
        self.assertEqual(insights_page.format_percent(0.25), "25%")
        self.assertEqual(insights_page.finger_name(Finger.L_MIDDLE), "Left middle")


@unittest.skipUnless(Gtk.init_check(), "needs a display")
class InsightsPageTests(unittest.TestCase):
    def setUp(self) -> None:
        Adw.init()
        self.service = FakeService()
        self.service.props.heatmap_recording = True
        self.controller = FakeController()
        self.page = InsightsPage()
        self.page.setup(self.service, self.controller)

    def tearDown(self) -> None:
        self.page.teardown()

    def shown(self) -> str:
        return self.page.stack.get_visible_child_name()

    def test_states(self) -> None:
        self.assertEqual(self.shown(), "insights")
        self.assertTrue(self.page.results_box.get_visible())
        self.service.report = report(presses=0, pairs=0)
        self.page.refresh()
        self.assertEqual(self.shown(), "insights")
        self.assertTrue(self.page.empty_status.get_visible())
        self.assertFalse(self.page.results_box.get_visible())
        self.service.report = None
        self.page.refresh()
        self.assertEqual(self.shown(), "no-map")
        self.service.has_layout = False
        self.page.refresh()
        self.assertEqual(self.shown(), "no-keyboard")

    def test_not_ready_controller_shows_no_keyboard(self) -> None:
        self.controller.state = "empty"
        self.assertEqual(self.shown(), "no-keyboard")

    def test_opt_in_only_without_recording_and_data(self) -> None:
        self.service.props.heatmap_recording = False
        self.assertEqual(self.shown(), "insights")       # data exists: still shown
        self.service.counts = {}
        self.page.refresh()
        self.assertEqual(self.shown(), "opt-in")
        self.page.start_button.emit("clicked")
        self.assertTrue(self.service.props.heatmap_recording)
        self.assertEqual(self.shown(), "insights")

    def test_record_switch_follows_service(self) -> None:
        self.page.record_row.set_active(False)
        self.assertFalse(self.service.props.heatmap_recording)
        self.service.props.heatmap_recording = True
        self.assertTrue(self.page.record_row.get_active())

    def test_hand_split(self) -> None:
        self.service.report = dataclasses.replace(report(), left_share=0.6)
        self.page.refresh()
        self.assertEqual(self.page.hands_row.get_subtitle(), "Left Hand {} · Right Hand {}".format(
            insights_page.format_percent(0.6), insights_page.format_percent(0.4)))

    def test_only_finger_load_is_queried(self) -> None:
        self.service.comparisons = [LayoutComparison("QWERTY", report(), 0.0, current=True)]
        self.page.refresh()
        self.assertEqual(self.service.layout_calls, [])

    def test_app_filter_wiring(self) -> None:
        self.assertFalse(self.page.app_dropdown.get_visible())
        self.service.apps = [FIREFOX, KITTY]
        self.page.refresh()
        self.assertTrue(self.page.app_dropdown.get_visible())
        self.page.app_dropdown.set_selected(2)
        self.assertEqual(self.service.calls[-1][1], "kitty")
        self.page.app_dropdown.set_selected(0)
        self.assertIsNone(self.service.calls[-1][1])

    def test_period_wiring(self) -> None:
        self.page.period_group.set_active_name("today")
        self.assertEqual(self.service.calls[-1][0], HeatmapPeriod.TODAY)
        self.page.period_group.set_active_name("all")
        self.assertEqual(self.service.calls[-1][0], HeatmapPeriod.ALL)


if __name__ == "__main__":
    unittest.main()
