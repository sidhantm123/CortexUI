"""Analog channel plot shown below the main time series."""
from typing import Dict, List, Optional
import numpy as np

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout

import pyqtgraph as pg

from models.analog import StimEvent
from .plot_widget import LEFT_AXIS_WIDTH

# One color per modality, assigned in the order modalities appear
MODALITY_COLORS = ["#d62728", "#1f77b4", "#2ca02c", "#9467bd", "#ff7f0e", "#8c564b"]

# Draw at most this many events' markers at once (only those in view are drawn)
MAX_MARKERS = 300


class AnalogPlot(QWidget):
    """Plot of one analog input, with detected event onsets/offsets as dashed lines."""

    view_range_changed = Signal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._graphics = pg.GraphicsLayoutWidget()
        self._graphics.setBackground("w")
        layout.addWidget(self._graphics)

        self._plot = self._graphics.addPlot(row=0, col=0)
        self._plot.setLabel("bottom", "Time", units="s")
        self._plot.setLabel("left", "Analog", units="V")
        self._plot.showGrid(x=True, y=True, alpha=0.3)
        self._plot.getAxis("left").setWidth(LEFT_AXIS_WIDTH)
        self._plot.getViewBox().setMouseMode(pg.ViewBox.PanMode)
        self._plot.enableAutoRange(axis="y", enable=True)
        self._plot.sigRangeChanged.connect(self._on_range_changed)

        self._curve = self._plot.plot(pen=pg.mkPen("#444444", width=1))
        self._title = pg.TextItem("", color="#444444", anchor=(0, 0))
        self._title.setParentItem(self._plot.getViewBox())
        self._title.setPos(5, 2)

        self._events: List[StimEvent] = []
        self._colors: Dict[str, str] = {}
        self._markers: List[pg.InfiniteLine] = []
        self._highlight: Optional[pg.LinearRegionItem] = None

    def plot_item(self) -> pg.PlotItem:
        return self._plot

    def set_title(self, text: str):
        self._title.setText(text)

    def set_data(self, data: Optional[np.ndarray], time_axis: Optional[np.ndarray]):
        if data is None or time_axis is None:
            self._curve.setData([], [])
            return
        self._curve.setData(time_axis, np.ravel(data))

    def set_events(self, events: List[StimEvent], modalities: List[str]):
        """Events to mark; colors follow the order of modalities."""
        self._events = sorted(events, key=lambda e: e.onset)
        self._colors = {
            m: MODALITY_COLORS[i % len(MODALITY_COLORS)] for i, m in enumerate(modalities)
        }
        self._update_markers()

    def highlight_event(self, event: Optional[StimEvent]):
        """Shade the selected event."""
        if self._highlight is not None:
            self._plot.removeItem(self._highlight)
            self._highlight = None
        if event is None:
            return
        self._highlight = pg.LinearRegionItem(
            values=[event.onset, event.offset],
            brush=pg.mkBrush(255, 200, 0, 60),
            movable=False,
        )
        self._plot.addItem(self._highlight)

    def set_x_link(self, plot: Optional[pg.PlotItem]):
        self._plot.setXLink(plot)

    def set_view_range(self, start: float, end: float):
        self._plot.setXRange(start, end, padding=0)

    def get_view_range(self):
        return tuple(self._plot.viewRange()[0])

    def _on_range_changed(self, view_box, ranges):
        self._update_markers()
        self.view_range_changed.emit(ranges[0][0], ranges[0][1])

    def _update_markers(self):
        for line in self._markers:
            self._plot.removeItem(line)
        self._markers = []
        if not self._events:
            return

        x0, x1 = self._plot.viewRange()[0]
        onsets = np.array([e.onset for e in self._events])
        i0 = int(np.searchsorted(onsets, x0 - 10.0))
        visible = [e for e in self._events[i0:] if e.onset <= x1 and e.offset >= x0]
        for e in visible[:MAX_MARKERS]:
            pen = pg.mkPen(self._colors.get(e.modality, "#d62728"), width=1, style=Qt.PenStyle.DashLine)
            for t in (e.onset, e.offset):
                line = pg.InfiniteLine(pos=t, angle=90, pen=pen, movable=False)
                self._plot.addItem(line)
                self._markers.append(line)

    def clear(self):
        self._curve.setData([], [])
        self._title.setText("")
        self.set_events([], [])
        self.highlight_event(None)
