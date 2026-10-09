"""ZSA keyboard discovery via sysfs/hidraw, plus hotplug monitoring.

Each ZSA keyboard exposes several HID interfaces (keyboard, mouse, consumer,
raw HID...). We group ``/sys/class/hidraw/hidrawN`` nodes by their parent USB
device and pick the one whose report descriptor declares the QMK raw-HID
application collection: Usage Page 0xFF60, Usage 0x61. The values come from
zsa/qmk_modules ``oryx/config.h`` (RAW_USAGE_PAGE / RAW_USAGE_ID).

Vendor/product IDs come from the ZSA QMK fork (branch ``firmware25``,
``keyboards/zsa/*/keyboard.json``) and ZSA's udev rules (legacy ``feed:1307``
ErgoDox EZ and ``feed:6060`` Planck EZ).

All filesystem roots are injectable so tests can run against a fake tree.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, replace
from typing import Iterable

from gi.repository import Gio, GLib, GObject

from ..models import Device, KeyboardModel
from . import geometries

log = logging.getLogger(__name__)

ZSA_VENDOR_ID = 0x3297
LEGACY_VENDOR_ID = 0xFEED

RAW_USAGE_PAGE = 0xFF60
RAW_USAGE_ID = 0x61

# (vendor, product) -> model. Source: zsa/qmk_firmware@firmware25 keyboard.json files.
PRODUCT_MODELS: dict[tuple[int, int], KeyboardModel] = {
    (ZSA_VENDOR_ID, 0x1977): KeyboardModel.VOYAGER,
    (ZSA_VENDOR_ID, 0x1969): KeyboardModel.MOONLANDER,   # Moonlander rev A
    (ZSA_VENDOR_ID, 0x1972): KeyboardModel.MOONLANDER,   # Moonlander rev B
    (ZSA_VENDOR_ID, 0x4974): KeyboardModel.ERGODOX_EZ,   # m32u4 base
    (ZSA_VENDOR_ID, 0x4975): KeyboardModel.ERGODOX_EZ,   # m32u4 shine
    (ZSA_VENDOR_ID, 0x4976): KeyboardModel.ERGODOX_EZ,   # m32u4 glow
    (ZSA_VENDOR_ID, 0x2030): KeyboardModel.ERGODOX_EZ,   # stm32 base
    (ZSA_VENDOR_ID, 0x2020): KeyboardModel.ERGODOX_EZ,   # stm32 shine
    (ZSA_VENDOR_ID, 0x2010): KeyboardModel.ERGODOX_EZ,   # stm32 glow
    (ZSA_VENDOR_ID, 0xC6CE): KeyboardModel.PLANCK_EZ,    # base
    (ZSA_VENDOR_ID, 0xC6CF): KeyboardModel.PLANCK_EZ,    # glow
    (LEGACY_VENDOR_ID, 0x1307): KeyboardModel.ERGODOX_EZ,
    (LEGACY_VENDOR_ID, 0x6060): KeyboardModel.PLANCK_EZ,
}

MODEL_NAMES: dict[KeyboardModel, str] = {
    KeyboardModel.VOYAGER: "Voyager",
    KeyboardModel.MOONLANDER: "Moonlander",
    KeyboardModel.ERGODOX_EZ: "ErgoDox EZ",
    KeyboardModel.PLANCK_EZ: "Planck EZ",
    KeyboardModel.UNKNOWN: "ZSA keyboard",
}


def model_for(vendor: int, product: int) -> KeyboardModel:
    if (vendor, product) in PRODUCT_MODELS:
        return PRODUCT_MODELS[(vendor, product)]
    return KeyboardModel.UNKNOWN


def is_zsa_vendor(vendor: int, product: int) -> bool:
    if vendor == ZSA_VENDOR_ID:
        return True
    # 0xFEED is the generic QMK vendor id. Only accept ZSA's legacy products.
    return (vendor, product) in PRODUCT_MODELS


# --- HID report descriptor -------------------------------------------------

def parse_application_collections(desc: bytes) -> list[tuple[int, int]]:
    """Return ``(usage_page, usage)`` for each top-level Application collection."""
    out: list[tuple[int, int]] = []
    usage_page = 0
    global_stack: list[int] = []
    usages: list[int] = []
    depth = 0
    i = 0
    n = len(desc)
    while i < n:
        prefix = desc[i]
        if prefix == 0xFE:                       # long item
            if i + 1 >= n:
                break
            i += 3 + desc[i + 1]
            continue
        size = (0, 1, 2, 4)[prefix & 0x03]
        typ = (prefix >> 2) & 0x03
        tag = prefix >> 4
        data = desc[i + 1:i + 1 + size]
        if len(data) < size:
            break
        value = int.from_bytes(data, "little") if size else 0
        i += 1 + size

        if typ == 1:                             # global
            if tag == 0x0:
                usage_page = value
            elif tag == 0xA:                     # push
                global_stack.append(usage_page)
            elif tag == 0xB and global_stack:    # pop
                usage_page = global_stack.pop()
        elif typ == 2:                           # local
            if tag == 0x0:
                usages.append(value if size == 4 else (usage_page << 16) | value)
        elif typ == 0:                           # main
            if tag == 0xA:                       # collection
                if depth == 0 and value == 0x01:
                    full = usages[0] if usages else (usage_page << 16)
                    out.append(((full >> 16) & 0xFFFF, full & 0xFFFF))
                depth += 1
            elif tag == 0xC:                     # end collection
                depth = max(0, depth - 1)
            usages = []
    return out


def is_raw_hid_descriptor(desc: bytes) -> bool:
    return (RAW_USAGE_PAGE, RAW_USAGE_ID) in parse_application_collections(desc)


# --- sysfs enumeration -----------------------------------------------------

@dataclass(frozen=True)
class HidrawNode:
    name: str            # "hidraw10"
    dev_path: str        # "/dev/hidraw10"
    hid_sysfs: str       # resolved sysfs dir of the HID device
    usb_device: str      # resolved sysfs dir of the parent USB device (stable id)
    vendor: int
    product: int
    hid_name: str
    uniq: str
    is_raw: bool
    accessible: bool


def _read_text(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def _read_bytes(path: str) -> bytes | None:
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def parse_uevent(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            out[key.strip()] = value.strip()
    return out


def parse_hid_id(value: str) -> tuple[int, int, int] | None:
    """``"0003:00003297:00001977"`` -> (bus, vendor, product)."""
    parts = value.split(":")
    if len(parts) != 3:
        return None
    try:
        return int(parts[0], 16), int(parts[1], 16), int(parts[2], 16)
    except ValueError:
        return None


def _usb_device_dir(hid_dir: str) -> str:
    """HID dir -> USB interface dir -> USB device dir (two levels up)."""
    iface = os.path.dirname(hid_dir)
    return os.path.dirname(iface)


def scan_hidraw(sysfs_root: str = "/sys", dev_root: str = "/dev") -> list[HidrawNode]:
    base = os.path.join(sysfs_root, "class", "hidraw")
    try:
        names = sorted(os.listdir(base), key=_natural_key)
    except OSError:
        return []
    nodes: list[HidrawNode] = []
    for name in names:
        if not name.startswith("hidraw"):
            continue
        hid_dir = os.path.realpath(os.path.join(base, name, "device"))
        uevent = _read_text(os.path.join(hid_dir, "uevent"))
        if uevent is None:
            continue
        env = parse_uevent(uevent)
        ids = parse_hid_id(env.get("HID_ID", ""))
        if ids is None:
            continue
        _bus, vendor, product = ids
        if not is_zsa_vendor(vendor, product):
            continue
        desc = _read_bytes(os.path.join(hid_dir, "report_descriptor")) or b""
        dev_path = os.path.join(dev_root, name)
        nodes.append(HidrawNode(
            name=name,
            dev_path=dev_path,
            hid_sysfs=hid_dir,
            usb_device=_usb_device_dir(hid_dir),
            vendor=vendor,
            product=product,
            hid_name=env.get("HID_NAME", ""),
            uniq=env.get("HID_UNIQ", ""),
            is_raw=is_raw_hid_descriptor(desc),
            accessible=os.path.exists(dev_path) and os.access(dev_path, os.R_OK | os.W_OK),
        ))
    return nodes


def _natural_key(name: str) -> tuple[str, int]:
    head = name.rstrip("0123456789")
    tail = name[len(head):]
    return head, int(tail) if tail else -1


def split_serial(serial: str) -> tuple[str | None, str | None]:
    """Oryx firmware serial/version ``"<layout>/<revision>"`` -> ids."""
    serial = serial.strip().strip("\x00")
    if not serial:
        return None, None
    layout, sep, revision = serial.partition("/")
    if not sep:
        return (layout or None), None
    return (layout or None), (revision or None)


def devices_from_nodes(nodes: Iterable[HidrawNode]) -> list[Device]:
    groups: dict[str, list[HidrawNode]] = {}
    for node in nodes:
        groups.setdefault(node.usb_device, []).append(node)
    devices: list[Device] = []
    for usb_dev, members in groups.items():
        raw = next((m for m in members if m.is_raw), None)
        first = raw or members[0]
        model = model_for(first.vendor, first.product)
        # HID_UNIQ carries the USB serial, which Oryx firmware sets to
        # "<layout>/<revision>". It's only a hint; the firmware query wins.
        layout_id, revision_id = split_serial(first.uniq) if "/" in first.uniq else (None, None)
        devices.append(Device(
            id=usb_dev,
            model=model,
            name=MODEL_NAMES.get(model, first.hid_name or "ZSA keyboard"),
            hidraw_path=raw.dev_path if raw else "",
            firmware_version=first.uniq or None,
            layout_id=layout_id,
            revision_id=revision_id,
            geometry=geometries.geometry_for(model),
            oryx_available=bool(raw and raw.accessible),
            access_denied=bool(raw and os.path.exists(raw.dev_path) and not raw.accessible),
        ))
    devices.sort(key=lambda d: (d.hidraw_path == "", _natural_key(os.path.basename(d.hidraw_path))))
    return devices


def enumerate_devices(sysfs_root: str = "/sys", dev_root: str = "/dev") -> list[Device]:
    return devices_from_nodes(scan_hidraw(sysfs_root, dev_root))


def device_permission_problem(device: Device) -> bool:
    """True when a raw HID node exists but we can't open it (missing udev rules)."""
    return device.access_denied


