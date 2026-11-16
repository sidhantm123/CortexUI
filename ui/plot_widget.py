"""Time series plot widget using pyqtgraph."""
from typing import List, Optional, Dict, Tuple
import numpy as np

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QStackedWidget
from PySide6.QtGui import QColor

import pyqtgraph as pg

from . import theme

# Configure pyqtgraph for performance
pg.setConfigOptions(
    antialias=False,  # Faster rendering
    useOpenGL=False,  # More compatible
    enableExperimental=False,
)


# Mouse help shown as the tooltip of time-series plots
TIME_PLOT_HINT = (
    "Scroll: zoom time · Drag: pan · Ctrl/⌘+scroll: zoom amplitude · "
    "Double-click: fit amplitude to the visible data\n"
    "Keys: ←/→ pan · +/− zoom · ↑/↓ next/previous electrode · [ / ] previous/next event"
)

EMPTY_RECORDING_HINT = "Open a recording folder (⌘O / Ctrl+O)\nor drop one on the window"
RMS_TOOLTIP = "RMS of the visible data, blanked samples excluded"
MAX_RMS_CHIP_TRACES = 3  # overlay: more traces are listed in the tooltip only


class EmptyStack(QStackedWidget):
    """A plot with a centred hint shown in its place while there is nothing to plot."""

    def __init__(self, plot: QWidget, hint: str, parent=None):
        super().__init__(parent)
        self._hint = QLabel(hint)
        self._hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        theme.set_role(self._hint, "empty")
        self.addWidget(self._hint)
        self.addWidget(plot)

    def set_empty(self, empty: bool):
        self.setCurrentIndex(0 if empty else 1)

# Ctrl on Windows/Linux; on macOS Qt reports ⌘ as Control and Ctrl as Meta, so accept both
_AMPLITUDE_ZOOM_KEYS = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier


class TimeZoomViewBox(pg.ViewBox):
    """
    ViewBox for time series: the wheel zooms time only, while y fits the visible data.

    Ctrl/⌘ + wheel zooms amplitude instead, which turns y auto-fit off until a
    double-click turns it back on.
    """

    def wheelEvent(self, ev, axis=None):
        if ev.modifiers() & _AMPLITUDE_ZOOM_KEYS:
            s = 1.02 ** (ev.delta() * self.state["wheelScaleFactor"])
            center = pg.Point(pg.functions.invertQTransform(self.childGroup.transform()).map(ev.pos()))
            self._resetTarget()
            self.scaleBy([None, s], center)
            self.enableAutoRange(axis=pg.ViewBox.YAxis, enable=False)
            ev.accept()
            self.sigRangeChangedManually.emit([False, True])
        else:
            super().wheelEvent(ev, axis=0)

    def mouseClickEvent(self, ev):
        # pyqtgraph delivers double-clicks here (ev.double()), not to mouseDoubleClickEvent
        if ev.double() and ev.button() == Qt.MouseButton.LeftButton:
            self.enableAutoRange(axis=pg.ViewBox.YAxis, enable=True)
            ev.accept()
            return
        super().mouseClickEvent(ev)


def setup_time_axes(plot: pg.PlotItem):
    """Mouse acts on time only; y auto-fits to the samples inside the visible time window."""
    plot.setMouseEnabled(x=True, y=False)
    plot.enableAutoRange(axis="y", enable=True)
    plot.setAutoVisible(y=True)
    plot.getViewBox().setMouseMode(pg.ViewBox.PanMode)


# Left axis width shared with the analog plot (x-linked plots align by screen position)
LEFT_AXIS_WIDTH = 70

def format_volts(v: float) -> str:
    """Readable Vrms: '83.5 µV', '--' for NaN."""
    if v is None or not np.isfinite(v):
        return "--"
    if v < 1e-6:
        return f"{v * 1e9:.1f} nV"
    if v < 1e-3:
        return f"{v * 1e6:.1f} µV"
    if v < 1:
        return f"{v * 1e3:.2f} mV"
    return f"{v:.4f} V"


# Most blanked spans shaded at once (only spans in view are passed in)
MAX_BLANK_SPANS = 300

# Trace colours (tuned for the dark plot background)
CHANNEL_COLORS = theme.TRACE_COLORS


