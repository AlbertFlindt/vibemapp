"""Keyboard information dialog."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from ..models import Device, KeyboardModel  # noqa: E402
from .controller import DeviceController  # noqa: E402
from .i18n import _  # noqa: E402
from .resources import ui_file  # noqa: E402

ORYX_BASE = "https://configure.zsa.io"

_MODEL_NAMES = {
    KeyboardModel.VOYAGER: "Voyager",
    KeyboardModel.MOONLANDER: "Moonlander",
    KeyboardModel.ERGODOX_EZ: "ErgoDox EZ",
    KeyboardModel.PLANCK_EZ: "Planck EZ",
}


def oryx_url(device: Device) -> str:
    if not device.layout_id or device.model == KeyboardModel.UNKNOWN:
        return ORYX_BASE
    revision = device.revision_id or "latest"
    return f"{ORYX_BASE}/{device.model.value}/layouts/{device.layout_id}/{revision}/0"


@Gtk.Template(filename=ui_file("device_dialog.ui"))
class DeviceDialog(Adw.Dialog):
    __gtype_name__ = "KmlDeviceDialog"

    name_label: Gtk.Label = Gtk.Template.Child()
    state_label: Gtk.Label = Gtk.Template.Child()
    info_group: Adw.PreferencesGroup = Gtk.Template.Child()
    oryx_group: Adw.PreferencesGroup = Gtk.Template.Child()
    oryx_row: Adw.ActionRow = Gtk.Template.Child()
    reload_row: Adw.ButtonRow = Gtk.Template.Child()
    disconnect_row: Adw.ButtonRow = Gtk.Template.Child()

    def __init__(self, controller: DeviceController) -> None:
        super().__init__()
        self.controller = controller
        self._rows: list[Adw.ActionRow] = []
        self._handler = controller.connect("notify::state", lambda *_a: self._sync())
        self.connect("closed", lambda *_a: controller.disconnect(self._handler))
        self._sync()

    def _add_row(self, title: str, value: str) -> None:
        row = Adw.ActionRow(title=title, subtitle=value, use_markup=False,
                            subtitle_selectable=True)
        row.add_css_class("property")
        self.info_group.add(row)
        self._rows.append(row)

    def _sync(self) -> None:
        for row in self._rows:
            self.info_group.remove(row)
        self._rows = []
        device = self.controller.device
        layout = self.controller.layout
        if device is None:
            self.name_label.set_label(_("No Keyboard"))
            self.state_label.set_label(_("Not connected"))
            self.oryx_group.set_visible(False)
            self.reload_row.set_visible(False)
            self.disconnect_row.set_visible(False)
            return
        self.name_label.set_label(device.name)
        self.state_label.set_label(_("Connected"))
        unknown = _("Unknown")
        self._add_row(_("Model"), _MODEL_NAMES.get(device.model, device.name))
        self._add_row(_("Firmware"), device.firmware_version or unknown)
        if layout is not None and layout.title:
            self._add_row(_("Layout"), layout.title)
        self._add_row(_("Layout ID"), device.layout_id or unknown)
        self._add_row(_("Revision"), device.revision_id or unknown)
        self._add_row(_("Oryx"), _("Available") if device.oryx_available
                      else _("Unavailable — the firmware has no Oryx support"))
        self._add_row(_("Device"), device.hidraw_path)
        self.oryx_group.set_visible(bool(device.layout_id))
        self.oryx_row.set_subtitle(oryx_url(device))
        self.oryx_row.set_subtitle_lines(1)
        self.reload_row.set_visible(device.oryx_available)
        self.disconnect_row.set_visible(True)

    @Gtk.Template.Callback()
    def on_oryx_activated(self, _row: Adw.ActionRow) -> None:
        device = self.controller.device
        if device is not None:
            Gtk.UriLauncher.new(oryx_url(device)).launch(self.get_root(), None, None, None)

    @Gtk.Template.Callback()
    def on_reload_activated(self, _row: Adw.ButtonRow) -> None:
        self.controller.reload_layout()
        self.close()

    @Gtk.Template.Callback()
    def on_disconnect_activated(self, _row: Adw.ButtonRow) -> None:
        self.controller.disconnect_device()
        self.close()
