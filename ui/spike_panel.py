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


class SpikePanel(QWidget):
    """Threshold settings; the threshold line and markers are drawn on the Time Series plot."""

    settings_changed = Signal()

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
            "Spikes are crossings of k × σ, with σ = median(|x|) / 0.6745 of the filtered, "
            "blanked data in view (blanked samples excluded). Drag the line on the plot to change k."
        )
        self._k_spin.valueChanged.connect(self._on_changed)
        form.addRow("Vrms threshold:", self._k_spin)

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

        self._everywhere_check = QCheckBox("Show threshold and markers on other tabs too")
        self._everywhere_check.toggled.connect(self._on_changed)
        layout.addWidget(self._everywhere_check)

        self._info_label = QLabel("")
        self._info_label.setWordWrap(True)
        self._info_label.setStyleSheet("font-size: 11px;")
        layout.addWidget(self._info_label)

        self._hint_label = QLabel("")
        self._hint_label.setWordWrap(True)
        self._hint_label.setStyleSheet("color: #b35900; font-size: 11px;")
        layout.addWidget(self._hint_label)

        self._detect_btn = QPushButton("Detect Spikes (whole recording)")
        self._detect_btn.setEnabled(False)
        self._detect_btn.setToolTip("Coming in the next step")
        layout.addWidget(self._detect_btn)

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

    def set_info(self, text: str):
        self._info_label.setText(text)

    def set_hints(self, hints: List[str]):
        self._hint_label.setText("\n".join(hints))

    def _on_changed(self, *_):
        if self._loading or self._state is None:
            return
        s = self._state.spike_settings
        s.k = self._k_spin.value()
        s.polarity = self._polarity_combo.currentData()
        s.refractory_ms = self._refractory_spin.value()
        s.show_everywhere = self._everywhere_check.isChecked()
        self.settings_changed.emit()
