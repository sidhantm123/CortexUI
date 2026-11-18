"""Opening recordings and sessions: a failure leaves the current state untouched."""
import json
import os
import shutil
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from blackrock_io.blackrock_reader import NEO_AVAILABLE
from core.app_state import AppState
from models.filter_config import FilterConfig, FilterType
from models.probe import ProbeContact, ProbeGeometry
from tests.test_blackrock import write_nsx_23


@unittest.skipUnless(NEO_AVAILABLE, "neo not installed")
class TestAtomicLoads(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.rec = os.path.join(self.tmp, "rec")
        os.mkdir(self.rec)
        data = np.random.default_rng(1).integers(-100, 100, size=(3000, 3)).astype(np.int16)
        write_nsx_23(os.path.join(self.rec, "rec.ns5"), [(0, data)])
        self.app = AppState()
        self.app.load_block(self.rec)
        self.state = self.app.stream_states["ns5"]
        self.state.filters = [FilterConfig(FilterType.HIGHPASS, cutoff_low=300.0)]
        self.session = os.path.join(self.tmp, "s.json")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _unchanged(self, block, reader):
        self.assertIs(self.app.block, block)
        self.assertIs(self.app.reader, reader)
        self.assertIs(self.app.stream_states["ns5"], self.state)
        self.assertEqual(self.app.current_stream, "ns5")
        data, _ = self.app.reader.get_stream_data("ns5", [0], 0.0, 0.05)
        self.assertEqual(data.shape, (1, 1500))

    def test_failed_open_keeps_the_current_recording(self):
        block, reader = self.app.block, self.app.reader
        empty = os.path.join(self.tmp, "empty")
        os.mkdir(empty)
        with self.assertRaises(Exception):
            self.app.load_block(empty)
        self._unchanged(block, reader)

    def test_probe_survives_opening_a_recording(self):
        probe = ProbeGeometry([ProbeContact(0, "1", 0.0, 0.0, "0", 10.0)])
        self.app.set_probe(probe)
        self.app.load_block(self.rec)
        self.assertIs(self.app.probe, probe)

    def test_unknown_and_invalid_filters_are_skipped_with_a_note(self):
        self.app.save_session(self.session)
        with open(self.session) as f:
            d = json.load(f)
        d["stream_states"]["ns5"]["filters"] = [
            {"filter_type": "wavelet_denoise", "level": 3},  # from some other version
            {"filter_type": "car", "channel_groups": [[0]]},  # invalid: a group needs 2 channels
            {"filter_type": "highpass", "cutoff_low": 250.0},
        ]
        with open(self.session, "w") as f:
            json.dump(d, f)
        restored = AppState()
        restored.load_session(self.session)
        self.assertEqual([f.filter_type for f in restored.stream_states["ns5"].filters], [FilterType.HIGHPASS])
        self.assertEqual(len(restored.load_messages), 1)
        self.assertIn("wavelet_denoise", restored.load_messages[0])
        self.assertIn("car", restored.load_messages[0])

    def test_corrupt_session_changes_nothing(self):
        self.app.save_session(self.session)
        with open(self.session) as f:
            d = json.load(f)
        d["events"] = [{"modality": "ICMS"}]  # missing required fields
        with open(self.session, "w") as f:
            json.dump(d, f)
        block, reader = self.app.block, self.app.reader
        with self.assertRaises(Exception):
            self.app.load_session(self.session)
        self._unchanged(block, reader)
        self.assertEqual(self.state.filters[0].cutoff_low, 300.0)

    def test_frequency_range_is_saved(self):
        self.state.fft_fmin, self.state.fft_fmax = 55.0, 65.0
        self.app.save_session(self.session)
        restored = AppState()
        restored.load_session(self.session)
        st = restored.stream_states["ns5"]
        self.assertEqual((st.fft_fmin, st.fft_fmax), (55.0, 65.0))

    def test_save_is_atomic(self):
        self.app.save_session(self.session)
        with open(self.session) as f:
            good = f.read()
        self.assertFalse(os.path.exists(self.session + ".tmp"))
        self.app.custom_modalities.append(object())  # can't be written as JSON
        with self.assertRaises(TypeError):
            self.app.save_session(self.session)
        with open(self.session) as f:
            self.assertEqual(f.read(), good)  # the previous session is intact
        self.assertFalse(os.path.exists(self.session + ".tmp"))


if __name__ == "__main__":
    unittest.main()
