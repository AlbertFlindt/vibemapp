"""ZsaKeyboardService: the keyboard's pointing device (SPEC 3.5)."""

from __future__ import annotations

import os
import shutil
import socket
import tempfile
import unittest

from vibemapp.backend.oryx import OryxConnection
from vibemapp.backend.oryx_api import LayoutCache, LayoutRepository
from vibemapp.backend.pointer import (VOYAGER_MOUSE_DESCRIPTOR, KnownTrackballs, VOYAGER_TOUCHPAD_DESCRIPTOR,
                                      voyager_trackpad_report)
from vibemapp.backend.zsa_service import ZsaKeyboardService
from vibemapp.models import Navigator, PointerAction, PointerKind

from ._util import FakeFirmware, drain, spin
from .test_devices import KBD_DESC, RAW_DESC, make_node
from .test_service import FakeWatcher
from .test_oryx_api import NAVIGATORS_TRACKPAD_RIGHT, SAMPLE, with_navigators


class FakeNodes:
    """Socket pairs standing in for hidraw nodes the service reads."""

    def __init__(self) -> None:
        self.opened: list[str] = []
        self.peers: dict[str, socket.socket] = {}
        self._keep: list[socket.socket] = []

    def opener(self, path: str) -> int:
        self.opened.append(os.path.basename(path))
        host, dev = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        self.peers[os.path.basename(path)] = dev
        self._keep.append(host)
        return os.dup(host.fileno())

    def send(self, name: str, data: bytes) -> bool:
        try:
            self.peers[name].send(data)
            return True
        except OSError:
            return False

    def close(self) -> None:
        for s in self._keep + list(self.peers.values()):
            s.close()


class PointerServiceBase(unittest.TestCase):
    TOUCHPAD = True
    # What Oryx sends for the layout; SAMPLE has ``navigators: null``.
    LAYOUT = SAMPLE

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = self.root = self.tmp.name
        make_node(root, 9, "5-2.2", 0, 0x3297, 0x1977, KBD_DESC)
        make_node(root, 10, "5-2.2", 1, 0x3297, 0x1977, RAW_DESC)
        make_node(root, 11, "5-2.2", 2, 0x3297, 0x1977, VOYAGER_MOUSE_DESCRIPTOR)
        if self.TOUCHPAD:
            make_node(root, 13, "5-2.2", 4, 0x3297, 0x1977, VOYAGER_TOUCHPAD_DESCRIPTOR)
        self.state = os.path.join(root, "state", "pointer.json")
        self.fw = FakeFirmware(layer=0)
        self.nodes = FakeNodes()
        self.svc = self.make()

    def make(self) -> ZsaKeyboardService:
        self.signals: list[tuple] = []
        root = self.root
        svc = ZsaKeyboardService(
            sysfs_root=os.path.join(root, "sys"), dev_root=os.path.join(root, "dev"),
            rules_path=os.path.join(root, "config", "smart-layers.json"),
            focus_watcher=FakeWatcher(),
            repository=LayoutRepository(LayoutCache(os.path.join(root, "cache")),
                                        fetcher=lambda lid, rev, geo: self.LAYOUT),
            connection_factory=lambda p, e, c: OryxConnection(p, e, c, opener=self.fw.opener),
            poll_interval_ms=0, heatmap_dir=os.path.join(root, "hm"),
            pointer_state_path=self.state, pointer_opener=self.nodes.opener)
        svc.connect("pointer-changed", lambda s: self.signals.append(("changed", s.get_pointer_kind())))
        svc.connect("pointer-activity", lambda s, a: self.signals.append(("activity", a)))
        svc.connect("connected", lambda s, d: self.signals.append(("connected",)))
        svc.connect("layout-loaded", lambda s, l: self.signals.append(("layout",)))
        return svc

    def tearDown(self) -> None:
        self.svc.shutdown()
        drain(0.05)
        self.fw.unplug()
        self.nodes.close()
        self.tmp.cleanup()

    def activities(self):
        return [s[1] for s in self.signals if s[0] == "activity"]

    def wait_connected(self) -> None:
        self.assertTrue(spin(lambda: ("connected",) in self.signals), self.signals)

    def remake(self, layout: dict) -> None:
        """Start again with Oryx answering ``layout`` (and nothing cached)."""
        self.svc.shutdown()
        shutil.rmtree(os.path.join(self.root, "cache"), ignore_errors=True)
        self.signals.clear()
        self.LAYOUT = layout
        self.svc = self.make()

    def wait_layout(self) -> None:
        self.assertTrue(spin(lambda: ("layout",) in self.signals), self.signals)

    def roll(self, seconds: float = 0.35) -> None:
        steps = int(seconds / 0.02)
        for _ in range(steps):
            self.nodes.send("hidraw11", bytes([0, 3, 1, 0, 0]))
            drain(0.02)


