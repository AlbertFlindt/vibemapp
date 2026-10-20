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


def labels(listbox: Gtk.ListBox) -> list[tuple[str, str]]:
    rows = []
    row = listbox.get_first_child()
    while row is not None:
        rows.append((row.get_title(), row.get_subtitle()))
        row = row.get_next_sibling()
    return rows


class HelperTests(unittest.TestCase):
    def test_key_label_uses_layer_then_base_then_index(self) -> None:
        self.assertEqual(insights_page.key_label(LAYOUT, 0, 0), "A")
        self.assertEqual(insights_page.key_label(LAYOUT, 1, 0), "!")
        self.assertEqual(insights_page.key_label(LAYOUT, 1, 1), "S")      # transparent: base
        self.assertEqual(insights_page.key_label(LAYOUT, 1, 2), "L2")     # hold label
        self.assertEqual(insights_page.key_label(LAYOUT, 1, 3), "Key 3")  # bound but unlabelled
        self.assertEqual(insights_page.key_label(LAYOUT, 0, 9), "Key 9")
        self.assertEqual(insights_page.key_label(None, 0, 4), "Key 4")

    def test_key_name_spells_out_glyphs_and_quotes_punctuation(self) -> None:
        layout = Layout("x", "X", LAYOUT.geometry, (Layer(0, "Base", keys=(
            KeyBinding(0, "⌫"), KeyBinding(1, ","), KeyBinding(2, "Esc"), KeyBinding(3, "7"))),))
        self.assertEqual([insights_page.key_name(layout, 0, i) for i in range(4)],
                         ["Backspace", "\u201c,\u201d", "Esc", "7"])

    def test_number_formats(self) -> None:
        self.assertEqual(insights_page.format_penalty(0.0), insights_page.format_number(0.0))
        self.assertTrue(insights_page.format_penalty(3.24).startswith("−"))
        self.assertEqual(insights_page.format_score(82.0), "82")
        self.assertIn("5", insights_page.format_percent(0.005))


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

    def test_pair_switch_follows_key_presses_and_service(self) -> None:
        self.assertTrue(self.page.pairs_row.get_sensitive())
        self.page.pairs_row.set_active(True)
        self.assertTrue(self.service.props.pair_recording)
        self.service.props.pair_recording = False
        self.assertFalse(self.page.pairs_row.get_active())
        self.page.record_row.set_active(False)
        self.assertFalse(self.service.props.heatmap_recording)
        self.assertFalse(self.page.pairs_row.get_sensitive())
        self.service.props.heatmap_recording = True
        self.assertTrue(self.page.pairs_row.get_sensitive())

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

    def test_worst_pairs_labelled_from_layout(self) -> None:
        titles = [t for t, _s in labels(self.page.same_list)]
        self.assertEqual(titles[1:], ["A → S", "D → F", "S → Key 9"])
        self.assertEqual(labels(self.page.same_list)[1][1], "Left middle · 7 times")
        self.assertEqual(labels(self.page.same_list)[2][1], "3 times")

    def test_no_pairs_explains_recording(self) -> None:
        self.service.report = report(pairs=0)
        self.page.refresh()
        rows = labels(self.page.same_list)
        self.assertEqual(len(rows), 1)
        self.assertIn("Record Key Pairs", rows[0][1])
        self.assertNotIn("pair", self.page.score_caption.get_label())   # presses only

    def test_score_rows_in_penalty_order(self) -> None:
        rows = labels(self.page.penalty_list)
        self.assertEqual([t for t, _s in rows],
                         ["Finger Overload", "Hand Imbalance", "Same-Finger Pairs", "Reach",
                          "Layer Switches"])
        self.assertEqual(self.page.score_label.get_label(),
                         "{} / 100".format(insights_page.format_number(82.0)))

    def _comparisons(self) -> list[LayoutComparison]:
        base = report()
        better = dataclasses.replace(
            base, score=96.4, same_finger_rate=0.014, reach_cost=0.5,
            penalties={"finger_overload": 3.2, "hand_imbalance": 0.0, "same_finger": 3.0,
                       "reach": 0.8, "layer_switch": 1.0})
        worse = dataclasses.replace(
            base, score=70.0, same_finger_rate=0.06, left_share=0.7,
            penalties={**base.penalties, "same_finger": 15.0, "hand_imbalance": 9.0})
        busy = dataclasses.replace(
            base, score=75.0, finger_load={**base.finger_load, Finger.L_INDEX: 0.18},
            penalties={**base.penalties, "finger_overload": 9.0})
        same = dataclasses.replace(base, score=82.04)
        return [LayoutComparison("Colemak-DH", better, 14.4),
                LayoutComparison("QWERTY", base, 0.0, current=True),
                LayoutComparison("Dvorak", same, 0.04),
                LayoutComparison("Workman", worse, -12.0),
                LayoutComparison("Busy", busy, -7.0)]

    @staticmethod
    def _suffixes(row: Adw.ActionRow) -> list[Gtk.Label]:
        found = []
        child = row.get_first_child()
        stack = [child]
        while stack:
            widget = stack.pop()
            while widget is not None:
                if isinstance(widget, Gtk.Label) and "numeric" in widget.get_css_classes():
                    found.append(widget)
                stack.append(widget.get_first_child())
                widget = widget.get_next_sibling()
        return found

    def test_alternative_layout_rows(self) -> None:
        self.service.comparisons = self._comparisons()
        self.page.refresh()
        rows = labels(self.page.layouts_list)
        self.assertEqual([t for t, _s in rows], ["Colemak-DH", "QWERTY", "Dvorak", "Workman", "Busy"])
        f = insights_page.format_number
        self.assertEqual(rows[0][1], "Same-finger 3.0% → 1.4% · Reach 0.80 → 0.50".replace(
            "3.0%", insights_page.format_percent(0.03, 1)).replace(
            "0.80", f(0.8, 2)).replace("0.50", f(0.5, 2)).replace("1.4%", insights_page.format_percent(0.014, 1)))
        self.assertTrue(rows[1][1].startswith("Same-finger "))
        self.assertNotIn("→", rows[1][1])
        self.assertEqual(rows[2][1], "Little change")
        pct = insights_page.format_percent
        self.assertEqual(rows[3][1], "Left hand {} → {} · Same-finger {} → {}".format(
            pct(0.5), pct(0.7), pct(0.03, 1), pct(0.06, 1)))
        self.assertTrue(rows[4][1].startswith("Busiest finger {} → {}".format(pct(0.1), pct(0.18))))
        self.assertNotIn("\u2212", rows[3][1] + rows[4][1])
        expected = [("+" + f(14.4), "success"), ("Current", "dim-label"),
                    (f(0.0), "dim-label"), ("\u2212" + f(12.0), "warning"),
                    ("\u2212" + f(7.0), "warning")]
        row = self.page.layouts_list.get_first_child()
        for text, css in expected:
            labels_ = self._suffixes(row)
            texts = [w.get_label() for w in labels_]
            self.assertIn(text, texts)
            self.assertIn(css, labels_[texts.index(text)].get_css_classes())
            row = row.get_next_sibling()
        scores = [w.get_label() for w in self._suffixes(self.page.layouts_list.get_first_child())]
        self.assertIn(f(96.4), scores)

    def test_alternative_layout_filters_and_empty_states(self) -> None:
        self.service.report = report(presses=1234)
        self.page.refresh()
        rows = labels(self.page.layouts_list)
        self.assertEqual(len(rows), 1)
        self.assertIn("2,000", rows[0][0])
        self.assertIn("1,234", rows[0][0])
        self.service.report = report(presses=5000)
        self.page.refresh()
        self.assertIn("no recognisable", labels(self.page.layouts_list)[0][0])
        self.service.apps = [FIREFOX]
        self.service.comparisons = self._comparisons()
        self.page.refresh()
        self.page.app_dropdown.set_selected(1)
        self.assertEqual(self.service.layout_calls[-1][1], "firefox")


if __name__ == "__main__":
    unittest.main()
