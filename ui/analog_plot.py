"""Analog channel plot shown below the main time series."""
from typing import Dict, List, Optional
import numpy as np

from PySide6.QtCore import Signal, Qt, QTimer
from PySide6.QtWidgets import QWidget, QVBoxLayout

import pyqtgraph as pg

from models.analog import StimEvent
from .plot_widget import LEFT_AXIS_WIDTH, TIME_PLOT_HINT, TimeZoomViewBox, setup_time_axes

# One color per modality, assigned in the order modalities appear
MODALITY_COLORS = ["#d62728", "#1f77b4", "#2ca02c", "#9467bd", "#ff7f0e", "#8c564b"]

# Draw at most this many events' markers at once (only those in view are drawn)
MAX_MARKERS = 300


class AnalogPlot(QWidget):
    """
    Plot of one analog input, with detected event onsets/offsets as dashed lines.

    The lines can be dragged to correct an event's onset or offset.
    """

    view_range_changed = Signal(float, float)
    event_time_dragged = Signal(object, str, float)  # StimEvent, "onset" | "offset", new time

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._graphics = pg.GraphicsLayoutWidget()
        self._graphics.setBackground("w")
        self._graphics.setToolTip(TIME_PLOT_HINT)
        layout.addWidget(self._graphics)

        self._plot = self._graphics.addPlot(row=0, col=0, viewBox=TimeZoomViewBox())
        self._plot.setLabel("bottom", "Time", units="s")
        self._plot.setLabel("left", "Analog", units="V")
        self._plot.showGrid(x=True, y=True, alpha=0.3)
        self._plot.getAxis("left").setWidth(LEFT_AXIS_WIDTH)
        setup_time_axes(self._plot)
        self._plot.sigRangeChanged.connect(self._on_range_changed)

        self._curve = self._plot.plot(pen=pg.mkPen("#444444", width=1))
        self._title = pg.TextItem("", color="#444444", anchor=(0, 0))
        self._title.setParentItem(self._plot.getViewBox())
        self._title.setPos(5, 2)

        self._events: List[StimEvent] = []
        self._colors: Dict[str, str] = {}
        self._markers: List[pg.InfiniteLine] = []
        self._highlight: Optional[pg.LinearRegionItem] = None
        self._highlighted: Optional[StimEvent] = None
        self._dragging = False

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
        self._highlighted = event
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

    def refresh_events(self):
        """Redraw markers and the highlight after event times changed."""
        self._events = sorted(self._events, key=lambda e: e.onset)
        self.highlight_event(self._highlighted)
        self._update_markers()

    def _update_markers(self):
        if self._dragging:
            return
        for line in self._markers:
            self._plot.removeItem(line)
        self._markers = []
        if not self._events:
            return

        x0, x1 = self._plot.viewRange()[0]
        onsets = np.array([e.onset for e in self._events])
        i0 = int(np.searchsorted(onsets, x0 - 10.0))
        visible = [e for e in self._events[i0:] if e.onset <= x1 and e.offset >= x0]
        eps = 1e-5  # keeps onset strictly before offset while dragging
        for e in visible[:MAX_MARKERS]:
            color = self._colors.get(e.modality, "#d62728")
            pen = pg.mkPen(color, width=1, style=Qt.PenStyle.DashLine)
            hover = pg.mkPen(color, width=3)
            for edge, t, bounds in (
                ("onset", e.onset, (0, e.offset - eps)),
                ("offset", e.offset, (e.onset + eps, None)),
            ):
                line = pg.InfiniteLine(
                    pos=t, angle=90, pen=pen, hoverPen=hover, movable=True, bounds=bounds
                )
                line.setToolTip(f"{e.modality} {e.number} {edge} — drag to adjust")
                line.sigDragged.connect(self._on_drag_started)
                line.sigPositionChangeFinished.connect(
                    lambda ln, ev=e, ed=edge: self._on_drag_finished(ev, ed, ln.value())
                )
                self._plot.addItem(line)
                self._markers.append(line)

    def _on_drag_started(self, _line):
        self._dragging = True

    def _on_drag_finished(self, event: StimEvent, edge: str, t: float):
        self._dragging = False
        # Emit after this signal returns: handlers rebuild the markers, including the sender
        QTimer.singleShot(0, lambda: self.event_time_dragged.emit(event, edge, float(t)))

    def clear(self):
        self._curve.setData([], [])
        self._title.setText("")
        self.set_events([], [])
        self.highlight_event(None)
