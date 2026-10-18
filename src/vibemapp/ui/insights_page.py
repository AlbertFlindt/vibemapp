"""Insights page: how comfortable the recorded typing is, as an ergo score and its parts."""

from __future__ import annotations

import locale

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, GObject, Gtk  # noqa: E402

from ..backend import ergo  # noqa: E402
from ..models import ErgoReport, Finger, HeatmapApp, HeatmapPeriod, KeyPairCount, Layout  # noqa: E402
from ..service import KeyboardService  # noqa: E402
from . import heat  # noqa: E402
from .controller import DeviceController  # noqa: E402
from .i18n import _, ngettext  # noqa: E402
from .resources import ui_file  # noqa: E402
from .transparency import binding_label, is_transparent  # noqa: E402
from .widgets import FingerLoadChart  # noqa: E402,F401  (registers the type)

# Same-finger pairs above this share of all pairs get a warning.
SAME_FINGER_TARGET = 0.01
MINUS = "−"


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


def format_penalty(points: float) -> str:
    """``3.2`` -> ``−3.2``; nothing taken off reads ``0.0``."""
    if round(points, 1) <= 0:
        return format_number(0.0)
    return MINUS + format_number(points)


def format_score(score: float) -> str:
    """``82.0`` -> ``82``, ``82.4`` -> ``82.4``."""
    return format_number(score, 0 if round(score, 1) == round(score) else 1)


def finger_kind(finger: Finger) -> str:
    return finger.value.split("-", 1)[1]


def finger_name(finger: Finger) -> str:
    """``Left middle``."""
    kind = {"pinky": _("pinky"), "ring": _("ring"), "middle": _("middle"),
            "index": _("index"), "thumb": _("thumb")}[finger_kind(finger)]
    # Translators: the first word is the hand, the second the finger: "Left middle"
    return (_("Left {finger}") if finger.is_left else _("Right {finger}")).format(finger=kind)


def key_label(layout: Layout | None, layer: int, index: int) -> str:
    """Name of a key as the layout shows it on ``layer`` (base layer if the key is
    transparent there), else ``Key N``."""
    def binding(position: int):
        source = next((l for l in layout.layers if l.index == position), None) if layout else None
        return next((k for k in source.keys if k.index == index), None) if source else None

    found = binding(layer)
    if found is None or is_transparent(found):
        found = binding(0)
    name = binding_label(found) if found is not None and not is_transparent(found) else ""
    if name:
        return name
    return _("Key {index}").format(index=index)


def _clear(listbox: Gtk.ListBox) -> None:
    child = listbox.get_first_child()
    while child is not None:
        next_child = child.get_next_sibling()
        listbox.remove(child)
        child = next_child


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
    same_list: Gtk.ListBox = Gtk.Template.Child()
    reach_label: Gtk.Label = Gtk.Template.Child()
    switch_label: Gtk.Label = Gtk.Template.Child()
    penalty_list: Gtk.ListBox = Gtk.Template.Child()
    score_label: Gtk.Label = Gtk.Template.Child()
    score_caption: Gtk.Label = Gtk.Template.Child()
    record_row: Adw.SwitchRow = Gtk.Template.Child()
    pairs_row: Adw.SwitchRow = Gtk.Template.Child()

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
            service.bind_property("pair-recording", self.pairs_row, "active", flags),
        ]
        self._handlers = [
            (service, service.connect("key-counts-changed", lambda *_a: self._queue_refresh())),
            (service, service.connect("notify::heatmap-recording", lambda *_a: self.refresh())),
            (service, service.connect("notify::pair-recording", lambda *_a: self.refresh())),
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
        self.pairs_row.set_sensitive(self.recording)
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
            self._show_report(report, layout)

    # --- report ----------------------------------------------------------

    def _show_report(self, report: ErgoReport, layout: Layout | None) -> None:
        self._fill_fingers(report)
        self._fill_same_finger(report, layout)
        self.reach_label.set_label(format_number(report.reach_cost, 2))
        self.switch_label.set_label(
            format_percent(report.layer_switch_rate, 1) if report.pairs else _("n/a"))
        self.switch_label.set_tooltip_text(
            None if report.pairs else _("Turn on Record Key Pairs to measure this"))
        self._fill_score(report)

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

    def _fill_same_finger(self, report: ErgoReport, layout: Layout | None) -> None:
        _clear(self.same_list)
        if not report.pairs:
            row = Adw.ActionRow(
                title=_("No Key Pairs Recorded"), use_markup=False,
                subtitle=_("Turn on Record Key Pairs below, then type for a while"))
            row.add_prefix(Gtk.Image.new_from_icon_name("dialog-information-symbolic"))
            self.same_list.append(row)
            return
        over = report.same_finger_rate > SAME_FINGER_TARGET
        row = Adw.ActionRow(title=_("Same-Finger Rate"), use_markup=False,
                            subtitle=_("Target under 1%"))
        icon = Gtk.Image.new_from_icon_name("dialog-warning-symbolic" if over
                                            else "object-select-symbolic")
        icon.add_css_class("warning" if over else "success")
        icon.update_property([Gtk.AccessibleProperty.LABEL],
                             [_("Above the target") if over else _("Within the target")])
        value = Gtk.Label(label=format_percent(report.same_finger_rate, 1),
                          valign=Gtk.Align.CENTER)
        value.add_css_class("numeric")
        value.add_css_class("warning" if over else "success")
        row.add_suffix(icon)
        row.add_suffix(value)
        self.same_list.append(row)
        for pair in report.worst_same_finger:
            self.same_list.append(self._pair_row(pair, layout))

    def _pair_row(self, pair: KeyPairCount, layout: Layout | None) -> Adw.ActionRow:
        first = key_label(layout, *pair.first)
        second = key_label(layout, *pair.second)
        count = ngettext("{count} time", "{count} times", pair.count).format(
            count=heat.format_count(pair.count))
        finger = pair.finger
        subtitle = (_("{finger} · {count}").format(finger=finger_name(finger), count=count)
                    if finger is not None else count)
        # Translators: two key names, the second pressed right after the first
        row = Adw.ActionRow(title=_("{first} → {second}").format(first=first, second=second),
                            subtitle=subtitle, use_markup=False)
        return row

    def _fill_score(self, report: ErgoReport) -> None:
        _clear(self.penalty_list)
        titles = {"finger_overload": _("Finger Overload"), "hand_imbalance": _("Hand Imbalance"),
                  "same_finger": _("Same-Finger Pairs"), "reach": _("Reach"),
                  "layer_switch": _("Layer Switches")}
        for key in ergo.PENALTY_KEYS:
            row = Adw.ActionRow(title=titles[key], use_markup=False)
            value = Gtk.Label(label=format_penalty(report.penalties.get(key, 0.0)),
                              valign=Gtk.Align.CENTER)
            value.add_css_class("numeric")
            row.add_suffix(value)
            self.penalty_list.append(row)
        self.score_label.set_label(
            _("{score} / 100").format(score=format_score(report.score)))
        presses = ngettext("{n} key press", "{n} key presses", report.presses).format(
            n=heat.format_count(report.presses))
        if report.pairs:
            pairs = ngettext("{n} key pair", "{n} key pairs", report.pairs).format(
                n=heat.format_count(report.pairs))
            caption = _("Based on {presses} and {pairs}").format(presses=presses, pairs=pairs)
        else:
            caption = _("Based on {presses}").format(presses=presses)
        self.score_caption.set_label(caption)
