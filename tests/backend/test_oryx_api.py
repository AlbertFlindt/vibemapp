from __future__ import annotations

import os
import tempfile
import time
import unittest

from vibemapp.backend import oryx_api
from vibemapp.backend.oryx_api import (LayoutCache, LayoutFetchError, LayoutRepository,
                                              key_binding_from_json, keycode_label, layout_sync,
                                              parse_layout)
from vibemapp.models import KeyboardModel, LayoutSync


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

    def test_transparent(self) -> None:
        all_null = {"customLabel": None, "doubleTap": None, "emoji": None, "glowColor": None,
                    "hold": None, "icon": None, "tap": None, "tapHold": None}
        b = key_binding_from_json(3, all_null)
        self.assertTrue(b.transparent)
        self.assertEqual((b.index, b.tap_label, b.hold_label, b.glyph), (3, "", "", None))
        b = key_binding_from_json(0, dict(all_null, glowColor="#ABCDEF"))
        self.assertEqual((b.transparent, b.color), (True, "#abcdef"))
        for code in ("KC_TRANSPARENT", "KC_TRNS"):
            b = key_binding_from_json(0, _key(code))
            self.assertTrue(b.transparent, code)
            self.assertEqual((b.tap_label, b.glyph), ("", None))
        self.assertTrue(key_binding_from_json(9, None).transparent)

    def test_not_transparent(self) -> None:
        b = key_binding_from_json(0, _key("KC_NO"))
        self.assertFalse(b.transparent)
        self.assertEqual(b.tap_label, "")
        self.assertFalse(key_binding_from_json(0, _key("KC_A")).transparent)
        self.assertFalse(key_binding_from_json(
            0, {"tap": None, "hold": {"code": "KC_LEFT_SHIFT"}}).transparent)
        self.assertFalse(key_binding_from_json(
            0, {"tap": None, "doubleTap": {"code": "KC_B"}}).transparent)
        b = key_binding_from_json(0, {"tap": None, "customLabel": "Macro"})
        self.assertEqual((b.transparent, b.tap_label), (False, "Macro"))


class HoldActionTests(unittest.TestCase):
    CTRL = {"leftCtrl": True, "leftShift": False, "rightAlt": False}

    def test_hold_combo_for_hold_another_key(self) -> None:
        # Albert's C key as Oryx sends it.
        b = key_binding_from_json(21, _key("KC_C", hold={
            "code": "KC_C", "modifiers": self.CTRL}, tappingTerm=None))
        self.assertEqual((b.hold_label, b.hold_combo, b.tapping_term_ms), ("⌃C", "Ctrl+C", None))
        b = key_binding_from_json(0, _key("KC_A", hold={"code": "KC_ESCAPE"}))
        self.assertEqual((b.hold_label, b.hold_combo), ("Esc", "Esc"))
        both = {"leftCtrl": True, "rightCtrl": True, "leftShift": True, "rightAlt": True}
        b = key_binding_from_json(0, _key("KC_A", hold={"code": "KC_T", "modifiers": both}))
        self.assertEqual(b.hold_combo, "Ctrl+Shift+AltGr+T")

    def test_no_combo_for_layers_and_modifiers(self) -> None:
        for hold in ({"code": "MO", "layer": 1}, {"code": "ALT_T"},
                     {"code": "KC_LEFT_SHIFT"}, {"code": "KC_LCTL", "modifiers": self.CTRL},
                     {"code": "KC_NO"}, {"code": "KC_TRANSPARENT"}, {"code": None}):
            self.assertEqual(key_binding_from_json(0, _key("KC_A", hold=hold)).hold_combo, "",
                             hold)
        self.assertEqual(key_binding_from_json(0, _key("KC_A")).hold_combo, "")
        # A modifier hold with more modifiers keeps its plain label.
        b = key_binding_from_json(0, _key("KC_A", hold={"code": "KC_LCTL",
                                                        "modifiers": self.CTRL}))
        self.assertEqual(b.hold_label, "Ctrl")

    def test_modifiers(self) -> None:
        b = key_binding_from_json(0, _key("KC_LCTL"))
        self.assertEqual((b.modifier, b.hold_modifier), ("Ctrl", ""))
        b = key_binding_from_json(0, _key("KC_LSFT", modifiers=self.CTRL))
        self.assertEqual(b.modifier, "Ctrl+Shift")
        # A home-row mod: hold A = Ctrl.
        b = key_binding_from_json(0, _key("KC_A", hold={"code": "KC_LCTL"}))
        self.assertEqual((b.modifier, b.hold_modifier, b.hold_combo), ("", "Ctrl", ""))
        for code, names in (("KC_RALT", "AltGr"), ("KC_RGUI", "Super"), ("MEH_T", "Ctrl+Shift+Alt"),
                            ("ALL_T", "Ctrl+Shift+Alt+Super"), ("SGUI_T", "Shift+Super")):
            self.assertEqual(oryx_api.modifier_names({"code": code}), names, code)
        for action in (None, {"code": "KC_A"}, {"code": "MO", "layer": 1}, {"code": None}):
            self.assertEqual(oryx_api.modifier_names(action), "", action)

    def test_tapping_terms(self) -> None:
        b = key_binding_from_json(0, _key("KC_A", hold={"code": "KC_B"}, tappingTerm=250))
        self.assertEqual(b.tapping_term_ms, 250)
        for bad in (0, -5, True, "200", 99999):
            self.assertIsNone(oryx_api.tapping_term(bad), bad)
        data = {"hashId": "x", "geometry": "voyager", "revision": {
            "hashId": "r", "config": {"danish": True}, "layers": []}}
        self.assertIsNone(parse_layout(data).tapping_term_ms)
        data["revision"]["config"] = {"tappingTerm": 180}
        self.assertEqual(parse_layout(data).tapping_term_ms, 180)
        data["revision"]["config"] = '{"tappingTerm": 220}'
        self.assertEqual(parse_layout(data).tapping_term_ms, 220)
        data["revision"]["config"] = "not json"
        self.assertIsNone(parse_layout(data).tapping_term_ms)
        del data["revision"]["config"]          # cached before 1.5
        self.assertIsNone(parse_layout(data).tapping_term_ms)


