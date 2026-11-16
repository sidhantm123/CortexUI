"""Main application window."""
import copy
import os
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Slot, QTimer, QSettings
from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QSplitter,
    QTabWidget,
    QStatusBar,
    QMenuBar,
    QMenu,
    QFileDialog,
    QMessageBox,
    QLabel,
    QApplication,
    QDialog,
)
from PySide6.QtGui import QAction, QActionGroup, QDragEnterEvent, QDropEvent

import numpy as np

from core.app_state import AppState
from core.workers import (
    WorkerManager,
    DataLoadWorker,
    FilterWorker,
    SpectralWorker,
    EventDetectWorker,
    BlankingWorker,
    ExportWorker,
    SpikeDetectWorker,
)
from models import StreamState, ChannelMode, NUMBER_BY_ELECTRODE, NUMBER_BY_CHANNEL
from dsp import apply_filter_chain, decimate_for_display
from dsp.blanking import apply_blanking, basic_result, blank_spans, blank_mask
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

        # Current data cache (raw + filtered buffers, wider than the visible view)
        self._raw_data: Optional[np.ndarray] = None
        self._raw_time: Optional[np.ndarray] = None
        self._raw_data_all: Optional[np.ndarray] = None  # All channels for CAR/CMR
        self._filtered_data: Optional[np.ndarray] = None
        self._blank_mask: Optional[np.ndarray] = None  # blanked samples of _filtered_data (None = none)

        # Buffer tracking — describes what time range / channels are in _filtered_data
        self._buffer_channels: list = []
        self._buffer_start: float = -1.0
        self._buffer_end: float = -1.0

        # Async load context — carried from _refresh_plot to _on_data_loaded_async
        self._load_seq: int = 0          # incremented on each new request to discard stale results
        self._pending_channels: list = []
        self._pending_buffer_start: float = 0.0
        self._pending_buffer_end: float = 0.0
        self._pending_needs_all: bool = False

        # Fast-path tracking
        self._last_plot_channels: list = []
        self._last_scatter_mode: bool = False

        # Pending operations
        self._pending_refresh = False

        # Analog viewer buffer (one analog channel, wider than its visible view)
        self._analog_key = None  # (stream, channel) in the buffer
        self._analog_data: Optional[np.ndarray] = None
        self._analog_time: Optional[np.ndarray] = None
        self._analog_buf = (-1.0, -1.0)
        self._analog_seq: int = 0
        self._pending_analog_refresh = False
        self._analog_workers = WorkerManager()
        self._detect_worker: Optional[EventDetectWorker] = None
        self._blank_worker: Optional[BlankingWorker] = None
        self._export_worker: Optional[ExportWorker] = None
        self._spike_worker: Optional[SpikeDetectWorker] = None

        self._setup_ui()
        self._setup_menu()
        self._setup_statusbar()
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

        # Left panel: Stream list
        self._stream_panel = StreamPanel()
        self._stream_panel.setMaximumWidth(250)
        self._stream_panel.setMinimumWidth(150)
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

        # Right panel: Controls
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)

        # Right tabs for different control groups
        self._control_tabs = QTabWidget()
        self._control_tabs.setUsesScrollButtons(True)

        # Channel control tab
        self._control_panel = ControlPanel()
        self._control_tabs.addTab(self._control_panel, "Channels")

        # Blanking tab
        self._blanking_panel = BlankingPanel(self._app_state)
        self._control_tabs.addTab(self._blanking_panel, "Blanking")

        # Filter tab
        self._filter_panel = FilterPanel()
        self._control_tabs.addTab(self._filter_panel, "Filters")

        # Spike detection tab
        self._spike_panel = SpikePanel()
        self._spike_tab_index = self._control_tabs.addTab(self._spike_panel, "Spike Detection")

        # Spectrum analysis tab
        self._spectrum_panel = SpectrumPanel()
        self._control_tabs.addTab(self._spectrum_panel, "Analysis")

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
        self._main_splitter.setSizes([200, 700, 340])

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

    def _setup_statusbar(self):
        """Set up the status bar."""
        self._statusbar = QStatusBar()
        self.setStatusBar(self._statusbar)

        self._status_label = QLabel("Ready - Open a TDT or Blackrock recording folder to begin")
        self._statusbar.addWidget(self._status_label, 1)

        self._blanking_indicator = QLabel("")
        self._statusbar.addPermanentWidget(self._blanking_indicator)

        self._filter_indicator = QLabel("")
        self._statusbar.addPermanentWidget(self._filter_indicator)

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
        self._raster_plot.plot_item().setXLink(self._time_plot.plot_item())
        self._raster_plot.channel_clicked.connect(self._on_raster_channel_clicked)
        self._sigma_selected: Optional[float] = None  # σ of the selected channel in view

        # Blanking
        self._blanking_panel.apply_requested.connect(self._on_apply_blanking)
        self._blanking_panel.remove_requested.connect(self._on_remove_blanking)

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
                first_stream = list(block.streams.keys())[0]
                self._on_stream_selected(first_stream)

            self._reset_analog_view()
            self._update_status()
            self._update_blanking_indicator()

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

            # Load and display data
            self._refresh_plot()

            self._update_status()
            self._update_blanking_indicator()

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
    def _on_view_range_requested(self, start: float, end: float):
        """Handle view range change request."""
        self._time_plot.set_view_range(start, end)
        self._refresh_plot()

    @Slot(object, object)
    def _on_plot_view_changed(self, start: float, end: float):
        """Handle plot view range change (from user interaction)."""
        state = self._app_state.get_current_state()
        if state is None:
            return

        state.set_view_range(start, end)
        self._control_panel.update_view_range(start, end)

        channels = state.get_active_channels()

        # Fast path: filtered buffer already covers this view — render immediately,
        # no debounce, no disk I/O, no re-filtering (~5 ms for slice + decimate).
        if (
            self._filtered_data is not None
            and self._raw_time is not None
            and channels == self._buffer_channels
            and start >= self._buffer_start
            and end <= self._buffer_end
        ):
            self._rerender_from_buffer(start, end, state)
            return

        # Buffer miss — debounce so we don't hammer disk while user is still dragging.
        if not self._pending_refresh:
            self._pending_refresh = True
            QTimer.singleShot(150, self._delayed_refresh)

    def _delayed_refresh(self):
        """Delayed refresh after view change."""
        self._pending_refresh = False
        self._refresh_plot()

    @Slot()
    def _on_filters_changed(self):
        """Handle filter chain change."""
        self._on_spike_inputs_changed()
        state = self._app_state.get_current_state()
        needs_all = (
            state is not None
            and not state.global_bypass
            and any(
                f.filter_type in (FilterType.CAR, FilterType.CMR) and not f.bypassed
                for f in state.filters
            )
        )
        # If CAR/CMR is now active but we only have the displayed-channel buffer
        # (raw_data_all is None), we need a full reload to get all channels.
        if needs_all and self._raw_data_all is None:
            self._refresh_plot()
        else:
            self._apply_filters()
        self._update_status()

    @Slot(bool)
    def _on_scatter_mode_changed(self, enabled: bool):
        """Handle scatter mode toggle."""
        state = self._app_state.get_current_state()
        if state:
            state.scatter_mode = enabled
        # Force full re-render since plot items change
        self._last_scatter_mode = not enabled  # Invalidate fast path
        self._apply_filters()

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

    def _refresh_plot(self):
        """Refresh the time series plot, using the in-memory buffer when possible."""
        state = self._app_state.get_current_state()
        if not state or self._app_state.block is None:
            return

        start, end = state.get_view_range()
        channels = state.get_active_channels()

        # ── Buffer fast path ────────────────────────────────────────────────
        # The view moved within the already-loaded + filtered buffer.
        # Just re-slice and re-decimate — no disk I/O, no re-filtering (~5 ms).
        # Filters are re-applied by _on_filters_changed when the chain changes.
        if (
            self._filtered_data is not None
            and self._raw_time is not None
            and channels == self._buffer_channels
            and start >= self._buffer_start
            and end <= self._buffer_end
        ):
            self._rerender_from_buffer(start, end, state)
            return

        # ── Buffer miss: async disk load ────────────────────────────────────
        # Load 1× the view duration as margin on each side so the next few
        # pan/zoom steps will hit the buffer without touching the disk.
        view_dur = max(end - start, 1.0)
        buf_start = max(0.0, start - view_dur)
        buf_end = min(state.duration, end + view_dur)

        needs_all = any(
            f.filter_type in (FilterType.CAR, FilterType.CMR) and not f.bypassed
            for f in state.filters
        ) and not state.global_bypass

        # When CAR/CMR is active we must load all channels for referencing
        load_channels = list(range(state.num_channels)) if needs_all else channels

        # Cancel any in-flight load; bump sequence so stale results are dropped
        self._worker_manager.cancel_all()
        self._load_seq += 1
        seq = self._load_seq

        # Store context for _on_data_loaded_async
        self._pending_channels = channels
        self._pending_buffer_start = buf_start
        self._pending_buffer_end = buf_end
        self._pending_needs_all = needs_all

        worker = DataLoadWorker(
            self._app_state.reader,
            state.stream_name,
            load_channels,
            buf_start,
            buf_end,
        )
        worker.signals.result.connect(
            lambda result, s=seq: self._on_data_loaded_async(result, s)
        )
        worker.signals.error.connect(
            lambda err: self._status_label.setText(f"Error loading data: {err}")
        )
        self._worker_manager.submit(worker)

    @Slot(object)
    def _on_data_loaded_async(self, result: dict, seq: int):
        """Receive async load result; discard stale results from cancelled workers."""
        if seq != self._load_seq:
            return  # A newer request was already issued — ignore this one

        state = self._app_state.get_current_state()
        if state is None:
            return

        data = result["data"]
        time_axis = result["time"]
        channels = self._pending_channels

        if self._pending_needs_all:
            # data contains ALL channels; split into display subset and full ref
            self._raw_data_all = data
            self._raw_data = data[channels, :] if len(channels) < data.shape[0] else data
        else:
            self._raw_data = data
            self._raw_data_all = None

        self._raw_time = time_axis

        # Record the buffer extent so future view changes can use the fast path
        self._buffer_start = self._pending_buffer_start
        self._buffer_end = self._pending_buffer_end
        self._buffer_channels = channels

        # Apply filters and render
        self._apply_filters()

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

        # Vrms of exactly what is visible, blanked samples excluded
        channels = state.get_active_channels()
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
            self._last_plot_channels = channels
            self._last_scatter_mode = scatter

    def _apply_filters(self):
        """Apply filter chain to the loaded buffer, then render the current view."""
        state = self._app_state.get_current_state()
        if state is None or self._raw_data is None:
            return

        channels = state.get_active_channels()

        # Blank stimulation artifacts on copies (the raw buffer is kept, so removing
        # blanking needs no reload), then apply the filter chain (always returns 3-tuple)
        raw = self._blank(self._raw_data, self._raw_time, channels, state.stream_name)
        raw_all = None
        if self._raw_data_all is not None:
            raw_all = self._blank(
                self._raw_data_all, self._raw_time, list(range(state.num_channels)), state.stream_name
            )
        data, warnings, vrms_dict = apply_filter_chain(
            raw,
            state.filters,
            state.sample_rate,
            state.global_bypass,
            all_channel_data=raw_all,
            channel_indices=channels,
        )
        if warnings:
            self._status_label.setText(f"Filter warning: {warnings[0]}")

        # Cache the full-resolution filtered buffer
        self._filtered_data = data

        # Blanked samples, so Vrms can leave them out
        results = self._app_state.active_blanking(state.stream_name)
        self._blank_mask = blank_mask(self._raw_time, channels, results) if results else None

        # Render only the visible view window (slice + decimate from buffer)
        view_start, view_end = state.get_view_range()
        self._rerender_from_buffer(view_start, view_end, state)

        # Update filter indicator
        if state.filters:
            if state.global_bypass:
                self._filter_indicator.setText("Filters: BYPASSED")
            else:
                active = sum(1 for f in state.filters if not f.bypassed)
                self._filter_indicator.setText(f"Filters: {active} active")
        else:
            self._filter_indicator.setText("")

    # ------------------------------------------------------------------ analog viewer

    def _reset_analog_view(self):
        """After a recording or session loads: rebuild the analog panel and plot."""
        self._analog_key = None
        self._analog_data = None
        self._analog_time = None
        self._analog_panel.refresh()
        self._analog_panel.set_summary("")
        self._blanking_panel.refresh()
        self._update_blanking_indicator()
        self._update_spike_view()
        self._analog_plot.highlight_event(None)
        self._update_analog_events()
        state = self._app_state.get_current_state()
        if state is not None:
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
            self._analog_plot.set_x_link(self._time_plot.plot_item())
            state = self._app_state.get_current_state()
            if state is not None:
                self._analog_plot.set_view_range(*state.get_view_range())
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
        self._update_analog_events()
        self._refresh_analog()

    @Slot(object, object)
    def _on_analog_view_changed(self, start: float, end: float):
        """Analog plot moved (linked to the main plot, or panned on its own)."""
        if self._analog_buffer_covers(self._analog_panel.selected_channel(), start, end):
            self._render_analog(start, end)
        elif not self._pending_analog_refresh:
            self._pending_analog_refresh = True
            QTimer.singleShot(150, self._delayed_analog_refresh)

    def _delayed_analog_refresh(self):
        self._pending_analog_refresh = False
        self._refresh_analog()

    def _analog_buffer_covers(self, key, start: float, end: float) -> bool:
        return (
            key is not None
            and key == self._analog_key
            and self._analog_data is not None
            and start >= self._analog_buf[0]
            and end <= self._analog_buf[1]
        )

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
            return

        stream, channel = key
        duration = self._app_state.stream_states[stream].duration
        view_dur = max(end - start, 1.0)
        buf_start = max(0.0, start - view_dur)
        buf_end = min(duration, end + view_dur)
        if buf_end <= buf_start:
            self._analog_plot.set_data(None, None)
            return

        self._analog_workers.cancel_all()
        self._analog_seq += 1
        seq = self._analog_seq
        worker = DataLoadWorker(
            self._app_state.reader, stream, [channel], buf_start, buf_end
        )
        worker.signals.result.connect(
            lambda result, s=seq, k=key, b=(buf_start, buf_end): self._on_analog_loaded(result, s, k, b)
        )
        worker.signals.error.connect(
            lambda err: self._status_label.setText(f"Error loading analog data: {err}")
        )
        self._analog_workers.submit(worker)

    def _on_analog_loaded(self, result: dict, seq: int, key, buf):
        if seq != self._analog_seq:
            return
        self._analog_key = key
        self._analog_data = np.ravel(result["data"])
        self._analog_time = result["time"]
        self._analog_buf = buf
        self._render_analog(*self._analog_plot.get_view_range())

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

        progress = progress_dialog("Detecting spikes across the recording...", self)

        worker = SpikeDetectWorker(
            self._app_state.reader, state.stream_name, state.ordered_neural_channels(),
            state.sample_rate, state.num_channels, int(round(state.duration * state.sample_rate)),
            (t0, t1), copy.deepcopy(state.spike_settings), blank_results,
            copy.deepcopy(active_filters), processing,
        )
        worker.signals.progress.connect(progress.setValue)
        progress.canceled.connect(worker.cancel)

        def done(result):
            progress.close()
            self._app_state.spike_results[state.stream_name] = result
            self._update_spike_view()
            self._plot_tabs.setCurrentIndex(self._raster_tab_index)
            self._status_label.setText(f"Detected {result.total():,} spikes")

        def failed(err):
            progress.close()
            QMessageBox.warning(self, "Spike Detection Error", f"Failed to detect spikes:\n\n{err}")

        worker.signals.result.connect(done)
        worker.signals.error.connect(failed)
        worker.signals.cancelled.connect(progress.close)
        # Not submitted to _worker_manager: plot refreshes call cancel_all() on it
        self._spike_worker = worker
        self._worker_manager.thread_pool.start(worker)
        progress.show()

    @Slot(int)
    def _on_raster_channel_clicked(self, channel: int):
        """Open the clicked electrode in Time Series."""
        state = self._app_state.get_current_state()
        if state is None:
            return
        state.channel_mode = ChannelMode.SINGLE
        state.selected_channel = channel
        self._control_panel.set_state(state)
        self._plot_tabs.setCurrentWidget(self._time_plot)
        self._refresh_plot()

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

    def _update_blanking_indicator(self):
        state = self._app_state.get_current_state()
        results = self._app_state.active_blanking(state.stream_name) if state else []
        names = ", ".join(r.modality for r in results)
        self._blanking_indicator.setText(f"Blanking: {names}" if names else "")

    def _blanking_changed(self):
        """Blanking was applied or removed: re-process the loaded buffer."""
        self._blanking_panel.update_status()
        self._update_blanking_indicator()
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

        progress = progress_dialog(f"Measuring baseline noise and artifact recovery ({modality})...", self)

        worker = BlankingWorker(
            self._app_state.reader, state.stream_name, state.sample_rate, state.duration,
            channels, modality, events, self._app_state.events, copy.deepcopy(settings),
        )
        worker.signals.progress.connect(progress.setValue)
        progress.canceled.connect(worker.cancel)

        def done(result):
            progress.close()
            self._app_state.blanking_results[modality] = result
            self._blanking_changed()
            self._status_label.setText(f"Blanking {modality}: {result.summary()}")

        def failed(err):
            progress.close()
            QMessageBox.warning(self, "Blanking Error", f"Failed to compute blanking:\n\n{err}")

        worker.signals.result.connect(done)
        worker.signals.error.connect(failed)
        worker.signals.cancelled.connect(progress.close)
        # Not submitted to _worker_manager: plot refreshes call cancel_all() on it
        self._blank_worker = worker
        self._worker_manager.thread_pool.start(worker)
        progress.show()

    @Slot(str)
    def _on_remove_blanking(self, modality: str):
        self._app_state.blanking_results.pop(modality, None)
        self._blanking_changed()

    @Slot(object)
    def _on_event_edited(self, event):
        """An event's onset/offset was edited in the table."""
        self._analog_plot.refresh_events()
        self._blanking_panel.refresh()

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

        progress = progress_dialog("Reading analog channels and detecting events...", self)

        worker = EventDetectWorker(self._app_state.reader, assignments, stream_info, labels)
        worker.signals.progress.connect(progress.setValue)
        progress.canceled.connect(worker.cancel)

        def done(result):
            progress.close()
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
            self._status_label.setText(f"Detected {len(result['events'])} events")

        def failed(err):
            progress.close()
            QMessageBox.warning(self, "Event Detection Error", f"Failed to detect events:\n\n{err}")

        worker.signals.result.connect(done)
        worker.signals.error.connect(failed)
        worker.signals.cancelled.connect(progress.close)
        # Not submitted to _worker_manager: plot refreshes call cancel_all() on it
        self._detect_worker = worker
        self._worker_manager.thread_pool.start(worker)
        progress.show()

    def _update_status(self):
        """Update status bar."""
        state = self._app_state.get_current_state()
        block = self._app_state.block

        if block and state:
            duration_str = f"{state.duration:.1f}s"
            if state.duration >= 60:
                mins = int(state.duration // 60)
                secs = state.duration % 60
                duration_str = f"{mins}m {secs:.1f}s"

            self._status_label.setText(
                f"Block: {block.name} | "
                f"Stream: {state.stream_name} | "
                f"{state.num_channels} ch @ {state.sample_rate:.0f} Hz | "
                f"Duration: {duration_str}"
            )
        else:
            self._status_label.setText("Ready - Open a TDT or Blackrock recording folder to begin")

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
                self._status_label.setText(f"Session saved to {filepath}")
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

        if filepath:
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

                self._status_label.setText(f"Session loaded from {filepath}")
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
        self._status_label.setText(
            f"Probe loaded: {n} contacts across {n_shanks} shanks"
        )

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

        progress = progress_dialog("Exporting...", self)

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
        worker.signals.progress.connect(progress.setValue)
        progress.canceled.connect(worker.cancel)

        def done(result):
            progress.close()
            summary_path = str(Path(filepath).with_suffix(".txt"))
            self._write_export_summary(
                summary_path, state, channels, opts, (i_start, i_stop), result,
                blank_results, active_filters, datetime.now(),
            )
            file_size_mb = os.path.getsize(filepath) / (1024 * 1024)
            clipped = (
                f"\n\n{result['n_clipped']:,} samples were clipped at the int16 limits; "
                "consider a smaller scale factor." if result["n_clipped"] else ""
            )
            self._status_label.setText(f"Exported: {filepath}")
            QMessageBox.information(
                self,
                "Export Complete",
                f"Binary file saved:\n{filepath}\n\n"
                f"Summary file:\n{summary_path}\n\n"
                f"File size: {file_size_mb:.1f} MB\n\n"
                f"Sample rate to use when importing: {fs:.4f} Hz{clipped}",
            )

        def failed(err):
            progress.close()
            QMessageBox.warning(self, "Export Error", f"Failed to export data:\n\n{err}")

        def cancelled():
            progress.close()
            try:
                os.remove(filepath)
            except OSError:
                pass
            self._status_label.setText("Export cancelled")

        worker.signals.result.connect(done)
        worker.signals.error.connect(failed)
        worker.signals.cancelled.connect(cancelled)
        # Not submitted to _worker_manager: plot refreshes call cancel_all() on it
        self._export_worker = worker
        self._worker_manager.thread_pool.start(worker)
        progress.show()

    def _write_export_summary(
        self,
        filepath: str,
        state,
        channels: list,
        opts: dict,
        sample_range: tuple,
        result: dict,
        blank_results: list,
        active_filters: list,
        export_time,
    ):
        """Write a companion .txt summary alongside the binary export."""
        fs = state.sample_rate
        i_start, i_stop = sample_range
        label = "current view" if opts["range"] == "current_view" else "full recording"
        time_range_str = f"{i_start / fs:.4f} – {i_stop / fs:.4f} s  ({label})"

        if opts["apply_blanking"] and blank_results:
            blank_lines = ["Blanking      : applied (before filters)"] + self._blanking_summary_lines(state.stream_name)
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
            f"Block         : {self._app_state.block.name}",
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
        """Handle window close."""
        # Cancel any running workers
        self._worker_manager.cancel_all()
        self._analog_workers.cancel_all()
        for worker in (self._detect_worker, self._blank_worker, self._export_worker, self._spike_worker):
            if worker is not None:
                worker.cancel()
        self._worker_manager.wait_all(3000)
        self._app_state.close()
        event.accept()
