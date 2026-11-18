"""Spike Detection tab: threshold k x sigma, polarity and refractory period."""
from typing import List, Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QFormLayout,
    QGroupBox,
    QLabel,
    QDoubleSpinBox,
    QComboBox,
    QCheckBox,
    QPushButton,
)

from models import StreamState
from dsp.spikes import NEGATIVE, POSITIVE, BOTH
from .theme import set_role, set_primary


class SpikePanel(QWidget):
    """Threshold settings; the threshold line is drawn on the Time Series plot."""

    settings_changed = Signal()
    threshold_settings_changed = Signal()  # k / polarity / refractory (affects detected spikes)
    detect_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state: Optional[StreamState] = None
        self._loading = False
        self._setup_ui()
        self.setEnabled(False)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)

        group = QGroupBox("Threshold")
        form = QFormLayout(group)
        self._k_spin = QDoubleSpinBox()
        self._k_spin.setRange(0.5, 50.0)
        self._k_spin.setDecimals(2)
        self._k_spin.setSingleStep(0.25)
        self._k_spin.setSuffix(" σ")
        self._k_spin.setToolTip(
            "Spikes are crossings of k × σ. σ = median(|x|) / 0.6745 of the filtered, blanked data "
            "in view (blanked samples excluded): a noise estimate that spikes barely affect, unlike "
            "the RMS (shown in the plot header), which spikes and artifacts inflate. "
            "Drag the dashed line on the plot to change k."
        )
        self._k_spin.valueChanged.connect(self._on_changed)
        form.addRow("Threshold (k × σ):", self._k_spin)

        self._polarity_combo = QComboBox()
        self._polarity_combo.addItem("Negative (below −kσ)", NEGATIVE)
        self._polarity_combo.addItem("Positive (above +kσ)", POSITIVE)
        self._polarity_combo.addItem("Both", BOTH)
        self._polarity_combo.currentIndexChanged.connect(self._on_changed)
        form.addRow("Polarity:", self._polarity_combo)

        self._refractory_spin = QDoubleSpinBox()
        self._refractory_spin.setRange(0.0, 50.0)
        self._refractory_spin.setDecimals(1)
        self._refractory_spin.setSingleStep(0.5)
        self._refractory_spin.setSuffix(" ms")
        self._refractory_spin.setToolTip("Crossings closer than this to the previous spike are not counted")
        self._refractory_spin.valueChanged.connect(self._on_changed)
        form.addRow("Refractory:", self._refractory_spin)
        layout.addWidget(group)

        self._everywhere_check = QCheckBox("Show threshold line on other tabs too")
        self._everywhere_check.toggled.connect(self._on_changed)
        layout.addWidget(self._everywhere_check)

        self._hint_label = QLabel("")
        self._hint_label.setWordWrap(True)
        set_role(self._hint_label, "warning")
        layout.addWidget(self._hint_label)

        self._detect_btn = QPushButton("Detect Spikes (whole recording)")
        self._detect_btn.setToolTip(
            "Freezes each electrode's σ from the current view, then finds threshold crossings "
            "across the whole recording (with the current blanking and filters)."
        )
        self._detect_btn.clicked.connect(self.detect_requested.emit)
        set_primary(self._detect_btn)
        layout.addWidget(self._detect_btn)

        self._summary_label = QLabel("")
        self._summary_label.setWordWrap(True)
        set_role(self._summary_label, "secondary")
        layout.addWidget(self._summary_label)

        layout.addStretch()

    # ---------------------------------------------------------------- state

    def set_state(self, state: StreamState):
        self._state = state
        self.setEnabled(True)
        s = state.spike_settings
        self._loading = True
        self._k_spin.setValue(s.k)
        self._polarity_combo.setCurrentIndex(max(0, self._polarity_combo.findData(s.polarity)))
        self._refractory_spin.setValue(s.refractory_ms)
        self._everywhere_check.setChecked(s.show_everywhere)
        self._loading = False

    def set_k(self, k: float):
        """Set k from the dragged threshold line."""
        self._k_spin.setValue(k)

    def set_hints(self, hints: List[str]):
        self._hint_label.setText("\n".join(hints))

    def set_summary(self, text: str, tooltip: str = "", stale: bool = False):
        """Result of the last whole-recording detection ('' = none)."""
        if stale and text:
            text += "\nOut of date (blanking, filters or threshold changed) — Detect again."
        self._summary_label.setText(text)
        self._summary_label.setToolTip(tooltip)
        set_role(self._summary_label, "error" if stale else "success")

    def _on_changed(self, *_):
        if self._loading or self._state is None:
            return
        s = self._state.spike_settings
        before = (s.k, s.polarity, s.refractory_ms)
        s.k = self._k_spin.value()
        s.polarity = self._polarity_combo.currentData()
        s.refractory_ms = self._refractory_spin.value()
        s.show_everywhere = self._everywhere_check.isChecked()
        self.settings_changed.emit()
        if (s.k, s.polarity, s.refractory_ms) != before:
            self.threshold_settings_changed.emit()
