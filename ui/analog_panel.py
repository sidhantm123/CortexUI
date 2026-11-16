"""Analog channel panel: assign analog inputs to modalities, detect and browse events."""
from typing import List, Optional, Tuple

from PySide6.QtCore import Signal, Qt, QTimer
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QCheckBox,
    QSpinBox,
    QDoubleSpinBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QComboBox,
    QInputDialog,
    QListWidget,
    QListWidgetItem,
    QMenu,
)

from core.app_state import AppState
from models.analog import AnalogAssignment, DetectionSettings, MODALITY_PRESETS, StimEvent
from .theme import set_role, set_primary

NONE_ITEM = "(none)"
NEW_ITEM = "New type…"

# Event table columns; only onset and offset can be edited
COL_EVENT, COL_ONSET, COL_OFFSET, COL_CHANNEL, COL_TYPE = range(5)


def _index_of(items: list, obj) -> int:
    """Position of obj in items by identity (events with equal fields are still distinct)."""
    for i, item in enumerate(items):
        if item is obj:
            return i
    return -1


class AssignChannelDialog(QDialog):
    """Pick a modality for an analog channel and set how its events are detected."""

    def __init__(self, label: str, modalities: List[str],
                 current: Optional[AnalogAssignment], parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Assign {label}")
        self._modalities = list(modalities)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self._modality_combo = QComboBox()
        self._modality_combo.addItem(NONE_ITEM)
        self._modality_combo.addItems(self._modalities)
        self._modality_combo.addItem(NEW_ITEM)
        self._modality_combo.activated.connect(self._on_modality_activated)
        form.addRow("Modality:", self._modality_combo)

        self._polarity_combo = QComboBox()
        self._polarity_combo.addItem("Active high (line rises during stim)", False)
        self._polarity_combo.addItem("Active low (line drops during stim)", True)
        form.addRow("Polarity:", self._polarity_combo)

        thr_row = QHBoxLayout()
        self._auto_thr_check = QCheckBox("Auto")
        self._auto_thr_check.setToolTip(
            "Midpoint between the line's idle level and its active level"
        )
        self._auto_thr_check.toggled.connect(lambda on: self._thr_spin.setEnabled(not on))
        thr_row.addWidget(self._auto_thr_check)
        self._thr_spin = QDoubleSpinBox()
        self._thr_spin.setRange(-100000, 100000)
        self._thr_spin.setDecimals(3)
        self._thr_spin.setSuffix(" mV")
        thr_row.addWidget(self._thr_spin)
        form.addRow("Threshold:", thr_row)

        self._gap_spin = QDoubleSpinBox()
        self._gap_spin.setRange(0, 10000)
        self._gap_spin.setDecimals(1)
        self._gap_spin.setSuffix(" ms")
        self._gap_spin.setToolTip("Crossings closer than this join into one event (pulse trains)")
        form.addRow("Merge gap:", self._gap_spin)

        self._min_dur_spin = QDoubleSpinBox()
        self._min_dur_spin.setRange(0, 100000)
        self._min_dur_spin.setDecimals(1)
        self._min_dur_spin.setSuffix(" ms")
        self._min_dur_spin.setToolTip("Events shorter than this are dropped")
        form.addRow("Min duration:", self._min_dur_spin)

        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        if current is not None:
            self._modality_combo.setCurrentText(current.modality)
            self._load_settings(current.settings)
        else:
            self._load_settings(DetectionSettings())
        self._update_enabled()

    def _load_settings(self, s: DetectionSettings):
        self._polarity_combo.setCurrentIndex(1 if s.active_low else 0)
        self._auto_thr_check.setChecked(s.threshold is None)
        self._thr_spin.setEnabled(s.threshold is not None)
        if s.threshold is not None:
            self._thr_spin.setValue(s.threshold * 1e3)
        self._gap_spin.setValue(s.merge_gap_ms)
        self._min_dur_spin.setValue(s.min_duration_ms)

    def _on_modality_activated(self, index: int):
        text = self._modality_combo.itemText(index)
        if text == NEW_ITEM:
            name, ok = QInputDialog.getText(self, "New modality", "Name:")
            name = name.strip()
            if ok and name:
                if name not in self._modalities:
                    self._modalities.append(name)
                    self._modality_combo.insertItem(self._modality_combo.count() - 1, name)
                self._modality_combo.setCurrentText(name)
            else:
                self._modality_combo.setCurrentIndex(0)
        elif text in MODALITY_PRESETS:
            self._load_settings(MODALITY_PRESETS[text])
        self._update_enabled()

    def _update_enabled(self):
        enabled = self._modality_combo.currentText() != NONE_ITEM
        for w in (self._polarity_combo, self._auto_thr_check, self._gap_spin, self._min_dur_spin):
            w.setEnabled(enabled)
        self._thr_spin.setEnabled(enabled and not self._auto_thr_check.isChecked())

    def modality(self) -> Optional[str]:
        text = self._modality_combo.currentText()
        return None if text in (NONE_ITEM, NEW_ITEM) else text

    def settings(self) -> DetectionSettings:
        return DetectionSettings(
            active_low=bool(self._polarity_combo.currentData()),
            threshold=None if self._auto_thr_check.isChecked() else self._thr_spin.value() * 1e-3,
            merge_gap_ms=self._gap_spin.value(),
            min_duration_ms=self._min_dur_spin.value(),
        )


class AnalogListDialog(QDialog):
    """Choose which channels of a stream are analog inputs."""

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Analog channels — {state.stream_name}")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Checked channels are analog inputs: they are shown in the analog\n"
            "viewer and left out of the electrode lists and CAR/CMR."
        ))
        self._list = QListWidget()
        for ch in range(state.num_channels):
            item = QListWidgetItem(state.channel_label(ch))
            item.setData(Qt.ItemDataRole.UserRole, ch)
            item.setCheckState(
                Qt.CheckState.Checked if ch in state.analog_channels else Qt.CheckState.Unchecked
            )
            self._list.addItem(item)
        layout.addWidget(self._list)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(320, 420)

    def channels(self) -> List[int]:
        return [
            self._list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self._list.count())
            if self._list.item(i).checkState() == Qt.CheckState.Checked
        ]


