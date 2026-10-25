"""Control panel widget for channel selection and view controls."""
from typing import Dict, List, Optional

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QGroupBox,
    QRadioButton,
    QButtonGroup,
    QSpinBox,
    QComboBox,
    QPushButton,
    QListWidget,
    QListWidgetItem,
    QDoubleSpinBox,
    QCheckBox,
    QFrame,
)

from models import StreamState, ChannelMode


class NumberSpinBox(QSpinBox):
    """Spinbox over a set of allowed numbers (e.g. electrode numbers with gaps); arrows skip gaps."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._allowed: List[int] = []

    def set_allowed(self, numbers: List[int]):
        self._allowed = sorted(numbers)
        if self._allowed:
            self.setRange(self._allowed[0], self._allowed[-1])

    def stepBy(self, steps: int):
        if not self._allowed:
            return super().stepBy(steps)
        value = self.value()
        if steps > 0:
            bigger = [n for n in self._allowed if n > value]
            target = bigger[min(steps, len(bigger)) - 1] if bigger else value
        else:
            smaller = [n for n in self._allowed if n < value]
            target = smaller[-min(-steps, len(smaller))] if smaller else value
        self.setValue(target)


class ControlPanel(QWidget):
    """
    Control panel for stream and channel selection.

    Contains:
    - Channel mode selection (single/overlay)
    - Single channel selector
    - Overlay channel list with add/remove
    - View range controls
    """

    # Signals
    channel_mode_changed = Signal(ChannelMode)
    single_channel_changed = Signal(int)
    overlay_channels_changed = Signal(list)  # List[int]
    view_range_requested = Signal(float, float)  # start, end
    refresh_requested = Signal()
    scatter_mode_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()
        self._state: Optional[StreamState] = None
        self._neural: List[int] = []  # neural channels in display order

    def _setup_ui(self):
        """Set up the widget layout."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)

        # Stream info
        self._stream_info_label = QLabel("No stream selected")
        self._stream_info_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(self._stream_info_label)

        # Channel mode group
        mode_group = QGroupBox("Channel Mode")
        mode_layout = QVBoxLayout(mode_group)

        self._mode_button_group = QButtonGroup(self)

        self._single_radio = QRadioButton("Single Channel")
        self._single_radio.setChecked(True)
        self._mode_button_group.addButton(self._single_radio, 0)
        mode_layout.addWidget(self._single_radio)

        self._overlay_radio = QRadioButton("Overlay Channels")
        self._mode_button_group.addButton(self._overlay_radio, 1)
        mode_layout.addWidget(self._overlay_radio)

        self._mode_button_group.idClicked.connect(self._on_mode_changed)

        layout.addWidget(mode_group)

        # Single channel selection
        self._single_group = QGroupBox("Channel Selection")
        single_layout = QHBoxLayout(self._single_group)

        single_layout.addWidget(QLabel("Channel:"))
        self._channel_spin = NumberSpinBox()
        self._channel_spin.setMinimum(1)
        self._channel_spin.setMaximum(1)
        self._channel_spin.valueChanged.connect(self._on_single_channel_changed)
        single_layout.addWidget(self._channel_spin)
        self._channel_name = QLabel("")
        self._channel_name.setStyleSheet("color: gray; font-size: 11px;")
        single_layout.addWidget(self._channel_name)
        single_layout.addStretch()

        layout.addWidget(self._single_group)

        # Overlay channel selection
        self._overlay_group = QGroupBox("Overlay Channels (max 8)")
        overlay_layout = QVBoxLayout(self._overlay_group)

        # Add channel row
        add_row = QHBoxLayout()
        add_row.addWidget(QLabel("Add:"))
        self._add_channel_combo = QComboBox()
        self._add_channel_combo.setMinimumWidth(80)
        add_row.addWidget(self._add_channel_combo)

        self._add_btn = QPushButton("+")
        self._add_btn.setFixedWidth(30)
        self._add_btn.clicked.connect(self._on_add_channel)
        add_row.addWidget(self._add_btn)
        add_row.addStretch()
        overlay_layout.addLayout(add_row)

        # Channel list
        self._overlay_list = QListWidget()
        self._overlay_list.setMaximumHeight(120)
        overlay_layout.addWidget(self._overlay_list)

        # Remove button
        self._remove_btn = QPushButton("Remove Selected")
        self._remove_btn.clicked.connect(self._on_remove_channel)
        overlay_layout.addWidget(self._remove_btn)

        self._overlay_group.setVisible(False)
        layout.addWidget(self._overlay_group)

        # View controls
        view_group = QGroupBox("View Range")
        view_layout = QVBoxLayout(view_group)

        # Start time
        start_row = QHBoxLayout()
        start_row.addWidget(QLabel("Start (s):"))
        self._start_spin = QDoubleSpinBox()
        self._start_spin.setDecimals(3)
        self._start_spin.setMinimum(0)
        self._start_spin.setMaximum(9999999)
        self._start_spin.setSingleStep(0.1)
        start_row.addWidget(self._start_spin)
        view_layout.addLayout(start_row)

        # End time
        end_row = QHBoxLayout()
        end_row.addWidget(QLabel("End (s):"))
        self._end_spin = QDoubleSpinBox()
        self._end_spin.setDecimals(3)
        self._end_spin.setMinimum(0)
        self._end_spin.setMaximum(9999999)
        self._end_spin.setSingleStep(0.1)
        end_row.addWidget(self._end_spin)
        view_layout.addLayout(end_row)

        # Apply button
        btn_row = QHBoxLayout()
        self._apply_view_btn = QPushButton("Apply View")
        self._apply_view_btn.clicked.connect(self._on_apply_view)
        btn_row.addWidget(self._apply_view_btn)

        self._reset_view_btn = QPushButton("Reset")
        self._reset_view_btn.clicked.connect(self._on_reset_view)
        btn_row.addWidget(self._reset_view_btn)
        view_layout.addLayout(btn_row)

        layout.addWidget(view_group)

        # Display options
        display_group = QGroupBox("Display")
        display_layout = QVBoxLayout(display_group)
        self._scatter_check = QCheckBox("Scatter / Dot Mode")
        self._scatter_check.setChecked(False)
        self._scatter_check.stateChanged.connect(self._on_scatter_mode_changed)
        display_layout.addWidget(self._scatter_check)
        layout.addWidget(display_group)

        # Refresh button
        self._refresh_btn = QPushButton("Refresh Plot")
        self._refresh_btn.clicked.connect(lambda: self.refresh_requested.emit())
        layout.addWidget(self._refresh_btn)

        layout.addStretch()

    def set_state(self, state: StreamState):
        """Set the stream state to display."""
        self._state = state

        # Only electrodes are offered (analog inputs have their own viewer), in display order
        self._neural = state.ordered_neural_channels()
        n_analog = len(state.analog_channels)

        # Update stream info
        self._stream_info_label.setText(
            f"{state.stream_name}\n"
            f"{len(self._neural)} ch @ {state.sample_rate:.0f} Hz"
            + (f" (+{n_analog} analog)" if n_analog else "")
        )

        # Channel selector: the value is the displayed number (electrode or channel)
        self._single_group.setTitle(
            "Electrode Selection" if state.uses_electrode_numbers else "Channel Selection"
        )
        self._channel_spin.blockSignals(True)
        self._channel_spin.set_allowed([state.display_number(c) for c in self._neural] or [1])
        if state.selected_channel in self._neural:
            self._channel_spin.setValue(state.display_number(state.selected_channel))
        self._channel_spin.blockSignals(False)
        self._update_channel_name()

        # Update overlay channel combo
        self._add_channel_combo.clear()
        for i in self._neural:
            self._add_channel_combo.addItem(state.channel_label(i), i)

        # Update mode
        if state.channel_mode == ChannelMode.SINGLE:
            self._single_radio.setChecked(True)
            self._single_group.setVisible(True)
            self._overlay_group.setVisible(False)
        else:
            self._overlay_radio.setChecked(True)
            self._single_group.setVisible(False)
            self._overlay_group.setVisible(True)

        # Update overlay list
        self._update_overlay_list()

        # Update view range
        self._start_spin.setMaximum(state.duration)
        self._end_spin.setMaximum(state.duration)
        self._start_spin.setValue(state.view_start)
        self._end_spin.setValue(state.view_end)

        # Restore scatter mode
        self._scatter_check.blockSignals(True)
        self._scatter_check.setChecked(state.scatter_mode)
        self._scatter_check.blockSignals(False)

    def _update_overlay_list(self):
        """Update the overlay channel list widget."""
        self._overlay_list.clear()
        if self._state:
            for ch in self._state.overlay_channels:
                item = QListWidgetItem(self._state.channel_label(ch, "Channel"))
                item.setData(Qt.ItemDataRole.UserRole, ch)
                self._overlay_list.addItem(item)

    def _on_mode_changed(self, button_id: int):
        """Handle channel mode change."""
        if button_id == 0:
            mode = ChannelMode.SINGLE
            self._single_group.setVisible(True)
            self._overlay_group.setVisible(False)
        else:
            mode = ChannelMode.OVERLAY
            self._single_group.setVisible(False)
            self._overlay_group.setVisible(True)

        if self._state:
            self._state.channel_mode = mode
        self.channel_mode_changed.emit(mode)

    def _on_single_channel_changed(self, value: int):
        """Handle single channel selection change."""
        if not self._state:
            return
        channel = self._state.channel_for_number(value)
        if channel is None:
            return  # typed a number with no channel (gap); wait for a valid one
        self._state.selected_channel = channel
        self._update_channel_name()
        self.single_channel_changed.emit(channel)

    def _update_channel_name(self):
        """Show the other identity of the selected channel, e.g. "Ch 125 · elec1-10"."""
        st = self._state
        if not st or st.selected_channel not in self._neural:
            self._channel_name.setText("")
            return
        ch = st.selected_channel
        label = st.channel_labels[ch] if st.channel_labels and ch < len(st.channel_labels) else ""
        other = f"Ch {ch + 1}" if st.uses_electrode_numbers else (f"Elec {st.electrode_numbers[ch]}" if st.electrode_numbers else "")
        self._channel_name.setText(" · ".join(x for x in (other, label) if x))

    def _on_add_channel(self):
        """Handle add channel button click."""
        if not self._state:
            return

        ch = self._add_channel_combo.currentData()
        if ch is not None and self._state.add_overlay_channel(ch):
            self._update_overlay_list()
            self.overlay_channels_changed.emit(self._state.overlay_channels.copy())

    def _on_remove_channel(self):
        """Handle remove channel button click."""
        if not self._state:
            return

        item = self._overlay_list.currentItem()
        if item:
            ch = item.data(Qt.ItemDataRole.UserRole)
            # Don't allow removing the last channel
            if len(self._state.overlay_channels) > 1:
                if self._state.remove_overlay_channel(ch):
                    self._update_overlay_list()
                    self.overlay_channels_changed.emit(
                        self._state.overlay_channels.copy()
                    )

    def _on_apply_view(self):
        """Handle apply view button click."""
        start = self._start_spin.value()
        end = self._end_spin.value()

        if end <= start:
            end = start + 0.1

        if self._state:
            self._state.view_start = start
            self._state.view_end = end

        self.view_range_requested.emit(start, end)

    def _on_reset_view(self):
        """Handle reset view button click."""
        if self._state:
            self._start_spin.setValue(0)
            self._end_spin.setValue(min(10.0, self._state.duration))
            self._on_apply_view()

    def update_view_range(self, start: float, end: float):
        """Update the view range spinboxes (called from plot)."""
        self._start_spin.blockSignals(True)
        self._end_spin.blockSignals(True)
        self._start_spin.setValue(start)
        self._end_spin.setValue(end)
        self._start_spin.blockSignals(False)
        self._end_spin.blockSignals(False)

        if self._state:
            self._state.view_start = start
            self._state.view_end = end

    def _on_scatter_mode_changed(self, state: int):
        """Handle scatter mode checkbox toggle."""
        enabled = state == Qt.CheckState.Checked.value
        if self._state:
            self._state.scatter_mode = enabled
        self.scatter_mode_changed.emit(enabled)

    def clear(self):
        """Clear the panel."""
        self._state = None
        self._stream_info_label.setText("No stream selected")
        self._channel_spin.setMaximum(0)
        self._channel_spin.setValue(0)
        self._add_channel_combo.clear()
        self._overlay_list.clear()
        self._start_spin.setValue(0)
        self._end_spin.setValue(10)
        self._scatter_check.setChecked(False)
