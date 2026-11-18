"""Tests for analog channels and stimulation event detection."""
import sys
import os
import json
import tempfile
import unittest
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import remove_dir  # noqa: E402

from dsp.events import auto_threshold, detect_line_events, find_trains
from models import StreamState, DetectionSettings, AnalogAssignment, StimEvent
from models.analog import number_events, is_analog_label
from core.app_state import AppState
from models.blanking import BlankingSettings, BlankResult
from models.filter_config import FilterConfig, FilterType

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
        self.assertFalse(is_analog_label("elec2-10", 202))  # 256-channel system: still an electrode
        self.assertFalse(is_analog_label("chan140", 140))
        self.assertTrue(is_analog_label("", 140))


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
        remove_dir(self.tmp)

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


class TestEventEditing(unittest.TestCase):

    def setUp(self):
        self.app = AppState()
        self.app.stream_states["ns5"] = StreamState("ns5", 4, FS, 100.0, analog_channels=[3])
        self.app.events = number_events([
            StimEvent("ICMS", 0, 10.0, 11.0, "ns5", 3, "ainp1"),
            StimEvent("ICMS", 0, 20.0, 21.0, "ns5", 3, "ainp1"),
        ])

    def test_edit_rounds_to_sample_and_marks_edited(self):
        e = self.app.events[0]
        self.assertIsNone(self.app.set_event_times(e, 10.123456789, e.offset))
        self.assertAlmostEqual(e.onset * FS, round(e.onset * FS), places=6)
        self.assertTrue(e.onset_edited)
        self.assertFalse(e.offset_edited)
        self.assertEqual(e.detected_onset, 10.0)
        self.assertEqual(self.app.edited_event_count(), 1)

    def test_invalid_edits_rejected(self):
        e = self.app.events[0]
        self.assertIsNotNone(self.app.set_event_times(e, 11.5, 11.0))  # onset after offset
        self.assertIsNotNone(self.app.set_event_times(e, -1.0, 11.0))
        self.assertIsNotNone(self.app.set_event_times(e, 10.0, 200.0))  # past the end
        self.assertEqual((e.onset, e.offset), (10.0, 11.0))
        self.assertFalse(e.edited)

    def test_moving_past_neighbour_renumbers(self):
        first = self.app.events[0]
        self.app.set_event_times(first, 25.0, 26.0)
        self.assertEqual([e.onset for e in self.app.events], [20.0, 25.0])
        self.assertEqual(first.number, 2)
        self.assertIs(self.app.events[1], first)

    def test_reset(self):
        e = self.app.events[0]
        self.app.set_event_times(e, 9.5, 11.5)
        self.app.reset_event_times(e)
        self.assertEqual((e.onset, e.offset), (10.0, 11.0))
        self.assertEqual(self.app.edited_event_count(), 0)

    def test_nan_and_inf_rejected(self):
        e = self.app.events[0]
        for bad in (float("nan"), float("inf")):
            self.assertEqual(self.app.set_event_times(e, bad, e.offset), "Enter a time in seconds")
        self.assertEqual((e.onset, e.offset), (10.0, 11.0))

    def test_excluded_trials_follow_their_events(self):
        self.app.blanking_settings["ICMS"] = BlankingSettings(excluded_trials=[1])
        first = self.app.events[0]
        self.app.set_event_times(first, 25.0, 26.0)  # now trial 2
        self.assertEqual(first.number, 2)
        self.assertEqual(self.app.blanking_settings["ICMS"].excluded_trials, [2])
        self.app.reset_event_times(first)
        self.assertEqual(self.app.blanking_settings["ICMS"].excluded_trials, [1])

    def test_old_session_event_without_detected_times(self):
        e = StimEvent.from_dict(
            {"modality": "PNS", "number": 1, "onset": 1.0, "offset": 2.0,
             "stream": "ns5", "channel": 3, "label": "ainp1"}
        )
        self.assertEqual((e.detected_onset, e.detected_offset), (1.0, 2.0))
        self.assertFalse(e.edited)

    def test_edited_times_survive_serialization(self):
        e = self.app.events[0]
        self.app.set_event_times(e, 9.5, 11.0)
        restored = StimEvent.from_dict(json.loads(json.dumps(e.to_dict())))
        self.assertEqual(restored.onset, 9.5)
        self.assertEqual(restored.detected_onset, 10.0)
        self.assertTrue(restored.onset_edited)


class TestAssignmentConsistency(unittest.TestCase):
    """Events, blanking and CAR groups follow assignment and analog-list changes."""

    def setUp(self):
        self.app = AppState()
        self.state = StreamState("ns5", 6, FS, 100.0, analog_channels=[4, 5])
        self.app.stream_states["ns5"] = self.state
        self.app.set_assignment("ns5", 4, AnalogAssignment("ns5", 4, "ICMS"))
        self.app.set_assignment("ns5", 5, AnalogAssignment("ns5", 5, "PNS"))
        self.app.events = number_events([
            StimEvent("ICMS", 0, 10.0, 11.0, "ns5", 4, "ainp1"),
            StimEvent("ICMS", 0, 20.0, 21.0, "ns5", 4, "ainp1"),
            StimEvent("PNS", 0, 30.0, 31.0, "ns5", 5, "ainp2"),
        ])
        for m in ("ICMS", "PNS"):
            self.app.blanking_results[m] = BlankResult(m, "ns5", [0, 1, 2, 3], [1], np.zeros((1, 4)), np.ones((1, 4)))

    def test_unassigning_drops_its_events_and_blanking(self):
        self.assertTrue(self.app.set_assignment("ns5", 4, None))
        self.assertEqual([e.modality for e in self.app.events], ["PNS"])
        self.assertNotIn("ICMS", self.app.blanking_results)
        self.assertIn("PNS", self.app.blanking_results)

    def test_changing_modality_drops_the_old_events(self):
        self.assertTrue(self.app.set_assignment("ns5", 4, AnalogAssignment("ns5", 4, "Tactor")))
        self.assertFalse(any(e.modality == "ICMS" for e in self.app.events))

    def test_same_modality_keeps_events(self):
        new = AnalogAssignment("ns5", 4, "ICMS", DetectionSettings(merge_gap_ms=50.0))
        self.assertFalse(self.app.set_assignment("ns5", 4, new))
        self.assertEqual(len(self.app.events), 3)
        self.assertFalse(self.app.blanking_results["ICMS"].stale)

    def test_a_modality_keeping_some_events_goes_stale(self):
        self.app.set_assignment("ns5", 5, None)  # the PNS line
        self.app.set_assignment("ns5", 5, AnalogAssignment("ns5", 5, "ICMS"))
        self.app.events.append(StimEvent("ICMS", 3, 40.0, 41.0, "ns5", 5, "ainp2"))
        self.assertFalse(self.app.set_assignment("ns5", 5, None))
        self.assertEqual(len(self.app.events), 2)
        self.assertTrue(self.app.blanking_results["ICMS"].stale)

    def test_analog_list_edit_cleans_events_and_car_groups(self):
        self.state.filters = [FilterConfig(FilterType.CAR, channel_groups=[[0, 1, 2], [3, 4]])]
        removed = self.app.set_analog_channels("ns5", [3, 5])  # 4 is an electrode now, 3 analog
        self.assertTrue(removed)  # ICMS lost its line and its events
        self.assertEqual([e.modality for e in self.app.events], ["PNS"])
        self.assertEqual(self.state.filters[0].channel_groups, [[0, 1, 2], [4]])


if __name__ == "__main__":
    unittest.main()
