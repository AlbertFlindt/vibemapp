"""Pointing device: HID descriptor parsing, classification and inference.

The trackpad fixtures are real reports from a Voyager + Navigator trackpad
(hidraw, 2026-11): ``<ms since the first report> <report hex>``. No
physical click was captured, so clicks use synthetic reports.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from vibemapp.backend import pointer
from vibemapp.backend.pointer import (VOYAGER_MOUSE_DESCRIPTOR as MOUSE_DESC,
                                      VOYAGER_TOUCHPAD_DESCRIPTOR as PAD_DESC,
                                      InterfaceClass, KnownTrackballs, MouseInterpreter,
                                      TrackballDetector, TrackpadInterpreter,
                                      voyager_trackpad_report as pad_report)
from vibemapp.models import POINTER_FRAME_MS, PointerAction as A, PointerKind

from .test_devices import KBD_DESC, RAW_DESC


def _capture(text: str) -> list[tuple[float, bytes]]:
    rows = []
    for line in text.strip().splitlines():
        ms, hexdata = line.split()
        rows.append((int(ms) / 1000.0, bytes.fromhex(hexdata)))
    return rows


ONE_FINGER_TAP = _capture('''
0 010300bc03d1030000000000007b1401
16 010300bc03d103000000000000bc1401
20 010300bc03d103000000000000fd1401
28 010300bc03d2030000000000003e1501
32 010300bc03d2030000000000007f1501
40 010300bd03d303000000000000c01501
47 010300bd03d303000000000000011601
55 010300bd03d403000000000000421601
59 010300bd03d403000000000000831601
66 010300bd03d503000000000000c41601
74 010100bf03da03000000000000051701
''')

TWO_FINGER_TAP = _capture('''
0 010300bd06b1020000000000006a9f01
19 010300bd06b102030107033b03ab9f02
27 010300bd06b002030107033b03ec9f02
31 010300bd06af02030107033b032da002
40 010300be06ac02030107033b036ea002
44 010300be06aa02030107033b03afa002
52 010300be06a902030106033b03f0a002
59 010300bf06a602030106033b0331a102
67 010300bf06a302030105033b0372a102
75 010300c006a002030105033c03b3a102
79 010300c0069f02030105033c03f4a102
86 010300c1069e02030104033c0335a202
91 010300c1069d02030104033c0376a202
99 010300c2069b02030104033d03b7a202
107 010300c2069a02030104033c03f8a202
114 010300c3069a02010106033b0339a302
119 010300c30699020000000000007aa301
126 010100c7069002000000000000bba301
''')

# Two fingers moving down; the last report (both lifted) is from the end of the stroke.
TWO_FINGER_SCROLL_DOWN = _capture('''
0 0103004d060d0100000000000085ba01
15 0103004d060d010301ee018001c6ba02
27 0103004c060b010301ee01800107bb02
31 0103004c0609010301ee01800148bb02
40 0103004b0607010301ee01810189bb02
45 0103004b0607010301ee018301cabb02
52 0103004a0609010301ed018e010bbc02
56 0103004a0613010301ec01a0014cbc02
64 0103004a0636010301ec01c6018dbc02
76 0103004b0673010301eb01f701cebc02
83 0103004b06d0010301eb0126020fbd02
91 0103004b062d020301eb01560250bd02
100 0103004c0668020301ed01840291bd02
107 0103004c0682020301f201a802d2bd02
115 0103004d06a3020301f701c90213be02
119 0103004e06bf020301fb01e10254be02
127 0103004e06ed020301ff01090395be02
131 0103004e062203030101023f03d6be02
139 0103004e06550303010402700317bf02
146 0103004e067a0303010602940358bf02
158 0103004c06990303010902af0399bf02
166 0103004b06bb0303010c02d403dabf02
174 0103004906ea0303010f020e041bc002
182 01010018068c010101cc011902afe502
''')


def play(interp, rows, start: float = 100.0):
    out = []
    for t, report in rows:
        out += interp.feed(report, start + t)
    return out


def actions(acts):
    """Actions with consecutive repeats (frame re-sends) collapsed."""
    seq = []
    for a in acts:
        key = (a.action, a.fingers, a.direction)
        if not seq or seq[-1] != key:
            seq.append(key)
    return seq


class DescriptorTests(unittest.TestCase):
    def test_touchpad_fields(self) -> None:
        d = pointer.parse_descriptor(PAD_DESC)
        self.assertTrue(d.uses_report_ids)
        self.assertEqual(d.applications, [(0x0D, 0x05), (0x0D, 0x0E), (0x01, 0x02)])
        xs = d.find(0x01, 0x30, 1)
        self.assertEqual([f.bit_offset for f in xs], [16, 64])
        self.assertEqual((xs[0].size, xs[0].logical_max, xs[0].signed), (16, 2048, False))
        self.assertAlmostEqual(xs[0].units_per_mm(), 2048 / 55.0, places=3)
        self.assertEqual([f.bit_offset for f in d.find(0x0D, 0x42, 1)], [1, 49])   # tip
        self.assertEqual([f.bit_offset for f in d.find(0x0D, 0x51, 1)], [8, 56])   # contact id
        self.assertEqual(d.find(0x0D, 0x54, 1)[0].bit_offset, 112)                 # count
        self.assertEqual([f.bit_offset for f in d.fields
                          if f.usage_page == 0x09 and f.report_id == 1], [116, 117, 118])
        # The mouse collection (report 6) is its own application.
        self.assertEqual({f.application for f in d.find(0x01, 0x30)},
                         {(0x0D, 0x05), (0x01, 0x02)})
        payload = d.payload(ONE_FINGER_TAP[0][1], 1)
        self.assertEqual((xs[0].read(payload), d.find(0x01, 0x31, 1)[0].read(payload)),
                         (956, 977))
        self.assertIsNone(d.payload(b"\x06\x00\x00", 1))

    def test_mouse_fields_are_signed(self) -> None:
        d = pointer.parse_descriptor(MOUSE_DESC)
        self.assertFalse(d.uses_report_ids)
        x = d.find(0x01, 0x30)[0]
        self.assertEqual((x.bit_offset, x.size, x.logical_min, x.logical_max), (8, 8, -127, 127))
        self.assertTrue(x.relative)
        self.assertEqual(x.read(bytes([0, 0xFF, 0, 0, 0])), -1)
        self.assertEqual(d.find(0x0C, 0x238)[0].bit_offset, 32)   # AC Pan

    def test_truncated_descriptor(self) -> None:
        for n in range(0, len(PAD_DESC), 7):
            pointer.parse_descriptor(PAD_DESC[:n])
            TrackpadInterpreter(PAD_DESC[:n]).feed(ONE_FINGER_TAP[0][1], 0.0)
        self.assertFalse(TrackpadInterpreter(b"").valid)
        self.assertFalse(MouseInterpreter(KBD_DESC).valid)

    def test_classify(self) -> None:
        self.assertEqual(pointer.classify(PAD_DESC), InterfaceClass.TOUCHPAD)
        self.assertEqual(pointer.classify(MOUSE_DESC), InterfaceClass.MOUSE)
        self.assertEqual(pointer.classify(KBD_DESC), InterfaceClass.OTHER)
        self.assertEqual(pointer.classify(RAW_DESC), InterfaceClass.OTHER)
        self.assertEqual(pointer.classify(b""), InterfaceClass.OTHER)
        # A shared interface that also carries a keyboard is never read.
        self.assertEqual(pointer.classify(KBD_DESC + MOUSE_DESC), InterfaceClass.OTHER)

    def test_report_builder_matches_hardware(self) -> None:
        self.assertEqual(pad_report([(0, 956 / 2048, 977 / 2048, True)], scan=0x147B),
                         ONE_FINGER_TAP[0][1])


class TrackpadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.t = TrackpadInterpreter(PAD_DESC)

    def test_real_one_finger_tap(self) -> None:
        out = play(self.t, ONE_FINGER_TAP)
        self.assertEqual(actions(out), [(A.TOUCH, 1, ""), (A.TAP, 1, ""), (A.IDLE, 0, "")])
        tap = next(a for a in out if a.action == A.TAP)
        self.assertEqual(tap.kind, PointerKind.TRACKPAD)
        self.assertEqual(len(tap.contacts), 1)
        self.assertAlmostEqual(tap.contacts[0][0], 956 / 2048, places=2)
        self.assertAlmostEqual(tap.contacts[0][1], 977 / 2048, places=2)
        self.assertEqual(out[-1].contacts, ())
        self.assertIsNone(self.t.next_deadline())

    def test_real_two_finger_tap(self) -> None:
        out = play(self.t, TWO_FINGER_TAP)
        self.assertEqual(actions(out), [(A.TOUCH, 1, ""), (A.TOUCH, 2, ""), (A.TOUCH, 1, ""),
                                        (A.TAP, 2, ""), (A.IDLE, 0, "")])
        tap = next(a for a in out if a.action == A.TAP)
        self.assertEqual(len(tap.contacts), 2)

    def test_real_two_finger_scroll(self) -> None:
        out = play(self.t, TWO_FINGER_SCROLL_DOWN)
        seq = actions(out)
        self.assertEqual(seq[:3], [(A.TOUCH, 1, ""), (A.TOUCH, 2, ""), (A.SCROLL, 2, "down")])
        self.assertEqual(seq[-1], (A.IDLE, 0, ""))
        self.assertNotIn(A.TAP, [s[0] for s in seq])
        scrolls = [a for a in out if a.action == A.SCROLL]
        self.assertGreater(len(scrolls), 2)                 # re-sent while moving
        self.assertTrue(all(len(a.contacts) == 2 for a in scrolls))
        self.assertGreater(sum(a.motion[1] for a in scrolls), 0.1)    # downwards, 0..1 units
        self.assertGreater(scrolls[-1].contacts[0][1], scrolls[0].contacts[0][1])

    def test_frames_are_rate_limited(self) -> None:
        out, times = [], []
        now = 0.0
        for k in range(100):                  # a 4 ms report rate, finger sliding
            now = k * 0.004
            got = self.t.feed(pad_report([(0, 0.2 + k * 0.004, 0.5, True)]), now)
            out += got
            times += [now] * len(got)
        moves = [t for a, t in zip(out, times) if a.action == A.MOVE]
        self.assertGreater(len(moves), 10)
        gaps = [b - a for a, b in zip(moves, moves[1:])]
        self.assertGreaterEqual(min(gaps), POINTER_FRAME_MS / 1000 - 1e-9)
        total = sum(a.motion[0] for a in out if a.action in (A.TOUCH, A.MOVE))
        self.assertGreater(total, 0.3)
        # The last update isn't lost when reports stop: a deadline flushes it.
        deadline = self.t.next_deadline()
        self.assertIsNotNone(deadline)
        self.assertLessEqual(deadline, now + POINTER_FRAME_MS / 1000 + 1e-9)

    def test_rest_goes_idle_then_moves_again(self) -> None:
        now = 0.0
        out = []
        for k in range(20):                   # move...
            now = k * 0.008
            out += self.t.feed(pad_report([(0, 0.2 + k * 0.01, 0.5, True)]), now)
        for k in range(1, 50):                # ...then rest for ~400 ms
            out += self.t.feed(pad_report([(0, 0.39, 0.5, True)]), now + k * 0.008)
        self.assertEqual(actions(out)[-1], (A.IDLE, 1, ""))
        self.assertEqual(len(out[-1].contacts), 1)       # fingers still shown
        now += 0.4
        more = []
        for k in range(10):
            more += self.t.feed(pad_report([(0, 0.39 + k * 0.01, 0.5, True)]), now + k * 0.008)
        self.assertEqual(actions(more)[0], (A.MOVE, 1, ""))

    def test_click_is_not_a_tap(self) -> None:
        out = self.t.feed(pad_report([(0, 0.5, 0.5, True)]), 0.0)
        out += self.t.feed(pad_report([(0, 0.5, 0.5, True)], frozenset({1})), 0.03)
        click = out[-1]
        self.assertEqual((click.action, click.button, click.buttons, click.fingers),
                         (A.CLICK, 1, frozenset({1}), 1))
        out = self.t.feed(pad_report([(0, 0.5, 0.5, True)]), 0.06)
        self.assertEqual((out[0].action, out[0].button, out[0].buttons),
                         (A.RELEASE, 1, frozenset()))
        out = self.t.feed(pad_report([(0, 0.5, 0.5, False)]), 0.09)
        self.assertEqual(actions(out), [(A.IDLE, 0, "")])

    def test_slow_touch_is_not_a_tap(self) -> None:
        self.t.feed(pad_report([(0, 0.5, 0.5, True)]), 0.0)
        self.t.feed(pad_report([(0, 0.5, 0.5, True)]), 0.15)
        out = self.t.feed(pad_report([(0, 0.5, 0.5, False)]), 0.25)
        self.assertEqual(actions(out), [(A.IDLE, 0, "")])

    def test_missed_lift_times_out(self) -> None:
        self.t.feed(pad_report([(0, 0.5, 0.5, True)]), 0.0)
        self.assertAlmostEqual(self.t.next_deadline(), TrackpadInterpreter.STALE_S)
        self.assertEqual(self.t.poll(0.1), [])
        out = self.t.poll(0.6)
        self.assertEqual(actions(out), [(A.IDLE, 0, "")])      # no TAP
        self.assertIsNone(self.t.next_deadline())

    def test_three_fingers_in_hybrid_reports(self) -> None:
        first = bytearray(pad_report([(0, 0.3, 0.5, True), (1, 0.5, 0.5, True)]))
        first[15] = 3                                  # 3 contacts, 2 in this report
        second = bytearray(pad_report([(2, 0.7, 0.5, True)]))
        second[15] = 0                                 # continuation
        self.assertEqual(self.t.feed(bytes(first), 0.0), [])
        out = self.t.feed(bytes(second), 0.001)
        self.assertEqual(actions(out), [(A.TOUCH, 3, "")])
        self.assertEqual(len(out[0].contacts), 3)

    def test_reset_forgets_touch(self) -> None:
        self.t.feed(pad_report([(0, 0.5, 0.5, True)]), 0.0)
        self.t.reset()
        self.assertIsNone(self.t.next_deadline())
        self.assertEqual(actions(self.t.feed(pad_report([(0, 0.5, 0.5, True)]), 1.0)),
                         [(A.TOUCH, 1, "")])


def mouse(dx=0, dy=0, wheel=0, pan=0, buttons=0) -> bytes:
    return bytes([buttons, dx & 0xFF, dy & 0xFF, wheel & 0xFF, pan & 0xFF])


class MouseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.m = MouseInterpreter(MOUSE_DESC)

    def test_move_frames_and_idle(self) -> None:
        out = self.m.feed(mouse(5, -3), 0.0)
        self.assertEqual(len(out), 1)
        self.assertEqual((out[0].kind, out[0].action, out[0].motion),
                         (PointerKind.TRACKBALL, A.MOVE, (5.0, -3.0)))
        self.assertTrue(self.m.last_moved)
        self.assertEqual(self.m.feed(mouse(2, 2), 0.005), [])      # within a frame
        out = self.m.feed(mouse(1, 1), 0.02)    # the pending frame goes out first
        self.assertEqual((out[0].action, out[0].motion), (A.MOVE, (2.0, 2.0)))
        self.assertAlmostEqual(self.m.next_deadline(), 0.02 + 0.016)
        out = self.m.poll(0.04)                 # ...and this report's motion next
        self.assertEqual((out[0].action, out[0].motion), (A.MOVE, (1.0, 1.0)))
        self.assertAlmostEqual(self.m.next_deadline(), 0.02 + 0.3)
        self.assertEqual(self.m.poll(0.1), [])
        out = self.m.poll(0.33)
        self.assertEqual([a.action for a in out], [A.IDLE])
        self.assertIsNone(self.m.next_deadline())

    def test_wheel_and_pan(self) -> None:
        out = self.m.feed(mouse(wheel=1), 0.0)
        self.assertEqual((out[0].action, out[0].direction, out[0].motion),
                         (A.SCROLL, "up", (0.0, 1.0)))
        out = self.m.feed(mouse(wheel=-1), 0.03)
        self.assertEqual((out[0].direction, out[0].motion), ("down", (0.0, -1.0)))
        out = self.m.feed(mouse(pan=2), 0.06)
        self.assertEqual((out[0].direction, out[0].motion), ("right", (2.0, 0.0)))
        self.assertFalse(self.m.last_moved)

    def test_buttons(self) -> None:
        out = self.m.feed(mouse(buttons=0b010), 0.0)
        self.assertEqual((out[0].action, out[0].button, out[0].buttons),
                         (A.CLICK, 2, frozenset({2})))
        out = self.m.feed(mouse(buttons=0), 0.1)
        self.assertEqual((out[0].action, out[0].button), (A.RELEASE, 2))
        self.assertIsNone(self.m.next_deadline())


class TrackballDetectorTests(unittest.TestCase):
    def test_motion_without_keys(self) -> None:
        d = TrackballDetector()
        self.assertFalse(d.motion(10.0))
        self.assertFalse(d.motion(10.1))
        self.assertTrue(d.motion(10.16))

    def test_mouse_keys_dont_count(self) -> None:
        d = TrackballDetector()
        d.key_event(True, 10.0)                 # a mouse key held
        for k in range(30):
            self.assertFalse(d.motion(10.0 + k * 0.02))
        d.key_event(False, 10.6)                # released: trailing motion
        self.assertFalse(d.motion(10.65))
        self.assertFalse(d.motion(10.75))       # first candidate after the grace
        d.key_event(True, 10.8)                 # a key went down in between
        d.key_event(False, 10.85)
        self.assertFalse(d.motion(11.2))
        self.assertTrue(d.motion(11.4))

    def test_gap_restarts(self) -> None:
        d = TrackballDetector()
        self.assertFalse(d.motion(1.0))
        self.assertFalse(d.motion(3.0))         # a lone twitch long ago doesn't count
        self.assertTrue(d.motion(3.2))


class KnownTrackballsTests(unittest.TestCase):
    def test_roundtrip_and_corrupt(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "state", "pointer.json")
            store = KnownTrackballs(path)
            self.assertNotIn("voyager:abc", store)
            store.add("voyager:abc")
            self.assertIn("voyager:abc", KnownTrackballs(path))
            with open(path) as f:
                self.assertEqual(json.load(f)["trackballs"], ["voyager:abc"])
            with open(path, "w") as f:
                f.write("{nope")
            with self.assertLogs("vibemapp.backend.pointer", "WARNING"):
                self.assertNotIn("voyager:abc", KnownTrackballs(path))

    def test_default_path(self) -> None:
        old = os.environ.get("XDG_STATE_HOME")
        os.environ["XDG_STATE_HOME"] = "/x/state"
        try:
            self.assertEqual(pointer.default_state_path(), "/x/state/vibemapp/pointer.json")
        finally:
            if old is None:
                del os.environ["XDG_STATE_HOME"]
            else:
                os.environ["XDG_STATE_HOME"] = old


if __name__ == "__main__":
    unittest.main()
