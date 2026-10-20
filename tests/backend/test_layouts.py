from __future__ import annotations

import time
import unittest

from vibemapp.backend import ergo, geometries, layouts
from vibemapp.models import COMPARISON_MIN_PRESSES, KeyBinding, KeyboardModel

MODEL = KeyboardModel.VOYAGER
GEO = geometries.geometry_for(MODEL)
FINGERS = geometries.FINGERS[MODEL]
HOMES = geometries.HOME_KEYS[MODEL]

_CODES = {",": "KC_COMMA", ".": "KC_DOT", "/": "KC_SLASH", ";": "KC_SCOLON"}


def block_index(slot: int) -> int:
    """Oryx index of QWERTY slot ``slot`` on the Voyager."""
    row, col = divmod(slot, 10)
    half, c = divmod(col, 5)
    return half * 26 + (row + 1) * 6 + c + (1 if half == 0 else 0)


SLOT_KEY = [block_index(k) for k in range(30)]


def bindings(danish: bool = False, skip: str = "") -> list[KeyBinding]:
    out = [KeyBinding(i, tap_label="x", tap_code="KC_ENTER") for i in range(len(GEO))]
    for k, ch in enumerate(layouts.SLOT_CHARS):
        if ch in skip:
            continue
        code = _CODES.get(ch, "KC_" + ch.upper())
        if danish and ch == ";":
            code = "DK_AE"
        out[SLOT_KEY[k]] = KeyBinding(SLOT_KEY[k], tap_label=ch.upper(), tap_code=code)
    return out


def typing(weights: dict[str, int]) -> dict:
    return {(0, SLOT_KEY[layouts.SLOT_CHARS.index(c)]): n for c, n in weights.items()}


ENGLISH = {"e": 1200, "t": 900, "a": 800, "o": 750, "i": 700, "n": 700, "s": 650, "h": 600,
           "r": 550, "d": 400, "l": 400, "u": 280, "c": 270, "m": 250, "w": 230, "f": 220,
           "g": 200, "y": 190, "p": 180, "b": 120, "v": 100, "k": 80}


class DefinitionTests(unittest.TestCase):
    def test_layouts_are_permutations(self) -> None:
        for name, s in layouts.LAYOUTS:
            self.assertEqual(sorted(s), sorted(layouts.SLOT_CHARS), name)
        self.assertEqual(layouts.LAYOUTS[0][1], layouts.SLOT_CHARS)

    def test_known_rows(self) -> None:
        d = dict(layouts.LAYOUTS)
        rows = lambda s: (s[:10], s[10:20], s[20:])        # noqa: E731
        self.assertEqual(rows(d["Colemak"]), ("qwfpgjluy;", "arstdhneio", "zxcvbkm,./"))
        self.assertEqual(rows(d["Colemak-DH"]), ("qwfpbjluy;", "arstgmneio", "zxcdvkh,./"))
        self.assertEqual(rows(d["Workman"]), ("qdrwbjfup;", "ashtgyneoi", "zxmcvkl,./"))
        self.assertEqual(rows(d["Dvorak"]), ("/,.pyfgcrl", "aoeuidhtns", ";qjkxbmwvz"))

    def test_slot_keys_are_the_voyager_block(self) -> None:
        self.assertEqual(layouts.find_block(bindings(), GEO), SLOT_KEY)


class FindBlockTests(unittest.TestCase):
    def test_danish_semicolon_is_the_key_right_of_l(self) -> None:
        self.assertEqual(layouts.find_block(bindings(danish=True), GEO), SLOT_KEY)

    def test_missing_letter(self) -> None:
        self.assertIsNone(layouts.find_block(bindings(skip="q"), GEO))

    def test_duplicate_letter(self) -> None:
        b = bindings()
        b[0] = KeyBinding(0, tap_label="A", tap_code="KC_A")
        self.assertIsNone(layouts.find_block(b, GEO))

    def test_language_variant_codes(self) -> None:
        b = [KeyBinding(x.index, tap_label=x.tap_label, tap_code=x.tap_code.replace("KC_", "DK_")
                        if len(x.tap_code) == 4 else x.tap_code) for x in bindings()]
        self.assertEqual(layouts.find_block(b, GEO), SLOT_KEY)

    def test_not_qwerty_shaped(self) -> None:
        b = bindings()
        q, w = SLOT_KEY[0], SLOT_KEY[1]                    # swap Q and W: row x-order breaks
        b[q], b[w] = (KeyBinding(q, tap_label="W", tap_code="KC_W"),
                      KeyBinding(w, tap_label="Q", tap_code="KC_Q"))
        self.assertIsNone(layouts.find_block(b, GEO))

    def test_label_fallback_without_codes(self) -> None:
        b = [KeyBinding(i, tap_label="Enter") for i in range(len(GEO))]
        for k, ch in enumerate(layouts.SLOT_CHARS):
            if ch.isalpha():
                b[SLOT_KEY[k]] = KeyBinding(SLOT_KEY[k], tap_label=ch.upper())
        self.assertEqual(layouts.find_block(b, GEO), SLOT_KEY)


