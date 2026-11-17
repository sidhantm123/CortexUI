"""Main application window."""
import copy
import os
import weakref
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Slot, QTimer, QSettings, QEvent
from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QSplitter,
    QTabWidget,
    QMenuBar,
    QMenu,
    QFileDialog,
    QMessageBox,
    QLabel,
    QApplication,
    QDialog,
    QAbstractSpinBox,
    QAbstractItemView,
    QComboBox,
    QLineEdit,
    QAbstractSlider,
    QScrollArea,
)
from PySide6.QtGui import QAction, QActionGroup, QDragEnterEvent, QDropEvent, QKeyEvent, QKeySequence, QShortcut

import numpy as np

from core.app_state import AppState
from core.view_buffer import window_target, process_buffer
from core.workers import (
    WorkerManager,
    BufferLoadWorker,
    FilterWorker,
    SpectralWorker,
    EventDetectWorker,
    BlankingWorker,
    ExportWorker,
    SpikeDetectWorker,
)
from models import StreamState, ChannelMode, NUMBER_BY_ELECTRODE, NUMBER_BY_CHANNEL
from dsp import apply_filter_chain, decimate_for_display
from dsp.blanking import apply_blanking, basic_result, blank_spans
from dsp.metrics import compute_vrms
from dsp.spikes import robust_sigma, NEGATIVE, POSITIVE, BOTH
from models.blanking import ADVANCED
from models.filter_config import FilterType

from .stream_panel import StreamPanel
from .control_panel import ControlPanel
from .filter_panel import FilterPanel
from .spectrum_panel import SpectrumPanel
from .plot_widget import TimeSeriesPlot, SpectrumPlot
from .probe_map_widget import ProbeMapWidget
from .analog_plot import AnalogPlot
from .analog_panel import AnalogPanel
from .blanking_panel import BlankingPanel
from .spike_panel import SpikePanel
from .raster_plot import RasterPlot
from .plot_widget import format_volts
from .theme import set_role, set_primary, progress_dialog
from .toast import Toast, INFO, WARNING, ERROR


# Focused widgets that keep the plot shortcut keys (arrows, +, −, [, ]) for themselves
_KEY_INPUTS = (QAbstractSpinBox, QAbstractItemView, QComboBox, QLineEdit, QAbstractSlider)
_KEY_TEXT = {Qt.Key.Key_Plus: "+", Qt.Key.Key_Equal: "=", Qt.Key.Key_Minus: "-",
             Qt.Key.Key_BracketLeft: "[", Qt.Key.Key_BracketRight: "]"}


def _scrolling(widget: QWidget) -> QScrollArea:
    """Let a control tab scroll instead of squashing its widgets when the window is short."""
    scroll = QScrollArea()
    scroll.setWidget(widget)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    return scroll


def _placeholder_tab(text: str) -> QWidget:
    """Empty tab for a feature that is coming in a later step."""
    widget = QWidget()
    layout = QVBoxLayout(widget)
    label = QLabel(text)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    set_role(label, "secondary")
    layout.addWidget(label)
    return widget


