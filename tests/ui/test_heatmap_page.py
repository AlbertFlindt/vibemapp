from __future__ import annotations

import unittest

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GObject, Gtk  # noqa: E402

from vibemapp.models import (HeatmapApp, HeatmapPeriod, KeyGeometry, Layer,  # noqa: E402
                             Layout)
from vibemapp.service import KeyboardService  # noqa: E402
from vibemapp.ui.heatmap_page import HeatmapPage  # noqa: E402

LAYOUT = Layout("abc", "Test", tuple(KeyGeometry(i, float(i), 0.0) for i in range(4)),
                (Layer(0, "Base"), Layer(1, "Symbols")))
FIREFOX = HeatmapApp("firefox", "Firefox", 30)
KITTY = HeatmapApp("kitty", "kitty", 10)
GAME = HeatmapApp("steam:620", "Portal 2", 5)


class FakeService(KeyboardService):
    def __init__(self) -> None:
        super().__init__()
        self.apps: list[HeatmapApp] = []
        self.calls: list[tuple] = []

    def get_layout(self) -> Layout | None:
        return LAYOUT

    def list_heatmap_apps(self, period: HeatmapPeriod) -> list[HeatmapApp]:
        return list(self.apps)

    def get_key_counts(self, period, layer=None, app=None) -> dict[int, int]:
        self.calls.append((period, layer, app))
        return {0: 3, 1: 1}


class FakeController(GObject.Object):
    state = GObject.Property(type=str, default="ready")


@unittest.skipUnless(Gtk.init_check(), "needs a display")
class AppFilterTests(unittest.TestCase):
    def setUp(self) -> None:
        Adw.init()
        self.service = FakeService()
        self.service.props.heatmap_recording = True
        self.page = HeatmapPage()
        self.page.setup(self.service, FakeController())

    def tearDown(self) -> None:
        self.page.teardown()

    def items(self) -> list[str]:
        model = self.page.app_dropdown.get_model()
        return [model.get_string(i) for i in range(model.get_n_items())] if model else []

    def test_hidden_without_apps(self) -> None:
        self.assertFalse(self.page.app_dropdown.get_visible())
        self.assertIsNone(self.page.app_filter())

    def test_items_are_all_apps_then_names(self) -> None:
        self.service.apps = [FIREFOX, KITTY]
        self.page.refresh()
        self.assertTrue(self.page.app_dropdown.get_visible())
        self.assertEqual(self.items(), ["All Apps", "Firefox", "kitty"])
        self.assertEqual(self.page.app_dropdown.get_selected(), 0)

    def test_selection_passes_key_to_service(self) -> None:
        self.service.apps = [FIREFOX, GAME]
        self.page.refresh()
        self.page.app_dropdown.set_selected(2)
        self.assertEqual(self.page.app_filter(), "steam:620")
        self.assertEqual(self.service.calls[-1][2], "steam:620")
        self.page.app_dropdown.set_selected(0)
        self.assertIsNone(self.service.calls[-1][2])

    def test_selection_kept_by_key_across_rebuild(self) -> None:
        self.service.apps = [FIREFOX, KITTY]
        self.page.refresh()
        self.page.app_dropdown.set_selected(2)           # kitty
        self.service.apps = [GAME, KITTY, FIREFOX]       # reordered, one added
        self.page.refresh()
        self.assertEqual(self.page.app_filter(), "kitty")
        self.assertEqual(self.page.app_dropdown.get_selected(), 2)
        self.assertEqual(self.service.calls[-1][2], "kitty")

    def test_falls_back_to_all_apps_when_app_disappears(self) -> None:
        self.service.apps = [FIREFOX, KITTY]
        self.page.refresh()
        self.page.app_dropdown.set_selected(2)
        self.service.apps = [FIREFOX]
        self.page.refresh()
        self.assertEqual(self.page.app_dropdown.get_selected(), 0)
        self.assertIsNone(self.service.calls[-1][2])
        self.service.apps = []
        self.page.refresh()
        self.assertFalse(self.page.app_dropdown.get_visible())


if __name__ == "__main__":
    unittest.main()