class SlotMapTests(unittest.TestCase):
    def test_qwerty_is_identity(self) -> None:
        self.assertEqual(layouts.slot_map(layouts.SLOT_CHARS, SLOT_KEY, set()), {})

    def test_colemak_moves_with_character(self) -> None:
        moves = layouts.slot_map(dict(layouts.LAYOUTS)["Colemak"], SLOT_KEY, set())
        idx = lambda c: SLOT_KEY[layouts.SLOT_CHARS.index(c)]        # noqa: E731
        self.assertEqual(moves[idx("e")], idx("k"))                  # e lands on QWERTY's K slot
        self.assertEqual(moves[idx("p")], idx("r"))
        self.assertEqual(moves[idx(";")], idx("p"))
        self.assertNotIn(idx("q"), moves)
        self.assertEqual(sorted(moves), sorted(moves.values()))      # a permutation

    def test_fixed_slot(self) -> None:
        d = dict(layouts.LAYOUTS)
        semi = layouts.SLOT_CHARS.index(";")
        moves = layouts.slot_map(d["Colemak"], SLOT_KEY, {semi})
        idx = lambda c: SLOT_KEY[layouts.SLOT_CHARS.index(c)]        # noqa: E731
        self.assertNotIn(idx(";"), moves)                            # Æ stays
        self.assertNotIn(idx(";"), moves.values())
        self.assertEqual(moves[idx("o")], idx("p"))                  # o goes where ; went
        self.assertEqual(len(set(moves.values())), len(moves))


class CompareTests(unittest.TestCase):
    def run_cmp(self, counts, pairs=None, b=None):
        return layouts.compare(b or bindings(), counts, pairs or {}, GEO, FINGERS, HOMES)

    def test_gates(self) -> None:
        self.assertEqual(self.run_cmp({}), [])
        small = typing({"e": COMPARISON_MIN_PRESSES - 1})
        self.assertEqual(self.run_cmp(small), [])
        self.assertTrue(self.run_cmp(typing({"e": COMPARISON_MIN_PRESSES})))
        self.assertEqual(self.run_cmp(typing(ENGLISH), b=bindings(skip="z")), [])

    def test_result_shape(self) -> None:
        counts = typing(ENGLISH)
        res = self.run_cmp(counts)
        self.assertEqual({r.name for r in res}, {n for n, _s in layouts.LAYOUTS})
        scores = [r.report.score for r in res]
        self.assertEqual(scores, sorted(scores, reverse=True))
        base = ergo.report(counts, {}, GEO, FINGERS, HOMES)
        cur = [r for r in res if r.current]
        self.assertEqual([r.name for r in cur], ["QWERTY"])
        self.assertEqual(cur[0].report, base)
        self.assertEqual(cur[0].score_delta, 0.0)
        for r in res:
            self.assertEqual(r.score_delta, round(r.report.score - base.score, 1))
            self.assertEqual(r.report.presses, base.presses)
        self.assertEqual(res, self.run_cmp(counts))

    def test_english_prefers_a_home_row_layout(self) -> None:
        res = self.run_cmp(typing(ENGLISH))
        self.assertNotEqual(res[0].name, "QWERTY")
        self.assertGreater(res[0].score_delta, 0)

    def test_matches_manual_move(self) -> None:
        counts = typing({"e": 3000, "t": 1500, "k": 600})
        pairs = {((0, SLOT_KEY[2]), (0, SLOT_KEY[4])): 50}           # e then t
        res = {r.name: r for r in self.run_cmp(counts, pairs)}
        idx = lambda c: SLOT_KEY[layouts.SLOT_CHARS.index(c)]        # noqa: E731
        s = dict(layouts.LAYOUTS)["Colemak"]
        # e -> slot 'k', t -> slot 'g'... via the layout string, computed by hand.
        want = {(0, idx(s_ch)): counts.get((0, idx(c)), 0)
                for c in "etk" for s_ch in [layouts.SLOT_CHARS[s.index(c)]]}
        wp = {((0, idx(layouts.SLOT_CHARS[s.index("e")])),
               (0, idx(layouts.SLOT_CHARS[s.index("t")]))): 50}
        self.assertEqual(res["Colemak"].report, ergo.report(want, wp, GEO, FINGERS, HOMES))

    def test_other_layers_and_keys_stay(self) -> None:
        counts = typing(ENGLISH)
        enter = next(i for i in range(len(GEO)) if i not in SLOT_KEY)
        counts[(0, enter)] = 5000
        counts[(1, SLOT_KEY[2])] = 3000                              # layer 1 on the e key
        moves = layouts.slot_map(dict(layouts.LAYOUTS)["Colemak"], SLOT_KEY, set())
        c, p = layouts._apply(counts, {}, moves)                     # noqa: SLF001
        self.assertEqual(c[(0, enter)], 5000)
        self.assertEqual(c[(1, SLOT_KEY[2])], 3000)
        self.assertEqual(sum(c.values()), sum(counts.values()))

    def test_danish_ae_stays(self) -> None:
        counts = typing(dict(ENGLISH, **{";": 500}))
        b = bindings(danish=True)
        semi = SLOT_KEY[layouts.SLOT_CHARS.index(";")]
        fixed = layouts._fixed_slots({x.index: x for x in b}, SLOT_KEY)    # noqa: SLF001
        self.assertEqual(fixed, {layouts.SLOT_CHARS.index(";")})
        for name, s in layouts.LAYOUTS:
            moves = layouts.slot_map(s, SLOT_KEY, fixed)
            self.assertNotIn(semi, moves)
            self.assertNotIn(semi, moves.values())
        res = self.run_cmp(counts, b=b)
        self.assertEqual(len(res), 5)

    def test_fast(self) -> None:
        counts = {(l, i): 10 + (i * 7 + l * 3) % 90 for l in range(4) for i in range(len(GEO))}
        pairs = {((l, i), (l, (i * 5) % len(GEO))): 20 for l in range(4) for i in range(len(GEO))}
        t = time.perf_counter()
        self.assertTrue(self.run_cmp(counts, pairs))
        self.assertLess(time.perf_counter() - t, 0.5)


if __name__ == "__main__":
    unittest.main()
