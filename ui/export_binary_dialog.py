"""Dialog for configuring binary (.i16 / .bin) export options."""
import html

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
    QDialogButtonBox,
    QButtonGroup,
    QCheckBox,
    QLineEdit,
    QPushButton,
)

from models import StreamState
from .channel_spec import format_channel_spec, parse_channel_spec
from . import theme
from .help_text import explain


class ExportBinaryDialog(QDialog):
    """Dialog for configuring int16 binary export (.i16 / .bin)."""

    def __init__(self, state: StreamState, blanking: str = "", filters: str = "",
                 stale_blanking=(), parent=None):
        """
        Args:
            blanking: description of the applied blanking ("" = none applied).
            filters: description of the active filter chain ("" = none / all bypassed).
            stale_blanking: modalities whose applied blanking is out of date.
        """
        super().__init__(parent)
        self._state = state
        self._stale = list(stale_blanking)
        self._ok_button = None
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
        explain("export.range", self._range_view, self._range_full)
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
        explain("export.blanking", self._blank_check)
        self._filter_check = QCheckBox(
            f"Apply filter chain  ({self._filters_desc})" if self._filters_desc
            else "Apply filter chain  (no active filters)"
        )
        self._filter_check.setChecked(bool(self._filters_desc))
        self._filter_check.setEnabled(bool(self._filters_desc))
        proc_layout.addWidget(self._filter_check)
        explain("export.filters", self._filter_check)
        self._stale_note = QLabel(
            f'<span style="color:{theme.ERROR};">{html.escape(", ".join(self._stale))} blanking is out of date (events or '
            "settings changed since Apply). Export will use the last applied blanking — cancel and click "
            "Apply Blanking to update.</span>"
        )
        self._stale_note.setWordWrap(True)
        proc_layout.addWidget(self._stale_note)
        self._blank_check.toggled.connect(self._update_stale_note)
        self._update_stale_note()
        hint = QLabel("<small>Blanking runs first, then the filters. Untick both for raw data.</small>")
        hint.setWordWrap(True)
        proc_layout.addWidget(hint)
        layout.addWidget(proc_group)

        # ── Channel selection (typed, in the app's numbering) ───────────────
        st = self._state
        self._number_word = "electrodes" if st.uses_electrode_numbers else "channels"
        ch_group = QGroupBox("Electrodes" if st.uses_electrode_numbers else "Channels")
        ch_layout = QVBoxLayout(ch_group)
        ch_layout.addWidget(QLabel(
            f"<small>Type {self._number_word} as numbers and ranges, e.g. <b>1-24, 37-45</b> or <b>33</b>.</small>"
        ))
        row = QHBoxLayout()
        self._ch_edit = QLineEdit()
        self._ch_edit.setPlaceholderText("e.g. 1-24, 37-45")
        self._ch_edit.textChanged.connect(self._on_channels_typed)
        row.addWidget(self._ch_edit, 1)
        all_btn = QPushButton("All Channels")
        all_btn.clicked.connect(
            lambda: self._ch_edit.setText(format_channel_spec(st.display_number(c) for c in st.neural_channels()))
        )
        row.addWidget(all_btn)
        shown_btn = QPushButton("Displayed")
        shown_btn.clicked.connect(
            lambda: self._ch_edit.setText(format_channel_spec(st.display_number(c) for c in st.get_active_channels()))
        )
        row.addWidget(shown_btn)
        ch_layout.addLayout(row)
        explain("export.channels", self._ch_edit, all_btn, shown_btn)
        self._ch_preview = QLabel("")
        self._ch_preview.setWordWrap(True)
        ch_layout.addWidget(self._ch_preview)
        layout.addWidget(ch_group)
        self._channels: list = []

        # ── Scale factor ──────────────────────────────────────────────────────
        scale_group = QGroupBox("Scale Factor")
        scale_layout = QVBoxLayout(scale_group)
        scale_label = QLabel("Multiply data by (e.g. 1e6 converts V → µV):")
        scale_layout.addWidget(scale_label)
        self._scale_spin = QDoubleSpinBox()
        self._scale_spin.setDecimals(0)
        self._scale_spin.setRange(1, 1_000_000_000)
        self._scale_spin.setValue(1_000_000)
        self._scale_spin.setSingleStep(1_000_000)
        scale_layout.addWidget(self._scale_spin)
        explain("export.scale", scale_label, self._scale_spin)
        layout.addWidget(scale_group)

        # ── Format options ────────────────────────────────────────────────────
        fmt_group = QGroupBox("Format")
        fmt_layout = QVBoxLayout(fmt_group)

        layout_row = QHBoxLayout()
        layout_label = QLabel("Sample layout:")
        layout_row.addWidget(layout_label)
        self._layout_combo = QComboBox()
        self._layout_combo.addItem("Interlaced (default, recommended)", "interlaced")
        self._layout_combo.addItem("Channel-sequential", "sequential")
        layout_row.addWidget(self._layout_combo)
        explain("export.layout", layout_label, self._layout_combo)
        fmt_layout.addLayout(layout_row)

        ext_row = QHBoxLayout()
        ext_label = QLabel("File extension:")
        ext_row.addWidget(ext_label)
        self._ext_combo = QComboBox()
        self._ext_combo.addItem(".i16  (TDT standard)", ".i16")
        self._ext_combo.addItem(".bin  (Kilosort / Plexon Offline Sorter)", ".bin")
        ext_row.addWidget(self._ext_combo)
        explain("export.extension", ext_label, self._ext_combo)
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
        self._ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)

        # Start with the displayed channels
        self._ch_edit.setText(
            format_channel_spec(self._state.display_number(c) for c in self._state.get_active_channels())
        )

    def _on_channels_typed(self, text: str):
        """Parse the typed selection, show a preview or the problem, and enable OK when valid."""
        st = self._state
        number_to_ch = {st.display_number(c): c for c in st.neural_channels()}
        reasons = {}
        if not st.uses_electrode_numbers:
            reasons = {c + 1: f"an analog channel ({st.channel_labels[c] if st.channel_labels else ''})".replace(" ()", "")
                       for c in st.analog_channels}
        noun = "electrode" if st.uses_electrode_numbers else "channel"
        numbers, error = parse_channel_spec(text, number_to_ch, reasons, noun)
        self._channels = [number_to_ch[n] for n in numbers]
        if error:
            self._ch_preview.setText(f'<span style="color:{theme.ERROR};">{html.escape(error)}</span>')
        else:
            names = [st.channel_label(c) for c in self._channels]
            shown = html.escape(", ".join(names[:6])) + (", …" if len(names) > 6 else "")
            word = self._number_word if len(names) != 1 else self._number_word[:-1]
            self._ch_preview.setText(f"<small>{len(names)} {word}: {shown}</small>")
        if self._ok_button is not None:
            self._ok_button.setEnabled(not error)

    def _update_stale_note(self):
        show = bool(self._stale) and self._blank_check.isChecked()
        self._stale_note.setVisible(show)

    def get_options(self) -> dict:
        """Return selected export options."""
        # Ascending order of the displayed numbers (electrode or channel)
        channels = sorted(self._channels, key=self._state.display_number)

        return {
            "range": "current_view" if self._range_view.isChecked() else "full",
            "channels": channels,
            "scale": self._scale_spin.value(),
            "layout": self._layout_combo.currentData(),
            "extension": self._ext_combo.currentData(),
            "apply_blanking": self._blank_check.isChecked(),
            "apply_filters": self._filter_check.isChecked(),
        }