class TapCodeTests(unittest.TestCase):
    def test_tap_code(self) -> None:
        self.assertEqual(key_binding_from_json(0, _key("KC_A")).tap_code, "KC_A")
        self.assertEqual(key_binding_from_json(0, _key("DK_AE")).tap_code, "DK_AE")
        self.assertEqual(key_binding_from_json(0, _key("KC_A", customLabel="x")).tap_code, "KC_A")
        self.assertIsNone(key_binding_from_json(0, None).tap_code)
        self.assertIsNone(key_binding_from_json(0, _key("KC_TRANSPARENT")).tap_code)
        self.assertIsNone(key_binding_from_json(0, {"tap": None, "customLabel": "M"}).tap_code)

    def test_parse_layout_has_tap_codes(self) -> None:
        main = parse_layout({"hashId": "x", "revision": {"layers": [
            {"title": "M", "position": 0, "keys": [_key("KC_A"), None]}]}}).layers[0]
        self.assertEqual([b.tap_code for b in main.keys], ["KC_A", None])


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
        self.assertTrue(keys[3].transparent)
        self.assertFalse(keys[1].transparent)
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
                     "layers { title color position keys }", " config "):
            self.assertIn(part, q)

    def test_latest_revision_async(self) -> None:
        from ._util import spin
        repo = LayoutRepository(self.cache, self.fetch)
        results = []
        repo.latest_revision_async("Bre9W", KeyboardModel.VOYAGER, results.append)
        self.assertTrue(spin(lambda: bool(results)))
        self.assertEqual(results, ["rbaBD4"])
        self.assertEqual(self.calls, [("Bre9W", "latest", "voyager")])
        # Never cached, and offline means "don't know".
        self.assertFalse(os.listdir(self.cache.directory) if os.path.isdir(self.cache.directory) else [])
        self.offline = True
        repo.latest_revision_async("Bre9W", KeyboardModel.VOYAGER, results.append)
        self.assertTrue(spin(lambda: len(results) == 2))
        self.assertIsNone(results[1])


class FetchLatestRevisionTests(unittest.TestCase):
    def test_request_and_answer(self) -> None:
        import io
        import json
        from unittest import mock
        sent = []

        def urlopen(req, timeout):
            sent.append(json.loads(req.data))
            return io.BytesIO(json.dumps(
                {"data": {"layout": {"revision": {"hashId": "n3wRev"}}}}).encode())

        with mock.patch("urllib.request.urlopen", urlopen):
            self.assertEqual(oryx_api.fetch_latest_revision_id("Bre9W", "voyager"), "n3wRev")
        self.assertEqual(sent[0]["variables"], {"hashId": "Bre9W", "geometry": "voyager"})
        self.assertIn('revisionId: \\"latest\\"', json.dumps(sent[0]["query"]))
        self.assertIn("revision { hashId }", sent[0]["query"])

    def test_missing_revision(self) -> None:
        import io
        import json
        from unittest import mock
        body = json.dumps({"data": {"layout": {"revision": None}}}).encode()
        with mock.patch("urllib.request.urlopen", lambda req, timeout: io.BytesIO(body)):
            self.assertIsNone(oryx_api.fetch_latest_revision_id("Bre9W", None))


class LayoutSyncTests(unittest.TestCase):
    def test_states(self) -> None:
        self.assertEqual(layout_sync(None, "rbaBD4", "rbaBD4"), LayoutSync.UNKNOWN)
        self.assertEqual(layout_sync("rbaBD4", None, None), LayoutSync.UNKNOWN)
        self.assertEqual(layout_sync("rbaBD4", "rbaBD4", None), LayoutSync.FLASHED)
        self.assertEqual(layout_sync("rbaBD4", "rbaBD4", "rbaBD4"), LayoutSync.FLASHED)
        self.assertEqual(layout_sync("rbaBD4", "rbaBD4", "n3wRev"), LayoutSync.ORYX_NEWER)
        # Showing another revision wins over anything Oryx says.
        self.assertEqual(layout_sync("rbaBD4", "n3wRev", "n3wRev"), LayoutSync.NOT_FLASHED)


if __name__ == "__main__":
    unittest.main()
