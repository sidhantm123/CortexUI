"""Mouse / trackpad gestures on the time plots (offscreen Qt)."""
import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QNativeGestureEvent, QPointingDevice, QWheelEvent
from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication([])

from ui.plot_widget import TimeSeriesPlot  # noqa: E402  (needs the QApplication)


class TestTimePlotGestures(unittest.TestCase):

    def setUp(self):
        self.plot = TimeSeriesPlot()
        self.plot.resize(900, 500)
        self.plot.set_empty(False)
        self.plot.show()
        t = np.arange(300000) / 30000.0
        self.plot.set_data(np.zeros((1, t.size)), t, [0])
        self.plot.set_view_range(2.0, 4.0)
        app.processEvents()
        self.vb = self.plot.plot_item().getViewBox()
        self.view = self.plot._graphics_widget.viewport()
        self.hits = []
        self.vb.sigMaxSpanHit.connect(lambda: self.hits.append(1))

    def tearDown(self):
        self.plot.close()

    def _x(self):
        return self.vb.viewRange()[0]

    def _wheel(self, pixel, angle, modifiers=Qt.KeyboardModifier.NoModifier):
        c = QPointF(self.view.rect().center())
        ev = QWheelEvent(c, QPointF(self.view.mapToGlobal(c.toPoint())), QPoint(*pixel), QPoint(*angle),
                         Qt.MouseButton.NoButton, modifiers, Qt.ScrollPhase.ScrollUpdate, False)
        QApplication.sendEvent(self.view, ev)
        app.processEvents()

    def _pinch(self, value):
        c = QPointF(self.view.rect().center())
        ev = QNativeGestureEvent(Qt.NativeGestureType.ZoomNativeGesture, QPointingDevice.primaryPointingDevice(),
                                 2, c, c, QPointF(self.view.mapToGlobal(c.toPoint())), value, QPointF(), 0)
        QApplication.sendEvent(self.view, ev)
        app.processEvents()

    def test_horizontal_swipe_pans_without_zooming(self):
        x0, x1 = self._x()
        for _ in range(5):
            self._wheel((-20, 0), (-60, 0))  # trackpad: fingers move left
        y0, y1 = self._x()
        self.assertAlmostEqual(y1 - y0, x1 - x0, places=9)
        self.assertGreater(y0, x0)  # content moved left: later times come into view

    def test_vertical_swipe_and_wheel_zoom(self):
        span = np.diff(self._x())[0]
        self._wheel((0, -10), (0, -30))  # trackpad swipe down
        self.assertGreater(np.diff(self._x())[0], span)
        span = np.diff(self._x())[0]
        self._wheel((0, 0), (0, 120))  # mouse wheel notch up
        self.assertLess(np.diff(self._x())[0], span)

    def test_zoom_out_stops_at_the_cap_for_every_input(self):
        self.vb.set_max_span(5.0)
        for _ in range(20):
            self._wheel((0, 0), (0, -120))
        self.assertAlmostEqual(np.diff(self._x())[0], 5.0, places=6)
        self.assertTrue(self.hits)
        self.plot.set_view_range(2.0, 4.0)
        app.processEvents()
        for _ in range(60):
            self._wheel((0, -10), (0, -30))
        self.assertAlmostEqual(np.diff(self._x())[0], 5.0, places=6)
        self.plot.set_view_range(2.0, 4.0)
        app.processEvents()
        for _ in range(40):
            self._pinch(-0.1)
        self.assertAlmostEqual(np.diff(self._x())[0], 5.0, places=6)

    def test_pinch_zooms_in(self):
        span = np.diff(self._x())[0]
        for _ in range(5):
            self._pinch(0.1)
        self.assertLess(np.diff(self._x())[0], span)

    def test_command_scroll_zooms_amplitude_only(self):
        x = list(self._x())
        y = list(self.vb.viewRange()[1])
        self._wheel((0, 0), (0, 120), Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(list(self._x()), x)
        self.assertNotEqual(list(self.vb.viewRange()[1]), y)


if __name__ == "__main__":
    unittest.main()
