from __future__ import annotations

import queue
import unittest

from keymappforlinux.backend import oryx
from keymappforlinux.backend.oryx import Cmd, Evt, OryxConnection

from ._util import FakeFirmware

# Captured from a real Voyager (GET_FW_VERSION reply, 32 bytes, no report id).
LIVE_FW_REPLY = bytes.fromhex(
    "0042726539572f726261424434fe0000000000000000000000000000b7ee0008")
LIVE_PROTO_REPLY = bytes.fromhex(
    "fe05fe0000000000d89f002000000000000000000000000000000000b7ee0008")


class EncodeTests(unittest.TestCase):
    def test_packet_has_report_id_and_32_bytes(self) -> None:
        pkt = oryx.encode_command(Cmd.GET_FW_VERSION)
        self.assertEqual(len(pkt), 33)
        self.assertEqual(pkt[0], 0x00)
        self.assertEqual(pkt[1], 0x00)
        self.assertEqual(pkt[2:], bytes(31))

    def test_set_layer_uses_layer_move(self) -> None:
        pkt = oryx.encode_set_layer(3)
        self.assertEqual(pkt[:4], bytes([0x00, 0x04, 0x01, 0x03]))
        off = oryx.encode_set_layer(2, on=False)
        self.assertEqual(off[:4], bytes([0x00, 0x04, 0x00, 0x02]))

    def test_command_ids_match_firmware(self) -> None:
        self.assertEqual(Cmd.PAIRING_INIT, 0x01)
        self.assertEqual(Cmd.SET_LAYER, 0x04)
        self.assertEqual(Cmd.GET_PROTOCOL_VERSION, 0xFE)
        self.assertEqual(Evt.LAYER, 0x05)
        self.assertEqual(Evt.KEYDOWN, 0x06)
        self.assertEqual(Evt.KEYUP, 0x07)

    def test_disallowed_commands_refused(self) -> None:
        for cmd in (Cmd.RGB_CONTROL, Cmd.SET_RGB_LED, Cmd.SET_STATUS_LED, Cmd.DISCONNECT,
                    Cmd.UPDATE_BRIGHTNESS, Cmd.SET_AUTOMOUSE, Cmd.PAIRING_VALIDATE, 0x42):
            with self.assertRaises(ValueError):
                oryx.encode_command(cmd)

    def test_layer_range(self) -> None:
        with self.assertRaises(ValueError):
            oryx.encode_set_layer(-1)
        with self.assertRaises(ValueError):
            oryx.encode_set_layer(32)

    def test_send_rejects_raw_disallowed_packet(self) -> None:
        conn = OryxConnection("/nonexistent", dispatch=lambda fn, *a: fn(*a))
        bad = bytes([0, Cmd.SET_RGB_LED]) + bytes(31)
        with self.assertRaises(ValueError):
            conn.send(bad)


class DecodeTests(unittest.TestCase):
    def test_live_fw_version(self) -> None:
        ev = oryx.decode_report(LIVE_FW_REPLY)
        self.assertEqual(ev.code, Evt.FW_VERSION)
        self.assertEqual(ev.text, "Bre9W/rbaBD4")

    def test_live_protocol_version(self) -> None:
        ev = oryx.decode_report(LIVE_PROTO_REPLY)
        self.assertEqual(ev.code, Evt.PROTOCOL_VERSION)
        self.assertEqual(ev.value, 5)

    def test_layer_and_keys(self) -> None:
        ev = oryx.decode_report(bytes([0x05, 2, 0xFE]) + bytes(29))
        self.assertEqual((ev.code, ev.layer), (Evt.LAYER, 2))
        down = oryx.decode_report(bytes([0x06, 4, 7, 0xFE]) + bytes(28))
        self.assertEqual((down.code, down.col, down.row), (Evt.KEYDOWN, 4, 7))
        up = oryx.decode_report(bytes([0x07, 1, 0, 0xFE]) + bytes(28))
        self.assertEqual((up.code, up.col, up.row), (Evt.KEYUP, 1, 0))

    def test_error_and_unknown(self) -> None:
        ev = oryx.decode_report(bytes([0xFF, 0xFF]) + bytes(30))
        self.assertEqual((ev.code, ev.value), (Evt.ERROR, 0xFF))
        self.assertEqual(oryx.decode_report(bytes([0x42])).name, "UNKNOWN_0x42")
        self.assertIsNone(oryx.decode_report(b""))


class ConnectionTests(unittest.TestCase):
    def _connect(self, fw: FakeFirmware):
        events: queue.SimpleQueue = queue.SimpleQueue()
        closed: queue.SimpleQueue = queue.SimpleQueue()
        conn = OryxConnection("/dev/fake", on_event=events.put, on_closed=closed.put,
                              dispatch=lambda fn, *a: fn(*a), opener=fw.opener)
        conn.open()
        return conn, events, closed

    def test_handshake_and_set_layer(self) -> None:
        fw = FakeFirmware(layer=0)
        conn, events, closed = self._connect(fw)
        try:
            res = conn.handshake(timeout=1.0)
            self.assertEqual(res.firmware_version, "Bre9W/rbaBD4")
            self.assertEqual(res.protocol_version, 5)
            self.assertTrue(res.paired)
            self.assertEqual(res.layer, 0)
            conn.set_layer(2)
            ev = events.get(timeout=1.0)
            self.assertEqual((ev.code, ev.layer), (Evt.LAYER, 2))
            fw.send(0x06, 3, 1, 0xFE)
            ev = events.get(timeout=1.0)
            self.assertEqual((ev.code, ev.col, ev.row), (Evt.KEYDOWN, 3, 1))
            sent = [p[1] for p in fw.received]
            self.assertEqual(sent, [0x00, 0xFE, 0x01, 0x04])
        finally:
            conn.close()
        self.assertIsNone(closed.get(timeout=1.0))

    def test_legacy_firmware_not_paired(self) -> None:
        fw = FakeFirmware(legacy=True, protocol=None)
        conn, _events, _closed = self._connect(fw)
        try:
            res = conn.handshake(timeout=0.5)
            self.assertFalse(res.paired)
            self.assertTrue(res.legacy_pairing)
            self.assertIsNone(res.protocol_version)
        finally:
            conn.close()

    def test_unplug_reports_closed(self) -> None:
        fw = FakeFirmware()
        conn, _events, closed = self._connect(fw)
        conn.handshake(timeout=1.0)
        fw.unplug()
        reason = closed.get(timeout=2.0)
        self.assertIsNotNone(reason)
        self.assertFalse(conn.is_open)
        conn.close()   # idempotent

    def test_close_flushes_final_write(self) -> None:
        fw = FakeFirmware(layer=0)
        conn, _events, _closed = self._connect(fw)
        conn.handshake(timeout=1.0)
        conn.set_layer(1)
        conn.set_layer(0)
        conn.close()
        import time
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and len(fw.set_layer_commands()) < 2:
            time.sleep(0.01)
        self.assertEqual(fw.set_layer_commands(), [(1, 1), (1, 0)])


if __name__ == "__main__":
    unittest.main()
