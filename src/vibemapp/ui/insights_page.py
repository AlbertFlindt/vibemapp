"""Insights page: how the recorded typing is spread over the fingers (Finger Load)."""

from __future__ import annotations

import locale

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, GObject, Gtk  # noqa: E402

from ..backend import ergo  # noqa: E402
from ..models import ErgoReport, Finger, HeatmapApp, HeatmapPeriod  # noqa: E402
from ..service import KeyboardService  # noqa: E402
from .controller import DeviceController  # noqa: E402
from .i18n import _  # noqa: E402
from .resources import ui_file  # noqa: E402
from .widgets import FingerLoadChart  # noqa: E402,F401  (registers the type)

# --- pure helpers (unit-tested) ----------------------------------------------

def format_number(value: float, decimals: int = 1) -> str:
    """Locale-aware decimal number, e.g. ``3,2`` in German."""
    return locale.format_string(f"%.{decimals}f", value, grouping=True)


def format_percent(share: float, decimals: int = 0) -> str:
    """``0.123`` -> ``12%``; below 1 % one decimal is kept so small rates don't read 0."""
    pct = share * 100
    if decimals == 0 and 0 < pct < 1:
        decimals = 1
    return _("{pct}%").format(pct=format_number(pct, decimals))


def finger_kind(finger: Finger) -> str:
    return finger.value.split("-", 1)[1]


def finger_name(finger: Finger) -> str:
    """``Left middle``."""
    kind = {"pinky": _("pinky"), "ring": _("ring"), "middle": _("middle"),
            "index": _("index"), "thumb": _("thumb")}[finger_kind(finger)]
    # Translators: the first word is the hand, the second the finger: "Left middle"
    return (_("Left {finger}") if finger.is_left else _("Right {finger}")).format(finger=kind)


