"""Tests for spike threshold crossings."""
import sys
import os
import json
import unittest
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dsp.spikes import robust_sigma, detect_crossings, align_to_peak, NEGATIVE, POSITIVE, BOTH
from models import StreamState
from models.spikes import SpikeSettings


class TestRobustSigma(unittest.TestCase):

    def test_gaussian_noise(self):
        x = np.random.default_rng(0).normal(0, 2.0, (2, 200000))
        np.testing.assert_allclose(robust_sigma(x), [2.0, 2.0], rtol=0.02)

    def test_spikes_barely_move_it(self):
        x = np.random.default_rng(1).normal(0, 1.0, 100000)
        x[::500] = -20  # 0.2% large spikes
        self.assertAlmostEqual(robust_sigma(x)[0], 1.0, delta=0.03)
        self.assertGreater(np.std(x), 1.3)  # plain RMS is inflated

    def test_mask_excludes_samples(self):
        x = np.r_[np.ones(100), 100 * np.ones(100)][None, :]
        mask = np.zeros_like(x, dtype=bool)
        mask[0, 100:] = True
        self.assertAlmostEqual(robust_sigma(x, mask)[0], 1 / 0.6745)
        self.assertTrue(np.isnan(robust_sigma(x, np.ones_like(mask))[0]))


class TestCrossings(unittest.TestCase):

    def setUp(self):
        self.x = np.zeros((1, 100))
        for i in (10, 14, 40, 70):
            self.x[0, i:i + 2] = -5
        self.x[0, 55:57] = 5

    def test_polarity(self):
        self.assertEqual(list(detect_crossings(self.x, [3], NEGATIVE)[0]), [10, 14, 40, 70])
        self.assertEqual(list(detect_crossings(self.x, [3], POSITIVE)[0]), [55])
        self.assertEqual(list(detect_crossings(self.x, [3], BOTH)[0]), [10, 14, 40, 55, 70])

    def test_refractory(self):
        self.assertEqual(list(detect_crossings(self.x, [3], NEGATIVE, refractory_n=5)[0]), [10, 40, 70])  # 14 is 4 samples after 10

    def test_refractory_carries_across_chunks(self):
        # previous chunk's last spike was 3 samples before this chunk's start
        got = detect_crossings(self.x, [3], NEGATIVE, refractory_n=18, last=[-3])  # 10 and 14 fall within 18 of -3
        self.assertEqual(list(got[0]), [40, 70])

    def test_blanked_samples_excluded(self):
        mask = np.zeros_like(self.x, dtype=bool)
        mask[0, 35:45] = True
        self.assertEqual(list(detect_crossings(self.x, [3], NEGATIVE, exclude=mask)[0]), [10, 14, 70])

    def test_nan_threshold_means_none(self):
        self.assertEqual(len(detect_crossings(self.x, [np.nan])[0]), 0)

    def test_align_to_peak(self):
        row = np.zeros(20)
        row[5:9] = [-4, -7, -9, -3]
        self.assertEqual(list(align_to_peak(row, np.array([5]), NEGATIVE, 10)), [7])
        self.assertEqual(list(align_to_peak(-row, np.array([5]), POSITIVE, 10)), [7])


class TestSettings(unittest.TestCase):

    def test_round_trip_and_defaults(self):
        st = StreamState("ns5", 4, 30000.0, 10.0)
        self.assertEqual(st.spike_settings.k, 4.75)
        st.spike_settings.k, st.spike_settings.polarity = 6.0, BOTH
        restored = StreamState.from_dict(json.loads(json.dumps(st.to_dict())))
        self.assertEqual((restored.spike_settings.k, restored.spike_settings.polarity), (6.0, BOTH))
        old = st.to_dict()
        del old["spike_settings"]
        self.assertEqual(StreamState.from_dict(old).spike_settings, SpikeSettings())


if __name__ == "__main__":
    unittest.main()
