"""Heatmap page: opt-in recording of per-key press counts, shown on the layout."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from ..models import HeatmapPeriod, Layout  # noqa: E402
from ..service import KeyboardService  # noqa: E402
from . import heat  # noqa: E402
from .controller import DeviceController  # noqa: E402
from .i18n import _, ngettext  # noqa: E402
from .keyboard_view import KeyboardView  # noqa: E402,F401  (registers the type)
from .resources import ui_file  # noqa: E402
from .rules_model import layer_label  # noqa: E402
from .widgets import HeatLegend  # noqa: E402,F401  (registers the type)

TOP_KEYS = 5


@Gtk.Template(filename=ui_file("heatmap_page.ui"))
class HeatmapPage(Adw.Bin):
    __gtype_name__ = "KmlHeatmapPage"

    window_title: Adw.WindowTitle = Gtk.Template.Child()
    pause_button: Gtk.Button = Gtk.Template.Child()
    reset_button: Gtk.Button = Gtk.Template.Child()
    paused_banner: Adw.Banner = Gtk.Template.Child()
    stack: Gtk.Stack = Gtk.Template.Child()
    period_group: Adw.ToggleGroup = Gtk.Template.Child()
    layer_dropdown: Gtk.DropDown = Gtk.Template.Child()
    keyboard_view: KeyboardView = Gtk.Template.Child()
    legend: HeatLegend = Gtk.Template.Child()
    total_label: Gtk.Label = Gtk.Template.Child()
    total_caption: Gtk.Label = Gtk.Template.Child()
    top_list: Gtk.ListBox = Gtk.Template.Child()

    def __init__(self) -> None:
        super().__init__()
        self.service: KeyboardService | None = None
        self.controller: DeviceController | None = None
        self._layout: Layout | None = None
        self._handlers: list[tuple[object, int]] = []
        self._layer_handler = 0
        self._refresh_source = 0
        self.period_group.connect("notify::active-name", lambda *_a: self.refresh())

    def setup(self, service: KeyboardService, controller: DeviceController) -> None:
        self.service = service
        self.controller = controller
        style = Adw.StyleManager.get_default()
        self._handlers = [
            (service, service.connect("key-counts-changed", lambda *_a: self._queue_refresh())),
            (service, service.connect("notify::heatmap-recording", lambda *_a: self.refresh())),
            (service, service.connect("layout-loaded", lambda *_a: self.refresh())),
            (controller, controller.connect("notify::state", lambda *_a: self.refresh())),
            (style, style.connect("notify::dark", lambda *_a: self.legend.set_maximum(
                self.keyboard_view.heat_max))),
        ]
        self.refresh()

    def teardown(self) -> None:
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

    def layer_filter(self) -> int | None:
        selected = self.layer_dropdown.get_selected()
        if selected in (0, Gtk.INVALID_LIST_POSITION):
            return None
        return int(selected) - 1

    def _queue_refresh(self) -> None:
        # Coalesce bursts of key-counts-changed.
        if not self._refresh_source:
            self._refresh_source = GLib.timeout_add(250, self._on_refresh_timeout)

    def _on_refresh_timeout(self) -> bool:
        self._refresh_source = 0
        self.refresh()
        return GLib.SOURCE_REMOVE

    def _set_layout(self, layout: Layout | None) -> None:
        if layout is self._layout:
            return
        self._layout = layout
        if self._layer_handler:
            self.layer_dropdown.disconnect(self._layer_handler)
            self._layer_handler = 0
        labels = [_("All Layers")]
        if layout is not None:
            labels += [layer_label(layer, layer.index) for layer in layout.layers]
            self.keyboard_view.set_geometry(layout.geometry)
        self.layer_dropdown.set_model(Gtk.StringList.new(labels))
        self.layer_dropdown.set_selected(0)
        self._layer_handler = self.layer_dropdown.connect(
            "notify::selected", lambda *_a: self.refresh())

    def refresh(self) -> None:
        if self.service is None:
            return
        recording = self.recording
        self.pause_button.set_icon_name("media-playback-pause-symbolic" if recording
                                        else "media-record-symbolic")
        self.pause_button.set_tooltip_text(_("Pause Recording") if recording
                                           else _("Resume Recording"))

        if not self.has_layout:
            self._set_layout(None)
            self._show("no-keyboard")
            return
        layout = self.service.get_layout()
        self._set_layout(layout)
        self.window_title.set_subtitle(layout.title if layout else "")

        everything = self.service.get_key_counts(HeatmapPeriod.ALL, None)
        if not recording and not any(everything.values()):
            self._show("opt-in")
            return
        self._show("heatmap")
        self.paused_banner.set_revealed(not recording)

        layer = self.layer_filter()
        counts = self.service.get_key_counts(self.period(), layer)
        self.keyboard_view.set_layer(self._label_layer(layer), animate=False)
        self.keyboard_view.set_heat(counts)
        maximum = self.keyboard_view.heat_max
        self.legend.set_maximum(maximum)

        total = sum(counts.values())
        self.total_label.set_label(heat.format_count(total))
        self.total_caption.set_label(ngettext("key press", "key presses", total))
        self._fill_top_keys(counts, total, layer)

    def _show(self, name: str) -> None:
        self.stack.set_visible_child_name(name)
        in_heatmap = name == "heatmap"
        self.pause_button.set_visible(in_heatmap)
        self.reset_button.set_visible(in_heatmap)
        if not in_heatmap:
            self.paused_banner.set_revealed(False)

    def _label_layer(self, layer: int | None):  # -> Layer | None
        """Layer whose labels name the keys: the filtered one, else the base."""
        if self._layout is None or not self._layout.layers:
            return None
        index = layer if layer is not None and layer < len(self._layout.layers) else 0
        return self._layout.layers[index]

    def _key_name(self, index: int, layer: int | None) -> str:
        source = self._label_layer(layer)
        binding = next((k for k in source.keys if k.index == index), None) if source else None
        name = (binding.tap_label or binding.hold_label) if binding else ""
        return name or _("Key {index}").format(index=index)

    def _fill_top_keys(self, counts: dict[int, int], total: int, layer: int | None) -> None:
        child = self.top_list.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self.top_list.remove(child)
            child = next_child
        ranked = heat.top_keys(counts, TOP_KEYS)
        if not ranked:
            empty = Adw.ActionRow(title=_("No key presses in this period"), use_markup=False)
            empty.add_css_class("dim-label")
            self.top_list.append(empty)
            return
        top_count = ranked[0][1]
        for rank, (index, count) in enumerate(ranked, start=1):
            row = Adw.ActionRow(title=self._key_name(index, layer), use_markup=False)
            badge = Gtk.Label(label=str(rank), width_chars=2)
            badge.add_css_class("dim-label")
            badge.add_css_class("numeric")
            row.add_prefix(badge)
            bar = Gtk.LevelBar(min_value=0, max_value=top_count, value=count,
                               valign=Gtk.Align.CENTER, width_request=96)
            bar.update_property([Gtk.AccessibleProperty.LABEL],
                                [_("Share of the most pressed key")])
            row.add_suffix(bar)
            value = Gtk.Label(label=heat.format_count(count), xalign=1, width_chars=7)
            value.add_css_class("numeric")
            row.add_suffix(value)
            row.set_subtitle(heat.share_label(count, total))
            self.top_list.append(row)

    # --- actions (wired by the window) -----------------------------------

    def toggle_recording(self) -> None:
        if self.service is not None:
            self.service.props.heatmap_recording = not self.recording

    def confirm_reset(self) -> None:
        if self.service is None or not self.has_layout:
            return
        dialog = Adw.AlertDialog(
            heading=_("Reset Heatmap?"),
            body=_("All recorded key presses for this layout will be deleted. "
                   "This can't be undone."))
        dialog.add_response("cancel", _("_Cancel"))
        dialog.add_response("reset", _("_Reset"))
        dialog.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def on_response(_dialog: Adw.AlertDialog, response: str) -> None:
            if response == "reset" and self.service is not None:
                self.service.reset_key_counts()
                self.refresh()

        dialog.connect("response", on_response)
        dialog.present(self)