@Gtk.Template(filename=ui_file("insights_page.ui"))
class InsightsPage(Adw.Bin):
    __gtype_name__ = "KmlInsightsPage"

    window_title: Adw.WindowTitle = Gtk.Template.Child()
    stack: Gtk.Stack = Gtk.Template.Child()
    start_button: Gtk.Button = Gtk.Template.Child()
    period_group: Adw.ToggleGroup = Gtk.Template.Child()
    app_dropdown: Gtk.DropDown = Gtk.Template.Child()
    empty_status: Adw.StatusPage = Gtk.Template.Child()
    results_box: Gtk.Box = Gtk.Template.Child()
    finger_chart: FingerLoadChart = Gtk.Template.Child()
    hands_row: Adw.ActionRow = Gtk.Template.Child()
    record_row: Adw.SwitchRow = Gtk.Template.Child()

    def __init__(self) -> None:
        super().__init__()
        self.service: KeyboardService | None = None
        self.controller: DeviceController | None = None
        self._handlers: list[tuple[object, int]] = []
        self._bindings: list[GObject.Binding] = []
        # Keys of the apps listed in app_dropdown (item 0 is All Apps).
        self._app_keys: list[str] = []
        self._app_handler = self.app_dropdown.connect(
            "notify::selected", lambda *_a: self.refresh())
        self._refresh_source = 0
        self.period_group.connect("notify::active-name", lambda *_a: self.refresh())
        self.start_button.connect("clicked", lambda *_a: self._start_recording())

    def setup(self, service: KeyboardService, controller: DeviceController) -> None:
        self.service = service
        self.controller = controller
        flags = GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE
        self._bindings = [
            service.bind_property("heatmap-recording", self.record_row, "active", flags),
        ]
        self._handlers = [
            (service, service.connect("key-counts-changed", lambda *_a: self._queue_refresh())),
            (service, service.connect("notify::heatmap-recording", lambda *_a: self.refresh())),
            (service, service.connect("layout-loaded", lambda *_a: self.refresh())),
            (controller, controller.connect("notify::state", lambda *_a: self.refresh())),
        ]
        self.refresh()

    def teardown(self) -> None:
        for binding in self._bindings:
            binding.unbind()
        self._bindings = []
        for obj, handler in self._handlers:
            obj.disconnect(handler)
        self._handlers = []
        if self._refresh_source:
            GLib.source_remove(self._refresh_source)
            self._refresh_source = 0

    # --- state -----------------------------------------------------------

    @property
    def recording(self) -> bool:
        return bool(self.service and self.service.props.heatmap_recording)

    @property
    def has_layout(self) -> bool:
        return (self.controller is not None and self.controller.state == "ready"
                and self.service is not None and self.service.get_layout() is not None)

    def period(self) -> HeatmapPeriod:
        try:
            return HeatmapPeriod(self.period_group.get_active_name() or "week")
        except ValueError:
            return HeatmapPeriod.WEEK

    def app_filter(self) -> str | None:
        """Key of the selected app, None for All Apps."""
        selected = self.app_dropdown.get_selected()
        if selected in (0, Gtk.INVALID_LIST_POSITION) or selected > len(self._app_keys):
            return None
        return self._app_keys[int(selected) - 1]

    def _set_apps(self, apps: list[HeatmapApp]) -> None:
        """Rebuild the app list, keeping the selection by key if it is still there."""
        keys = [app.key for app in apps]
        self.app_dropdown.set_visible(bool(apps))
        if keys == self._app_keys and (not keys or self.app_dropdown.get_model() is not None):
            return
        wanted = self.app_filter()
        self.app_dropdown.handler_block(self._app_handler)
        try:
            self._app_keys = keys
            self.app_dropdown.set_model(
                Gtk.StringList.new([_("All Apps")] + [app.name for app in apps]))
            self.app_dropdown.set_selected(keys.index(wanted) + 1 if wanted in keys else 0)
        finally:
            self.app_dropdown.handler_unblock(self._app_handler)

    def _start_recording(self) -> None:
        if self.service is not None:
            self.service.props.heatmap_recording = True

    def _queue_refresh(self) -> None:
        # Coalesce bursts of key-counts-changed.
        if not self._refresh_source:
            self._refresh_source = GLib.timeout_add(250, self._on_refresh_timeout)

    def _on_refresh_timeout(self) -> bool:
        self._refresh_source = 0
        self.refresh()
        return GLib.SOURCE_REMOVE

    def refresh(self) -> None:
        if self.service is None:
            return
        if not self.has_layout:
            self.stack.set_visible_child_name("no-keyboard")
            return
        layout = self.service.get_layout()
        self.window_title.set_subtitle(layout.title if layout else "")

        everything = self.service.get_key_counts(HeatmapPeriod.ALL, None)
        if not self.recording and not any(everything.values()):
            self.stack.set_visible_child_name("opt-in")
            return

        self._set_apps(self.service.list_heatmap_apps(self.period()))
        report = self.service.get_ergo_report(self.period(), self.app_filter())
        if report is None:
            self.stack.set_visible_child_name("no-map")
            return
        self.stack.set_visible_child_name("insights")
        has_presses = report.presses > 0
        self.empty_status.set_visible(not has_presses)
        self.results_box.set_visible(has_presses)
        if has_presses:
            self._fill_fingers(report)

    # --- report ----------------------------------------------------------

    def _fill_fingers(self, report: ErgoReport) -> None:
        order = list(Finger)
        capacity = ergo.WEIGHTS["finger_capacity"]
        loads = [report.finger_load.get(f, 0.0) for f in order]
        caps = [capacity[finger_kind(f)] for f in order]           # type: ignore[index]
        kinds = {"pinky": (_("Pinky"), _("P")), "ring": (_("Ring"), _("R")),
                 "middle": (_("Middle"), _("M")), "index": (_("Index"), _("I")),
                 "thumb": (_("Thumb"), _("T"))}
        names = [kinds[finger_kind(f)][0] for f in order]
        initials = [kinds[finger_kind(f)][1] for f in order]
        left, right = _("Left Hand"), _("Right Hand")
        parts = [_("{finger} {pct}").format(finger=finger_name(f), pct=format_percent(load))
                 + (_(" (over its comfortable share)") if load > cap + 1e-9 else "")
                 for f, load, cap in zip(order, loads, caps)]
        self.finger_chart.set_data(loads, caps, names, initials, (left, right),
                                   ", ".join(parts))
        self.hands_row.set_subtitle(
            _("{left_hand} {left} · {right_hand} {right}").format(
                left_hand=left, left=format_percent(report.left_share),
                right_hand=right, right=format_percent(1 - report.left_share)))
