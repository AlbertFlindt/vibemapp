from __future__ import annotations

import unittest

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GObject, Gtk  # noqa: E402

from vibemapp.models import KeyGeometry, Layer, Layout, LayoutSync  # noqa: E402
from vibemapp.service import KeyboardService  # noqa: E402
from vibemapp.ui.layout_page import LayoutPage  # noqa: E402

LAYOUT = Layout("abc", "Test", tuple(KeyGeometry(i, float(i), 0.0) for i in range(4)),
                (Layer(0, "Base"), Layer(1, "Symbols")), revision_id="rev1")


class FakeService(KeyboardService):
    def __init__(self) -> None:
        super().__init__()
        self.sync = LayoutSync.UNKNOWN

    def get_layout(self) -> Layout | None:
        return LAYOUT

    def get_layout_sync(self) -> LayoutSync:
        return self.sync

    def get_active_layer(self) -> int:
        return 0

    def set_sync(self, sync: LayoutSync) -> None:
        self.sync = sync
        self.emit("layout-sync-changed")


class FakeController(GObject.Object):
    state = GObject.Property(type=str, default="ready")
    message = GObject.Property(type=str, default="")
    device = None

    def __init__(self) -> None:
        super().__init__()
        self.reloads = 0

    def reload_layout(self) -> None:
        self.reloads += 1


@unittest.skipUnless(Gtk.init_check(), "needs a display")
class SyncBannerTests(unittest.TestCase):
    def setUp(self) -> None:
        Adw.init()
        self.service = FakeService()
        self.controller = FakeController()
        self.page = LayoutPage()
        self.page.setup(self.service, self.controller)

    def tearDown(self) -> None:
        self.page.teardown()

    def test_hidden_while_in_sync_or_unknown(self) -> None:
        self.assertFalse(self.page.sync_banner.get_revealed())
        self.service.set_sync(LayoutSync.FLASHED)
        self.assertFalse(self.page.sync_banner.get_revealed())

    def test_unflashed_changes_in_oryx(self) -> None:
        self.service.set_sync(LayoutSync.ORYX_NEWER)
        self.assertTrue(self.page.sync_banner.get_revealed())
        self.assertIn("aren't flashed", self.page.sync_banner.get_title())
        self.assertIsNone(self.page.sync_banner.get_button_label())

    def test_not_the_flashed_layout_offers_reload(self) -> None:
        self.service.set_sync(LayoutSync.NOT_FLASHED)
        self.assertTrue(self.page.sync_banner.get_revealed())
        self.assertIn("may be wrong", self.page.sync_banner.get_title())
        self.page.sync_banner.emit("button-clicked")
        self.assertEqual(self.controller.reloads, 1)

    def test_hidden_when_not_ready(self) -> None:
        self.service.set_sync(LayoutSync.NOT_FLASHED)
        self.controller.state = "disconnected"
        self.assertFalse(self.page.sync_banner.get_revealed())


if __name__ == "__main__":
    unittest.main()
