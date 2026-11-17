"""Analog channel plot shown below the main time series."""
from typing import Dict, List, Optional
import numpy as np

from PySide6.QtCore import Signal, Qt, QTimer
from PySide6.QtWidgets import QWidget, QVBoxLayout

import pyqtgraph as pg

from models.analog import StimEvent
from . import theme
from .plot_widget import LEFT_AXIS_WIDTH, TIME_PLOT_HINT, TimeZoomViewBox, enable_pinch_zoom, setup_time_axes

# One color per modality, assigned in the order modalities appear
MODALITY_COLORS = theme.MODALITY_COLORS

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
        self._graphics.setToolTip(TIME_PLOT_HINT)
        layout.addWidget(self._graphics)

        self._plot = self._graphics.addPlot(row=0, col=0, viewBox=TimeZoomViewBox())
        self._pinch = enable_pinch_zoom(self._graphics, self._plot.getViewBox())
        # Data stays in volts; the axis shows mV (tick labels scaled only)
        theme.style_plot(self._plot, self._graphics, left_scale=1e3)
        theme.axis_label(self._plot, "bottom", "Time (s)")
        theme.axis_label(self._plot, "left", "Analog (mV)")
        self._plot.getAxis("left").setWidth(LEFT_AXIS_WIDTH)
        setup_time_axes(self._plot)
        self._plot.sigXRangeChanged.connect(self._on_x_range_changed)  # not y: the auto-fit would re-render

        self._curve = self._plot.plot(pen=pg.mkPen(theme.ANALOG_TRACE, width=1))
        self._title = pg.TextItem("", color=theme.TEXT_SECONDARY, anchor=(0, 0))
        self._title.setParentItem(self._plot.getViewBox())
        self._title.setPos(5, 2)

        self._events: List[StimEvent] = []
        self._colors: Dict[str, str] = {}
        self._markers: List[pg.InfiniteLine] = []
        self._marker_key = None  # the visible events the markers show
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
        self._update_markers(force=True)

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
            brush=pg.mkBrush(*theme.HIGHLIGHT_SHADE),
            movable=False,
        )
        self._plot.addItem(self._highlight)

    def set_x_link(self, plot: Optional[pg.PlotItem]):
        self._plot.setXLink(plot)

    def set_view_range(self, start: float, end: float):
        self._plot.setXRange(start, end, padding=0)

    def get_view_range(self):
        return tuple(self._plot.viewRange()[0])

    def _on_x_range_changed(self, view_box, x_range):
        self._update_markers()
        self.view_range_changed.emit(x_range[0], x_range[1])

    def refresh_events(self):
        """Redraw markers and the highlight after event times changed."""
        self._events = sorted(self._events, key=lambda e: e.onset)
        self.highlight_event(self._highlighted)
        self._update_markers(force=True)

    def _update_markers(self, force: bool = False):
        """Draggable lines for the events in view; rebuilt only when that set changes (pans
        call this on every frame)."""
        if self._dragging:
            return
        visible = []
        if self._events:
            x0, x1 = self._plot.viewRange()[0]
            onsets = np.array([e.onset for e in self._events])
            i0 = int(np.searchsorted(onsets, x0 - 10.0))
            visible = [e for e in self._events[i0:] if e.onset <= x1 and e.offset >= x0][:MAX_MARKERS]
        key = [(id(e), e.onset, e.offset, e.modality) for e in visible]
        if not force and key == self._marker_key:
            return
        self._marker_key = key
        for line in self._markers:
            self._plot.removeItem(line)
        self._markers = []
        eps = 1e-5  # keeps onset strictly before offset while dragging
        for e in visible:
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
