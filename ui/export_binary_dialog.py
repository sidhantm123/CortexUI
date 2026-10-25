"""Dialog for configuring binary (.i16 / .bin) export options."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QGroupBox,
    QRadioButton,
    QLabel,
    QDoubleSpinBox,
    QComboBox,
    QListWidget,
    QListWidgetItem,
    QDialogButtonBox,
    QButtonGroup,
    QCheckBox,
)

from models import StreamState


class ExportBinaryDialog(QDialog):
    """Dialog for configuring int16 binary export (.i16 / .bin)."""

    def __init__(self, state: StreamState, blanking: str = "", filters: str = "", parent=None):
        """
        Args:
            blanking: description of the applied blanking ("" = none applied).
            filters: description of the active filter chain ("" = none / all bypassed).
        """
        super().__init__(parent)
        self._state = state
        self._blanking_desc = blanking
        self._filters_desc = filters
        self.setWindowTitle("Export Binary File")
        self.setMinimumWidth(400)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # ── Export range ─────────────────────────────────────────────────────
        range_group = QGroupBox("Export Range")
        range_layout = QVBoxLayout(range_group)
        self._range_btn_grp = QButtonGroup(self)

        s, e = self._state.view_start, self._state.view_end
        self._range_view = QRadioButton(
            f"Current view  ({s:.3f} – {e:.3f} s)"
        )
        self._range_full = QRadioButton(
            f"Full recording  (0 – {self._state.duration:.3f} s)"
        )
        self._range_view.setChecked(True)
        self._range_btn_grp.addButton(self._range_view)
        self._range_btn_grp.addButton(self._range_full)
        range_layout.addWidget(self._range_view)
        range_layout.addWidget(self._range_full)
        layout.addWidget(range_group)

        # ── Processing stage ─────────────────────────────────────────────────
        proc_group = QGroupBox("Processing")
        proc_layout = QVBoxLayout(proc_group)
        self._blank_check = QCheckBox(
            f"Apply blanking  ({self._blanking_desc})" if self._blanking_desc
            else "Apply blanking  (none applied)"
        )
        self._blank_check.setChecked(bool(self._blanking_desc))
        self._blank_check.setEnabled(bool(self._blanking_desc))
        proc_layout.addWidget(self._blank_check)
        self._filter_check = QCheckBox(
            f"Apply filter chain  ({self._filters_desc})" if self._filters_desc
            else "Apply filter chain  (no active filters)"
        )
        self._filter_check.setChecked(bool(self._filters_desc))
        self._filter_check.setEnabled(bool(self._filters_desc))
        proc_layout.addWidget(self._filter_check)
        hint = QLabel("<small>Blanking runs first, then the filters. Untick both for raw data.</small>")
        hint.setWordWrap(True)
        proc_layout.addWidget(hint)
        layout.addWidget(proc_group)

        # ── Channel selection ─────────────────────────────────────────────────
        ch_group = QGroupBox("Channels")
        ch_layout = QVBoxLayout(ch_group)
        self._ch_btn_grp = QButtonGroup(self)

        displayed = self._state.get_active_channels()
        displayed_str = ", ".join(str(c + 1) for c in displayed)
        self._ch_displayed = QRadioButton(
            f"Currently displayed  (Ch {displayed_str})"
        )
        self._ch_all = QRadioButton(
            f"All channels  ({len(self._state.neural_channels())} total)"
        )
        self._ch_select = QRadioButton("Select channels:")

        self._ch_displayed.setChecked(True)
        self._ch_btn_grp.addButton(self._ch_displayed)
        self._ch_btn_grp.addButton(self._ch_all)
        self._ch_btn_grp.addButton(self._ch_select)

        ch_layout.addWidget(self._ch_displayed)
        ch_layout.addWidget(self._ch_all)
        ch_layout.addWidget(self._ch_select)

        # Scrollable channel checklist (enabled only for "Select channels")
        self._ch_list = QListWidget()
        self._ch_list.setMaximumHeight(100)
        self._ch_list.setEnabled(False)
        for i in self._state.ordered_neural_channels():
            item = QListWidgetItem(self._state.channel_label(i, "Channel"))
            item.setData(Qt.ItemDataRole.UserRole, i)
            item.setCheckState(
                Qt.CheckState.Checked if i in displayed else Qt.CheckState.Unchecked
            )
            self._ch_list.addItem(item)

        self._ch_select.toggled.connect(self._ch_list.setEnabled)
        ch_layout.addWidget(self._ch_list)
        layout.addWidget(ch_group)

        # ── Scale factor ──────────────────────────────────────────────────────
        scale_group = QGroupBox("Scale Factor")
        scale_layout = QVBoxLayout(scale_group)
        scale_layout.addWidget(
            QLabel("Multiply data by (e.g. 1e6 converts V → µV):")
        )
        self._scale_spin = QDoubleSpinBox()
        self._scale_spin.setDecimals(0)
        self._scale_spin.setRange(1, 1_000_000_000)
        self._scale_spin.setValue(1_000_000)
        self._scale_spin.setSingleStep(1_000_000)
        scale_layout.addWidget(self._scale_spin)
        layout.addWidget(scale_group)

        # ── Format options ────────────────────────────────────────────────────
        fmt_group = QGroupBox("Format")
        fmt_layout = QVBoxLayout(fmt_group)

        layout_row = QHBoxLayout()
        layout_row.addWidget(QLabel("Sample layout:"))
        self._layout_combo = QComboBox()
        self._layout_combo.addItem("Interlaced (default, recommended)", "interlaced")
        self._layout_combo.addItem("Channel-sequential", "sequential")
        layout_row.addWidget(self._layout_combo)
        fmt_layout.addLayout(layout_row)

        ext_row = QHBoxLayout()
        ext_row.addWidget(QLabel("File extension:"))
        self._ext_combo = QComboBox()
        self._ext_combo.addItem(".i16  (TDT standard)", ".i16")
        self._ext_combo.addItem(".bin  (Kilosort / Plexon Offline Sorter)", ".bin")
        ext_row.addWidget(self._ext_combo)
        fmt_layout.addLayout(ext_row)

        layout.addWidget(fmt_group)

        # ── Info label ────────────────────────────────────────────────────────
        info = QLabel(
            f"<small><b>Sample rate: {self._state.sample_rate:.6f} Hz</b> — "
            "specify this when importing in Plexon Offline Sorter or Kilosort.</small>"
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        # ── Buttons ───────────────────────────────────────────────────────────
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_options(self) -> dict:
        """Return selected export options."""
        if self._ch_displayed.isChecked():
            channels = self._state.get_active_channels()
        elif self._ch_all.isChecked():
            channels = self._state.ordered_neural_channels()
        else:
            channels = [
                self._ch_list.item(i).data(Qt.ItemDataRole.UserRole)
                for i in range(self._ch_list.count())
                if self._ch_list.item(i).checkState() == Qt.CheckState.Checked
            ]
            if not channels:
                channels = self._state.get_active_channels()

        # Written in ascending order of the displayed numbers (electrode or channel)
        channels = sorted(set(channels), key=self._state.display_number)

        return {
            "range": "current_view" if self._range_view.isChecked() else "full",
            "channels": channels,
            "scale": self._scale_spin.value(),
            "layout": self._layout_combo.currentData(),
            "extension": self._ext_combo.currentData(),
            "apply_blanking": self._blank_check.isChecked(),
            "apply_filters": self._filter_check.isChecked(),
        }