class TrackpadServiceTests(PointerServiceBase):
    def test_detected_and_read_only_while_tracking(self) -> None:
        self.wait_connected()
        self.assertEqual(self.svc.get_pointer_kind(), PointerKind.TRACKPAD)
        self.assertIn(("changed", PointerKind.TRACKPAD), self.signals)
        self.assertEqual(self.nodes.opened, [])        # nothing read yet; mouse not needed
        self.svc.props.pointer_tracking = True
        self.assertEqual(self.nodes.opened, ["hidraw13"])
        self.nodes.send("hidraw13", voyager_trackpad_report([(0, 0.5, 0.5, True)]))
        self.assertTrue(spin(lambda: bool(self.activities())))
        first = self.activities()[0]
        self.assertEqual((first.kind, first.action, first.fingers),
                         (PointerKind.TRACKPAD, PointerAction.TOUCH, 1))
        # A missed lift still ends in IDLE, through the service's timer.
        self.assertTrue(spin(lambda: self.activities()[-1].action == PointerAction.IDLE,
                             timeout=2.0))
        self.svc.props.pointer_tracking = False
        n = len(self.activities())
        self.nodes.send("hidraw13", voyager_trackpad_report([(0, 0.5, 0.5, True)]))
        drain(0.1)
        self.assertEqual(len(self.activities()), n)
        self.svc.props.pointer_tracking = True        # opened again
        self.assertEqual(self.nodes.opened, ["hidraw13", "hidraw13"])

    def test_disconnect_clears_kind(self) -> None:
        self.wait_connected()
        self.svc.props.pointer_tracking = True
        self.svc.disconnect_device()
        self.assertIsNone(self.svc.get_pointer_kind())
        self.assertEqual(self.signals[-1], ("changed", None))
        self.assertIsNone(self.svc._touchpad_reader)

    def test_unreadable_node_is_quiet(self) -> None:
        self.wait_connected()

        def deny(path: str) -> int:
            raise PermissionError(13, "Permission denied")
        self.svc._pointer_opener = deny
        with self.assertLogs("vibemapp.backend.zsa_service", "INFO"):
            self.svc.props.pointer_tracking = True
        self.assertEqual(self.svc.get_pointer_kind(), PointerKind.TRACKPAD)


class TrackballServiceTests(PointerServiceBase):
    """The motion heuristic: only while the layout doesn't name its Navigators
    (here: an Oryx answer without the field, like a pre-1.10 cache)."""
    TOUCHPAD = False
    LAYOUT = with_navigators(legacy=True)

    def test_motion_without_keys_is_a_trackball(self) -> None:
        self.wait_connected()
        self.assertIsNone(self.svc.get_pointer_kind())
        self.assertEqual(self.nodes.opened, ["hidraw11"])    # read to spot a trackball
        self.svc.props.pointer_tracking = True
        self.roll()
        self.assertEqual(self.svc.get_pointer_kind(), PointerKind.TRACKBALL)
        self.assertIn(("changed", PointerKind.TRACKBALL), self.signals)
        self.assertTrue(os.path.exists(self.state))
        self.roll(0.1)
        acts = self.activities()
        self.assertTrue(acts and acts[0].kind == PointerKind.TRACKBALL
                        and acts[0].action == PointerAction.MOVE, acts)
        self.assertTrue(spin(lambda: self.activities()[-1].action == PointerAction.IDLE))

        # Known on the next start, before it moves.
        self.svc.shutdown()
        self.svc = self.make()
        self.wait_connected()
        self.assertEqual(self.svc.get_pointer_kind(), PointerKind.TRACKBALL)

    def test_mouse_keys_are_not_a_trackball(self) -> None:
        self.wait_connected()
        self.assertTrue(spin(lambda: self.svc._conn is not None))
        self.fw.send(0x06, 0, 5, 0xFE)                     # a (mouse) key held
        self.assertTrue(spin(lambda: self.svc.get_pressed_keys() == {24}))
        self.roll()
        self.assertIsNone(self.svc.get_pointer_kind())
        self.assertFalse(os.path.exists(self.state))
        self.assertEqual(self.activities(), [])



