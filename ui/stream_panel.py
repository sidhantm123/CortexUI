"""Stream list panel widget."""
from typing import Optional

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QLabel,
    QTreeWidget,
    QTreeWidgetItem,
    QGroupBox,
    QHeaderView,
)

from models.recording import RecordingBlock
from .theme import ElidedLabel, set_role, mono_font, SIZE_BODY

NO_BLOCK_HINT = "Open a folder (⌘O / Ctrl+O)\nor drop one on the window"


def format_duration(seconds: float) -> str:
    """Duration in human-readable form, e.g. '16 min 6.6 s'."""
    if seconds < 60:
        return f"{seconds:.1f} s"
    if seconds < 3600:
        return f"{int(seconds // 60)} min {seconds % 60:.1f} s"
    return f"{int(seconds // 3600)} h {int(seconds % 3600 // 60)} min {seconds % 60:.0f} s"


class StreamPanel(QWidget):
    """
    Panel showing available streams in the loaded recording.

    Displays:
    - Block name (middle-elided; full name in the tooltip) and info
    - Tree of streams with channel counts and sample rates
    """

    # Signals
    stream_selected = Signal(str)  # stream name

    def __init__(self, parent=None):
        super().__init__(parent)
        self._block: Optional[RecordingBlock] = None
        self._setup_ui()

    def _setup_ui(self):
        """Set up the widget layout."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)

        # Block info group
        block_group = QGroupBox("Block Info")
        block_layout = QVBoxLayout(block_group)
        block_layout.setContentsMargins(6, 6, 0, 0)
        block_layout.setSpacing(4)

        self._block_name_label = ElidedLabel("No block loaded")
        set_role(self._block_name_label, "heading")
        block_layout.addWidget(self._block_name_label)

        self._duration_label = QLabel(NO_BLOCK_HINT)
        set_role(self._duration_label, "secondary")
        block_layout.addWidget(self._duration_label)

        layout.addWidget(block_group)

        # Streams tree
        streams_group = QGroupBox("Streams")
        streams_layout = QVBoxLayout(streams_group)
        streams_layout.setContentsMargins(6, 6, 0, 0)

        self._streams_tree = QTreeWidget()
        self._streams_tree.setHeaderLabels(["Stream", "Channels", "Rate (Hz)"])
        self._streams_tree.setRootIsDecorated(False)
        header = self._streams_tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setMinimumSectionSize(40)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._streams_tree.itemClicked.connect(self._on_item_clicked)
        streams_layout.addWidget(self._streams_tree)

        layout.addWidget(streams_group)
        layout.addStretch()

    def set_block(self, block: RecordingBlock):
        """Set the recording to display."""
        self._block = block

        # Update block info
        self._block_name_label.set_full_text(block.name)
        self._block_name_label.setToolTip(f"{block.name}\n{block.path}")
        self._duration_label.setText(
            f"Duration: {format_duration(block.duration)}\n"
            f"Streams: {len(block.streams)}"
        )

        # Populate streams tree
        self._streams_tree.clear()
        numbers = mono_font(SIZE_BODY)
        right = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        for name, info in sorted(block.streams.items()):
            item = QTreeWidgetItem([name, str(info.num_channels), f"{info.sample_rate:.0f}"])
            item.setData(0, Qt.ItemDataRole.UserRole, name)
            for col in (1, 2):
                item.setFont(col, numbers)
                item.setTextAlignment(col, right)
            self._streams_tree.addTopLevelItem(item)

        # Select first stream
        if self._streams_tree.topLevelItemCount() > 0:
            self._streams_tree.setCurrentItem(self._streams_tree.topLevelItem(0))

    def _on_item_clicked(self, item: QTreeWidgetItem, column: int):
        """Handle stream item click."""
        stream_name = item.data(0, Qt.ItemDataRole.UserRole)
        if stream_name:
            self.stream_selected.emit(stream_name)

    def select_stream(self, stream_name: str):
        """Programmatically select a stream."""
        for i in range(self._streams_tree.topLevelItemCount()):
            item = self._streams_tree.topLevelItem(i)
            if item.data(0, Qt.ItemDataRole.UserRole) == stream_name:
                self._streams_tree.setCurrentItem(item)
                break

    def get_selected_stream(self) -> Optional[str]:
        """Get currently selected stream name."""
        item = self._streams_tree.currentItem()
        if item:
            return item.data(0, Qt.ItemDataRole.UserRole)
        return None

    def clear(self):
        """Clear the panel."""
        self._block = None
        self._block_name_label.set_full_text("No block loaded")
        self._duration_label.setText(NO_BLOCK_HINT)
        self._streams_tree.clear()
