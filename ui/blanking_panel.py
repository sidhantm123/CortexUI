"""Blanking tab: per-modality settings for removing stimulation artifacts around events."""
from typing import Dict, List

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QGroupBox,
    QLabel,
    QTabWidget,
    QDoubleSpinBox,
    QSpinBox,
    QRadioButton,
    QButtonGroup,
    QCheckBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QAbstractItemView,
    QScrollArea,
)

from core.app_state import AppState
from models.blanking import BASIC, ADVANCED, LINEAR, CUBIC, BlankingSettings
from .help_text import explain, tooltip
from .theme import set_role, set_primary


def _spin(minimum: float, maximum: float, decimals: int, suffix: str, step: float = 1.0) -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setRange(minimum, maximum)
    spin.setDecimals(decimals)
    spin.setSingleStep(step)
    spin.setSuffix(suffix)
    return spin


class ModalityBlankingPage(QWidget):
    """Basic/Advanced blanking settings for one modality."""

    apply_requested = Signal(str)
    remove_requested = Signal(str)
    stale_changed = Signal()  # settings edited: an applied result is now out of date

    def __init__(self, app_state: AppState, modality: str, parent=None):
        super().__init__(parent)
        self._app_state = app_state
        self._modality = modality
        self._loading = False
        self._setup_ui()
        self.load()

    @property
    def settings(self) -> BlankingSettings:
        return self._app_state.get_blanking_settings(self._modality)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        self._mode_tabs = QTabWidget()
        self._mode_tabs.addTab(self._make_basic(), "Basic")
        self._mode_tabs.addTab(self._make_advanced(), "Advanced")
        self._mode_tabs.currentChanged.connect(self._on_changed)
        for i in range(self._mode_tabs.count()):
            self._mode_tabs.setTabToolTip(i, tooltip("blanking.mode"))
        layout.addWidget(self._mode_tabs, 1)

        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)
        set_role(self._status_label, "secondary")
        layout.addWidget(self._status_label)

        buttons = QHBoxLayout()
        self._apply_btn = QPushButton("Apply Blanking")
        self._apply_btn.clicked.connect(lambda: self.apply_requested.emit(self._modality))
        set_primary(self._apply_btn)
        buttons.addWidget(self._apply_btn)
        self._remove_btn = QPushButton("Remove Blanking")
        self._remove_btn.clicked.connect(lambda: self.remove_requested.emit(self._modality))
        buttons.addWidget(self._remove_btn)
        layout.addLayout(buttons)
        explain("blanking.apply", self._apply_btn, self._remove_btn)

    def _make_basic(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        group = QGroupBox("Padding")
        form = QFormLayout(group)
        self._pre_spin = _spin(0, 10000, 1, " ms")
        self._pre_spin.valueChanged.connect(self._on_changed)
        form.addRow("Pre-stim pad:", self._pre_spin)
        self._post_spin = _spin(0, 60000, 1, " ms", 10)
        self._post_spin.valueChanged.connect(self._on_changed)
        form.addRow("Post-stim pad:", self._post_spin)
        explain("blanking.pre", self._pre_spin, form.labelForField(self._pre_spin))
        explain("blanking.post", self._post_spin, form.labelForField(self._post_spin))
        layout.addWidget(group)
        note = QLabel("Every trial and every electrode is blanked, then filled by linear interpolation.")
        note.setWordWrap(True)
        set_role(note, "secondary")
        layout.addWidget(note)
        layout.addStretch()
        return page

    def _make_advanced(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        pad_form = QFormLayout()
        self._adv_pre_spin = _spin(0, 10000, 1, " ms")
        self._adv_pre_spin.valueChanged.connect(self._on_changed)
        pad_form.addRow("Pre-stim pad:", self._adv_pre_spin)
        explain("blanking.pre", self._adv_pre_spin, pad_form.labelForField(self._adv_pre_spin))
        layout.addLayout(pad_form)

        group = QGroupBox("Adaptive Pad")
        form = QFormLayout(group)
        self._window_spin = _spin(1, 1000, 0, " ms", 10)
        self._window_spin.valueChanged.connect(self._on_changed)
        form.addRow("Window size:", self._window_spin)
        self._n_windows_spin = QSpinBox()
        self._n_windows_spin.setRange(1, 100)
        self._n_windows_spin.valueChanged.connect(self._on_changed)
        form.addRow("Num windows:", self._n_windows_spin)
        self._mult_spin = _spin(0.1, 100, 2, " ×", 0.05)
        self._mult_spin.valueChanged.connect(self._on_changed)
        form.addRow("Vrms multiple:", self._mult_spin)
        self._min_post_spin = _spin(0, 60000, 0, " ms", 50)
        self._min_post_spin.valueChanged.connect(self._on_changed)
        form.addRow("Min post-pad:", self._min_post_spin)
        self._max_post_spin = _spin(1, 60000, 0, " ms", 100)
        self._max_post_spin.valueChanged.connect(self._on_changed)
        form.addRow("Max post-pad:", self._max_post_spin)
        for key, field in (("blanking.window", self._window_spin), ("blanking.n_windows", self._n_windows_spin),
                           ("blanking.multiplier", self._mult_spin), ("blanking.min_post", self._min_post_spin),
                           ("blanking.max_post", self._max_post_spin)):
            explain(key, field, form.labelForField(field))
        self._rule_label = QLabel("")
        self._rule_label.setWordWrap(True)
        set_role(self._rule_label, "secondary")
        form.addRow(self._rule_label)
        layout.addWidget(group)

        trials_row = QHBoxLayout()
        trials_label = QLabel("Choose Trials")
        trials_row.addWidget(trials_label)
        trials_row.addStretch()
        self._all_trials_check = QCheckBox("Blank All Trials")
        self._all_trials_check.toggled.connect(self._on_all_trials)
        trials_row.addWidget(self._all_trials_check)
        layout.addLayout(trials_row)

        self._trials_table = QTableWidget(0, 4)
        self._trials_table.setHorizontalHeaderLabels(["Trial", "Onset", "Offset", "Blank"])
        self._trials_table.verticalHeader().setVisible(False)
        self._trials_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._trials_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._trials_table.setMinimumHeight(110)
        self._trials_table.itemChanged.connect(self._on_trial_toggled)
        layout.addWidget(self._trials_table, 1)
        explain("blanking.trials", trials_label, self._all_trials_check, self._trials_table)

        interp_row = QHBoxLayout()
        interp_label = QLabel("Interpolation:")
        interp_row.addWidget(interp_label)
        self._interp_group = QButtonGroup(self)
        self._linear_radio = QRadioButton("Linear")
        self._cubic_radio = QRadioButton("Cubic")
        for i, radio in enumerate((self._linear_radio, self._cubic_radio)):
            self._interp_group.addButton(radio, i)
            interp_row.addWidget(radio)
        interp_row.addStretch()
        self._interp_group.idClicked.connect(self._on_changed)
        explain("blanking.interpolation", interp_label, self._linear_radio, self._cubic_radio)
        layout.addLayout(interp_row)
        return page

    # ---------------------------------------------------------------- state

    def events(self) -> List:
        return [e for e in self._app_state.events if e.modality == self._modality]

    def load(self):
        """Show the stored settings, trials and status."""
        self._loading = True
        s = self.settings
        self._mode_tabs.setCurrentIndex(1 if s.mode == ADVANCED else 0)
        self._pre_spin.setValue(s.pre_ms)
        self._post_spin.setValue(s.post_ms)
        self._adv_pre_spin.setValue(s.adv_pre_ms)
        self._window_spin.setValue(s.window_ms)
        self._n_windows_spin.setValue(s.n_windows)
        self._mult_spin.setValue(s.multiplier)
        self._min_post_spin.setValue(s.min_post_ms)
        self._max_post_spin.setValue(s.max_post_ms)
        (self._cubic_radio if s.interpolation == CUBIC else self._linear_radio).setChecked(True)

        self._trials_table.blockSignals(True)
        events = self.events()
        self._trials_table.setRowCount(len(events))
        for row, e in enumerate(events):
            for col, text in enumerate([str(e.number), f"{e.onset:.4f}", f"{e.offset:.4f}"]):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._trials_table.setItem(row, col, item)
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            check.setCheckState(
                Qt.CheckState.Unchecked if e.number in s.excluded_trials else Qt.CheckState.Checked
            )
            check.setData(Qt.ItemDataRole.UserRole, e.number)
            self._trials_table.setItem(row, 3, check)
        self._trials_table.blockSignals(False)
        self._all_trials_check.blockSignals(True)
        self._all_trials_check.setChecked(not any(e.number in s.excluded_trials for e in events))
        self._all_trials_check.blockSignals(False)

        self._loading = False
        self._update_rule()
        self.update_status()

    def update_status(self):
        res = self._app_state.blanking_results.get(self._modality)
        if res is None:
            self._status_label.setText("Not applied.")
            set_role(self._status_label, "secondary")
        elif res.stale:
            self._status_label.setText(
                f"Applied ({res.summary()}). Events or settings changed — Apply again to update."
            )
            set_role(self._status_label, "error")
        else:
            self._status_label.setText(f"Applied: {res.summary()}.")
            set_role(self._status_label, "success")
        self._remove_btn.setEnabled(res is not None)
        self._apply_btn.setEnabled(bool(self.events()))

    def _update_rule(self):
        s = self.settings
        self._rule_label.setText(
            f"Blank until {s.n_windows} · {s.window_ms:g} ms windows in a row are below "
            f"{s.multiplier:g}× each channel's baseline Vrms (spike band), "
            f"between {s.min_post_ms:g} and {s.max_post_ms:g} ms after offset."
        )

    def _on_changed(self, *_):
        if self._loading:
            return
        s = self.settings
        s.mode = ADVANCED if self._mode_tabs.currentIndex() == 1 else BASIC
        s.pre_ms = self._pre_spin.value()
        s.post_ms = self._post_spin.value()
        s.adv_pre_ms = self._adv_pre_spin.value()
        s.window_ms = self._window_spin.value()
        s.n_windows = self._n_windows_spin.value()
        s.multiplier = self._mult_spin.value()
        s.min_post_ms = self._min_post_spin.value()
        s.max_post_ms = max(self._max_post_spin.value(), s.min_post_ms)
        s.interpolation = CUBIC if self._cubic_radio.isChecked() else LINEAR
        self._app_state.mark_blanking_stale(self._modality)
        self.stale_changed.emit()
        self._update_rule()
        self.update_status()

    def _on_trial_toggled(self, item: QTableWidgetItem):
        if item.column() != 3 or self._loading:
            return
        excluded = set(self.settings.excluded_trials)
        number = item.data(Qt.ItemDataRole.UserRole)
        if item.checkState() == Qt.CheckState.Checked:
            excluded.discard(number)
        else:
            excluded.add(number)
        self.settings.excluded_trials = sorted(excluded)
        self._all_trials_check.blockSignals(True)
        self._all_trials_check.setChecked(not excluded)
        self._all_trials_check.blockSignals(False)
        self._on_changed()

    def _on_all_trials(self, checked: bool):
        if not checked:
            return  # unchecking "all" doesn't untick every trial; pick trials individually
        self.settings.excluded_trials = []
        self.load()
        self._on_changed()


class BlankingPanel(QWidget):
    """The Blanking tab: one sub-tab per modality that has events."""

    apply_requested = Signal(str)
    remove_requested = Signal(str)
    stale_changed = Signal()  # settings edited: an applied result is now out of date

    def __init__(self, app_state: AppState, parent=None):
        super().__init__(parent)
        self._app_state = app_state
        self._pages: Dict[str, ModalityBlankingPage] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        self._empty_label = QLabel(
            "Detect events first.\n\nAssign analog channels and click Detect Events in the "
            "Analog Ch. panel; each modality with events gets a tab here."
        )
        self._empty_label.setWordWrap(True)
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        set_role(self._empty_label, "secondary")
        layout.addWidget(self._empty_label)

        self._tabs = QTabWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._tabs)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._scroll = scroll
        layout.addWidget(scroll, 1)
        self.refresh()

    def refresh(self):
        """Rebuild modality tabs to match the events; reload every page."""
        modalities = []
        for e in self._app_state.events:
            if e.modality not in modalities:
                modalities.append(e.modality)

        if list(self._pages) != modalities:
            current = self._tabs.tabText(self._tabs.currentIndex()) if self._tabs.count() else None
            self._tabs.clear()
            for page in self._pages.values():
                page.deleteLater()
            self._pages = {}
            for m in modalities:
                page = ModalityBlankingPage(self._app_state, m)
                page.apply_requested.connect(self.apply_requested.emit)
                page.remove_requested.connect(self.remove_requested.emit)
                page.stale_changed.connect(self.stale_changed.emit)
                self._pages[m] = page
                self._tabs.addTab(page, m)
            if current in modalities:
                self._tabs.setCurrentIndex(modalities.index(current))
        else:
            for page in self._pages.values():
                page.load()

        has = bool(modalities)
        self._empty_label.setVisible(not has)
        self._scroll.setVisible(has)

    def update_status(self):
        for page in self._pages.values():
            page.update_status()