class LayoutNavigatorTests(PointerServiceBase):
    """The layout's ``navigators`` come before the hardware guesses."""
    TOUCHPAD = False

    def test_layout_trackball_needs_no_motion(self) -> None:
        self.remake(with_navigators({"left": {"kind": "trackball"}}))
        self.wait_layout()
        self.assertEqual(self.svc.get_pointer_kind(), PointerKind.TRACKBALL)
        self.assertEqual(self.svc.get_layout().navigators,
                         (Navigator("left", PointerKind.TRACKBALL),))
        self.assertIn(("changed", PointerKind.TRACKBALL), self.signals)
        self.assertFalse(os.path.exists(self.state))      # nothing to remember
        self.svc.props.pointer_tracking = True
        self.roll(0.1)                                    # activity from the first roll
        acts = self.activities()
        self.assertTrue(acts and acts[0].kind == PointerKind.TRACKBALL
                        and acts[0].action == PointerAction.MOVE, acts)

    def test_layout_trackpad_without_touchpad_interface(self) -> None:
        self.remake(with_navigators(NAVIGATORS_TRACKPAD_RIGHT))
        self.wait_connected()
        self.assertTrue(spin(lambda: self.svc._mouse_reader is not None))  # heuristic first
        self.wait_layout()
        self.assertEqual(self.svc.get_pointer_kind(), PointerKind.TRACKPAD)
        self.assertEqual(self.signals.count(("changed", PointerKind.TRACKPAD)), 1)
        self.assertIsNone(self.svc._mouse_reader)         # no trackball to look for

    def test_layout_without_navigator_stops_the_heuristic(self) -> None:
        self.wait_layout()                                # SAMPLE: navigators null
        self.assertIsNone(self.svc.get_pointer_kind())
        self.assertIsNone(self.svc._mouse_reader)
        self.roll()
        self.assertIsNone(self.svc.get_pointer_kind())
        self.assertFalse(os.path.exists(self.state))

    def test_layout_overrides_a_remembered_trackball(self) -> None:
        self.wait_connected()
        KnownTrackballs(self.state).add(self.svc._pointer_key)
        self.remake(with_navigators(NAVIGATORS_TRACKPAD_RIGHT))
        self.wait_connected()
        self.assertEqual(self.svc.get_pointer_kind(), PointerKind.TRACKBALL)   # until it loads
        self.wait_layout()
        self.assertEqual(self.svc.get_pointer_kind(), PointerKind.TRACKPAD)
        self.assertEqual(self.signals[-2:], [("changed", PointerKind.TRACKPAD), ("layout",)])


class BothNavigatorsTests(PointerServiceBase):
    """A trackpad on one half and a trackball on the other."""
    LAYOUT = with_navigators({"left": {"kind": "trackball"}, "right": {"kind": "trackpad"}})

    def test_trackpad_reported_both_read(self) -> None:
        self.wait_layout()
        self.assertEqual(self.svc.get_pointer_kind(), PointerKind.TRACKPAD)
        self.assertIsNotNone(self.svc._mouse_reader)
        self.svc.props.pointer_tracking = True
        self.nodes.send("hidraw13", voyager_trackpad_report([(0, 0.5, 0.5, True)]))
        self.roll(0.1)
        kinds = {a.kind for a in self.activities()}
        self.assertEqual(kinds, {PointerKind.TRACKPAD, PointerKind.TRACKBALL})


if __name__ == "__main__":
    unittest.main()
