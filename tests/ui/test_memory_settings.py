from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from gi.repository import GLib

from vibemapp.ui import settings as st

SCHEMA = Path(__file__).resolve().parents[2] / "data" / "io.github.albertflindt.Vibemapp.gschema.xml"
# In-memory runs (tests, screenshots) never migrate anything.
INTENTIONAL_DIFFERENCES = {"migrated-from-keymappforlinux"}


class MemoryDefaultsTests(unittest.TestCase):
    def test_every_schema_key_has_the_schema_default(self) -> None:
        root = ET.parse(SCHEMA).getroot()
        for key in root.iter("key"):
            name = key.get("name")
            with self.subTest(key=name):
                self.assertIn(name, st._DEFAULTS)
                if name in INTENTIONAL_DIFFERENCES:
                    continue
                default = GLib.Variant.parse(GLib.VariantType.new(key.get("type")),
                                             key.findtext("default"), None, None).unpack()
                self.assertEqual(st._DEFAULTS[name], default)


class MemoryListTests(unittest.TestCase):
    def test_int_array_round_trip_and_change_signal(self) -> None:
        settings = st.MemorySettings()
        changed: list[str] = []
        settings.connect("changed::overlay-layers", lambda _s, key: changed.append(key))
        self.assertEqual(settings.get_value("overlay-layers").unpack(), [])
        settings.set_value("overlay-layers", GLib.Variant("ai", [1, 3]))
        value = settings.get_value("overlay-layers")
        self.assertEqual(value.get_type_string(), "ai")
        self.assertEqual(value.unpack(), [1, 3])
        self.assertEqual(changed, ["overlay-layers"])
        settings.set_value("overlay-layers", GLib.Variant("ai", [1, 3]))
        self.assertEqual(changed, ["overlay-layers"])

    def test_instances_do_not_share_lists(self) -> None:
        first = st.MemorySettings()
        first.set_value("overlay-layers", GLib.Variant("ai", [2]))
        self.assertEqual(st.MemorySettings().get_value("overlay-layers").unpack(), [])
        self.assertEqual(st._DEFAULTS["overlay-layers"], [])


if __name__ == "__main__":
    unittest.main()
