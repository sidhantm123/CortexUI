"""Spike raster: one row per electrode, a tick per detected spike."""
from typing import Callable, Dict, List, Optional

import numpy as np
from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout

import pyqtgraph as pg

from . import theme
from .analog_plot import MODALITY_COLORS
from .plot_widget import LEFT_AXIS_WIDTH, TIME_PLOT_HINT, TimeZoomViewBox

TICK_HALF_HEIGHT = 0.4  # rows are 1 unit apart
MAX_EVENT_BANDS = 300


class RasterPlot(QWidget):
    """
    Raster of detected spikes. Rows follow the given channel order (bottom = first).
    Only spikes inside the visible time window are drawn, as vertical line segments.
    """

    channel_clicked = Signal(int)  # channel index

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._graphics = pg.GraphicsLayoutWidget()
        layout.addWidget(self._graphics)

        self._plot = self._graphics.addPlot(row=0, col=0, viewBox=TimeZoomViewBox())
        theme.style_plot(self._plot, self._graphics)
        self._plot.showGrid(x=True, y=False, alpha=theme.GRID_ALPHA)
        theme.axis_label(self._plot, "bottom", "Time (s)")
        theme.axis_label(self._plot, "left", "Electrode")
        self._plot.getAxis("left").setWidth(LEFT_AXIS_WIDTH)
        self._plot.setMouseEnabled(x=True, y=False)
        self._plot.getViewBox().setMouseMode(pg.ViewBox.PanMode)
        self._plot.hideButtons()
        self._graphics.setToolTip(TIME_PLOT_HINT + " · Click a row to open that electrode")

        self._ticks = pg.PlotCurveItem(pen=pg.mkPen(theme.TEXT, width=1), connect="pairs")
        self._plot.addItem(self._ticks)
        self._bands: List[pg.LinearRegionItem] = []
        self._stale_text = pg.TextItem("", color=theme.ERROR, anchor=(1, 0))
        self._stale_text.setParentItem(self._plot.getViewBox())

        self._channels: List[int] = []
        self._times: Dict[int, np.ndarray] = {}
        self._describe: Optional[Callable[[int], str]] = None
        self._events = []
        self._colors: Dict[str, str] = {}

        self._plot.sigXRangeChanged.connect(lambda *_: self._redraw())
        self._plot.getViewBox().sigResized.connect(self._place_stale_text)
        self._plot.scene().sigMouseClicked.connect(self._on_click)
        self._plot.scene().sigMouseMoved.connect(self._on_hover)

    def plot_item(self) -> pg.PlotItem:
        return self._plot

    def set_spikes(self, channels: List[int], times: Dict[int, np.ndarray],
                   numbers: Dict[int, int], describe: Callable[[int], str], stale: bool = False):
        """
        Args:
            channels: rows, bottom to top (display order).
            times: channel -> sorted spike times (s).
            numbers: channel -> number shown on the y axis (electrode or channel number).
            describe: channel -> hover text.
        """
        self._channels = list(channels)
        self._times = times
        self._describe = describe
        axis = self._plot.getAxis("left")
        n = len(channels)
        every = 1 if n <= 32 else (4 if n <= 64 else 8)
        major = [(i, str(numbers[c])) for i, c in enumerate(channels) if i % every == 0]
        axis.setTicks([major, [(i, "") for i in range(n)]])
        self._plot.setYRange(-0.6, n - 0.4, padding=0)
        self.set_stale(stale)
        self._redraw()

    def set_stale(self, stale: bool):
        self._stale_text.setText(
            "Out of date — blanking, filters or threshold changed. Detect again." if stale else ""
        )
        self._place_stale_text()

    def set_events(self, events, modalities: List[str]):
        self._events = sorted(events, key=lambda e: e.onset)
        self._colors = {m: MODALITY_COLORS[i % len(MODALITY_COLORS)] for i, m in enumerate(modalities)}
        self._redraw()

    def clear(self):
        self.set_spikes([], {}, {}, lambda c: "", False)
        self.set_events([], [])

    def _place_stale_text(self, *_):
        self._stale_text.setPos(self._plot.getViewBox().width() - 6, 4)

    def _redraw(self):
        x0, x1 = self._plot.viewRange()[0]
        xs, ys = [], []
        for row, ch in enumerate(self._channels):
            t = self._times.get(ch)
            if t is None or t.size == 0:
                continue
            seg = t[np.searchsorted(t, x0): np.searchsorted(t, x1, side="right")]
            if seg.size:
                xs.append(np.repeat(seg, 2))
                ys.append(np.tile([row - TICK_HALF_HEIGHT, row + TICK_HALF_HEIGHT], seg.size))
        if xs:
            self._ticks.setData(np.concatenate(xs), np.concatenate(ys))
        else:
            self._ticks.setData([], [])

        for band in self._bands:
            self._plot.removeItem(band)
        self._bands = []
        for e in [e for e in self._events if e.offset >= x0 and e.onset <= x1][:MAX_EVENT_BANDS]:
            color = pg.mkColor(self._colors.get(e.modality, "#d62728"))
            color.setAlpha(45)
            band = pg.LinearRegionItem(values=[e.onset, e.offset], brush=pg.mkBrush(color),
                                       pen=pg.mkPen(None), movable=False)
            band.setZValue(-10)
            self._plot.addItem(band)
            self._bands.append(band)

    def _row_at(self, scene_pos) -> Optional[int]:
        vb = self._plot.getViewBox()
        if not vb.sceneBoundingRect().contains(scene_pos):
            return None
        row = int(round(vb.mapSceneToView(scene_pos).y()))
        return row if 0 <= row < len(self._channels) else None

    def _on_click(self, ev):
        if ev.button() != Qt.MouseButton.LeftButton or ev.double():
            return
        row = self._row_at(ev.scenePos())
        if row is not None:
            self.channel_clicked.emit(self._channels[row])

    def _on_hover(self, pos):
        row = self._row_at(pos)
        text = self._describe(self._channels[row]) if row is not None and self._describe else ""
        self._graphics.setToolTip(text or TIME_PLOT_HINT + " · Click a row to open that electrode")
