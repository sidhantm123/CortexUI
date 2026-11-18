"""Render path: decimation, and plot items that must not be rebuilt on every frame (offscreen Qt)."""
import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication([])

from dsp.decimation import decimate_for_display  # noqa: E402
from models.analog import StimEvent  # noqa: E402
from models.probe import ProbeContact, ProbeGeometry  # noqa: E402
from ui.analog_plot import AnalogPlot  # noqa: E402
from ui.plot_widget import SpectrumPlot, TimeSeriesPlot  # noqa: E402
from ui.probe_map_widget import ProbeMapWidget  # noqa: E402


class TestDecimation(unittest.TestCase):

    def test_minmax_keeps_the_end_of_the_view(self):
        n = 10_000 + 1_999  # 1999 samples past the last full bin of 2000 bins
        t = np.arange(n) / 30000.0
        x = np.zeros((2, n))
        x[0, -1] = 5.0  # a peak in the very last sample
        x[1, -3] = -4.0
        d, dt = decimate_for_display(x, t, target_points=4000)
        self.assertEqual(dt[-1], t[-1])
        self.assertEqual(d[0].max(), 5.0)
        self.assertEqual(d[1].min(), -4.0)
        self.assertTrue(np.all(np.diff(dt) >= 0))

    def test_exact_multiple_is_unchanged(self):
        t = np.arange(8000) / 1000.0
        x = np.sin(t)[None, :]
        d, dt = decimate_for_display(x, t, target_points=4000)
        self.assertEqual((d.shape[1], dt.size), (4000, 4000))
        self.assertEqual(dt[-1], t[-1])


class TestSpectrumBand(unittest.TestCase):

    def test_band_crops_the_drawn_spectrum(self):
        plot = SpectrumPlot()
        freqs = np.linspace(0, 1000, 1001)
        plot.set_data(freqs, np.ones((2, freqs.size)), [3, 5], method="psd")
        drawn = lambda: [item.getData()[0] for item in plot._plot_items.values()]
        self.assertTrue(all(x.size == 1001 for x in drawn()))
        plot.set_band(55.0, 65.0)  # redraws the last result, no new data needed
        for x in drawn():
            self.assertEqual((x.min(), x.max(), x.size), (55.0, 65.0, 11))
        plot.set_data(freqs, np.ones(freqs.size), [3])  # a new result keeps the band
        self.assertEqual(drawn()[0].size, 11)
        plot.set_band(None, None)
        self.assertEqual(drawn()[0].size, 1001)


class TestRenderItems(unittest.TestCase):

    def test_probe_map_accepts_nan_rms(self):
        probe = ProbeGeometry([ProbeContact(c, str(c), c * 100.0, 0.0, "0", 10.0) for c in range(4)])
        w = ProbeMapWidget()
        w.show()
        w.set_probe(probe)
        w.update_vrms({0: 1e-5, 1: float("nan"), 2: 2e-5})  # NaN: fully blanked in view
        w.update_vrms({0: float("nan")})
        w.close()

    def test_unwired_contact_is_never_selected(self):
        probe = ProbeGeometry([ProbeContact(0, "1", 0.0, 0.0, "0", 10.0), ProbeContact(-1, "2", 100.0, 0.0, "0", 10.0)])
        w = ProbeMapWidget()
        w.show()
        w.set_probe(probe)  # nothing selected yet: -1, which an unwired contact used to match
        self.assertEqual(len(w._sel_scatter.data), 0)
        w.set_selected_channel(0)
        self.assertEqual(len(w._sel_scatter.data), 1)
        w.close()

    def test_blank_spans_reuse_their_items(self):
        plot = TimeSeriesPlot()
        plot.set_blank_spans([(1.0, 2.0), (3.0, 4.0)])
        items = list(plot._blank_items)
        plot.set_blank_spans([(1.0, 2.0), (3.0, 4.0)])
        self.assertEqual(plot._blank_items, items)
        plot.set_blank_spans([(1.5, 2.0)])
        self.assertEqual(plot._blank_items, items[:1])
        self.assertEqual(tuple(items[0].getRegion()), (1.5, 2.0))
        self.assertNotIn(items[1], plot.plot_item().items)

    def test_analog_markers_are_kept_while_panning(self):
        plot = AnalogPlot()
        plot.resize(800, 300)
        plot.show()
        plot.set_view_range(0.0, 10.0)
        events = [StimEvent("ICMS", i + 1, 2.0 + 3 * i, 2.5 + 3 * i, "ns5", 0, "ainp2") for i in range(3)]
        plot.set_events(events, ["ICMS"])
        markers = list(plot._markers)
        self.assertEqual(len(markers), 6)
        plot.set_view_range(0.1, 10.1)  # same events in view
        self.assertEqual(plot._markers, markers)
        plot.set_view_range(5.0, 15.0)  # the first event leaves the view
        self.assertEqual(len(plot._markers), 4)
        plot.close()


if __name__ == "__main__":
    unittest.main()
