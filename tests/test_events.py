"""Tests for analog channels and stimulation event detection."""
import sys
import os
import json
import shutil
import tempfile
import unittest
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dsp.events import auto_threshold, detect_line_events, find_trains
from models import StreamState, DetectionSettings, AnalogAssignment, StimEvent
from models.analog import number_events, is_analog_label
from core.app_state import AppState

FS = 30000.0


def pulse_line(fs, duration, trains, pulse_ms=0.5, period_ms=5.0, active_low=False, noise=0.0):
    """Line idling at 0 (or 1 V if active_low) with pulse trains [(start_s, stop_s), ...] of 1 V pulses."""
    n = int(duration * fs)
    x = np.zeros(n)
    for start, stop in trains:
        t = start
        while t < stop:
            i0 = int(t * fs)
            x[i0:i0 + int(pulse_ms * 1e-3 * fs)] = 1.0
            t += period_ms * 1e-3
    if active_low:
        x = 1.0 - x
    if noise:
        x += np.random.default_rng(0).normal(0, noise, n)
    return x


class TestDetection(unittest.TestCase):

    def test_pulse_train_merges_into_one_event(self):
        x = pulse_line(FS, 3.0, [(0.5, 0.55), (2.0, 2.05)], noise=0.01)
        on, off, thr = detect_line_events(x, FS, DetectionSettings(merge_gap_ms=20.0))
        self.assertEqual(len(on), 2)
        np.testing.assert_allclose(on, [0.5, 2.0], atol=1e-3)
        self.assertAlmostEqual(off[0], 0.5 + 0.045 + 0.0005, delta=2e-3)
        self.assertAlmostEqual(thr, 0.5, delta=0.1)

    def test_small_merge_gap_splits_pulses(self):
        x = pulse_line(FS, 1.0, [(0.5, 0.55)])
        on, _, _ = detect_line_events(x, FS, DetectionSettings(merge_gap_ms=2.0))
        self.assertEqual(len(on), 10)  # 50 ms train at 5 ms period

    def test_active_low_line(self):
        x = pulse_line(FS, 2.0, [(1.0, 1.3)], pulse_ms=300, period_ms=1000, active_low=True)
        on, off, _ = detect_line_events(x, FS, DetectionSettings(active_low=True))
        self.assertEqual(len(on), 1)
        self.assertAlmostEqual(on[0], 1.0, delta=1e-3)
        self.assertAlmostEqual(off[0], 1.3, delta=1e-3)
        # Detected with the wrong polarity, the idle level is the "event"
        on_wrong, _, _ = detect_line_events(x, FS, DetectionSettings(active_low=False))
        self.assertNotEqual(len(on_wrong), 1)

    def test_min_duration_drops_short_events(self):
        x = pulse_line(FS, 2.0, [(0.5, 0.5005), (1.0, 1.2)], pulse_ms=0.5, period_ms=1000)
        x[int(1.0 * FS):int(1.2 * FS)] = 1.0
        on, _, _ = detect_line_events(x, FS, DetectionSettings(min_duration_ms=10.0))
        np.testing.assert_allclose(on, [1.0], atol=1e-3)

    def test_manual_threshold(self):
        x = pulse_line(FS, 1.0, [(0.5, 0.501)], period_ms=1000) * 0.3
        on, _, thr = detect_line_events(x, FS, DetectionSettings(threshold=0.2))
        self.assertEqual(len(on), 1)
        self.assertEqual(thr, 0.2)
        on, _, _ = detect_line_events(x, FS, DetectionSettings(threshold=0.5))
        self.assertEqual(len(on), 0)

    def test_flat_line_has_no_events(self):
        on, off, _ = detect_line_events(np.zeros(1000), FS, DetectionSettings())
        self.assertEqual(len(on), 0)

    def test_event_running_to_end(self):
        x = np.zeros(100)
        x[90:] = 1.0
        on, off = find_trains(x, 0.5, False, 5)
        self.assertEqual(list(on), [90])
        self.assertEqual(list(off), [99])

    def test_auto_threshold_midpoint(self):
        x = pulse_line(FS, 1.0, [(0.1, 0.9)])
        thr, span = auto_threshold(x, active_low=False)
        self.assertAlmostEqual(thr, 0.5)
        self.assertAlmostEqual(span, 1.0)


