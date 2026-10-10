from __future__ import annotations

import os
import tempfile
import unittest

from vibemapp.backend import devices
from vibemapp.backend.devices import DeviceMonitor
from vibemapp.models import KeyboardModel

from ._util import spin

# Real Voyager descriptors (raw HID interface and boot keyboard interface).
RAW_DESC = bytes.fromhex(
    "0660ff0961a1010962150026ff009520750881020963150026ff00952075089102c0")
KBD_DESC = bytes.fromhex(
    "05010906a101050719e029e7150025019508750181020501950575010508190129059102c0")


def make_node(root: str, n: int, usb: str, iface: int, vid: int, pid: int,
              desc: bytes, uniq: str = "Bre9W/rbaBD4", accessible: bool = True,
              name: str = "ZSA Technology Labs Voyager") -> None:
    hid = os.path.join(root, "sys", "devices", "pci0", "usb1", usb, f"{usb}:1.{iface}",
                       f"0003:{vid:04X}:{pid:04X}.{n:04X}")
    os.makedirs(hid, exist_ok=True)
    with open(os.path.join(hid, "uevent"), "w") as f:
        f.write(f"DRIVER=hid-generic\nHID_ID=0003:{vid:08X}:{pid:08X}\n"
                f"HID_NAME={name}\nHID_PHYS=usb-0000:0f:00.4-2.2/input{iface}\n"
                f"HID_UNIQ={uniq}\nMODALIAS=hid:b0003\n")
    with open(os.path.join(hid, "report_descriptor"), "wb") as f:
        f.write(desc)
    cls = os.path.join(root, "sys", "class", "hidraw", f"hidraw{n}")
    os.makedirs(cls, exist_ok=True)
    os.symlink(hid, os.path.join(cls, "device"))
    os.makedirs(os.path.join(root, "dev"), exist_ok=True)
    node = os.path.join(root, "dev", f"hidraw{n}")
    with open(node, "w"):
        pass
    os.chmod(node, 0o600 if accessible else 0o000)


class DescriptorTests(unittest.TestCase):
    def test_raw_hid_detection(self) -> None:
        self.assertEqual(devices.parse_application_collections(RAW_DESC), [(0xFF60, 0x61)])
        self.assertTrue(devices.is_raw_hid_descriptor(RAW_DESC))
        self.assertFalse(devices.is_raw_hid_descriptor(KBD_DESC))
        self.assertFalse(devices.is_raw_hid_descriptor(b"\x06\x60"))  # truncated

    def test_models(self) -> None:
        self.assertEqual(devices.model_for(0x3297, 0x1977), KeyboardModel.VOYAGER)
        self.assertEqual(devices.model_for(0x3297, 0x1972), KeyboardModel.MOONLANDER)
        self.assertEqual(devices.model_for(0x3297, 0xC6CF), KeyboardModel.PLANCK_EZ)
        self.assertEqual(devices.model_for(0xFEED, 0x1307), KeyboardModel.ERGODOX_EZ)
        self.assertEqual(devices.model_for(0x3297, 0x0001), KeyboardModel.UNKNOWN)
        self.assertFalse(devices.is_zsa_vendor(0xFEED, 0x0000))

    def test_split_serial(self) -> None:
        self.assertEqual(devices.split_serial("Bre9W/rbaBD4"), ("Bre9W", "rbaBD4"))
        self.assertEqual(devices.split_serial("Bre9W"), ("Bre9W", None))
        self.assertEqual(devices.split_serial(""), (None, None))


class EnumerationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.sys = os.path.join(self.root, "sys")
        self.dev = os.path.join(self.root, "dev")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_voyager_picks_raw_interface(self) -> None:
        make_node(self.root, 9, "5-2.2", 0, 0x3297, 0x1977, KBD_DESC)
        make_node(self.root, 10, "5-2.2", 1, 0x3297, 0x1977, RAW_DESC)
        make_node(self.root, 11, "5-2.2", 2, 0x3297, 0x1977, KBD_DESC, accessible=False)
        make_node(self.root, 3, "1-1", 0, 0x046D, 0xC52B, RAW_DESC, name="Logitech")
        found = devices.enumerate_devices(self.sys, self.dev)
        self.assertEqual(len(found), 1)
        d = found[0]
        self.assertEqual(d.model, KeyboardModel.VOYAGER)
        self.assertEqual(d.name, "Voyager")
        self.assertEqual(d.hidraw_path, os.path.join(self.dev, "hidraw10"))
        self.assertTrue(d.id.endswith("/usb1/5-2.2"))
        self.assertEqual((d.layout_id, d.revision_id), ("Bre9W", "rbaBD4"))
        self.assertEqual(len(d.geometry), 52)
        self.assertEqual(d.oryx_available, os.access(d.hidraw_path, os.R_OK | os.W_OK))
        self.assertEqual(d.access_denied, not d.oryx_available)

    @unittest.skipIf(os.geteuid() == 0, "root bypasses permissions")
    def test_inaccessible_raw_node(self) -> None:
        make_node(self.root, 4, "3-1", 1, 0x3297, 0x1972, RAW_DESC, accessible=False)
        d = devices.enumerate_devices(self.sys, self.dev)[0]
        self.assertEqual(d.model, KeyboardModel.MOONLANDER)
        self.assertFalse(d.oryx_available)
        self.assertTrue(d.access_denied)
        self.assertTrue(devices.device_permission_problem(d))

    def test_no_raw_interface(self) -> None:
        make_node(self.root, 5, "4-1", 0, 0x3297, 0xC6CE, KBD_DESC, uniq="")
        d = devices.enumerate_devices(self.sys, self.dev)[0]
        self.assertEqual(d.hidraw_path, "")
        self.assertFalse(d.oryx_available)
        self.assertFalse(d.access_denied)     # no Oryx interface is not a permission issue
        self.assertIsNone(d.layout_id)

    def test_empty_tree(self) -> None:
        self.assertEqual(devices.enumerate_devices(self.sys, self.dev), [])

    def test_monitor_hotplug(self) -> None:
        os.makedirs(self.dev)
        mon = DeviceMonitor(self.sys, self.dev, poll_interval_ms=100, settle_ms=20)
        changes = []
        mon.connect("changed", lambda _m: changes.append(len(mon.devices)))
        mon.start()
        try:
            make_node(self.root, 10, "5-2.2", 1, 0x3297, 0x1977, RAW_DESC)
            self.assertTrue(spin(lambda: changes == [1]))
            self.assertFalse(mon.rescan())     # unchanged -> no signal
            os.unlink(os.path.join(self.dev, "hidraw10"))
            os.unlink(os.path.join(self.sys, "class", "hidraw", "hidraw10", "device"))
            self.assertTrue(spin(lambda: changes == [1, 0]))
        finally:
            mon.stop()


if __name__ == "__main__":
    unittest.main()