class MainWindow(QMainWindow):
    """Main application window."""

    # Maximum points to plot without decimation
    MAX_PLOT_POINTS = 8000

    # Largest plot buffer held in memory, in channels × samples (1e8 float32 = 400 MB). The
    # view length is limited so the buffer can hold at least twice the view: 136 channels
    # under CAR/CMR at 30 kHz → 12.3 s; 8 overlay channels → 208 s.
    MAX_BUFFER_SAMPLES = 1e8

    # Longest view by default (user setting: Channels → View Range → Max length)
    DEFAULT_MAX_VIEW_S = 20.0

    # A user-driven move outside the buffer waits this long for the next one before loading
    MISS_DEBOUNCE_MS = 80

    # Filters get this much real data beyond the view on a first ("view-first") load
    FILTER_PAD_S = 1.0

    # One background extension reads at most this much new recording per side (~0.3 s from
    # a cold disk), so the buffer's edge advances in small, frequent steps during a pan
    MAX_EXTEND_S = 10.0

    def __init__(self):
        super().__init__()

        self.setWindowTitle("Multi-Channel Neural Viewer")
        self.setMinimumSize(1200, 800)

        # App-wide channel numbering preference (electrode numbers by default)
        self._settings = QSettings("NSP", "MultiChannelNeuralViewer")
        saved = self._settings.value("numbering", NUMBER_BY_ELECTRODE)
        StreamState.numbering = saved if saved in (NUMBER_BY_ELECTRODE, NUMBER_BY_CHANNEL) else NUMBER_BY_ELECTRODE

        # State
        self._app_state = AppState()
        self._worker_manager = WorkerManager()

        # Plot buffer: a sliding window of samples around the view (float32), see _refresh_plot
        self._raw_data: Optional[np.ndarray] = None  # displayed rows (= _buffer_channels)
        self._raw_time: Optional[np.ndarray] = None
        self._raw_data_all: Optional[np.ndarray] = None  # All channels for CAR/CMR
        self._filtered_data: Optional[np.ndarray] = None
        self._blank_mask: Optional[np.ndarray] = None  # blanked samples of _filtered_data (None = none)

        # Buffer tracking — describes what time range / channels are in _filtered_data
        self._buffer_channels: list = []
        self._buffer_start: float = -1.0  # seconds, = _buf_range / fs
        self._buffer_end: float = -1.0
        self._buf_range: Optional[tuple] = None  # [start, stop) samples held
        self._buf_key: Optional[tuple] = None  # (stream, loaded channels) the raw rows belong to

        # Async loads
        self._load_seq: int = 0          # incremented on each new request to discard stale results
        self._inflight = None  # (stream, channels, needs_all, want_start, want_stop) of the load under way
        self._reload_pending = False  # the view ran past the in-flight load: extend again when it lands
        self._pan_dir = 0  # -1 / 0 / +1: direction of the last pan (sets where the buffer reads ahead)
        self._setting_range = False  # True while code, not the user, is setting the plot range
        self._max_span: Optional[float] = None  # current view-length limit (s), see _update_view_limits
        saved_max = self._settings.value("max_view_s", self.DEFAULT_MAX_VIEW_S, type=float)
        self._user_max_view: float = saved_max if saved_max and saved_max > 0 else self.DEFAULT_MAX_VIEW_S

        # Raster ↔ Time Series sync (manual: the raster may zoom past the Time Series cap)
        self._syncing_raster = False
        self._raster_result_id = None  # spike result shown in the raster (its first view is the whole recording)

        # Fast-path tracking
        self._last_plot_channels: list = []
        self._last_scatter_mode: bool = False

        # Pending operations
        self._pending_refresh = False

        # Analog viewer buffer (one analog channel, a sliding window around its view)
        self._analog_key = None  # (stream, channel) in the buffer
        self._analog_data: Optional[np.ndarray] = None
        self._analog_time: Optional[np.ndarray] = None
        self._analog_range: Optional[tuple] = None  # [start, stop) samples held
        self._analog_inflight = None  # (key, want_start, want_stop) of the analog load under way
        self._analog_reload_pending = False
        self._analog_waiting = False  # waiting for the main load to bring its pages into the cache
        self._analog_seq: int = 0
        self._pending_analog_refresh = False
        self._analog_workers = WorkerManager()
        # Long jobs behind a progress dialog (see _run_job), by kind, e.g. "export"
        self._jobs: dict = {}
        self._recording_gen = 0  # bumped when the recording is replaced or the app quits

        self._last_filter_warning = ""  # shown once until it changes

        self._setup_ui()
        self._setup_menu()
        self._setup_shortcuts()
        self._connect_signals()

        # Enable drag and drop
        self.setAcceptDrops(True)

    def _setup_ui(self):
        """Set up the main UI layout."""
        central = QWidget()
        self.setCentralWidget(central)

        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(5, 5, 5, 5)

        # Main splitter
        self._main_splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left panel: block info and streams (View → Show Recording Panel, or drag it shut)
        self._stream_panel = StreamPanel()
        self._stream_panel.setMaximumWidth(280)
        self._stream_panel.setMinimumWidth(225)  # Stream / Channels / Rate (Hz) headers fit
        self._main_splitter.addWidget(self._stream_panel)

        # Center: Plots
        center_widget = QWidget()
        center_layout = QVBoxLayout(center_widget)
        center_layout.setContentsMargins(0, 0, 0, 0)

        # Tab widget for Time Series / Spectrum
        self._plot_tabs = QTabWidget()

        # Time series tab
        self._time_plot = TimeSeriesPlot()
        self._plot_tabs.addTab(self._time_plot, "Time Series")

        # Raster tab (enabled once spikes are detected)
        self._raster_plot = RasterPlot()
        self._raster_tab_index = self._plot_tabs.addTab(self._raster_plot, "Raster")
        self._plot_tabs.setTabEnabled(self._raster_tab_index, False)

        # Spectrum tab
        self._spectrum_plot = SpectrumPlot()
        self._plot_tabs.addTab(self._spectrum_plot, "Spectrum")

        # Analog viewer below the main plot
        self._analog_plot = AnalogPlot()

        center_splitter = QSplitter(Qt.Orientation.Vertical)
        center_splitter.addWidget(self._plot_tabs)
        center_splitter.addWidget(self._analog_plot)
        center_splitter.setSizes([650, 280])
        center_layout.addWidget(center_splitter)
        self._main_splitter.addWidget(center_widget)
        # Messages float over the plots (there is no status bar)
        self._toast = Toast(center_widget)

        # Right panel: Controls
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)

        # Right tabs for different control groups
        self._control_tabs = QTabWidget()
        self._control_tabs.setUsesScrollButtons(True)

        # Channel control tab
        self._control_panel = ControlPanel()
        self._control_tabs.addTab(_scrolling(self._control_panel), "Channels")

        # Blanking tab
        self._blanking_panel = BlankingPanel(self._app_state)
        self._control_tabs.addTab(self._blanking_panel, "Blanking")

        # Filter tab
        self._filter_panel = FilterPanel()
        self._control_tabs.addTab(_scrolling(self._filter_panel), "Filters")

        # Spike detection tab
        self._spike_panel = SpikePanel()
        self._spike_tab_index = self._control_tabs.addTab(_scrolling(self._spike_panel), "Spike Detection")

        # Spectrum analysis tab
        self._spectrum_panel = SpectrumPanel()
        self._control_tabs.addTab(_scrolling(self._spectrum_panel), "Analysis")

        # Probe map tab
        self._probe_map = ProbeMapWidget()
        self._control_tabs.addTab(self._probe_map, "Probe Map")

        # Analog channel controls below the control tabs
        self._analog_tabs = QTabWidget()
        self._analog_panel = AnalogPanel(self._app_state)
        self._analog_tabs.addTab(self._analog_panel, "Analog Ch.")

        right_splitter = QSplitter(Qt.Orientation.Vertical)
        right_splitter.addWidget(self._control_tabs)
        right_splitter.addWidget(self._analog_tabs)
        right_splitter.setSizes([520, 400])
        right_layout.addWidget(right_splitter)

        right_widget.setMaximumWidth(380)
        right_widget.setMinimumWidth(200)
        self._main_splitter.addWidget(right_widget)

        # Set splitter sizes
        self._main_splitter.setSizes([230, 670, 340])
        self._main_splitter.splitterMoved.connect(self._on_main_splitter_moved)

        main_layout.addWidget(self._main_splitter)

    def _setup_menu(self):
        """Set up the menu bar."""
        menubar = self.menuBar()

        # File menu
        file_menu = menubar.addMenu("File")

        open_action = QAction("Open Recording Folder...", self)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self._on_open_folder)
        file_menu.addAction(open_action)

        file_menu.addSeparator()

        save_session = QAction("Save Session...", self)
        save_session.setShortcut("Ctrl+S")
        save_session.triggered.connect(self._on_save_session)
        file_menu.addAction(save_session)

        load_session = QAction("Load Session...", self)
        load_session.setShortcut("Ctrl+L")
        load_session.triggered.connect(self._on_load_session)
        file_menu.addAction(load_session)

        file_menu.addSeparator()

        load_probe_action = QAction("Load Probe...", self)
        load_probe_action.triggered.connect(self._on_load_probe)
        file_menu.addAction(load_probe_action)

        file_menu.addSeparator()

        export_menu = file_menu.addMenu("Export")

        export_binary_action = QAction("Stream as Binary (.i16 / .bin)...", self)
        export_binary_action.triggered.connect(self._on_export_binary)
        export_menu.addAction(export_binary_action)

        file_menu.addSeparator()

        exit_action = QAction("Exit", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # View menu
        view_menu = menubar.addMenu("View")

        reset_view = QAction("Reset View", self)
        reset_view.setShortcut("Ctrl+R")
        reset_view.triggered.connect(lambda: self._time_plot.reset_view())
        view_menu.addAction(reset_view)

        view_menu.addSeparator()

        self._show_recording_action = QAction("Show Recording Panel", self, checkable=True)
        self._show_recording_action.setShortcut("Ctrl+1")
        self._show_recording_action.toggled.connect(self._set_recording_panel_visible)
        view_menu.addAction(self._show_recording_action)
        self._show_recording_action.setChecked(
            self._settings.value("show_recording_panel", True, type=bool)
        )
        self._set_recording_panel_visible(self._show_recording_action.isChecked())

        recording_info = QAction("Recording Info...", self)
        recording_info.setShortcut("Ctrl+I")
        recording_info.triggered.connect(self._on_recording_info)
        view_menu.addAction(recording_info)

        view_menu.addSeparator()
        numbering_menu = view_menu.addMenu("Number Channels By")
        numbering_group = QActionGroup(self)
        numbering_group.setExclusive(True)
        self._number_by_electrode = QAction("Electrode", self, checkable=True)
        self._number_by_electrode.setToolTip("Electrode numbers from the channel labels, e.g. elec1-10 → 10")
        self._number_by_channel = QAction("Channel (recording order)", self, checkable=True)
        for action, mode in (
            (self._number_by_electrode, NUMBER_BY_ELECTRODE),
            (self._number_by_channel, NUMBER_BY_CHANNEL),
        ):
            numbering_group.addAction(action)
            numbering_menu.addAction(action)
            action.triggered.connect(lambda checked, m=mode: self._set_numbering(m))
        self._update_numbering_actions()

        # Help menu
        help_menu = menubar.addMenu("Help")

        shortcuts_action = QAction("Keyboard Shortcuts", self)
        shortcuts_action.triggered.connect(self._on_show_shortcuts)
        help_menu.addAction(shortcuts_action)

        about_action = QAction("About", self)
        about_action.triggered.connect(self._on_about)
        help_menu.addAction(about_action)

    def _update_numbering_actions(self):
        """Check the active numbering; grey out Electrode when this stream's labels don't allow it."""
        state = self._app_state.get_current_state()
        available = state is None or state.electrode_numbers is not None
        self._number_by_electrode.setEnabled(available)
        self._number_by_electrode.setText(
            "Electrode" if available else f"Electrode (unavailable: {state.electrode_note})"
        )
        by_electrode = StreamState.numbering == NUMBER_BY_ELECTRODE and available
        self._number_by_electrode.setChecked(by_electrode)
        self._number_by_channel.setChecked(not by_electrode)

    def _set_numbering(self, mode: str):
        """Switch app-wide numbering and refresh every channel list, keeping the selection."""
        StreamState.numbering = mode
        self._settings.setValue("numbering", mode)
        self._update_numbering_actions()
        state = self._app_state.get_current_state()
        if state is None:
            return
        self._control_panel.set_state(state)
        self._spectrum_panel.set_state(state)
        self._update_spike_view()
        self._last_plot_channels = []  # rebuild legends / Vrms labels
        view_start, view_end = state.get_view_range()
        self._rerender_from_buffer(view_start, view_end, state)

    def _set_recording_panel_visible(self, visible: bool):
        self._stream_panel.setVisible(visible)
        if visible and self._main_splitter.sizes()[0] == 0:  # was dragged shut
            sizes = self._main_splitter.sizes()
            sizes[0], sizes[1] = 230, max(200, sizes[1] - 230)
            self._main_splitter.setSizes(sizes)
        self._settings.setValue("show_recording_panel", visible)

    def _on_main_splitter_moved(self, *_):
        if self._stream_panel.isVisible() and self._main_splitter.sizes()[0] == 0:
            self._show_recording_action.setChecked(False)

    # ------------------------------------------------------------------ keyboard shortcuts

    SHORTCUTS = [
        ("← / →", "Pan back / forward by a quarter of the view"),
        ("+ / −", "Zoom time in / out"),
        ("↑ / ↓", "Next / previous electrode (Single mode)"),
        ("[ / ]", "Previous / next event"),
        ("⌘R / Ctrl+R", "Reset view"),
        ("⌘1 / Ctrl+1", "Show or hide the recording panel"),
    ]

    def _setup_shortcuts(self):
        """
        Plot navigation keys. Text fields, spin boxes, lists and tables keep their own
        arrow keys (Qt lets the focused widget claim a key before a shortcut fires).
        """
        K = Qt.Key
        bindings = [
            ((K.Key_Left,), lambda: self._pan_view(-0.25)),
            ((K.Key_Right,), lambda: self._pan_view(0.25)),
            ((K.Key_Plus, K.Key_Equal), lambda: self._zoom_view(0.5)),
            ((K.Key_Minus,), lambda: self._zoom_view(2.0)),
            ((K.Key_Up,), lambda: self._step_electrode(1)),
            ((K.Key_Down,), lambda: self._step_electrode(-1)),
            ((K.Key_BracketLeft,), lambda: self._step_event(-1)),
            ((K.Key_BracketRight,), lambda: self._step_event(1)),
        ]
        self._plot_shortcuts = []
        for keys, handler in bindings:
            for key in keys:
                shortcut = QShortcut(QKeySequence(key), self)
                shortcut.activated.connect(lambda k=key, h=handler: self._on_plot_key(k, h))
                self._plot_shortcuts.append(shortcut)

    def _on_plot_key(self, key, handler):
        """Run a plot shortcut, unless an input that uses this key (spin box, table, list) has focus."""
        focus = QApplication.focusWidget()
        if isinstance(focus, _KEY_INPUTS):
            # Hand the key back; shortcuts are off meanwhile, or the key would trigger them again
            text = _KEY_TEXT.get(key, "")
            for shortcut in self._plot_shortcuts:
                shortcut.setEnabled(False)
            try:
                for kind in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
                    QApplication.sendEvent(focus, QKeyEvent(kind, key, Qt.KeyboardModifier.NoModifier, text))
            finally:
                for shortcut in self._plot_shortcuts:
                    shortcut.setEnabled(True)
            return
        handler()

    def _pan_view(self, fraction: float):
        state = self._app_state.get_current_state()
        if state is None:
            return
        start, end = state.get_view_range()
        width = end - start
        start = min(max(0.0, start + fraction * width), max(0.0, state.duration - width))
        self._on_view_range_requested(start, start + width, direction=1 if fraction > 0 else -1)

    def _zoom_view(self, factor: float):
        state = self._app_state.get_current_state()
        if state is None:
            return
        start, end = state.get_view_range()
        mid = (start + end) / 2
        width = min(max((end - start) * factor, 10.0 / state.sample_rate), state.duration)
        if self._max_span is not None and width > self._max_span:
            width = self._max_span
            self._on_max_span_hit()
        start = min(max(0.0, mid - width / 2), state.duration - width)
        self._on_view_range_requested(start, start + width)

    def _step_electrode(self, step: int):
        state = self._app_state.get_current_state()
        if state is None or state.channel_mode != ChannelMode.SINGLE:
            return
        order = state.ordered_neural_channels()
        if state.selected_channel not in order:
            return
        i = order.index(state.selected_channel) + step
        if 0 <= i < len(order):
            state.selected_channel = order[i]
            self._control_panel.set_state(state)
            self._probe_map.set_selected_channel(order[i])
            self._refresh_plot()

    def _step_event(self, step: int):
        """Jump to the event before/after the centre of the view (all modalities)."""
        state = self._app_state.get_current_state()
        events = sorted(self._app_state.events, key=lambda e: e.onset)
        if state is None or not events:
            return
        start, end = state.get_view_range()
        mid = (start + end) / 2
        if step > 0:
            target = next((e for e in events if (e.onset + e.offset) / 2 > mid + 1e-6), None)
        else:
            target = next((e for e in reversed(events) if (e.onset + e.offset) / 2 < mid - 1e-6), None)
        if target is not None:
            self._analog_panel.show_selected_event(target)
            self._on_event_selected(target)

    def _on_show_shortcuts(self):
        rows = "".join(f"<tr><td><b>{k}</b>&nbsp;&nbsp;&nbsp;</td><td>{v}</td></tr>" for k, v in self.SHORTCUTS)
        QMessageBox.information(
            self, "Keyboard Shortcuts",
            f"<table>{rows}</table><p>Mouse: scroll zooms time, drag pans, Ctrl/⌘+scroll zooms "
            "amplitude, double-click fits amplitude to the visible data.</p>"
            "<p>Trackpad: swipe sideways to pan, swipe up/down or pinch to zoom time, ⌘ + swipe "
            "up/down to zoom amplitude.</p>"
            f"<p>The view can be at most {self._user_max_view:g} s long "
            "(Channels → View Range → Max length).</p>",
        )

    def _connect_signals(self):
        """Connect all signals."""
        # Stream panel
        self._stream_panel.stream_selected.connect(self._on_stream_selected)

        # Control panel
        self._control_panel.channel_mode_changed.connect(self._on_channel_mode_changed)
        self._control_panel.single_channel_changed.connect(self._on_channel_changed)
        self._control_panel.single_channel_changed.connect(
            self._probe_map.set_selected_channel
        )
        self._control_panel.overlay_channels_changed.connect(self._on_channels_changed)
        self._control_panel.view_range_requested.connect(self._on_view_range_requested)
        self._control_panel.refresh_requested.connect(self._refresh_plot)

        # Scatter mode
        self._control_panel.scatter_mode_changed.connect(self._on_scatter_mode_changed)

        # Filter panel
        self._filter_panel.filters_changed.connect(self._on_filters_changed)

        # Spectrum panel
        self._spectrum_panel.analyze_requested.connect(self._on_analyze_requested)
        self._spectrum_panel.enable_selection_changed.connect(
            self._time_plot.enable_selection
        )

        # Plot signals
        self._time_plot.view_range_changed.connect(self._on_plot_view_changed)
        self._time_plot.region_selected.connect(self._spectrum_panel.update_interval)

        # Analog viewer
        self._analog_plot.set_x_link(self._time_plot.plot_item())
        self._analog_plot.view_range_changed.connect(self._on_analog_view_changed)
        self._analog_panel.sync_changed.connect(self._on_analog_sync_changed)
        self._analog_panel.channel_selected.connect(self._on_analog_channel_selected)
        self._analog_panel.analog_list_changed.connect(self._on_analog_list_changed)
        self._analog_panel.assignments_changed.connect(self._update_analog_events)
        self._analog_panel.detect_requested.connect(self._on_detect_events)
        self._analog_panel.event_selected.connect(self._on_event_selected)
        self._analog_panel.events_edited.connect(self._on_event_edited)
        self._analog_plot.event_time_dragged.connect(self._on_event_dragged)

        # Spike threshold
        # Coalesce rapid changes (dragging the threshold line) into one re-render per ~30 ms
        self._spike_timer = QTimer(self)
        self._spike_timer.setSingleShot(True)
        self._spike_timer.setInterval(30)
        self._spike_timer.timeout.connect(self._rerender_current)
        self._spike_panel.settings_changed.connect(self._spike_timer.start)
        self._control_tabs.currentChanged.connect(lambda _i: self._rerender_current())
        self._time_plot.threshold_dragged.connect(self._on_threshold_dragged)
        self._spike_panel.detect_requested.connect(self._on_detect_spikes)
        self._spike_panel.threshold_settings_changed.connect(self._on_spike_inputs_changed)
        # The raster is not x-linked: it may zoom out past the Time Series cap (whole-recording
        # overview); _on_plot_view_changed / _on_raster_view_changed keep them in step within it
        self._raster_plot.channel_clicked.connect(self._on_raster_channel_clicked)
        self._raster_plot.view_range_changed.connect(self._on_raster_view_changed)
        for plot in (self._time_plot, self._analog_plot):
            plot.plot_item().getViewBox().sigMaxSpanHit.connect(self._on_max_span_hit)

        # View-length cap
        self._control_panel.set_max_view(self._user_max_view)
        self._control_panel.max_view_changed.connect(self._on_max_view_changed)
        self._sigma_selected: Optional[float] = None  # σ of the selected channel in view

        # Blanking
        self._blanking_panel.apply_requested.connect(self._on_apply_blanking)
        self._blanking_panel.remove_requested.connect(self._on_remove_blanking)
        self._blanking_panel.stale_changed.connect(self._update_plot_chips)

    def _on_open_folder(self):
        """Handle open folder action."""
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select TDT Block or Blackrock Recording Folder",
            "",
            QFileDialog.Option.ShowDirsOnly,
        )

        if folder:
            self._load_block(folder)

    def _load_block(self, folder_path: str):
        """Load a TDT or Blackrock recording from folder."""
        if not self._export_may_stop("open the recording"):
            return
        self._stop_jobs()

        # Show progress
        progress = progress_dialog("Loading recording...", self, cancellable=False, maximum=0)
        progress.show()
        QApplication.processEvents()

        try:
            block = self._app_state.load_block(folder_path)

            # Update UI
            self._stream_panel.set_block(block)

            # Select first stream
            if block.streams:
                first_stream = self._stream_panel.get_selected_stream() or list(block.streams.keys())[0]
                self._on_stream_selected(first_stream)

            self._reset_analog_view()
            self._time_plot.set_empty(False)

        except Exception as e:
            QMessageBox.critical(
                self,
                "Error Loading Block",
                f"Failed to load recording:\n\n{e}\n\n"
                "Ensure the folder contains valid TDT recording files "
                "(.tdx, .tev, .tsq, etc.) or Blackrock files (.ns1-.ns6).",
            )

        finally:
            progress.close()
            progress.deleteLater()

    @Slot(str)
    def _on_stream_selected(self, stream_name: str):
        """Handle stream selection."""
        self._app_state.select_stream(stream_name)
        state = self._app_state.get_current_state()

        if state:
            # Reset tracking state for new stream
            self._last_plot_channels = []
            self._last_scatter_mode = False
            self._raw_data_all = None
            # Invalidate the data buffer so we don't show stale data
            self._filtered_data = None
            self._raw_data = None
            self._raw_time = None
            self._buffer_channels = []
            self._buffer_start = -1.0
            self._buffer_end = -1.0
            self._buf_range = None
            self._buf_key = None
            self._max_span = None  # the cap depends on the stream (rate, duration)
            self._inflight = None
            self._reload_pending = False
            self._load_seq += 1  # a load still running for the previous stream must not land here

            # Restore scatter mode from stream state
            self._time_plot.set_scatter_mode(state.scatter_mode)

            # Update control panels
            self._control_panel.set_state(state)
            self._filter_panel.set_state(state)
            self._spectrum_panel.set_state(state)
            self._spike_panel.set_state(state)
            self._analog_panel.refresh()
            self._update_numbering_actions()
            self._update_spike_view()

            # Show this stream's own view (the plot would otherwise keep the previous
            # stream's range and draw the new data off-screen), then load it
            self._on_view_range_requested(*state.get_view_range())

            self._update_plot_chips()

    @Slot(ChannelMode)
    def _on_channel_mode_changed(self, mode: ChannelMode):
        """Handle channel mode change."""
        self._refresh_plot()

    @Slot(int)
    def _on_channel_changed(self, channel: int):
        """Handle single channel selection change."""
        self._refresh_plot()

    @Slot(list)
    def _on_channels_changed(self, channels: list):
        """Handle overlay channels change."""
        self._refresh_plot()

    @Slot(float, float)
    def _on_view_range_requested(self, start: float, end: float, direction: int = 0):
        """
        Set the view from code (event jump, Apply View, keys, stream switch) and refresh once.
        direction: pan direction for read-ahead (arrow keys); 0 for a jump.
        """
        self._pan_dir = direction
        state = self._app_state.get_current_state()
        if state is not None:
            self._update_view_limits(state)
            if self._max_span is not None and end - start > self._max_span * (1 + 1e-9):
                # Keep the requested start (pyqtgraph would shrink it around the centre)
                end = start + self._max_span
                self._toast.show_message(self._cap_message(state), WARNING)
        # setXRange emits sigRangeChanged synchronously; _on_plot_view_changed must not
        # start a second render or load for the same move
        self._setting_range = True
        try:
            self._time_plot.set_view_range(start, end)
        finally:
            self._setting_range = False
        self._refresh_plot()

    @Slot(object, object)
    def _on_plot_view_changed(self, start: float, end: float):
        """Handle plot view range change (from user interaction)."""
        state = self._app_state.get_current_state()
        if state is None:
            return

        if not self._setting_range:
            # Pan direction, for reading ahead (a zoom moves the centre only a little)
            p0, p1 = state.get_view_range()
            moved = (start + end) / 2 - (p0 + p1) / 2
            if abs(moved) > 0.02 * max(p1 - p0, 1e-9):
                self._pan_dir = 1 if moved > 0 else -1
        state.set_view_range(start, end)
        self._control_panel.update_view_range(start, end)
        self._sync_raster_to_time(start, end)
        if self._setting_range:
            return  # the caller refreshes once itself

        start, end = state.get_view_range()  # clamped to the recording (the plot may show past its ends)
        channels = state.get_active_channels()

        # Fast path: filtered buffer already covers this view — render immediately,
        # no debounce, no disk I/O, no re-filtering (~5 ms for slice + decimate).
        if self._buffer_covers(state, channels, start, end):
            self._rerender_from_buffer(start, end, state)
            self._maybe_prefetch()
            return

        # Buffer miss: show what is loaded now (instead of the previous frame), then load
        # once the user pauses briefly, so a fast pan doesn't start a read per wheel step
        if self._filtered_data is not None and channels == self._buffer_channels:
            self._rerender_from_buffer(start, end, state)
        if not self._pending_refresh:
            self._pending_refresh = True
            QTimer.singleShot(self.MISS_DEBOUNCE_MS, self._delayed_refresh)

    def _delayed_refresh(self):
        """Delayed refresh after view change."""
        self._pending_refresh = False
        self._refresh_plot()

    @Slot()
    def _on_filters_changed(self):
        """Handle filter chain change."""
        self._on_spike_inputs_changed()
        state = self._app_state.get_current_state()
        needs_all = state is not None and self._chain_needs_all(state)
        # If CAR/CMR is now active but we only have the displayed-channel buffer
        # (raw_data_all is None), we need a full reload to get all channels.
        if needs_all and self._raw_data_all is None:
            self._refresh_plot()
        else:
            self._apply_filters()

    @Slot(bool)
    def _on_scatter_mode_changed(self, enabled: bool):
        """Handle scatter mode toggle."""
        state = self._app_state.get_current_state()
        if state:
            state.scatter_mode = enabled
        # Plot items change, so force set_data on the next render (no re-filtering needed)
        self._last_scatter_mode = not enabled
        self._rerender_current()

    @Slot(dict)
    def _on_analyze_requested(self, params: dict):
        """Handle spectrum analysis request."""
        state = self._app_state.get_current_state()
        if not state or self._app_state.block is None:
            return

        # Show progress
        progress = progress_dialog("Computing spectrum...", self)
        progress.show()

        try:
            # Load data for analysis interval
            data, time_axis = self._app_state.reader.get_stream_data(
                state.stream_name,
                channels=params["channels"],
                start_time=params["start"],
                end_time=params["end"],
            )

            # Blank stimulation artifacts, then apply filters if any
            data = self._blank(data, time_axis, params["channels"], state.stream_name)
            if state.filters and not state.global_bypass:
                data, _, _ = apply_filter_chain(
                    data, state.filters, state.sample_rate, state.global_bypass
                )

            progress.setValue(50)

            # Compute spectrum
            worker = SpectralWorker(
                data,
                state.sample_rate,
                method=params["method"],
                channels=list(range(data.shape[0])) if data.ndim > 1 else [0],
                nperseg=params["nperseg"],
                overlap=params["overlap"],
            )

            # Run synchronously for simplicity
            if params["method"] == "fft":
                from dsp import compute_fft
                freqs, magnitude = compute_fft(data, state.sample_rate)
            else:
                from dsp import compute_psd
                freqs, magnitude = compute_psd(
                    data,
                    state.sample_rate,
                    nperseg=params["nperseg"],
                    overlap=params["overlap"],
                )

            progress.setValue(100)

            # Display result
            self._spectrum_plot.set_data(
                freqs,
                magnitude,
                params["channels"],
                method=params["method"],
                channel_names=[state.channel_label(c) for c in params["channels"]],
            )

            # Switch to spectrum tab
            self._plot_tabs.setCurrentWidget(self._spectrum_plot)

        except Exception as e:
            QMessageBox.warning(
                self,
                "Analysis Error",
                f"Failed to compute spectrum:\n\n{e}",
            )

        finally:
            progress.close()
            progress.deleteLater()

    def _refresh_plot(self):
        """Refresh the time series plot, using the in-memory buffer when possible."""
        state = self._app_state.get_current_state()
        if not state or self._app_state.block is None:
            return

        self._update_view_limits(state)
        start, end = state.get_view_range()
        channels = state.get_active_channels()
        needs_all = self._chain_needs_all(state)

        # ── Buffer fast path ────────────────────────────────────────────────
        # The view moved within the already-loaded + filtered buffer.
        # Just re-slice and re-decimate — no disk I/O, no re-filtering (~5 ms).
        # Filters are re-applied by _on_filters_changed when the chain changes.
        # A CAR/CMR that was just added needs the all-channel buffer, so that is a miss.
        if self._buffer_covers(state, channels, start, end):
            self._rerender_from_buffer(start, end, state)
            self._maybe_prefetch()
            return

        # ── Channel change under CAR/CMR: every channel is already in memory ───
        if (
            needs_all
            and self._raw_data_all is not None
            and self._raw_time is not None
            and start >= self._buffer_start
            and end <= self._buffer_end
        ):
            self._raw_data = self._raw_data_all[channels]
            self._buffer_channels = channels
            self._apply_filters()
            self._maybe_prefetch()
            return

        # ── Buffer miss: background load ────────────────────────────────────
        # Still overlapping the buffer (a pan ran past it): extend it, reading only the new
        # part. Otherwise (a jump): read just the view plus a filter pad first, so data shows
        # quickly; _maybe_prefetch then grows the margins in the background.
        load_key = (state.stream_name, tuple(self._load_channels(state)))
        v0, v1 = self._view_samples(state)
        overlaps = (
            self._buf_range is not None and self._buf_key == load_key
            and self._buf_range[0] < v1 and v0 < self._buf_range[1]
        )
        self._start_buffer_load(state, full=overlaps)

    # ------------------------------------------------------------------ plot buffer

    def _load_channels(self, state) -> list:
        """Rows read from disk: every channel under CAR/CMR, else the displayed ones."""
        return list(range(state.num_channels)) if self._chain_needs_all(state) else state.get_active_channels()

    @staticmethod
    def _n_samples(state) -> int:
        return int(round(state.duration * state.sample_rate))

    def _view_samples(self, state) -> tuple:
        """The current view as [start, stop) samples."""
        start, end = state.get_view_range()
        fs, n = state.sample_rate, self._n_samples(state)
        return max(0, int(np.floor(start * fs))), min(n, int(np.ceil(end * fs)) + 1)

    # Buffer margins as fractions of the view, (before, after): ahead of a pan the buffer
    # holds 1.5 views (so reading starts early), behind it half a view (for small reversals)
    MARGINS_STILL = (0.5, 0.5)
    MARGINS_PANNING = (0.5, 1.5)

    def _margin_samples(self, n_load: int, view_n: int) -> tuple:
        """Samples kept (before, after) the view, following the pan direction, within MAX_BUFFER_SAMPLES."""
        before, after = self.MARGINS_STILL if self._pan_dir == 0 else self.MARGINS_PANNING
        if self._pan_dir < 0:
            before, after = after, before
        before, after = int(before * view_n), int(after * view_n)
        room = max(0, int(self.MAX_BUFFER_SAMPLES // max(1, n_load)) - view_n)
        if before + after > room:
            scale = room / (before + after)
            before, after = int(before * scale), int(after * scale)
        return before, after

    def _buffer_covers(self, state, channels, start: float, end: float) -> bool:
        return (
            self._filtered_data is not None
            and self._raw_time is not None
            and channels == self._buffer_channels
            and start >= self._buffer_start
            and end <= self._buffer_end
            and (not self._chain_needs_all(state) or self._raw_data_all is not None)
        )

    def _processing_signature(self, state) -> tuple:
        """Everything a processed buffer depends on besides the raw samples."""
        return (
            state.global_bypass,
            [f.to_dict() for f in state.filters],
            tuple(id(r) for r in self._app_state.active_blanking(state.stream_name)),
        )

    def _start_buffer_load(self, state, full: bool):
        """
        Load the buffer for the current view in the background.

        full: the whole target (view ± margin); otherwise just the view ± a filter pad.
        Only samples the current buffer doesn't hold are read.
        """
        fs = state.sample_rate
        channels = state.get_active_channels()
        needs_all = self._chain_needs_all(state)
        load_channels = self._load_channels(state)
        load_key = (state.stream_name, tuple(load_channels))
        v0, v1 = self._view_samples(state)
        before, after = self._margin_samples(len(load_channels), v1 - v0)
        if not full:
            pad = int(self.FILTER_PAD_S * fs)
            before, after = min(before, pad), min(after, pad)
        want = window_target(v0, v1, self._n_samples(state), before, after)
        if want[1] <= want[0]:
            return

        reuse = self._buf_range is not None and self._buf_key == load_key
        if reuse and self._buf_range[0] <= v0 and v1 <= self._buf_range[1]:
            # Read-ahead while the view is still covered: at most MAX_EXTEND_S of new samples
            # per side, so results land often; _maybe_prefetch continues from each landing.
            # (After a miss the full lead is read instead: a capped read would end at the
            # view's edge and land behind it again.)
            step = int(self.MAX_EXTEND_S * fs)
            b0, b1 = self._buf_range
            want = (max(want[0], b0 - step), min(want[1], b1 + step))
        if reuse and want == self._buf_range and channels == self._buffer_channels:
            return  # already holding exactly this

        # A load for these channels is already reading: never throw that work away while the
        # view is still near it. If it covers the view, let it land; if a pan ran past it,
        # extend again from its result as soon as it lands (_reload_pending). Cancelling it
        # and re-reading from the older buffer is what made fast pans fall behind for good.
        request = (state.stream_name, tuple(channels), needs_all)
        if self._inflight is not None and self._inflight[:3] == request:
            w0, w1 = self._inflight[3], self._inflight[4]
            if w0 <= v0 and v1 <= w1:
                return
            if w0 < v1 and v0 < w1:
                self._reload_pending = True
                return

        # A jump elsewhere or other channels: drop whatever is in flight (its result is
        # discarded by the sequence check) and start over
        self._reload_pending = False
        self._worker_manager.cancel_all()
        self._load_seq += 1
        seq = self._load_seq
        self._inflight = request + want

        old = old_range = None
        if reuse:
            old = self._raw_data_all if needs_all else self._raw_data
            old_range = self._buf_range if old is not None else None
            if old is not None and not needs_all and list(load_channels) != list(self._buffer_channels):
                old = old_range = None  # rows don't match
        process = {
            "rows": channels if needs_all else None,
            "all_channels": needs_all,
            "channels": channels,
            "num_channels": state.num_channels,
            "filters": copy.deepcopy(state.filters),
            "global_bypass": state.global_bypass,
            "blank_results": list(self._app_state.active_blanking(state.stream_name)),
        }
        tag = {
            "stream": state.stream_name, "channels": channels, "needs_all": needs_all,
            "load_key": load_key, "signature": self._processing_signature(state),
        }
        worker = BufferLoadWorker(
            self._app_state.reader, state.stream_name, load_channels, want, fs,
            old=old, old_range=old_range, process=process, tag=tag,
        )
        worker.signals.result.connect(lambda result, s=seq: self._on_buffer_loaded(result, s))
        worker.signals.error.connect(lambda err, s=seq: self._on_data_load_error(err, s))
        self._worker_manager.submit(worker)

    def _maybe_prefetch(self):
        """
        Grow the buffer in the background before the view reaches its edge, so a steady pan
        doesn't wait for the disk: once a third of the margin on a side is used up (and that
        side isn't the recording's end), load the full target (only new samples are read).
        """
        state = self._app_state.get_current_state()
        if state is None or self._buf_range is None or self._inflight is not None:
            return
        start, end = state.get_view_range()
        if not self._buffer_covers(state, state.get_active_channels(), start, end):
            return
        v0, v1 = self._view_samples(state)
        n_total = self._n_samples(state)
        before, after = self._margin_samples(len(self._load_channels(state)), v1 - v0)
        b0, b1 = self._buf_range
        short_left = b0 > 0 and v0 - b0 < before * 2 // 3
        short_right = b1 < n_total and b1 - v1 < after * 2 // 3
        if short_left or short_right:
            self._start_buffer_load(state, full=True)

    def _update_view_limits(self, state):
        """
        Cap the view length: the user's maximum (Channels → View Range), and memory — the
        buffer must hold at least twice the view within MAX_BUFFER_SAMPLES.
        """
        n_load = state.num_channels if self._chain_needs_all(state) else len(state.get_active_channels())
        memory_cap = self.MAX_BUFFER_SAMPLES / (2 * max(1, n_load) * state.sample_rate)
        max_span = min(self._user_max_view, memory_cap, state.duration)
        if max_span == self._max_span:
            return
        self._max_span = max_span
        start, end = state.get_view_range()
        self._setting_range = True  # a clamp here must not start a refresh of its own
        try:
            for plot in (self._time_plot, self._analog_plot):
                plot.plot_item().getViewBox().set_max_span(max_span)
            if end - start > max_span * (1 + 1e-9):
                self._time_plot.set_view_range(start, start + max_span)
                self._toast.show_message(self._cap_message(state), WARNING)
        finally:
            self._setting_range = False

    def _cap_message(self, state) -> str:
        n = state.num_channels if self._chain_needs_all(state) else len(state.get_active_channels())
        memory_cap = self.MAX_BUFFER_SAMPLES / (2 * max(1, n) * state.sample_rate)
        if memory_cap < self._user_max_view and memory_cap < state.duration:
            return f"View limited to {self._max_span:.3g} s while {n} channels are loaded (memory)"
        return f"View limited to {self._max_span:.3g} s (Max length: Channels → View Range)"

    def _on_max_span_hit(self):
        """A zoom-out stopped at the view-length limit."""
        state = self._app_state.get_current_state()
        if state is None or self._max_span is None:
            return
        self._toast.show_message(self._cap_message(state), WARNING)

    @Slot(float)
    def _on_max_view_changed(self, seconds: float):
        """The user changed the maximum view length."""
        self._user_max_view = float(seconds)
        self._settings.setValue("max_view_s", self._user_max_view)
        self._max_span = None  # re-apply
        state = self._app_state.get_current_state()
        if state is not None:
            self._update_view_limits(state)
            self._refresh_plot()

    def _on_data_load_error(self, err: str, seq: int):
        if seq != self._load_seq:
            return  # a cancelled or superseded load
        self._inflight = None
        self._reload_pending = False
        if self._analog_waiting:
            self._analog_waiting = False
            self._refresh_analog()
        self._toast.show_message(f"Error loading data: {err}", ERROR)

    def _on_buffer_loaded(self, result: dict, seq: int):
        """A background load landed: swap the buffer in, render, keep prefetching."""
        if seq != self._load_seq:
            return  # A newer request was already issued — ignore this one
        self._inflight = None

        state = self._app_state.get_current_state()
        tag = result["tag"]
        if state is None or state.stream_name != tag["stream"]:
            return

        data = result["data"]
        channels = tag["channels"]
        fs = state.sample_rate
        if tag["needs_all"]:
            self._raw_data_all = data
            self._raw_data = data[channels]
        else:
            self._raw_data = data
            self._raw_data_all = None
        self._raw_time = result["time"]
        self._buf_range = result["range"]
        self._buf_key = tag["load_key"]
        self._buffer_start = self._buf_range[0] / fs
        self._buffer_end = self._buf_range[1] / fs
        self._buffer_channels = channels

        # The old filtered buffer must never be sliced against the new time axis
        self._filtered_data = None
        self._blank_mask = None
        try:
            if result["processed"] is not None and tag["signature"] == self._processing_signature(state):
                self._show_filter_warnings(result["warnings"])
                self._filtered_data = result["processed"]
                self._blank_mask = result["mask"]
                self._rerender_from_buffer(*state.get_view_range(), state)
                self._update_plot_chips()
            else:
                # Filters or blanking changed while it loaded: process the new samples now
                self._apply_filters()
        except Exception as e:
            self._raw_data = self._raw_data_all = self._raw_time = None
            self._buf_range = self._buf_key = None
            self._buffer_channels = []
            self._reload_pending = False
            self._toast.show_message(f"Could not process the loaded data: {e}", ERROR)
            return
        # The analog plot reads this same window now, while its pages are in the OS cache
        # (before the next main load starts reading new pages)
        self._sync_analog_with_main()
        if self._reload_pending:
            # The view moved past this load while it was reading: extend from here now
            self._reload_pending = False
            self._refresh_plot()
        else:
            self._maybe_prefetch()

    def _sync_analog_with_main(self):
        key = self._current_analog_key()
        if key is None or not self._analog_follows_main(key):
            return
        if self._analog_waiting or self._analog_range != self._buf_range:
            self._analog_waiting = False
            self._refresh_analog()
            self._maybe_prefetch_analog()

    def _show_filter_warnings(self, warnings):
        warning = warnings[0] if warnings else ""
        if warning and warning != self._last_filter_warning:
            self._toast.show_message(f"Filter warning: {warning}", WARNING)
        self._last_filter_warning = warning

    def _rerender_from_buffer(self, start: float, end: float, state):
        """
        Slice the in-memory filtered buffer to [start, end] and render.
        Uses binary search (O(log n)) — no boolean mask, no copy for the slice.
        Typically ≤ 5 ms for a 30-second buffer at 24 kHz.
        """
        if self._filtered_data is None or self._raw_time is None:
            return

        time = self._raw_time
        data = self._filtered_data

        # Binary search is faster than a boolean mask for sorted time arrays
        i0 = max(0, int(np.searchsorted(time, start, side="left")))
        i1 = min(len(time), int(np.searchsorted(time, end, side="right")))
        if i1 <= i0:
            return

        # Slice — creates a view (no copy) for C-contiguous arrays
        data_view = data[:, i0:i1] if data.ndim > 1 else data[i0:i1]
        time_view = time[i0:i1]

        # Vrms of exactly what is visible, blanked samples excluded. The buffer's channels:
        # the selection may already have moved on while its load is still in flight.
        channels = self._buffer_channels
        if not channels:
            return
        mask_view = self._blank_mask[:, i0:i1] if self._blank_mask is not None else None
        vrms = compute_vrms(data_view, channels, mask_view)
        state.vrms_values = vrms
        self._time_plot.set_vrms([(state.channel_label(c), vrms.get(c)) for c in channels])
        self._probe_map.update_vrms(vrms)
        self._update_spike_overlay(state, channels, data_view, time_view, mask_view, end - start)

        n = data_view.shape[1] if data_view.ndim > 1 else len(data_view)
        if n > self.MAX_PLOT_POINTS:
            plot_data, plot_time = decimate_for_display(
                data_view, time_view, target_points=self.MAX_PLOT_POINTS
            )
        else:
            plot_data = data_view
            plot_time = time_view

        self._time_plot.set_blank_spans(
            blank_spans(self._app_state.active_blanking(state.stream_name), channels, start, end)
        )
        scatter = state.scatter_mode
        if channels == self._last_plot_channels and scatter == self._last_scatter_mode:
            self._time_plot.update_data(plot_data, plot_time)
        else:
            self._time_plot.set_scatter_mode(scatter)
            self._time_plot.set_data(
                plot_data, plot_time, channels, [state.channel_label(c) for c in channels]
            )
            self._time_plot.set_title(*self._plot_title(state, channels))
            self._last_plot_channels = channels
            self._last_scatter_mode = scatter

    @staticmethod
    def _chain_needs_all(state) -> bool:
        """True when an active CAR/CMR filter needs every channel of the stream loaded."""
        return not state.global_bypass and any(
            f.filter_type in (FilterType.CAR, FilterType.CMR) and not f.bypassed
            for f in state.filters
        )

    def _apply_filters(self):
        """Apply filter chain to the loaded buffer, then render the current view."""
        state = self._app_state.get_current_state()
        if state is None or self._raw_data is None:
            return

        channels = self._buffer_channels  # the rows of _raw_data (not the current selection)
        if not channels:
            return

        # The all-channel buffer (~400 MB for a 12 s view of 136 ch) is only kept while a
        # CAR/CMR filter needs it; the displayed rows stay valid on their own
        needs_all = self._chain_needs_all(state)
        if not needs_all and self._raw_data_all is not None:
            self._raw_data_all = None
            self._buf_key = (state.stream_name, tuple(channels))

        # Blank stimulation artifacts on copies (the raw buffer is kept, so removing blanking
        # needs no reload), then filter: the same steps background loads run (process_buffer)
        data, mask, warnings = process_buffer(
            self._raw_data, self._raw_data_all if needs_all else None, self._raw_time, channels,
            state.num_channels, state.filters, state.global_bypass, state.sample_rate,
            self._app_state.active_blanking(state.stream_name),
        )
        self._show_filter_warnings(warnings)

        # Cache the full-resolution filtered buffer, and the blanked samples (left out of Vrms)
        self._filtered_data = data
        self._blank_mask = mask

        # Render only the visible view window (slice + decimate from buffer)
        view_start, view_end = state.get_view_range()
        self._rerender_from_buffer(view_start, view_end, state)

        self._update_plot_chips()

    # ------------------------------------------------------------------ analog viewer

    def _reset_analog_view(self):
        """After a recording or session loads: rebuild the analog panel and plot."""
        self._analog_key = None
        self._analog_data = None
        self._analog_time = None
        self._analog_range = None
        self._analog_inflight = None
        self._analog_reload_pending = False
        self._analog_waiting = False
        self._analog_seq += 1  # an analog load for the previous recording must not land
        self._analog_panel.refresh()
        self._analog_panel.set_summary("")
        self._blanking_panel.refresh()
        self._update_plot_chips()
        self._update_spike_view()
        self._analog_plot.highlight_event(None)
        self._update_analog_events()
        state = self._app_state.get_current_state()
        # Only when not linked: pyqtgraph links push range changes back to the master plot,
        # re-derived from the follower's pixel geometry (that is how a 10 s view came out
        # as 7.52 s at load, and why linked ranges drifted by a few ms after jumps)
        if state is not None and not self._analog_panel.is_synced():
            self._analog_plot.set_view_range(*state.get_view_range())
        self._refresh_analog()

    def _update_analog_events(self):
        """Redraw event markers on the analog plot."""
        self._analog_plot.set_events(
            self._app_state.events, self._app_state.modality_names()
        )
        self._raster_plot.set_events(self._app_state.events, self._app_state.modality_names())
        self._update_analog_title()

    def _current_analog_key(self):
        """Selected analog channel, or None if nothing (still) loaded has it."""
        key = self._analog_panel.selected_channel()
        if key is None or key[0] not in self._app_state.stream_states:
            return None
        return key

    def _update_analog_title(self):
        key = self._current_analog_key()
        if key is None:
            self._analog_plot.set_title("No analog channels")
            return
        state = self._app_state.stream_states[key[0]]
        label = state.channel_label(key[1])
        assignment = self._app_state.analog_assignments.get(key)
        self._analog_plot.set_title(
            f"{label} — {assignment.modality}" if assignment else label
        )

    @Slot(bool)
    def _on_analog_sync_changed(self, synced: bool):
        if synced:
            # Linking aligns the analog plot to the main plot by itself; setting its range here
            # would push a geometry-derived range back onto the main plot
            self._analog_plot.set_x_link(self._time_plot.plot_item())
        else:
            self._analog_plot.set_x_link(None)

    @Slot()
    def _on_analog_channel_selected(self):
        self._update_analog_title()
        self._analog_plot.highlight_event(None)
        self._refresh_analog()

    @Slot(str)
    def _on_analog_list_changed(self, stream_name: str):
        """Analog channels of a stream were edited: refresh the electrode lists too."""
        state = self._app_state.get_stream_state(stream_name)
        if state is not None and stream_name == self._app_state.current_stream:
            self._control_panel.set_state(state)
            self._spectrum_panel.set_state(state)
            self._last_plot_channels = []
            self._buffer_channels = []
            self._refresh_plot()
        self._app_state.mark_blanking_stale()  # electrode set changed
        self._on_spike_inputs_changed()
        self._blanking_panel.update_status()
        self._update_plot_chips()
        self._update_analog_events()
        self._refresh_analog()

    @Slot(object, object)
    def _on_analog_view_changed(self, start: float, end: float):
        """Analog plot moved (linked to the main plot, or panned on its own)."""
        key = self._current_analog_key()
        if self._analog_buffer_covers(key, start, end):
            self._render_analog(start, end)
            self._maybe_prefetch_analog()
            return
        if key is not None and key == self._analog_key:
            self._render_analog(start, end)  # what is loaded, until the rest arrives
        if not self._pending_analog_refresh:
            self._pending_analog_refresh = True
            QTimer.singleShot(self.MISS_DEBOUNCE_MS, self._delayed_analog_refresh)

    def _delayed_analog_refresh(self):
        self._pending_analog_refresh = False
        self._refresh_analog()

    def _analog_buffer_covers(self, key, start: float, end: float) -> bool:
        if key is None or key != self._analog_key or self._analog_data is None or self._analog_range is None:
            return False
        fs = self._app_state.stream_states[key[0]].sample_rate
        return start >= self._analog_range[0] / fs and end <= self._analog_range[1] / fs

    def _analog_view_samples(self, key) -> tuple:
        st = self._app_state.stream_states[key[0]]
        start, end = self._analog_plot.get_view_range()
        fs, n = st.sample_rate, self._n_samples(st)
        v0 = max(0, min(n, int(np.floor(max(0.0, start) * fs))))
        v1 = max(v0, min(n, int(np.ceil(end * fs)) + 1))
        return v0, v1

    def _refresh_analog(self):
        """Show the selected analog channel for the analog plot's current view."""
        key = self._current_analog_key()
        if key is None or self._app_state.block is None:
            self._analog_plot.set_data(None, None)
            self._update_analog_title()
            return

        start, end = self._analog_plot.get_view_range()
        if self._analog_buffer_covers(key, start, end):
            self._render_analog(start, end)
            self._maybe_prefetch_analog()
            return
        v0, v1 = self._analog_view_samples(key)
        overlaps = (
            key == self._analog_key and self._analog_range is not None
            and self._analog_range[0] < v1 and v0 < self._analog_range[1]
        )
        self._start_analog_load(key, full=overlaps)

    def _analog_follows_main(self, key) -> bool:
        """Synced and on the main plot's stream: the analog plot reuses the main buffer's window."""
        return self._analog_panel.is_synced() and key[0] == self._app_state.current_stream

    def _start_analog_load(self, key, full: bool):
        """
        Load the analog channel's window in the background (one channel, nothing to filter).

        When it follows the main plot, it loads exactly the main buffer's sample window:
        the file stores every channel side by side, so those pages were just read and are
        in the OS cache (~0.1 s instead of a second cold read competing with the main one).
        """
        stream, channel = key
        st = self._app_state.stream_states[stream]
        v0, v1 = self._analog_view_samples(key)
        if v1 <= v0:
            self._analog_plot.set_data(None, None)
            return
        if self._analog_follows_main(key):
            main = self._buf_range
            if main is not None and main[0] <= v0 and v1 <= main[1]:
                want = main
            elif self._inflight is not None:
                self._analog_waiting = True  # read once the main load has brought the pages in
                return
            else:
                want = window_target(v0, v1, self._n_samples(st), 0)
        else:
            margin = (v1 - v0) if full else 0
            want = window_target(v0, v1, self._n_samples(st), margin)
        same = key == self._analog_key and self._analog_range is not None
        if same and want == self._analog_range:
            return
        inflight = self._analog_inflight
        if inflight is not None and inflight[0] == key:
            if inflight[1] <= v0 and v1 <= inflight[2]:
                return  # a load covering this view is on its way
            if inflight[1] < v1 and v0 < inflight[2]:
                self._analog_reload_pending = True  # extend from its result when it lands
                return

        self._analog_reload_pending = False
        self._analog_workers.cancel_all()
        self._analog_seq += 1
        seq = self._analog_seq
        self._analog_inflight = (key,) + want
        worker = BufferLoadWorker(
            self._app_state.reader, stream, [channel], want, st.sample_rate,
            old=self._analog_data[None, :] if same else None,
            old_range=self._analog_range if same else None,
            tag=key,
        )
        worker.signals.result.connect(lambda result, s=seq: self._on_analog_loaded(result, s))
        worker.signals.error.connect(lambda err, s=seq: self._on_analog_load_error(err, s))
        self._analog_workers.submit(worker)

    def _maybe_prefetch_analog(self):
        key = self._current_analog_key()
        if key is None or self._analog_inflight is not None or not self._analog_buffer_covers(
            key, *self._analog_plot.get_view_range()
        ):
            return
        if self._analog_follows_main(key):
            # Keep the same window as the main buffer (cache hits)
            if self._buf_range is not None and self._analog_range != self._buf_range:
                self._start_analog_load(key, full=True)
            return
        v0, v1 = self._analog_view_samples(key)
        n_total = self._n_samples(self._app_state.stream_states[key[0]])
        b0, b1 = self._analog_range
        half = (v1 - v0) // 2
        if (b0 > 0 and v0 - b0 < half) or (b1 < n_total and b1 - v1 < half):
            self._start_analog_load(key, full=True)

    def _on_analog_load_error(self, err: str, seq: int):
        if seq != self._analog_seq:
            return
        self._analog_inflight = None
        self._analog_reload_pending = False
        self._toast.show_message(f"Error loading analog data: {err}", ERROR)

    def _on_analog_loaded(self, result: dict, seq: int):
        if seq != self._analog_seq:
            return
        self._analog_inflight = None
        key = result["tag"]
        if key not in self._app_state.analog_channel_list():
            return  # the channel went away while it loaded
        self._analog_key = key
        self._analog_data = np.ravel(result["data"])
        self._analog_time = result["time"]
        self._analog_range = result["range"]
        if self._analog_reload_pending:
            self._analog_reload_pending = False
            self._refresh_analog()
        else:
            self._render_analog(*self._analog_plot.get_view_range())
            self._maybe_prefetch_analog()

    def _render_analog(self, start: float, end: float):
        time, data = self._analog_time, self._analog_data
        if time is None or data is None:
            return
        i0 = int(np.searchsorted(time, start, side="left"))
        i1 = int(np.searchsorted(time, end, side="right"))
        if i1 <= i0:
            self._analog_plot.set_data(None, None)
            return
        data_view, time_view = data[i0:i1], time[i0:i1]
        if data_view.size > self.MAX_PLOT_POINTS:
            data_view, time_view = decimate_for_display(
                data_view, time_view, target_points=self.MAX_PLOT_POINTS
            )
        self._analog_plot.set_data(data_view, time_view)

    @Slot(object)
    def _on_event_selected(self, event):
        """Jump to an event: the main plot (and the analog plot, when not synced)."""
        pad = 0.25 * event.duration
        start, end = event.onset - pad, event.offset + pad
        if end - start < 0.05:
            mid = (event.onset + event.offset) / 2
            start, end = mid - 0.025, mid + 0.025
        start = max(0.0, start)

        self._analog_plot.highlight_event(event)
        if self._app_state.get_current_state() is not None:
            self._on_view_range_requested(start, end)
        if not self._analog_panel.is_synced():
            self._analog_plot.set_view_range(start, end)

    # ------------------------------------------------------------------ spike threshold

    def _rerender_current(self):
        state = self._app_state.get_current_state()
        if state is not None and self._filtered_data is not None:
            self._rerender_from_buffer(*state.get_view_range(), state)

    def _spike_overlay_visible(self, state) -> bool:
        return (
            self._control_tabs.currentIndex() == self._spike_tab_index
            or state.spike_settings.show_everywhere
        )

    def _update_spike_overlay(self, state, channels, data_view, time_view, mask_view, view_s):
        """Position the draggable threshold line(s) for the selected channel (Single mode only)."""
        single = state.channel_mode == ChannelMode.SINGLE
        if not self._spike_overlay_visible(state) or not single:
            self._sigma_selected = None
            self._time_plot.set_threshold_lines([])
            return

        s = state.spike_settings
        data = np.atleast_2d(data_view)[:1]
        mask = np.atleast_2d(mask_view)[:1] if mask_view is not None else None
        sigma = robust_sigma(data, mask)[0]
        self._sigma_selected = float(sigma) if np.isfinite(sigma) else None
        if self._sigma_selected is None:
            self._time_plot.set_threshold_lines([])  # fully blanked in view
        else:
            thr = s.k * self._sigma_selected
            self._time_plot.set_threshold_lines({NEGATIVE: [-thr], POSITIVE: [thr], BOTH: [-thr, thr]}[s.polarity])

        hints = []
        active = [] if state.global_bypass else [f for f in state.filters if not f.bypassed]
        if not any(f.filter_type in (FilterType.HIGHPASS, FilterType.BANDPASS) for f in active):
            hints.append(
                "No high-pass or band-pass filter is active — thresholds on broadband data "
                "aren't meaningful (e.g. add a 250–5000 Hz band-pass)."
            )
        if view_s < 5.0:
            hints.append(f"View is {view_s:.2f} s — σ from under 5 s of data is noisy.")
        self._spike_panel.set_hints(hints)

    # ------------------------------------------------------------------ spike detection

    def _on_spike_inputs_changed(self):
        """Blanking, filters or threshold settings changed: detected spikes are out of date."""
        state = self._app_state.get_current_state()
        res = self._app_state.spike_results.get(state.stream_name) if state else None
        if res is not None and not res.stale:  # cheap when already stale (e.g. while dragging the line)
            self._app_state.mark_spikes_stale(state.stream_name)
            self._update_spike_view()

    def _update_spike_view(self):
        """Show the current stream's detection result in the panel and the Raster tab."""
        state = self._app_state.get_current_state()
        res = self._app_state.spike_results.get(state.stream_name) if state else None
        self._plot_tabs.setTabEnabled(self._raster_tab_index, res is not None)
        if res is None:
            self._spike_panel.set_summary("")
            self._raster_plot.clear()
            if self._plot_tabs.currentIndex() == self._raster_tab_index:
                self._plot_tabs.setCurrentWidget(self._time_plot)
            return

        rows = [c for c in state.ordered_neural_channels() if c in res.times]
        rates = [res.rate(c) for c in rows]
        sign = {NEGATIVE: "−", POSITIVE: "+", BOTH: "±"}.get(res.polarity, "")
        summary = (
            f"Detected {res.total():,} spikes on {len(rows)} electrodes · "
            f"σ from {res.sigma_window[0]:.1f}–{res.sigma_window[1]:.1f} s · "
            f"k = {res.k:g} · median rate {np.median(rates) if rates else 0:.2f} Hz"
        )
        tooltip = "\n".join(
            f"{state.channel_label(c)}: σ = {format_volts(res.sigma[c])}, threshold "
            f"{sign}{format_volts(res.k * res.sigma[c])}, {len(res.times[c])} spikes"
            for c in rows
        )
        self._spike_panel.set_summary(summary, tooltip, res.stale)

        def describe(c):
            return f"{state.channel_label(c)}: {len(res.times[c]):,} spikes · {res.rate(c):.2f} Hz"

        self._raster_plot.set_spikes(
            rows, res.times, {c: state.display_number(c) for c in rows}, describe, res.stale
        )
        if id(res) != self._raster_result_id:
            # A new result opens on the whole recording (the raster isn't capped)
            self._raster_result_id = id(res)
            self._syncing_raster = True
            try:
                self._raster_plot.set_view(0.0, res.duration or state.duration)
            finally:
                self._syncing_raster = False

    @Slot()
    def _on_detect_spikes(self):
        state = self._app_state.get_current_state()
        if state is None or self._app_state.block is None:
            return
        t0, t1 = state.get_view_range()
        if t1 - t0 < 5.0:
            answer = QMessageBox.question(
                self, "Short View",
                f"σ will be frozen from the current view, which is only {t1 - t0:.2f} s long, so the "
                "thresholds may be noisy. Detect anyway?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        blank_results = self._app_state.active_blanking(state.stream_name)
        active_filters = [] if state.global_bypass else [f for f in state.filters if not f.bypassed]
        processing = "; ".join(filter(None, [
            ("Blanking: " + ", ".join(r.modality for r in blank_results)) if blank_results else "",
            ("Filters: " + ", ".join(f.get_display_name() for f in active_filters)) if active_filters else "",
        ])) or "raw (no blanking, no filters)"

        worker = SpikeDetectWorker(
            self._app_state.reader, state.stream_name, state.ordered_neural_channels(),
            state.sample_rate, state.num_channels, int(round(state.duration * state.sample_rate)),
            (t0, t1), copy.deepcopy(state.spike_settings), blank_results,
            copy.deepcopy(active_filters), processing,
        )

        def done(result):
            self._app_state.spike_results[state.stream_name] = result
            self._update_spike_view()
            self._plot_tabs.setCurrentIndex(self._raster_tab_index)
            self._toast.show_message(f"Detected {result.total():,} spikes")

        self._run_job("spike detection", worker, "Detecting spikes across the recording...", done,
                      "Failed to detect spikes")

    @Slot(int, float)
    def _on_raster_channel_clicked(self, channel: int, t: float):
        """Open the clicked electrode in Time Series, centred on the clicked time."""
        state = self._app_state.get_current_state()
        if state is None or channel not in state.neural_channels():
            return
        state.channel_mode = ChannelMode.SINGLE
        state.selected_channel = channel
        self._control_panel.set_state(state)
        self._probe_map.set_selected_channel(channel)
        self._plot_tabs.setCurrentWidget(self._time_plot)
        start, end = state.get_view_range()
        width = end - start
        start = min(max(0.0, t - width / 2), max(0.0, state.duration - width))
        self._on_view_range_requested(start, start + width)

    def _sync_raster_to_time(self, start: float, end: float):
        """Time Series moved: the raster follows while it isn't zoomed out past the cap."""
        if self._syncing_raster or self._max_span is None:
            return
        r0, r1 = self._raster_plot.view_range()
        if r1 - r0 > self._max_span * (1 + 1e-6):
            return  # the raster is showing a wider overview; leave it there
        self._syncing_raster = True
        try:
            self._raster_plot.set_view(start, end)
        finally:
            self._syncing_raster = False

    @Slot(float, float)
    def _on_raster_view_changed(self, start: float, end: float):
        """The raster moved: Time Series follows while the raster's span fits the cap."""
        if self._syncing_raster or self._max_span is None or end - start > self._max_span * (1 + 1e-6):
            return
        if self._app_state.get_current_state() is None:
            return
        self._syncing_raster = True
        try:
            self._on_view_range_requested(start, end)
        finally:
            self._syncing_raster = False

    @Slot(float)
    def _on_threshold_dragged(self, level: float):
        """The threshold line was dragged: k = |y| / σ of the selected channel."""
        if not self._sigma_selected:
            return
        self._spike_panel.set_k(level / self._sigma_selected)

    # ------------------------------------------------------------------ blanking

    def _blank(self, data, time_axis, channels, stream_name):
        """Data with this stream's applied blanking interpolated across (unchanged if none)."""
        results = self._app_state.active_blanking(stream_name)
        if not results or data is None or time_axis is None:
            return data
        return apply_blanking(data, time_axis, channels, results)

    def _blanking_summary_lines(self, stream_name: str) -> list:
        results = self._app_state.active_blanking(stream_name)
        if not results:
            return ["  (none)"]
        lines = []
        for r in results:
            s = self._app_state.get_blanking_settings(r.modality)
            if r.mode == ADVANCED:
                how = (f"adaptive: pre {s.adv_pre_ms:g} ms, until {s.n_windows} x {s.window_ms:g} ms "
                       f"windows < {s.multiplier:g}x baseline (post {s.min_post_ms:g}-{s.max_post_ms:g} ms)")
            else:
                how = f"pre {s.pre_ms:g} ms, post {s.post_ms:g} ms"
            stale = "  (out of date when exported)" if r.stale else ""
            lines.append(f"  {r.modality}: {how}, {r.interpolation} interpolation, {r.summary()}{stale}")
        return lines

    def _update_plot_chips(self):
        """Blanking and filter chips in the Time Series header."""
        state = self._app_state.get_current_state()
        if state is None:
            self._time_plot.set_chips([])
            return
        chips = []
        results = self._app_state.active_blanking(state.stream_name)
        if results:
            stale = [r.modality for r in results if r.stale]
            tooltip = "Stimulation artifacts are blanked (interpolated across) before filtering.\n" + "\n".join(
                line.strip() for line in self._blanking_summary_lines(state.stream_name)
            )
            if stale:
                tooltip += f"\nOut of date: {', '.join(stale)} — events or settings changed; apply again."
            chips.append(("Blanked: " + ", ".join(r.modality for r in results), tooltip, bool(stale)))
        if state.filters and state.global_bypass:
            chips.append(("Filters bypassed", "All filters are bypassed: the plot shows unfiltered data", True))
        else:
            active = [f for f in state.filters if not f.bypassed]
            if active:
                names = [f.get_display_name() for f in active]
                text = " → ".join(n.split(" (")[0] for n in names) if len(active) <= 2 else f"{len(active)} filters"
                tooltip = "Filters, applied in order:\n" + "\n".join(f"{i}. {n}" for i, n in enumerate(names, 1))
                chips.append((text, tooltip, False))
        self._time_plot.set_chips(chips)

    def _plot_title(self, state, channels) -> tuple:
        """Header title and tooltip: 'Elec 10 · Ch 125 · elec1-10', or 'N electrodes' in overlay."""
        def describe(c):
            parts = [f"Ch {c + 1}"]
            if state.uses_electrode_numbers and c in state.electrode_numbers:
                parts.insert(0, f"Elec {state.electrode_numbers[c]}")
            if state.channel_labels and c < len(state.channel_labels) and state.channel_labels[c]:
                parts.append(state.channel_labels[c])
            return " · ".join(parts)

        if len(channels) == 1:
            return describe(channels[0]), ""
        noun = "electrodes" if state.uses_electrode_numbers else "channels"
        return f"{len(channels)} {noun}", "\n".join(describe(c) for c in channels)

    def _blanking_changed(self):
        """Blanking was applied or removed: re-process the loaded buffer."""
        self._blanking_panel.update_status()
        self._update_plot_chips()
        self._on_spike_inputs_changed()
        self._apply_filters()

    @Slot(str)
    def _on_apply_blanking(self, modality: str):
        state = self._app_state.get_current_state()
        if state is None:
            return
        events = [e for e in self._app_state.events if e.modality == modality]
        if not events:
            return
        settings = self._app_state.get_blanking_settings(modality)
        channels = state.neural_channels()

        if settings.mode != ADVANCED:
            self._app_state.blanking_results[modality] = basic_result(
                modality, state.stream_name, events, channels, settings
            )
            self._blanking_changed()
            return

        if all(e.number in settings.excluded_trials for e in events):
            QMessageBox.information(self, "No Trials", "Tick at least one trial to blank.")
            return

        worker = BlankingWorker(
            self._app_state.reader, state.stream_name, state.sample_rate, state.duration,
            channels, modality, events, self._app_state.events, copy.deepcopy(settings),
        )

        def done(result):
            self._app_state.blanking_results[modality] = result
            self._blanking_changed()
            self._toast.show_message(f"Blanking {modality}: {result.summary()}")

        self._run_job("blanking", worker,
                      f"Measuring baseline noise and artifact recovery ({modality})...", done,
                      "Failed to compute blanking")

    @Slot(str)
    def _on_remove_blanking(self, modality: str):
        self._app_state.blanking_results.pop(modality, None)
        self._blanking_changed()

    @Slot(object)
    def _on_event_edited(self, event):
        """An event's onset/offset was edited in the table."""
        self._analog_plot.refresh_events()
        self._blanking_panel.refresh()
        self._update_plot_chips()

    @Slot(object, str, float)
    def _on_event_dragged(self, event, edge: str, t: float):
        """An onset/offset line was dragged on the analog plot."""
        onset = t if edge == "onset" else event.onset
        offset = t if edge == "offset" else event.offset
        error = self._app_state.set_event_times(event, onset, offset)
        self._analog_panel.set_edit_message(error or "")
        self._analog_panel.refresh()
        self._analog_panel.show_selected_event(event)
        self._analog_plot.refresh_events()
        self._blanking_panel.refresh()
        self._update_plot_chips()

    @Slot()
    def _on_detect_events(self):
        """Detect events on every assigned analog channel (runs in the background)."""
        assignments = list(self._app_state.analog_assignments.values())
        if not assignments or self._app_state.block is None:
            return

        n_edited = self._app_state.edited_event_count()
        if n_edited:
            answer = QMessageBox.question(
                self,
                "Discard Edits?",
                f"Re-detecting will discard {n_edited} edited event"
                f"{'s' if n_edited != 1 else ''}. Continue?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        streams = {a.stream for a in assignments}
        stream_info = {
            s: (self._app_state.stream_states[s].sample_rate, self._app_state.stream_states[s].duration)
            for s in streams
        }
        labels = {
            (a.stream, a.channel): self._app_state.stream_states[a.stream].channel_labels[a.channel]
            if self._app_state.stream_states[a.stream].channel_labels
            else f"Ch {a.channel + 1}"
            for a in assignments
        }

        worker = EventDetectWorker(self._app_state.reader, assignments, stream_info, labels)

        def done(result):
            self._app_state.events = result["events"]
            self._app_state.blanking_results.clear()  # trial times changed
            self._on_spike_inputs_changed()
            self._analog_panel.set_edit_message("")
            self._blanking_panel.refresh()
            self._apply_filters()
            lines = [
                f"{label} → {modality}: {count} events (threshold {thr * 1e3:.3g} mV)"
                for label, modality, thr, count in result["summary"]
            ]
            self._analog_panel.set_summary("\n".join(lines))
            self._analog_panel.refresh()
            self._update_analog_events()
            self._toast.show_message(f"Detected {len(result['events'])} events")

        self._run_job("event detection", worker, "Reading analog channels and detecting events...", done,
                      "Failed to detect events")

    def _run_job(self, kind: str, worker, text: str, on_done, error_text: str, on_cancelled=None):
        """
        Run a long job behind a progress dialog. on_done(result) runs only if the job wasn't
        cancelled (Cancel, another recording opened, quitting) and the recording is the same;
        the error of a cancelled job is not shown. One job of each kind at a time.
        """
        if kind in self._jobs:  # cancelled, but still finishing its current chunk
            self._toast.show_message(f"The previous {kind} is still stopping; try again in a moment", WARNING)
            return
        gen = self._recording_gen
        progress = progress_dialog(text, self)
        self._jobs[kind] = worker
        # The callbacks live in connections on worker.signals: holding the worker itself there
        # would be a cycle the GC can't see (see WorkerManager.submit). _jobs keeps it alive.
        ref = weakref.ref(worker)

        def finish():  # each worker emits exactly one of result / error / cancelled
            self._jobs.pop(kind, None)
            progress.close()  # emits canceled: check is_cancelled() before this
            progress.deleteLater()

        def done(result):
            if ref().is_cancelled():  # Cancel pressed after the worker's last check
                cancelled()
                return
            current = gen == self._recording_gen
            finish()
            if current:
                on_done(result)

        def failed(err):
            show = not ref().is_cancelled() and gen == self._recording_gen
            finish()
            if show:
                QMessageBox.warning(self, f"{kind.title()} Error", f"{error_text}:\n\n{err}")

        def cancelled():
            finish()
            if on_cancelled is not None:
                on_cancelled()

        worker.signals.progress.connect(progress.setValue)
        progress.canceled.connect(worker.cancel)
        worker.signals.result.connect(done)
        worker.signals.error.connect(failed)
        worker.signals.cancelled.connect(cancelled)
        # Not submitted to _worker_manager: plot refreshes call cancel_all() on it
        self._worker_manager.thread_pool.start(worker)
        progress.show()

    def _stop_jobs(self):
        """Cancel every long job; results still on their way are then ignored."""
        self._recording_gen += 1
        for worker in self._jobs.values():
            worker.cancel()

    def _export_may_stop(self, action: str) -> bool:
        """Ask before an action stops a running export. False: keep exporting, don't act."""
        worker = self._jobs.get("export")
        if worker is None or worker.is_cancelled():
            return True
        answer = QMessageBox.question(
            self, "Export Running",
            f"An export is still running. Stop it, delete the partial file and {action}?",
        )
        return answer == QMessageBox.StandardButton.Yes

    def _on_save_session(self):
        """Save current session to file."""
        if self._app_state.block is None:
            QMessageBox.information(
                self, "No Data", "No recording is currently loaded."
            )
            return

        filepath, _ = QFileDialog.getSaveFileName(
            self,
            "Save Session",
            "",
            "JSON Files (*.json)",
        )

        if filepath:
            try:
                self._app_state.save_session(filepath)
                self._toast.show_message(f"Session saved to {filepath}")
            except Exception as e:
                QMessageBox.warning(
                    self, "Save Error", f"Failed to save session:\n\n{e}"
                )

    def _on_load_session(self):
        """Load session from file."""
        filepath, _ = QFileDialog.getOpenFileName(
            self,
            "Load Session",
            "",
            "JSON Files (*.json)",
        )

        if filepath and self._export_may_stop("load the session"):
            self._stop_jobs()
            try:
                self._app_state.load_session(filepath)

                # Update UI
                block = self._app_state.block
                if block:
                    self._stream_panel.set_block(block)

                    if self._app_state.current_stream:
                        self._stream_panel.select_stream(self._app_state.current_stream)
                        self._on_stream_selected(self._app_state.current_stream)

                self._reset_analog_view()

                # Restore probe geometry if it was saved with the session
                if self._app_state.probe is not None:
                    self._probe_map.set_probe(self._app_state.probe)
                    self._filter_panel.set_probe(self._app_state.probe)

                self._time_plot.set_empty(self._app_state.block is None)
                self._toast.show_message(f"Session loaded from {filepath}")
                if self._app_state.load_messages:
                    QMessageBox.information(
                        self, "Session Loaded", "\n\n".join(self._app_state.load_messages)
                    )

            except Exception as e:
                QMessageBox.warning(
                    self, "Load Error", f"Failed to load session:\n\n{e}"
                )

    def _on_recording_info(self):
        """Show metadata for the loaded recording, including Blackrock .ccf settings."""
        if self._app_state.block is None:
            QMessageBox.information(
                self, "No Data", "No recording is currently loaded."
            )
            return

        from .recording_info_dialog import RecordingInfoDialog

        RecordingInfoDialog(self._app_state.block, self).exec()

    def _on_about(self):
        """Show about dialog."""
        QMessageBox.about(
            self,
            "About Multi-Channel Neural Viewer",
            "<h3>Multi-Channel Neural Viewer</h3>"
            "<p>Version 1.0.0</p>"
            "<p>A cross-platform application for viewing and analyzing "
            "Tucker-Davis Technologies and Blackrock Neurotech neural recording data.</p>"
            "<p>Features:</p>"
            "<ul>"
            "<li>Multi-channel time series visualization</li>"
            "<li>Configurable filter chains</li>"
            "<li>FFT and PSD analysis</li>"
            "<li>Session save/load</li>"
            "</ul>",
        )

    def _on_load_probe(self):
        """Open the probe import dialog and apply geometry to the app."""
        from .probe_import_dialog import ProbeImportDialog

        dialog = ProbeImportDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        geometry = dialog.geometry()
        if geometry is None:
            return

        self._app_state.set_probe(geometry)
        self._probe_map.set_probe(geometry)
        self._filter_panel.set_probe(geometry)

        n = geometry.channel_count()
        n_shanks = len(geometry.get_shank_groups())
        self._toast.show_message(f"Probe loaded: {n} contacts across {n_shanks} shanks")

    def _on_export_binary(self):
        """Export the current stream as an int16 binary file (.i16 / .bin)."""
        from datetime import datetime

        state = self._app_state.get_current_state()
        if not state or self._app_state.block is None:
            QMessageBox.information(
                self, "No Data", "No recording is currently loaded."
            )
            return

        from .export_binary_dialog import ExportBinaryDialog

        blank_results = self._app_state.active_blanking(state.stream_name)
        active_filters = [] if state.global_bypass else [f for f in state.filters if not f.bypassed]
        dialog = ExportBinaryDialog(
            state,
            blanking=", ".join(r.modality for r in blank_results),
            filters=", ".join(f.get_display_name() for f in active_filters),
            stale_blanking=[r.modality for r in blank_results if r.stale],
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        opts = dialog.get_options()
        channels = opts["channels"]

        if not channels:
            QMessageBox.warning(self, "Export Error", "No channels selected.")
            return

        # Default save path: <block_folder>/<stream_name><ext>
        default_path = str(
            Path(self._app_state.block.path)
            / (state.stream_name + opts["extension"])
        )
        ext = opts["extension"]
        filepath, _ = QFileDialog.getSaveFileName(
            self,
            "Export Binary File",
            default_path,
            f"Binary Files (*{ext})",
        )
        if not filepath:
            return
        if not filepath.endswith(ext):
            filepath += ext

        fs = state.sample_rate
        if opts["range"] == "current_view":
            t1, t2 = state.get_view_range()
        else:
            t1, t2 = 0.0, state.duration
        n_stream = int(round(state.duration * fs))
        i_start = max(0, int(round(t1 * fs)))
        i_stop = min(n_stream, max(i_start + 1, int(round(t2 * fs))))
        # The summary describes the recording and blanking as they are now, at the start
        block_name = self._app_state.block.name
        blank_summary = self._blanking_summary_lines(state.stream_name) if blank_results else None

        worker = ExportWorker(
            reader=self._app_state.reader,
            stream=state.stream_name,
            channels=list(channels),
            i_start=i_start,
            i_stop=i_stop,
            fs=fs,
            num_channels=state.num_channels,
            filepath=filepath,
            scale=opts["scale"],
            layout=opts["layout"],
            blank_results=blank_results if opts["apply_blanking"] else [],
            filters=copy.deepcopy(active_filters) if opts["apply_filters"] else None,
        )

        def done(result):
            summary_path = str(Path(filepath).with_suffix(".txt"))
            self._write_export_summary(
                summary_path, block_name, state, channels, opts, (i_start, i_stop), result,
                blank_summary, active_filters, datetime.now(),
            )
            file_size_mb = os.path.getsize(filepath) / (1024 * 1024)
            clipped = (
                f"\n\n{result['n_clipped']:,} samples were clipped at the int16 limits; "
                "consider a smaller scale factor." if result["n_clipped"] else ""
            )
            self._toast.show_message(f"Exported: {filepath}")
            QMessageBox.information(
                self,
                "Export Complete",
                f"Binary file saved:\n{filepath}\n\n"
                f"Summary file:\n{summary_path}\n\n"
                f"File size: {file_size_mb:.1f} MB\n\n"
                f"Sample rate to use when importing: {fs:.4f} Hz{clipped}",
            )

        def cancelled():
            try:  # the worker deletes a partial file; this is for a Cancel after the last chunk
                os.remove(filepath)
            except OSError:
                pass
            self._toast.show_message("Export cancelled")

        self._run_job("export", worker, "Exporting...", done, "Failed to export data", cancelled)

    def _write_export_summary(
        self,
        filepath: str,
        block_name: str,
        state,
        channels: list,
        opts: dict,
        sample_range: tuple,
        result: dict,
        blank_summary: Optional[list],
        active_filters: list,
        export_time,
    ):
        """Write a companion .txt summary alongside the binary export.

        blank_summary: _blanking_summary_lines() when the export started, None without blanking.
        """
        fs = state.sample_rate
        i_start, i_stop = sample_range
        label = "current view" if opts["range"] == "current_view" else "full recording"
        time_range_str = f"{i_start / fs:.4f} – {i_stop / fs:.4f} s  ({label})"

        if opts["apply_blanking"] and blank_summary:
            blank_lines = ["Blanking      : applied (before filters)"] + blank_summary
        else:
            blank_lines = ["Blanking      : not applied"]
        if opts["apply_filters"] and active_filters:
            filter_lines = ["Filters       : applied, in order"] + [
                f"  {i}. {f.get_display_name()}" for i, f in enumerate(active_filters, 1)
            ]
        else:
            filter_lines = ["Filters       : not applied"]

        ch_1based = [c + 1 for c in channels]
        labels = [state.channel_labels[c] if state.channel_labels else "" for c in channels]
        electrodes = (
            [state.electrode_numbers[c] for c in channels] if state.electrode_numbers else None
        )
        layout_desc = (
            "Interlaced  (ch0_s0, ch1_s0, ..., ch0_s1, ch1_s1, ...)"
            if opts["layout"] == "interlaced"
            else "Channel-sequential  (all samples ch0, then ch1, ...)"
        )

        lines = [
            "Binary Export Summary",
            "=" * 40,
            f"Export Date   : {export_time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"Block         : {block_name}",
            f"Stream        : {state.stream_name}",
            f"Electrodes    : {electrodes if electrodes else 'n/a (labels have no electrode numbers)'}",
            f"Channels      : {ch_1based}  (1-based recording order)",
            f"Labels        : {labels}",
            f"File order    : as listed above (row i of the file = item i)",
            f"Time Range    : {time_range_str}",
            f"Sample Range  : {i_start} – {i_stop}  (stream samples, end exclusive)",
            f"Sample Rate   : {fs:.6f} Hz",
            f"Samples/Ch    : {result['n_samples']}",
            f"Num Channels  : {len(channels)}",
            f"Scale Factor  : {opts['scale']:g}  (volts multiplied by this before int16 conversion)",
            f"Clipped       : {result['n_clipped']} samples at the int16 limits",
            f"Data Type     : int16  (little-endian)",
            f"Sample Layout : {layout_desc}",
            f"File Extension: {opts['extension']}",
            "",
        ] + blank_lines + [""] + filter_lines

        with open(filepath, "w") as fh:
            fh.write("\n".join(lines) + "\n")

    # Drag and drop support
    def dragEnterEvent(self, event: QDragEnterEvent):
        """Handle drag enter."""
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if urls and urls[0].isLocalFile():
                path = urls[0].toLocalFile()
                if os.path.isdir(path):
                    event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        """Handle drop."""
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if os.path.isdir(path):
                self._load_block(path)

    def closeEvent(self, event):
        """Stop every background job, wait for them, then close the reader."""
        if not self._export_may_stop("quit"):
            event.ignore()
            return
        self._stop_jobs()
        self._load_seq += 1  # plot loads still landing are dropped
        self._analog_seq += 1
        self._worker_manager.cancel_all()
        self._analog_workers.cancel_all()
        self._worker_manager.thread_pool.clear()  # queued jobs never start
        # Jobs check for Cancel at least once per chunk (< 1 s), so this is normally short.
        # Closing the reader under a job still reading would only make it fail, never crash.
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self._worker_manager.wait_all(10000)
        finally:
            QApplication.restoreOverrideCursor()
        self._app_state.close()
        event.accept()