class AnalogPanel(QWidget):
    """Controls for the analog viewer (the "Analog Ch." tab)."""

    channel_selected = Signal()  # selected analog channel changed
    analog_list_changed = Signal(str)  # stream whose analog channel list was edited
    assignments_changed = Signal()
    detect_requested = Signal()
    event_selected = Signal(object)  # StimEvent
    events_edited = Signal(object)  # StimEvent whose onset/offset changed
    sync_changed = Signal(bool)

    def __init__(self, app_state: AppState, parent=None):
        super().__init__(parent)
        self._app_state = app_state
        self._channels: List[Tuple[str, int]] = []
        self._modality_events: List[StimEvent] = []
        self._setup_ui()
        self.refresh()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)

        self._sync_check = QCheckBox("Sync X-axes")
        self._sync_check.setChecked(True)
        self._sync_check.setToolTip("Keep this plot's time axis linked to the main plot")
        self._sync_check.toggled.connect(self.sync_changed.emit)
        layout.addWidget(self._sync_check)

        grid = QGridLayout()
        grid.addWidget(QLabel("Channel selection:"), 0, 0)
        self._channel_spin = QSpinBox()
        self._channel_spin.setMinimum(1)
        self._channel_spin.valueChanged.connect(self._on_channel_changed)
        grid.addWidget(self._channel_spin, 0, 1)
        self._assign_btn = QPushButton("Assign Channel")
        self._assign_btn.clicked.connect(self._on_assign)
        grid.addWidget(self._assign_btn, 0, 2)

        self._channel_label = QLabel("")
        set_role(self._channel_label, "secondary")
        grid.addWidget(self._channel_label, 1, 0, 1, 2)
        self._edit_list_btn = QPushButton("Edit List…")
        self._edit_list_btn.setToolTip("Choose which channels of this stream are analog inputs")
        self._edit_list_btn.clicked.connect(self._on_edit_list)
        grid.addWidget(self._edit_list_btn, 1, 2)

        grid.addWidget(QLabel("Event selection:"), 2, 0)
        self._event_spin = QSpinBox()
        self._event_spin.setMinimum(1)
        self._event_spin.valueChanged.connect(self._on_event_changed)
        grid.addWidget(self._event_spin, 2, 1)
        self._detect_btn = QPushButton("Detect Events")
        self._detect_btn.setToolTip("Find events on every assigned channel")
        self._detect_btn.clicked.connect(self.detect_requested.emit)
        set_primary(self._detect_btn)
        grid.addWidget(self._detect_btn, 2, 2)

        self._event_label = QLabel("")
        set_role(self._event_label, "secondary")
        grid.addWidget(self._event_label, 3, 0, 1, 3)
        layout.addLayout(grid)

        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(["Event", "Onset", "Offset", "Channel", "Type"])
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        self._table.setToolTip("Double-click an onset or offset to edit it")
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.cellClicked.connect(self._on_row_clicked)
        self._table.itemChanged.connect(self._on_item_changed)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_table_menu)
        layout.addWidget(self._table, 1)

        self._edit_msg_label = QLabel("")
        self._edit_msg_label.setWordWrap(True)
        set_role(self._edit_msg_label, "error")
        layout.addWidget(self._edit_msg_label)

        self._summary_label = QLabel("")
        self._summary_label.setWordWrap(True)
        set_role(self._summary_label, "secondary")
        layout.addWidget(self._summary_label)

    # ---------------------------------------------------------------- state

    def is_synced(self) -> bool:
        return self._sync_check.isChecked()

    def selected_channel(self) -> Optional[Tuple[str, int]]:
        i = self._channel_spin.value() - 1
        return self._channels[i] if 0 <= i < len(self._channels) else None

    def _label(self, key: Tuple[str, int]) -> str:
        stream, ch = key
        state = self._app_state.stream_states[stream]
        label = state.channel_labels[ch] if state.channel_labels and ch < len(state.channel_labels) else f"Ch {ch + 1}"
        if len({s for s, _ in self._channels}) > 1:
            label = f"{stream}: {label}"
        return label

    def refresh(self):
        """Rebuild from the app state (after load, list edits, assignments or detection)."""
        previous = self.selected_channel()
        self._channels = self._app_state.analog_channel_list()

        self._channel_spin.blockSignals(True)
        self._channel_spin.setMaximum(max(1, len(self._channels)))
        if previous in self._channels:
            self._channel_spin.setValue(self._channels.index(previous) + 1)
        self._channel_spin.blockSignals(False)

        has_channels = bool(self._channels)
        self._channel_spin.setEnabled(has_channels)
        self._assign_btn.setEnabled(has_channels)
        self._edit_list_btn.setEnabled(self._app_state.get_current_state() is not None)
        self._detect_btn.setEnabled(bool(self._app_state.analog_assignments))

        self._fill_table()
        self._update_channel_info()

    def set_summary(self, text: str):
        self._summary_label.setText(text)

    def _fill_table(self):
        events = self._app_state.events
        self._table.blockSignals(True)
        self._table.setRowCount(len(events))
        for row, e in enumerate(events):
            values = [str(e.number), f"{e.onset:.4f}", f"{e.offset:.4f}", e.label, e.modality]
            for col, v in enumerate(values):
                item = QTableWidgetItem(v)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if col not in (COL_ONSET, COL_OFFSET):
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self._table.setItem(row, col, item)
            self._mark_edited(row, e)
        self._table.blockSignals(False)

    def _mark_edited(self, row: int, e: StimEvent):
        """Bold an edited onset/offset, with the detected time in its tooltip."""
        for col, edited, detected in (
            (COL_ONSET, e.onset_edited, e.detected_onset),
            (COL_OFFSET, e.offset_edited, e.detected_offset),
        ):
            item = self._table.item(row, col)
            font = item.font()
            font.setBold(edited)
            item.setFont(font)
            item.setToolTip(
                f"Edited — detected at {detected:.4f} s (right-click to reset)" if edited else ""
            )

    def _update_channel_info(self):
        """Update the channel label and the event spinbox for the selected channel's modality."""
        key = self.selected_channel()
        if key is None:
            self._channel_label.setText("No analog channels found")
            self._modality_events = []
        else:
            a = self._app_state.analog_assignments.get(key)
            self._channel_label.setText(
                f"{self._label(key)} — {a.modality if a else 'unassigned'}"
            )
            self._modality_events = (
                [e for e in self._app_state.events if e.modality == a.modality] if a else []
            )

        n = len(self._modality_events)
        self._event_spin.blockSignals(True)
        self._event_spin.setMaximum(max(1, n))
        self._event_spin.blockSignals(False)
        self._event_spin.setEnabled(n > 0)
        if key is None or self._app_state.analog_assignments.get(key) is None:
            self._event_label.setText("")
        else:
            modality = self._app_state.analog_assignments[key].modality
            self._event_label.setText(f"{n} {modality} events")

    # ---------------------------------------------------------------- handlers

    def _on_channel_changed(self, _value: int):
        self._update_channel_info()
        self.channel_selected.emit()

    def _on_event_changed(self, value: int):
        if 0 < value <= len(self._modality_events):
            event = self._modality_events[value - 1]
            self._select_row(event)
            self.event_selected.emit(event)

    def _on_row_clicked(self, row: int, _col: int):
        if not 0 <= row < len(self._app_state.events):
            return
        event = self._app_state.events[row]
        key = (event.stream, event.channel)
        if key in self._channels and self.selected_channel() != key:
            self._channel_spin.setValue(self._channels.index(key) + 1)
        i = _index_of(self._modality_events, event)
        if i >= 0:
            self._event_spin.blockSignals(True)
            self._event_spin.setValue(i + 1)
            self._event_spin.blockSignals(False)
        self.event_selected.emit(event)

    def _select_row(self, event: StimEvent):
        row = _index_of(self._app_state.events, event)
        if row < 0:
            return
        self._table.selectRow(row)
        self._table.scrollToItem(self._table.item(row, 0))

    def show_selected_event(self, event: StimEvent):
        """Select an event's row and event number without jumping the view (after an edit)."""
        self._select_row(event)
        i = _index_of(self._modality_events, event)
        if i >= 0:
            self._event_spin.blockSignals(True)
            self._event_spin.setValue(i + 1)
            self._event_spin.blockSignals(False)

    def set_edit_message(self, text: str):
        self._edit_msg_label.setText(text)

    def _on_item_changed(self, item: QTableWidgetItem):
        row, col = item.row(), item.column()
        if col not in (COL_ONSET, COL_OFFSET) or not 0 <= row < len(self._app_state.events):
            return
        event = self._app_state.events[row]
        try:
            value = float(item.text())
        except ValueError:
            error = "Enter a time in seconds"
        else:
            onset = value if col == COL_ONSET else event.onset
            offset = value if col == COL_OFFSET else event.offset
            error = self._app_state.set_event_times(event, onset, offset)

        # Rebuild after this signal returns: refresh() replaces the item being edited
        QTimer.singleShot(0, lambda: self._after_edit(event, error))

    def _after_edit(self, event: StimEvent, error):
        self.set_edit_message(error or "")
        self.refresh()
        self.show_selected_event(event)
        if error is None:
            self.events_edited.emit(event)

    def _on_table_menu(self, pos):
        row = self._table.rowAt(pos.y())
        if not 0 <= row < len(self._app_state.events):
            return
        event = self._app_state.events[row]
        menu = QMenu(self)
        reset = menu.addAction("Reset to detected times")
        reset.setEnabled(event.edited)
        if menu.exec(self._table.viewport().mapToGlobal(pos)) is reset:
            self._app_state.reset_event_times(event)
            self.set_edit_message("")
            self.refresh()
            self.show_selected_event(event)
            self.events_edited.emit(event)

    def _on_assign(self):
        key = self.selected_channel()
        if key is None:
            return
        dialog = AssignChannelDialog(
            self._label(key),
            self._app_state.modality_names(),
            self._app_state.analog_assignments.get(key),
            self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        modality = dialog.modality()
        assignment = (
            AnalogAssignment(key[0], key[1], modality, dialog.settings()) if modality else None
        )
        self._app_state.set_assignment(key[0], key[1], assignment)
        self.refresh()
        self.assignments_changed.emit()

    def _on_edit_list(self):
        state = self._app_state.get_current_state()
        if state is None:
            return
        dialog = AnalogListDialog(state, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._app_state.set_analog_channels(state.stream_name, dialog.channels())
        self.refresh()
        self.analog_list_changed.emit(state.stream_name)
