from __future__ import annotations

import json
import os
import tempfile
import time
import unittest

from vibemapp.backend.smart_layers import RulesStore, match_rule, rule_from_json, rule_to_json
from vibemapp.backend.steam import (SteamLibrary, VdfError, app_id_from_class,
                                    app_id_from_environ, parse_vdf)
from vibemapp.models import AppRule, FocusedWindow, RuleMatch, SteamGame

LIBRARYFOLDERS = """"libraryfolders"
{
\t"0"
\t{
\t\t"path"\t\t"%(main)s"
\t\t"apps"\t{ "2694490" "1" }
\t}
\t"1"
\t{
\t\t"path"\t\t"%(second)s"
\t}
\t"2"
\t{
\t\t"path"\t\t"%(main)s"
\t}
}
"""


def manifest(app_id: str, name: str) -> str:
    return ('"AppState"\n{\n\t"appid"\t\t"%s"\n\t"name"\t\t"%s"\n'
            '\t"StateFlags"\t\t"4"\n\t"UserConfig"\n\t{\n\t\t"language"\t\t"english"\n\t}\n}\n'
            % (app_id, name))


def write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


class VdfTests(unittest.TestCase):
    def test_parse(self) -> None:
        data = parse_vdf('// comment\n"Root"\n{\n  "Key" "v a l"\n  bare value\n'
                         '  "esc" "a\\"b\\\\c"\n  "Sub" { "x" "1" } "dup" "1" "dup" "2"\n'
                         '  "cond" "y" [$WIN32]\n}\n')
        self.assertEqual(data, {"root": {"key": "v a l", "bare": "value", "esc": 'a"b\\c',
                                         "sub": {"x": "1"}, "dup": "2", "cond": "y"}})

    def test_errors(self) -> None:
        for bad in ('"a" { "b" "c"', '"a" "b" }', '"unterminated', '{ }'):
            with self.assertRaises(VdfError, msg=bad):
                parse_vdf(bad)


class ClassAndEnvironTests(unittest.TestCase):
    def test_class(self) -> None:
        self.assertEqual(app_id_from_class("steam_app_2694490"), "2694490")
        for cls in ("steam_app_0", "steam_app_", "steam_app_12x", "steam", "xsteam_app_1"):
            self.assertIsNone(app_id_from_class(cls), cls)

    def test_environ(self) -> None:
        with tempfile.TemporaryDirectory() as proc:
            def env(pid: int, *pairs: bytes) -> None:
                os.makedirs(os.path.join(proc, str(pid)))
                with open(os.path.join(proc, str(pid), "environ"), "wb") as f:
                    f.write(b"\0".join(pairs) + b"\0")
            env(10, b"HOME=/h", b"SteamAppId=2694490", b"SteamGameId=2694490")
            env(11, b"SteamGameId=999", b"PATH=/bin")
            env(12, b"SteamAppId=0", b"SteamGameId=0")
            env(13, b"SteamAppId=abc")
            env(14, b"SteamAppIdX=5", b"XSteamAppId=6")
            self.assertEqual(app_id_from_environ(10, proc), "2694490")
            self.assertEqual(app_id_from_environ(11, proc), "999")
            for pid in (12, 13, 14, 99, None, 0, -1):
                self.assertIsNone(app_id_from_environ(pid, proc), pid)
            if os.geteuid() != 0:
                os.chmod(os.path.join(proc, "10", "environ"), 0)
                self.assertIsNone(app_id_from_environ(10, proc))       # EACCES
            os.makedirs(os.path.join(proc, "15", "environ"))           # EISDIR-ish oddity
            self.assertIsNone(app_id_from_environ(15, proc))


class LibraryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.main = os.path.join(t, "share", "Steam")
        self.second = os.path.join(t, "mnt", "SteamLibrary")
        self.proc = os.path.join(t, "proc")
        os.makedirs(self.proc)
        # ~/.steam/steam is a symlink to the main root (deduplicated)
        os.makedirs(os.path.join(t, "dotsteam"))
        os.makedirs(os.path.join(self.main, "steamapps"))
        os.symlink(self.main, os.path.join(t, "dotsteam", "steam"))
        write(os.path.join(self.main, "steamapps", "libraryfolders.vdf"),
              LIBRARYFOLDERS % {"main": self.main, "second": self.second})
        sa = os.path.join(self.main, "steamapps")
        write(os.path.join(sa, "appmanifest_2694490.acf"), manifest("2694490", "Path of Exile 2"))
        write(os.path.join(sa, "appmanifest_228980.acf"),
              manifest("228980", "Steamworks Common Redistributables"))
        write(os.path.join(sa, "appmanifest_4628710.acf"), manifest("4628710", "Proton 11.0"))
        write(os.path.join(sa, "appmanifest_1.acf"), "garbage {{{")
        sb = os.path.join(self.second, "steamapps")
        write(os.path.join(sb, "appmanifest_1391110.acf"),
              manifest("1391110", "Steam Linux Runtime 2.0 (soldier)"))
        write(os.path.join(sb, "appmanifest_2483190.acf"), manifest("2483190", "Forza Horizon 6"))
        write(os.path.join(sb, "appmanifest_70.acf"), manifest("70", "alpha game"))
        self.lib = SteamLibrary([self.main, os.path.join(t, "dotsteam", "steam"),
                                 os.path.join(t, "missing")], proc_root=self.proc)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_libraries_deduplicated(self) -> None:
        self.assertEqual(self.lib.library_dirs(),
                         [os.path.realpath(self.main), os.path.realpath(self.second)])

    def test_games_filtered_and_sorted(self) -> None:
        self.assertEqual(self.lib.games(), [
            SteamGame("70", "alpha game"), SteamGame("2483190", "Forza Horizon 6"),
            SteamGame("2694490", "Path of Exile 2")])
        self.assertEqual(self.lib.name_for("4628710"), "Proton 11.0")   # tools still named

    def test_cache_invalidated_by_mtime(self) -> None:
        self.lib.games()
        sig = self.lib._signature
        self.lib.games()
        self.assertIs(self.lib._signature, sig)                       # cached
        path = os.path.join(self.second, "steamapps", "appmanifest_2483190.acf")
        write(path, manifest("2483190", "Forza Horizon 7"))
        os.utime(path, ns=(time.time_ns() + 10**9,) * 2)
        self.assertIn(SteamGame("2483190", "Forza Horizon 7"), self.lib.games())
        os.unlink(path)
        self.assertNotIn("2483190", [g.app_id for g in self.lib.games()])

    def test_no_steam(self) -> None:
        self.assertEqual(SteamLibrary([os.path.join(self.tmp.name, "nope")]).games(), [])

    def test_describe(self) -> None:
        os.makedirs(os.path.join(self.proc, "42"))
        with open(os.path.join(self.proc, "42", "environ"), "wb") as f:
            f.write(b"SteamAppId=2483190\0")
        d = self.lib.describe
        self.assertEqual(d("steam_app_2694490", "PoE", None),
                         FocusedWindow("steam_app_2694490", "PoE", None, "2694490", "Path of Exile 2"))
        self.assertEqual(d("ForzaHorizon6", "Forza", 42).steam_game_name, "Forza Horizon 6")
        self.assertIsNone(d("steam", "Steam", 42).steam_app_id)        # client is never a game
        self.assertIsNone(d("kitty", "sh", 7).steam_app_id)            # pid without environ
        self.assertEqual(d("steam_app_5", "?", None).steam_app_id, "5")
        self.assertIsNone(d("steam_app_5", "?", None).steam_game_name)  # not installed here


class RuleMatchTests(unittest.TestCase):
    RULES = [
        AppRule("2694490", "Path of Exile 2", 3, match=RuleMatch.STEAM_GAME),
        AppRule("", "Any Steam game", 2, match=RuleMatch.STEAM_GAME),
        AppRule("steam_app_.*", "Proton windows", 4, match=RuleMatch.CLASS_PATTERN),
        AppRule("kitty", "Kitty", 1),
        AppRule("fire.*", "Firefox pattern", 5, match=RuleMatch.CLASS_PATTERN,
                title_pattern="YouTube"),
        AppRule("(", "Broken", 6, match=RuleMatch.CLASS_PATTERN),
    ]

    def m(self, cls: str, title: str = "", steam: str | None = None) -> int | None:
        rule = match_rule(self.RULES, cls, title, steam)
        return rule.layer_index if rule else None

    def test_kinds_and_order(self) -> None:
        self.assertEqual(self.m("steam_app_2694490", steam="2694490"), 3)   # specific first
        self.assertEqual(self.m("steam_app_1", steam="1"), 2)                # any game
        self.assertEqual(self.m("ForzaHorizon6", steam="2483190"), 2)        # native game
        self.assertEqual(self.m("steam_app_7"), 4)       # pattern without Steam detection
        self.assertEqual(self.m("kitty"), 1)
        self.assertIsNone(self.m("steam"))
        self.assertIsNone(self.m("xsteam_app_1"))        # fullmatch, not search
        self.assertEqual(self.m("firefox", "YouTube"), 5)
        self.assertIsNone(self.m("firefox", "Docs"))
        self.assertIsNone(self.m("("))                   # invalid regex never matches
        self.assertIsNone(self.m("Kitty"))

    def test_json_backwards_compatible(self) -> None:
        old = {"app_class": "kitty", "display_name": "Kitty", "layer_index": 1}
        self.assertEqual(rule_from_json(old).match, RuleMatch.APP)
        self.assertIsNone(rule_from_json({**old, "match": "something-new"}))
        self.assertIsNone(rule_from_json({**old, "app_class": ""}))           # APP needs a class
        any_game = rule_from_json({"app_class": "", "layer_index": 2, "match": "steam-game"})
        self.assertEqual((any_game.match, any_game.app_class), (RuleMatch.STEAM_GAME, ""))
        for rule in self.RULES:
            data = rule_to_json(rule)
            self.assertIsInstance(data["match"], str)
            self.assertEqual(json.loads(json.dumps(data))["match"], RuleMatch(rule.match).value)
            self.assertEqual(rule_from_json(json.loads(json.dumps(data))), rule)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "smart-layers.json")
            with open(path, "w") as f:      # a v0.2 file without "match"
                json.dump({"version": 1, "layouts": {"L": {"fallback": 0, "rules": [old]}}}, f)
            store = RulesStore(path)
            self.assertEqual(store.get("L")[0][0].match, RuleMatch.APP)
            store.set("L", self.RULES, None)
            self.assertEqual(RulesStore(path).get("L"), (self.RULES, None))
            with open(path) as f:
                self.assertEqual(json.load(f)["layouts"]["L"]["rules"][1]["match"], "steam-game")


if __name__ == "__main__":
    unittest.main()