def _fingerprint(devices: list[Device]) -> tuple:
    return tuple((d.id, d.hidraw_path, d.oryx_available, d.access_denied, d.firmware_version)
                 for d in devices)


# --- hotplug -----------------------------------------------------------------

class DeviceMonitor(GObject.Object):
    """Watches ``/dev`` for hidraw nodes and re-enumerates on change.

    Emits ``changed`` (on the main loop) when the device list differs from the
    previous scan. A slow poll covers missed events and late ACL changes from
    udev (uaccess is applied shortly after the node appears).
    """

    __gtype_name__ = "KmlDeviceMonitor"
    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_LAST, None, ())}

    def __init__(self, sysfs_root: str = "/sys", dev_root: str = "/dev",
                 poll_interval_ms: int = 3000, settle_ms: int = 400) -> None:
        super().__init__()
        self._sysfs_root = sysfs_root
        self._dev_root = dev_root
        self._poll_interval_ms = poll_interval_ms
        self._settle_ms = settle_ms
        self._devices: list[Device] = []
        self._fingerprint: tuple = ()
        self._monitor: Gio.FileMonitor | None = None
        self._poll_id = 0
        self._settle_id = 0

    @property
    def devices(self) -> list[Device]:
        return list(self._devices)

    def start(self) -> None:
        self.rescan()
        try:
            gfile = Gio.File.new_for_path(self._dev_root)
            self._monitor = gfile.monitor_directory(Gio.FileMonitorFlags.NONE, None)
            self._monitor.connect("changed", self._on_file_changed)
        except GLib.Error as exc:
            log.warning("Can't monitor %s (%s); polling only", self._dev_root, exc.message)
            self._monitor = None
        if self._poll_interval_ms > 0 and not self._poll_id:
            self._poll_id = GLib.timeout_add(self._poll_interval_ms, self._on_poll)

    def stop(self) -> None:
        if self._monitor is not None:
            self._monitor.cancel()
            self._monitor = None
        for attr in ("_poll_id", "_settle_id"):
            sid = getattr(self, attr)
            if sid:
                GLib.source_remove(sid)
                setattr(self, attr, 0)

    def rescan(self) -> bool:
        """Re-enumerate now. Returns True and emits ``changed`` if the set changed."""
        devices = enumerate_devices(self._sysfs_root, self._dev_root)
        fp = _fingerprint(devices)
        if fp == self._fingerprint:
            return False
        self._devices = devices
        self._fingerprint = fp
        self.emit("changed")
        return True

    def _on_file_changed(self, _mon, gfile: Gio.File, _other, _event) -> None:
        name = gfile.get_basename() or ""
        if not name.startswith("hidraw"):
            return
        if self._settle_id:
            GLib.source_remove(self._settle_id)
        self._settle_id = GLib.timeout_add(self._settle_ms, self._on_settled)

    def _on_settled(self) -> bool:
        self._settle_id = 0
        self.rescan()
        return GLib.SOURCE_REMOVE

    def _on_poll(self) -> bool:
        self.rescan()
        return GLib.SOURCE_CONTINUE


def with_firmware(device: Device, firmware_version: str | None) -> Device:
    """Return a copy of ``device`` updated with the firmware-reported version."""
    if not firmware_version:
        return device
    layout_id, revision_id = split_serial(firmware_version)
    return replace(device, firmware_version=firmware_version,
                   layout_id=layout_id or device.layout_id,
                   revision_id=revision_id or device.revision_id)