class TimeSeriesPlot(QWidget):
    """
    High-performance time series plot widget.

    Features:
    - Multi-channel overlay with distinct colors
    - Mouse wheel zoom
    - Pan with drag
    - Double-click to reset
    - Region selection for FFT
    """

    # Signals
    view_range_changed = Signal(float, float)  # start, end in seconds
    region_selected = Signal(float, float)  # start, end for FFT selection
    threshold_dragged = Signal(float)  # |y| of a dragged threshold line (volts)

    def __init__(self, parent=None):
        super().__init__(parent)

        self._setup_ui()
        self._setup_plot()

        # Data state
        self._plot_items: Dict[int, pg.PlotDataItem] = {}  # channel -> plot item
        self._current_data: Optional[np.ndarray] = None
        self._current_time: Optional[np.ndarray] = None
        self._channel_indices: List[int] = []
        self._channel_names: Optional[List[str]] = None

        # Scatter mode
        self._scatter_mode: bool = False

        # Selection region
        self._selection_region: Optional[pg.LinearRegionItem] = None
        self._selection_enabled = False

        # Shaded blanked spans
        self._blank_items: List[pg.LinearRegionItem] = []

        # Spike threshold lines (draggable)
        self._threshold_lines: List[pg.InfiniteLine] = []
        self._dragging_threshold = False

    def _setup_ui(self):
        """Header (title + chips) above the plot; a hint replaces both until a recording is open."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._header = QWidget()
        self._header.setObjectName("plotHeader")
        self._header.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        row = QHBoxLayout(self._header)
        row.setContentsMargins(12, 6, 12, 2)
        row.setSpacing(6)
        self._title_label = theme.ElidedLabel("", min_width=100)  # elides when the header is narrow
        theme.set_role(self._title_label, "heading")
        row.addWidget(self._title_label, 1)
        self._chip_row = QHBoxLayout()
        self._chip_row.setSpacing(6)
        row.addLayout(self._chip_row)
        self._chips: List[QLabel] = []  # blanking / filter state, reused between updates
        self._rms_chip = QLabel("")
        theme.set_role(self._rms_chip, "chip-mono")
        self._rms_chip.setTextFormat(Qt.TextFormat.RichText)
        self._rms_chip.hide()
        row.addWidget(self._rms_chip)
        self._rms_html = ""
        layout.addWidget(self._header)

        self._graphics_widget = pg.GraphicsLayoutWidget()
        self._graphics_widget.setToolTip(TIME_PLOT_HINT)
        self._stack = EmptyStack(self._graphics_widget, EMPTY_RECORDING_HINT)
        layout.addWidget(self._stack)
        self.set_empty(True)

    def set_empty(self, empty: bool):
        """Show the 'open a recording' hint instead of the plot."""
        self._stack.set_empty(empty)
        self._header.setVisible(not empty)

    def set_title(self, title: str, tooltip: str = ""):
        self._title_label.set_full_text(title)
        self._title_label.setToolTip(tooltip or title)

    def set_chips(self, chips: List[Tuple[str, str, bool]]):
        """Processing state chips: (text, tooltip, is_warning)."""
        while len(self._chips) < len(chips):
            label = theme.ElidedLabel("", min_width=60)
            theme.set_role(label, "chip")  # before the first polish (fixes the padding)
            self._chip_row.addWidget(label)
            self._chips.append(label)
        for label, (text, tooltip, warning) in zip(self._chips, chips):
            label.set_full_text(text)
            label.setToolTip(f"{text}\n{tooltip}")
            theme.set_role(label, "chip-warning" if warning else "chip")
            label.show()
        for label in self._chips[len(chips):]:
            label.hide()

    def _setup_plot(self):
        """Set up the plot axes."""
        self._plot = self._graphics_widget.addPlot(row=0, col=0, viewBox=TimeZoomViewBox())
        # Data stays in volts; the axis shows µV (tick labels scaled only)
        theme.style_plot(self._plot, self._graphics_widget, left_scale=1e6)
        theme.axis_label(self._plot, "bottom", "Time (s)")
        theme.axis_label(self._plot, "left", "Amplitude (µV)")
        # Fixed axis width so the linked analog plot lines up in time
        self._plot.getAxis("left").setWidth(LEFT_AXIS_WIDTH)

        # Scroll/drag act on time; y fits the visible data (Ctrl/⌘+scroll zooms amplitude)
        setup_time_axes(self._plot)

        # Connect signals
        self._plot.sigRangeChanged.connect(self._on_range_changed)

    def plot_item(self) -> pg.PlotItem:
        """The underlying PlotItem (used to link other plots' x-axes)."""
        return self._plot

    def _on_range_changed(self, view_box, ranges):
        """Handle view range changes."""
        x_range = ranges[0]
        self.view_range_changed.emit(x_range[0], x_range[1])

    def set_data(
        self,
        data: np.ndarray,
        time_axis: np.ndarray,
        channel_indices: List[int],
        channel_names: Optional[List[str]] = None,
    ):
        """
        Set data to display.

        Args:
            data: Shape (n_channels, n_samples) or (n_samples,)
            time_axis: Time values in seconds
            channel_indices: Original channel indices for labeling
            channel_names: Legend names, one per entry in channel_indices
        """
        self._current_data = data
        self._current_time = time_axis
        self._channel_indices = channel_indices
        self._channel_names = channel_names

        # Clear existing plots
        for item in self._plot_items.values():
            self._plot.removeItem(item)
        self._plot_items.clear()

        if data is None or time_axis is None:
            return

        # Ensure 2D
        if data.ndim == 1:
            data = data.reshape(1, -1)

        # Plot each channel
        for i, ch_idx in enumerate(channel_indices):
            if i >= data.shape[0]:
                break

            color = QColor(CHANNEL_COLORS[i % len(CHANNEL_COLORS)])
            name = channel_names[i] if channel_names else f"Ch {ch_idx}"

            if self._scatter_mode:
                plot_item = self._plot.plot(
                    time_axis,
                    data[i],
                    pen=None,
                    symbol="o",
                    symbolSize=2,
                    symbolBrush=pg.mkBrush(color),
                    symbolPen=None,
                    name=name,
                )
            else:
                pen = pg.mkPen(color=color, width=1)
                plot_item = self._plot.plot(
                    time_axis,
                    data[i],
                    pen=pen,
                    name=name,
                )
            self._plot_items[ch_idx] = plot_item

        # Auto-range Y axis
        self._plot.enableAutoRange(axis="y", enable=True)

    def update_data(self, data: np.ndarray, time_axis: np.ndarray):
        """
        Update data without recreating plot items (faster for filtering).

        Args:
            data: Same shape as previous set_data call
            time_axis: Time values
        """
        if data is None:
            return

        if data.ndim == 1:
            data = data.reshape(1, -1)

        for i, ch_idx in enumerate(self._channel_indices):
            if ch_idx in self._plot_items and i < data.shape[0]:
                self._plot_items[ch_idx].setData(time_axis, data[i])

    def set_vrms(self, entries: List[Tuple[str, float]]):
        """
        RMS chip: 'RMS 6.9 µV' for one trace; a coloured dot and value per trace in overlay
        (the first few; all of them in the tooltip). NaN = fully blanked in view.
        """
        if not entries:
            html, tooltip = "", ""
        elif len(entries) == 1:
            html = f"RMS {format_volts(entries[0][1])}"
            tooltip = f"{entries[0][0]}\n{RMS_TOOLTIP}"
        else:
            parts = [
                f'<span style="color:{CHANNEL_COLORS[i % len(CHANNEL_COLORS)]}">●</span>&nbsp;{format_volts(v)}'
                for i, (_name, v) in enumerate(entries[:MAX_RMS_CHIP_TRACES])
            ]
            extra = len(entries) - MAX_RMS_CHIP_TRACES
            html = "RMS&nbsp; " + "&nbsp;&nbsp;".join(parts) + (f"&nbsp;&nbsp;+{extra}" if extra > 0 else "")
            tooltip = RMS_TOOLTIP + "\n" + "\n".join(f"{name}: {format_volts(v)}" for name, v in entries)
        if html != self._rms_html:
            self._rms_html = html
            self._rms_chip.setText(html)
            self._rms_chip.setVisible(bool(html))
        self._rms_chip.setToolTip(tooltip)

    def set_threshold_lines(self, levels: List[float]):
        """
        Show draggable horizontal threshold lines at these y values (volts); [] hides them.
        Positions aren't touched while the user is dragging one.
        """
        if self._dragging_threshold:
            return
        while len(self._threshold_lines) < len(levels):
            line = pg.InfiniteLine(
                angle=0, movable=True,
                pen=pg.mkPen(theme.THRESHOLD, width=1.5, style=Qt.PenStyle.DashLine),
                hoverPen=pg.mkPen(theme.THRESHOLD, width=3),
            )
            line.setZValue(30)
            line.setToolTip("Spike threshold — drag to change k")
            line.sigDragged.connect(self._on_threshold_dragging)
            line.sigPositionChangeFinished.connect(self._on_threshold_drag_finished)
            self._plot.addItem(line, ignoreBounds=True)
            self._threshold_lines.append(line)
        for i, line in enumerate(self._threshold_lines):
            if i < len(levels):
                line.blockSignals(True)
                line.setValue(levels[i])
                line.blockSignals(False)
                line.show()
            else:
                line.hide()

    def _on_threshold_dragging(self, line):
        self._dragging_threshold = True
        self.threshold_dragged.emit(abs(float(line.value())))

    def _on_threshold_drag_finished(self, line):
        self._dragging_threshold = False
        self.threshold_dragged.emit(abs(float(line.value())))

    def set_blank_spans(self, spans: List[Tuple[float, float]]):
        """Lightly shade blanked time spans (at most MAX_BLANK_SPANS)."""
        for item in self._blank_items:
            self._plot.removeItem(item)
        self._blank_items = []
        for start, end in spans[:MAX_BLANK_SPANS]:
            item = pg.LinearRegionItem(
                values=[start, end], brush=pg.mkBrush(*theme.BLANK_SHADE),
                pen=pg.mkPen(None), movable=False,
            )
            item.setZValue(-10)
            self._plot.addItem(item)
            self._blank_items.append(item)

    def set_scatter_mode(self, enabled: bool):
        """
        Enable or disable scatter/dot rendering mode.

        Args:
            enabled: True for scatter dots, False for lines
        """
        if self._scatter_mode == enabled:
            return
        self._scatter_mode = enabled
        # Re-render with current data if available
        if self._current_data is not None and self._current_time is not None:
            self.set_data(
                self._current_data, self._current_time, self._channel_indices, self._channel_names
            )

    def set_view_range(self, start: float, end: float):
        """Set the visible time range."""
        self._plot.setXRange(start, end, padding=0)

    def get_view_range(self) -> Tuple[float, float]:
        """Get current visible time range."""
        x_range = self._plot.viewRange()[0]
        return (x_range[0], x_range[1])

    def reset_view(self):
        """Reset to full data range."""
        if self._current_time is not None and len(self._current_time) > 0:
            self._plot.setXRange(
                self._current_time[0],
                self._current_time[-1],
                padding=0.02,
            )
        self._plot.enableAutoRange(axis="y", enable=True)

    def enable_selection(self, enabled: bool = True):
        """Enable or disable region selection for FFT."""
        self._selection_enabled = enabled

        if enabled:
            if self._selection_region is None:
                # Create selection region
                if self._current_time is not None and len(self._current_time) > 1:
                    t_start = self._current_time[0]
                    t_end = self._current_time[-1]
                    mid = (t_start + t_end) / 2
                    span = (t_end - t_start) * 0.1

                    self._selection_region = pg.LinearRegionItem(
                        values=[mid - span / 2, mid + span / 2],
                        brush=pg.mkBrush(*theme.SELECTION_SHADE),
                    )
                    self._selection_region.sigRegionChangeFinished.connect(
                        self._on_region_changed
                    )
                    self._plot.addItem(self._selection_region)
        else:
            if self._selection_region is not None:
                self._plot.removeItem(self._selection_region)
                self._selection_region = None

    def get_selection_range(self) -> Optional[Tuple[float, float]]:
        """Get current selection range, if any."""
        if self._selection_region is not None:
            region = self._selection_region.getRegion()
            return (region[0], region[1])
        return None

    def set_selection_range(self, start: float, end: float):
        """Set the selection range."""
        if self._selection_region is not None:
            self._selection_region.setRegion([start, end])

    def _on_region_changed(self):
        """Handle selection region change."""
        if self._selection_region is not None:
            region = self._selection_region.getRegion()
            self.region_selected.emit(region[0], region[1])

    def clear(self):
        """Clear all data from the plot."""
        for item in self._plot_items.values():
            self._plot.removeItem(item)
        self._plot_items.clear()
        self._current_data = None
        self._current_time = None
        self._channel_indices = []
        self._channel_names = None

        if self._selection_region is not None:
            self._plot.removeItem(self._selection_region)
            self._selection_region = None


class SpectrumPlot(QWidget):
    """
    Spectrum plot widget for FFT/PSD display.

    Features:
    - Linear or log frequency axis
    - Linear or dB magnitude axis
    - Multi-channel overlay
    """

    def __init__(self, parent=None):
        super().__init__(parent)

        self._setup_ui()
        self._setup_plot()

        self._plot_items: Dict[int, pg.PlotDataItem] = {}
        self._log_freq = False
        self._log_mag = True  # Default to dB scale

    def _setup_ui(self):
        """Set up the widget layout."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._graphics_widget = pg.GraphicsLayoutWidget()
        self._stack = EmptyStack(
            self._graphics_widget,
            "No spectrum yet.\nChoose electrodes and an interval in the Analysis tab, then click Analyze.",
        )
        layout.addWidget(self._stack)
        self._stack.set_empty(True)

    def _setup_plot(self):
        """Set up the plot axes."""
        self._plot = self._graphics_widget.addPlot(row=0, col=0)
        theme.style_plot(self._plot, self._graphics_widget)
        theme.axis_label(self._plot, "bottom", "Frequency (Hz)")
        theme.axis_label(self._plot, "left", "Power (dB)")
        self._plot.setMouseEnabled(x=True, y=True)

    def set_data(
        self,
        freqs: np.ndarray,
        magnitude: np.ndarray,
        channel_indices: List[int],
        method: str = "psd",
        channel_names: Optional[List[str]] = None,
    ):
        """
        Set spectrum data to display.

        Args:
            freqs: Frequency values in Hz
            magnitude: Magnitude values (can be 1D or 2D)
            channel_indices: Channel indices for labeling
            method: 'fft' or 'psd' for appropriate labeling
            channel_names: Legend names, one per entry in channel_indices
        """
        # Clear existing
        for item in self._plot_items.values():
            self._plot.removeItem(item)
        self._plot_items.clear()

        self._stack.set_empty(freqs is None or magnitude is None)
        if freqs is None or magnitude is None:
            return

        # Ensure 2D
        if magnitude.ndim == 1:
            magnitude = magnitude.reshape(1, -1)

        # Update axis label
        if method == "psd":
            theme.axis_label(self._plot, "left", "PSD (V²/Hz)")
        else:
            theme.axis_label(self._plot, "left", "Magnitude")

        # Convert to dB if log magnitude
        if self._log_mag:
            # Avoid log(0)
            magnitude = 10 * np.log10(np.maximum(magnitude, 1e-20))
            if method == "psd":
                theme.axis_label(self._plot, "left", "PSD (dB/Hz)")
            else:
                theme.axis_label(self._plot, "left", "Magnitude (dB)")

        # Plot each channel
        for i, ch_idx in enumerate(channel_indices):
            if i >= magnitude.shape[0]:
                break

            color = QColor(CHANNEL_COLORS[i % len(CHANNEL_COLORS)])
            pen = pg.mkPen(color=color, width=1)

            if self._log_freq:
                # Filter out zero/negative frequencies for log scale
                mask = freqs > 0
                plot_freqs = freqs[mask]
                plot_mag = magnitude[i, mask]
            else:
                plot_freqs = freqs
                plot_mag = magnitude[i]

            plot_item = self._plot.plot(
                plot_freqs,
                plot_mag,
                pen=pen,
                name=channel_names[i] if channel_names else f"Ch {ch_idx}",
            )
            self._plot_items[ch_idx] = plot_item

        # Set log scale if enabled
        self._plot.setLogMode(x=self._log_freq, y=False)

        # Auto-range
        self._plot.enableAutoRange()

    def set_log_frequency(self, enabled: bool):
        """Enable or disable log frequency axis."""
        self._log_freq = enabled
        self._plot.setLogMode(x=enabled, y=False)

    def set_log_magnitude(self, enabled: bool):
        """Enable or disable dB magnitude scale."""
        self._log_mag = enabled
        # Would need to re-plot data to apply this change

    def clear(self):
        """Clear all data from the plot."""
        for item in self._plot_items.values():
            self._plot.removeItem(item)
        self._plot_items.clear()
        self._stack.set_empty(True)
