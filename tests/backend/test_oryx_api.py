from __future__ import annotations

import os
import tempfile
import time
import unittest

from vibemapp.backend import oryx_api
from vibemapp.backend.oryx_api import (LayoutCache, LayoutFetchError, LayoutRepository,
                                              key_binding_from_json, keycode_label, parse_layout)
from vibemapp.models import KeyboardModel


def _key(code, hold=None, **extra):
    k = {"tap": {"code": code, "layer": extra.pop("layer", None),
                 "modifiers": extra.pop("modifiers", None), "color": None}}
    if hold is not None:
        k["hold"] = hold
    k.update(extra)
    return k


SAMPLE = {
    "hashId": "Bre9W", "title": "Flindt 1.1", "geometry": "voyager",
    "revision": {"hashId": "rbaBD4", "qmkVersion": "25.0", "layers": [
        {"title": "Sym", "color": "#FF0000", "position": 1, "keys": [_key("KC_EXLM")]},
        {"title": "Main", "color": None, "position": 0, "keys": [
            _key("KC_ESCAPE"), _key("KC_A", hold={"code": "MO", "layer": 1}),
            _key("KC_Z", hold={"code": "ALT_T"}), None,
            _key("KC_SPACE", modifiers={"leftGui": True, "leftCtrl": False}),
            _key("KC_UP", customLabel="MoveScreen", glowColor="#ABCDEF"),
            _key("KC_AUDIO_VOL_UP")]},
        {"title": "", "color": "bogus", "position": 2, "keys": []},
    ]},
}


class LabelTests(unittest.TestCase):
    def test_basic_labels(self) -> None:
        cases = {"KC_A": "A", "KC_1": "1", "KC_F12": "F12", "KC_ESCAPE": "Esc",
                 "KC_BSPC": "⌫", "KC_LEFT": "←", "KC_LEFT_GUI": "Super", "KC_QUOTE": "'",
                 "KC_TRANSPARENT": "▽", "KC_NO": "", "KC_KP_PLUS": "+", "KC_KP_7": "7",
                 "DE_UE": "Ü", "DK_AE": "Æ", "DK_LBRC": "[", "CTL_T": "Ctrl",
                 "ALL_T": "Hyper", "RGB_VAI": "Bri+", "KC_MS_BTN1": "Click",
                 "KC_SOMETHING_NEW": "Something New", "": ""}
        for code, label in cases.items():
            self.assertEqual(keycode_label(code), label, code)

    def test_layer_labels(self) -> None:
        self.assertEqual(keycode_label("MO", 2), "L2")
        self.assertEqual(keycode_label("TG", 2), "TG(2)")
        self.assertEqual(keycode_label("OSL", 8), "OSL(8)")

    def test_binding(self) -> None:
        b = key_binding_from_json(4, _key("KC_SPACE", modifiers={"leftGui": True}))
        self.assertEqual((b.index, b.tap_label), (4, "❖Space"))
        b = key_binding_from_json(0, _key("KC_AUDIO_VOL_UP"))
        self.assertEqual(b.glyph, "audio-volume-high-symbolic")
        b = key_binding_from_json(0, {"tap": None, "hold": {"code": "KC_LEFT_SHIFT"}})
        self.assertEqual((b.tap_label, b.hold_label), ("", "Shift"))
        b = key_binding_from_json(0, _key("KC_A", emoji="🙂"))
        self.assertEqual(b.tap_label, "🙂")
        self.assertEqual(key_binding_from_json(9, None).tap_label, "")


class ParseTests(unittest.TestCase):
    def test_parse_layout(self) -> None:
        layout = parse_layout(SAMPLE)
        self.assertEqual((layout.id, layout.title, layout.revision_id),
                         ("Bre9W", "Flindt 1.1", "rbaBD4"))
        self.assertEqual(len(layout.geometry), 52)
        self.assertEqual([l.title for l in layout.layers], ["Main", "Sym", "Layer 2"])
        self.assertEqual([l.color for l in layout.layers], [None, "#ff0000", None])
        keys = layout.layers[0].keys
        self.assertEqual([k.index for k in keys], list(range(7)))
        self.assertEqual((keys[1].tap_label, keys[1].hold_label), ("A", "L1"))
        self.assertEqual(keys[2].hold_label, "Alt")
        self.assertEqual(keys[3].tap_label, "")
        self.assertEqual((keys[5].tap_label, keys[5].color), ("MoveScreen", "#abcdef"))


class RepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = LayoutCache(os.path.join(self.tmp.name, "layouts"))
        self.calls: list[tuple] = []
        self.offline = False

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def fetch(self, lid, rev, geo):
        self.calls.append((lid, rev, geo))
        if self.offline:
            raise LayoutFetchError("offline", offline=True)
        return SAMPLE

    def test_fetch_then_cache_then_offline(self) -> None:
        repo = LayoutRepository(self.cache, self.fetch)
        layout = repo.load_blocking("Bre9W", "rbaBD4", KeyboardModel.VOYAGER)
        self.assertEqual(layout.title, "Flindt 1.1")
        self.assertEqual(self.calls, [("Bre9W", "rbaBD4", "voyager")])
        self.assertTrue(os.path.exists(self.cache.path_for("Bre9W", "rbaBD4")))
        # Exact revision is immutable: served from cache without network.
        repo.load_blocking("Bre9W", "rbaBD4", KeyboardModel.VOYAGER)
        self.assertEqual(len(self.calls), 1)
        # Unknown revision while offline: newest cached revision.
        self.offline = True
        layout = repo.load_blocking("Bre9W", None, KeyboardModel.VOYAGER)
        self.assertEqual(layout.revision_id, "rbaBD4")
        with self.assertRaises(LayoutFetchError):
            repo.load_blocking("other", None, KeyboardModel.VOYAGER)

    def test_async_marshals_to_main_loop(self) -> None:
        from ._util import spin
        import threading
        repo = LayoutRepository(self.cache, self.fetch)
        results = []
        main = threading.current_thread()
        repo.load_async("Bre9W", "rbaBD4", KeyboardModel.VOYAGER,
                        lambda l, e: results.append((l, e, threading.current_thread() is main)))
        self.assertTrue(spin(lambda: bool(results)))
        layout, err, on_main = results[0]
        self.assertIsNone(err)
        self.assertTrue(on_main)
        self.assertEqual(layout.id, "Bre9W")

    def test_query_text(self) -> None:
        q = oryx_api.LAYOUT_QUERY
        for part in ("layout(hashId: $hashId, geometry: $geometry, revisionId: $revisionId)",
                     "layers { title color position keys }"):
            self.assertIn(part, q)


if __name__ == "__main__":
    unittest.main()