class TestEventsModel(unittest.TestCase):

    def test_numbering_per_modality(self):
        events = [
            StimEvent("PNS", 0, 5.0, 6.0, "ns5", 131, "ainp4"),
            StimEvent("ICMS", 0, 3.0, 3.05, "ns5", 129, "ainp2"),
            StimEvent("ICMS", 0, 1.0, 1.05, "ns5", 129, "ainp2"),
            StimEvent("PNS", 0, 2.0, 3.0, "ns5", 131, "ainp4"),
        ]
        numbered = number_events(events)
        self.assertEqual(
            [(e.modality, e.number, e.onset) for e in numbered],
            [("ICMS", 1, 1.0), ("ICMS", 2, 3.0), ("PNS", 1, 2.0), ("PNS", 2, 5.0)],
        )

    def test_analog_label_detection(self):
        self.assertTrue(is_analog_label("ainp2", 130))
        self.assertTrue(is_analog_label("AINP7"))
        self.assertTrue(is_analog_label("custom", 129))
        self.assertFalse(is_analog_label("elec1-63", 1))
        self.assertFalse(is_analog_label("elec1-63", 128))


class TestNeuralChannels(unittest.TestCase):

    def test_neural_channels_exclude_analog(self):
        state = StreamState("ns5", 10, FS, 5.0, analog_channels=[8, 9])
        self.assertEqual(state.neural_channels(), list(range(8)))

    def test_selection_moves_off_analog(self):
        state = StreamState("ns5", 4, FS, 5.0, analog_channels=[0])
        self.assertEqual(state.selected_channel, 1)
        self.assertEqual(state.overlay_channels, [1])
        state.overlay_channels = [1, 2, 3]
        state.selected_channel = 3
        state.set_analog_channels([3])
        self.assertEqual(state.selected_channel, 0)  # first electrode now that 0 isn't analog
        self.assertEqual(state.overlay_channels, [1, 2])


class TestSessionRoundTrip(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_assignments_and_events_saved(self):
        from tests.test_blackrock import write_nsx_23
        rng = np.random.default_rng(1)
        data = rng.integers(-500, 500, size=(3000, 4)).astype(np.int16)
        write_nsx_23(
            os.path.join(self.tmp, "rec.ns5"), [(0, data)],
            labels=["elec1", "elec2", "ainp1", "ainp2"],
        )

        app = AppState()
        app.load_block(self.tmp)
        state = app.stream_states["ns5"]
        self.assertEqual(state.analog_channels, [2, 3])
        self.assertEqual(state.neural_channels(), [0, 1])

        app.set_assignment("ns5", 2, AnalogAssignment(
            "ns5", 2, "Vibration", DetectionSettings(active_low=True, merge_gap_ms=5.0)))
        app.events = [StimEvent("Vibration", 1, 0.01, 0.02, "ns5", 2, "ainp1")]
        self.assertIn("Vibration", app.modality_names())

        session = os.path.join(self.tmp, "session.json")
        app.save_session(session)

        restored = AppState()
        restored.load_session(session)
        a = restored.analog_assignments[("ns5", 2)]
        self.assertEqual(a.modality, "Vibration")
        self.assertTrue(a.settings.active_low)
        self.assertEqual(a.settings.merge_gap_ms, 5.0)
        self.assertEqual(restored.events, app.events)
        self.assertIn("Vibration", restored.modality_names())

        # Editing the analog list drops assignments and events on removed channels
        restored.set_analog_channels("ns5", [3])
        self.assertEqual(restored.analog_assignments, {})
        self.assertEqual(restored.events, [])
        self.assertEqual(restored.stream_states["ns5"].neural_channels(), [0, 1, 2])


if __name__ == "__main__":
    unittest.main()
