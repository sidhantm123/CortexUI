"""Tests for stimulation artifact blanking."""
import sys
import os
import json
import unittest
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dsp.blanking import (
    apply_blanking, adaptive_end_windows, basic_result, blank_spans, clean_windows, window_rms,
)
from models import StreamState, StimEvent
from models.analog import number_events
from models.blanking import BlankResult, BlankingSettings, ADVANCED, CUBIC, LINEAR
from core.app_state import AppState

FS = 1000.0


def result(channels, starts, ends, interpolation=LINEAR):
    starts = np.atleast_2d(np.asarray(starts, float))
    ends = np.atleast_2d(np.asarray(ends, float))
    return BlankResult("ICMS", "ns5", list(channels), list(range(1, starts.shape[0] + 1)),
                       starts, ends, interpolation=interpolation)


class TestInterpolation(unittest.TestCase):

    def setUp(self):
        self.t = np.arange(1000) / FS
        self.x = np.vstack([np.sin(2 * np.pi * 3 * self.t), np.cos(2 * np.pi * 3 * self.t)])
        self.x[:, 400:500] = 50.0  # artifact

    def test_linear_fills_exactly_the_interval(self):
        out = apply_blanking(self.x, self.t, [0, 1], [result([0], [[0.4]], [[0.499]])])
        np.testing.assert_allclose(out[0, 400:500], np.linspace(self.x[0, 399], self.x[0, 500], 102)[1:-1])
        np.testing.assert_array_equal(out[0, :400], self.x[0, :400])
        np.testing.assert_array_equal(out[0, 500:], self.x[0, 500:])
        np.testing.assert_array_equal(out[1], self.x[1])  # channel 1 not blanked
        self.assertEqual(self.x[0, 450], 50.0)  # input untouched

    def test_cubic_follows_curvature(self):
        x = np.vstack([self.t ** 2])
        x[0, 400:500] = 50.0
        out = apply_blanking(x, self.t, [0], [result([0], [[0.4]], [[0.499]], CUBIC)])
        np.testing.assert_allclose(out[0, 400:500], self.t[400:500] ** 2, atol=1e-6)

    def test_interval_crossing_buffer_edge_holds_edge_value(self):
        out = apply_blanking(self.x, self.t, [0, 1], [result([0, 1], [[0.95, 0.95]], [[1.5, 1.5]])])
        np.testing.assert_array_equal(out[:, 950:], np.repeat(self.x[:, 949:950], 50, axis=1))
        out = apply_blanking(self.x, self.t, [0, 1], [result([0, 1], [[-1, -1]], [[0.05, 0.05]])])
        np.testing.assert_array_equal(out[:, :51], np.repeat(self.x[:, 51:52], 51, axis=1))

    def test_rows_follow_channel_indices(self):
        # Buffer rows are channels [7, 3]; result blanks channel 3 only
        out = apply_blanking(self.x, self.t, [7, 3], [result([3], [[0.4]], [[0.499]])])
        np.testing.assert_array_equal(out[0], self.x[0])
        self.assertLess(np.abs(out[1, 400:500]).max(), 2)

    def test_blank_spans_merge(self):
        res = result([0, 1], [[0.1, 0.15], [0.3, 0.3]], [[0.2, 0.25], [0.4, 0.45]])
        self.assertEqual(blank_spans([res], [0, 1], 0, 1), [(0.1, 0.25), (0.3, 0.45)])
        self.assertEqual(blank_spans([res], [0], 0.35, 1), [(0.3, 0.4)])


class TestIntervals(unittest.TestCase):

    def test_basic_pads_all_trials(self):
        events = [StimEvent("ICMS", 1, 1.0, 2.0, "ns5", 9, "ainp2"), StimEvent("ICMS", 2, 5.0, 6.0, "ns5", 9, "ainp2")]
        s = BlankingSettings(pre_ms=10, post_ms=500, excluded_trials=[2])  # exclusions are Advanced-only
        res = basic_result("ICMS", "ns5", events, [0, 1, 2], s)
        self.assertEqual(res.start_s.shape, (2, 3))
        np.testing.assert_allclose(res.start_s[:, 0], [0.99, 4.99])
        np.testing.assert_allclose(res.end_s[:, 2], [2.5, 6.5])

    def test_clean_windows_avoid_events(self):
        starts = clean_windows([(10, 11), (50, 51)], duration=100, n=20, guard_s=3)
        self.assertEqual(len(starts), 20)
        for t in starts:
            for a, b in [(10, 11), (50, 51)]:
                self.assertTrue(t + 1 <= a - 3 or t >= b + 3)
        self.assertEqual(clean_windows([(0, 100)], duration=100), [])

    def test_adaptive_end(self):
        # windows: noisy until 6, quiet after; one quiet blip at 2
        below = np.array([[False, False, True, False, False, False, True, True, True, True, True, True]])
        k, capped = adaptive_end_windows(below, n_windows=3, min_win=2, max_win=10)
        self.assertEqual(k[0], 6)  # blip at 2 doesn't count: needs 3 in a row
        self.assertFalse(capped[0])

    def test_adaptive_min_and_cap(self):
        quiet = np.ones((1, 12), bool)
        k, capped = adaptive_end_windows(quiet, 3, min_win=2, max_win=10)
        self.assertEqual((k[0], capped[0]), (2, False))
        noisy = np.zeros((1, 12), bool)
        k, capped = adaptive_end_windows(noisy, 3, min_win=2, max_win=10)
        self.assertEqual((k[0], capped[0]), (10, True))

    def test_late_outlier_does_not_extend(self):
        below = np.array([[False, True, True, True, False, True, True, True, True, True]])
        k, _ = adaptive_end_windows(below, 3, min_win=0, max_win=8)
        self.assertEqual(k[0], 1)

    def test_window_rms(self):
        x = np.vstack([np.r_[np.ones(10), 2 * np.ones(10)]])
        np.testing.assert_allclose(window_rms(x, 10), [[1.0, 2.0]])


class TestBlankingState(unittest.TestCase):

    def test_edit_marks_stale_and_session_round_trip(self):
        app = AppState()
        app.stream_states["ns5"] = StreamState("ns5", 4, 30000.0, 100.0, analog_channels=[3])
        app.events = number_events([StimEvent("ICMS", 0, 10.0, 11.0, "ns5", 3, "ainp1")])
        s = app.get_blanking_settings("ICMS")
        s.mode, s.multiplier = ADVANCED, 2.0
        app.blanking_results["ICMS"] = result([0, 1, 2], [[9.9, 9.9, 9.9]], [[11.5, 12.0, 11.2]])
        self.assertEqual(len(app.active_blanking("ns5")), 1)
        self.assertEqual(app.active_blanking("other"), [])

        app.set_event_times(app.events[0], 10.0, 11.5)
        self.assertTrue(app.blanking_results["ICMS"].stale)

        restored_s = BlankingSettings.from_dict(json.loads(json.dumps(s.to_dict())))
        self.assertEqual((restored_s.mode, restored_s.multiplier), (ADVANCED, 2.0))
        r = BlankResult.from_dict(json.loads(json.dumps(app.blanking_results["ICMS"].to_dict())))
        np.testing.assert_allclose(r.end_s, [[11.5, 12.0, 11.2]])
        self.assertTrue(r.stale)


if __name__ == "__main__":
    unittest.main()
