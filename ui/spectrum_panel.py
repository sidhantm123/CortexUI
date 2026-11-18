"""Spectrum analysis panel widget."""
from typing import List, Optional

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QGroupBox,
    QPushButton,
    QComboBox,
    QSpinBox,
    QDoubleSpinBox,
    QCheckBox,
    QListWidget,
    QListWidgetItem,
)

from models import StreamState
from .theme import set_role


class SpectrumPanel(QWidget):
    """
    Panel for controlling FFT/PSD analysis.

    Features:
    - Method selection (FFT/PSD)
    - Time interval selection
    - Frequency range shown (applies to the last result at once, no re-analysis)
    - Channel selection for analysis
    - PSD parameters (nperseg, overlap)
    """

    # Signals
    analyze_requested = Signal(dict)  # Analysis parameters
    band_changed = Signal(float, float)  # frequency range to show (Hz)
    enable_selection_changed = Signal(bool)  # Enable/disable plot selection

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state: Optional[StreamState] = None
        self._setup_ui()

    def _setup_ui(self):
        """Set up the widget layout."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)

        layout.addWidget(QLabel("<b>Spectrum Analysis</b>"))

        # Method selection
        method_group = QGroupBox("Method")
        method_layout = QVBoxLayout(method_group)

        self._method_combo = QComboBox()
        self._method_combo.addItem("FFT Magnitude", "fft")
        self._method_combo.addItem("PSD (Welch)", "psd")
        self._method_combo.currentIndexChanged.connect(self._on_method_changed)
        method_layout.addWidget(self._method_combo)

        layout.addWidget(method_group)

        # Time interval
        interval_group = QGroupBox("Time Interval")
        interval_layout = QVBoxLayout(interval_group)

        # Enable selection on plot
        self._enable_selection_check = QCheckBox("Select on plot")
        self._enable_selection_check.stateChanged.connect(self._on_selection_toggled)
        interval_layout.addWidget(self._enable_selection_check)

        # Start time
        start_row = QHBoxLayout()
        start_row.addWidget(QLabel("Start (s):"))
        self._start_spin = QDoubleSpinBox()
        self._start_spin.setDecimals(3)
        self._start_spin.setMinimum(0)
        self._start_spin.setMaximum(9999999)
        start_row.addWidget(self._start_spin)
        interval_layout.addLayout(start_row)

        # End time
        end_row = QHBoxLayout()
        end_row.addWidget(QLabel("End (s):"))
        self._end_spin = QDoubleSpinBox()
        self._end_spin.setDecimals(3)
        self._end_spin.setMinimum(0)
        self._end_spin.setMaximum(9999999)
        end_row.addWidget(self._end_spin)
        interval_layout.addLayout(end_row)

        layout.addWidget(interval_group)

        # Frequency range shown in the Spectrum plot
        band_group = QGroupBox("Frequency Range")
        band_layout = QVBoxLayout(band_group)
        self._fmin_spin = QDoubleSpinBox()
        self._fmax_spin = QDoubleSpinBox()
        for label, spin in (("Min (Hz):", self._fmin_spin), ("Max (Hz):", self._fmax_spin)):
            row = QHBoxLayout()
            row.addWidget(QLabel(label))
            spin.setDecimals(1)
            spin.setRange(0.0, 15000.0)
            spin.setKeyboardTracking(False)  # one update per typed number
            row.addWidget(spin)
            band_layout.addLayout(row)
        self._fmax_spin.setValue(15000.0)
        self._band_note = QLabel("")
        set_role(self._band_note, "error")
        self._band_note.setVisible(False)
        band_layout.addWidget(self._band_note)
        layout.addWidget(band_group)

        # Channel selection
        channel_group = QGroupBox("Channels")
        channel_layout = QVBoxLayout(channel_group)

        self._use_all_check = QCheckBox("Use all displayed channels")
        self._use_all_check.setChecked(True)
        self._use_all_check.stateChanged.connect(self._on_use_all_changed)
        channel_layout.addWidget(self._use_all_check)

        self._channel_list = QListWidget()
        self._channel_list.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
        self._channel_list.setMaximumHeight(80)
        self._channel_list.setEnabled(False)
        channel_layout.addWidget(self._channel_list)

        layout.addWidget(channel_group)

        # PSD parameters
        self._psd_group = QGroupBox("PSD Parameters")
        psd_layout = QVBoxLayout(self._psd_group)

        nperseg_row = QHBoxLayout()
        nperseg_row.addWidget(QLabel("Segment size:"))
        self._nperseg_spin = QSpinBox()
        self._nperseg_spin.setRange(64, 16384)
        self._nperseg_spin.setValue(1024)
        self._nperseg_spin.setSingleStep(64)
        nperseg_row.addWidget(self._nperseg_spin)
        psd_layout.addLayout(nperseg_row)

        overlap_row = QHBoxLayout()
        overlap_row.addWidget(QLabel("Overlap:"))
        self._overlap_spin = QDoubleSpinBox()
        self._overlap_spin.setRange(0, 0.99)
        self._overlap_spin.setValue(0.5)
        self._overlap_spin.setSingleStep(0.1)
        overlap_row.addWidget(self._overlap_spin)
        psd_layout.addLayout(overlap_row)

        layout.addWidget(self._psd_group)

        # Analyze button
        self._analyze_btn = QPushButton("Analyze")
        self._analyze_btn.clicked.connect(self._on_analyze)
        self._fmin_spin.valueChanged.connect(self._on_band_changed)
        self._fmax_spin.valueChanged.connect(self._on_band_changed)
        layout.addWidget(self._analyze_btn)

        layout.addStretch()

        # Initial visibility
        self._on_method_changed(0)

    def set_state(self, state: StreamState):
        """Set the stream state."""
        self._state = state

        # Update time range limits
        self._start_spin.setMaximum(state.duration)
        self._end_spin.setMaximum(state.duration)

        # Set default interval if not set
        if state.fft_start is not None:
            self._start_spin.setValue(state.fft_start)
        else:
            self._start_spin.setValue(state.view_start)

        if state.fft_end is not None:
            self._end_spin.setValue(state.fft_end)
        else:
            self._end_spin.setValue(state.view_end)

        # Frequency range: the full spectrum unless one was set
        nyquist = state.sample_rate / 2
        for spin in (self._fmin_spin, self._fmax_spin):
            spin.blockSignals(True)
            spin.setMaximum(nyquist)
        self._fmin_spin.setValue(state.fft_fmin if state.fft_fmin is not None else 0.0)
        self._fmax_spin.setValue(state.fft_fmax if state.fft_fmax is not None else nyquist)
        for spin in (self._fmin_spin, self._fmax_spin):
            spin.blockSignals(False)
        self._on_band_changed()

        # Update PSD params
        self._nperseg_spin.setValue(state.psd_nperseg)
        self._overlap_spin.setValue(state.psd_overlap)

        # Update channel list
        self._channel_list.clear()
        for i in state.ordered_neural_channels():
            item = QListWidgetItem(state.channel_label(i, "Channel"))
            item.setData(Qt.ItemDataRole.UserRole, i)
            self._channel_list.addItem(item)

        # Select channels
        if state.fft_channels:
            for i in range(self._channel_list.count()):
                item = self._channel_list.item(i)
                ch = item.data(Qt.ItemDataRole.UserRole)
                item.setSelected(ch in state.fft_channels)

    def _on_method_changed(self, index: int):
        """Handle method selection change."""
        method = self._method_combo.currentData()
        self._psd_group.setVisible(method == "psd")

    def _on_selection_toggled(self, state: int):
        """Handle selection checkbox toggle."""
        enabled = state == Qt.CheckState.Checked.value
        self.enable_selection_changed.emit(enabled)

    def _on_use_all_changed(self, state: int):
        """Handle use all channels checkbox."""
        use_all = state == Qt.CheckState.Checked.value
        self._channel_list.setEnabled(not use_all)

    def band(self):
        """(min Hz, max Hz) to show, or None while min is not below max."""
        fmin, fmax = self._fmin_spin.value(), self._fmax_spin.value()
        return (fmin, fmax) if fmin < fmax else None

    def _on_band_changed(self, *_):
        band = self.band()
        self._band_note.setText("" if band else "Min must be below Max")
        self._band_note.setVisible(band is None)
        self._analyze_btn.setEnabled(band is not None)
        if band is None:
            return
        if self._state is not None:
            nyquist = self._state.sample_rate / 2
            self._state.fft_fmin = band[0] if band[0] > 0 else None
            self._state.fft_fmax = band[1] if band[1] < nyquist else None
        self.band_changed.emit(*band)

    def update_interval(self, start: float, end: float):
        """Update interval from plot selection."""
        self._start_spin.setValue(start)
        self._end_spin.setValue(end)

    def _on_analyze(self):
        """Handle analyze button click."""
        if not self._state:
            return

        # Get selected channels
        if self._use_all_check.isChecked():
            channels = self._state.get_active_channels()
        else:
            channels = []
            for item in self._channel_list.selectedItems():
                channels.append(item.data(Qt.ItemDataRole.UserRole))
            if not channels:
                channels = self._state.get_active_channels()

        # Save settings to state
        self._state.fft_start = self._start_spin.value()
        self._state.fft_end = self._end_spin.value()
        self._state.fft_channels = channels.copy()
        self._state.psd_nperseg = self._nperseg_spin.value()
        self._state.psd_overlap = self._overlap_spin.value()

        # Emit analysis request
        params = {
            "method": self._method_combo.currentData(),
            "start": self._start_spin.value(),
            "end": self._end_spin.value(),
            "channels": channels,
            "nperseg": self._nperseg_spin.value(),
            "overlap": self._overlap_spin.value(),
            "band": self.band(),
        }
        self.analyze_requested.emit(params)

    def clear(self):
        """Clear the panel."""
        self._state = None
        self._channel_list.clear()
        self._start_spin.setValue(0)
        self._end_spin.setValue(10)
