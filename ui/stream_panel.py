"""Recording card: block name and details, plus a stream list when there is more than one stream."""
from typing import Dict, Optional

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
)

from models.recording import RecordingBlock, StreamInfo
from .theme import ElidedLabel, set_role

SOURCE_NAMES = {"tdt": "TDT", "blackrock": "Blackrock"}


def format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f} s"
    if seconds < 3600:
        return f"{int(seconds // 60)} min {seconds % 60:.1f} s"
    return f"{int(seconds // 3600)} h {int(seconds % 3600 // 60)} min {seconds % 60:.0f} s"


def format_rate(hz: float) -> str:
    return f"{hz / 1000:g} kHz" if hz >= 1000 else f"{hz:g} Hz"


def stream_summary(info: StreamInfo, n_analog: Optional[int] = None) -> str:
    """'ns5 · 136 ch (128 + 8 analog) · 30 kHz'."""
    if n_analog is None:
        n_analog = len(info.analog_channels)
    channels = f"{info.num_channels} ch"
    if n_analog:
        channels += f" ({info.num_channels - n_analog} + {n_analog} analog)"
    return f"{info.name} · {channels} · {format_rate(info.sample_rate)}"


class StreamPanel(QWidget):
    """
    Compact recording card (left panel).

    Shows the block name (middle-elided), duration and format, and the current stream's
    channels and rate. The stream list only appears when the recording has several streams.
    """

    stream_selected = Signal(str)  # stream name

    def __init__(self, parent=None):
        super().__init__(parent)
        self._block: Optional[RecordingBlock] = None
        self._analog_counts: Dict[str, int] = {}  # current analog-list sizes (the user can edit them)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 6, 8)
        layout.setSpacing(4)

        self._name_label = ElidedLabel("No recording")
        set_role(self._name_label, "heading")
        layout.addWidget(self._name_label)

        self._detail_label = QLabel("Open a folder (⌘O / Ctrl+O)\nor drop one on the window")
        self._detail_label.setWordWrap(True)
        set_role(self._detail_label, "secondary")
        layout.addWidget(self._detail_label)

        self._streams_title = QLabel("Streams")
        set_role(self._streams_title, "secondary")
        layout.addSpacing(8)
        layout.addWidget(self._streams_title)

        self._streams_list = QListWidget()
        self._streams_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._streams_list.currentItemChanged.connect(self._on_current_changed)
        layout.addWidget(self._streams_list)

        layout.addStretch()
        self._set_streams_visible(False)

    def _set_streams_visible(self, visible: bool):
        self._streams_title.setVisible(visible)
        self._streams_list.setVisible(visible)

    def set_block(self, block: RecordingBlock):
        """Show a loaded recording."""
        self._block = block
        self._analog_counts = {}
        self._name_label.set_full_text(block.name)

        self._streams_list.blockSignals(True)
        self._streams_list.clear()
        for name, info in sorted(block.streams.items()):
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, name)
            self._streams_list.addItem(item)
        if self._streams_list.count():
            self._streams_list.setCurrentRow(0)
        self._streams_list.blockSignals(False)

        self._update_details()
        rows = self._streams_list.count()
        row_h = self._streams_list.sizeHintForRow(0) if rows else 0
        self._streams_list.setFixedHeight(rows * row_h + 2 * self._streams_list.frameWidth() + 2)
        self._set_streams_visible(rows > 1)

    def set_analog_counts(self, counts: Dict[str, int]):
        """Number of analog inputs per stream, after the user edits an analog list."""
        self._analog_counts = dict(counts)
        self._update_details()

    def _summary(self, name: str) -> str:
        return stream_summary(self._block.streams[name], self._analog_counts.get(name))

    def _update_details(self):
        block = self._block
        if block is None:
            return
        for i in range(self._streams_list.count()):
            item = self._streams_list.item(i)
            item.setText(self._summary(item.data(Qt.ItemDataRole.UserRole)))
            item.setToolTip(item.text())
        lines = [f"{format_duration(block.duration)} · {SOURCE_NAMES.get(block.source, block.source)}"]
        current = self.get_selected_stream()
        if current in block.streams:
            lines.append(self._summary(current))
        self._detail_label.setText("\n".join(lines))
        self._detail_label.setToolTip(f"{block.path}\n" + "\n".join(lines))

    def _on_current_changed(self, item: Optional[QListWidgetItem], _previous):
        if item is None:
            return
        self._update_details()
        stream_name = item.data(Qt.ItemDataRole.UserRole)
        if stream_name:
            self.stream_selected.emit(stream_name)

    def select_stream(self, stream_name: str):
        """Programmatically select a stream (no signal)."""
        for i in range(self._streams_list.count()):
            if self._streams_list.item(i).data(Qt.ItemDataRole.UserRole) == stream_name:
                self._streams_list.blockSignals(True)
                self._streams_list.setCurrentRow(i)
                self._streams_list.blockSignals(False)
                break
        self._update_details()

    def get_selected_stream(self) -> Optional[str]:
        item = self._streams_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def clear(self):
        self._block = None
        self._name_label.set_full_text("No recording")
        self._detail_label.setText("Open a folder (⌘O / Ctrl+O)\nor drop one on the window")
        self._detail_label.setToolTip("")
        self._streams_list.clear()
        self._set_streams_visible(False)
