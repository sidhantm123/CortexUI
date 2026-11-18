"""Tests for whole-recording spike detection."""
import sys
import os
import json
import tempfile
import unittest
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import remove_dir  # noqa: E402

from blackrock_io import BlackrockReader
from core.export import iter_processed_chunks
from core.spike_detect import freeze_sigma, detect_spikes
from dsp.spikes import robust_sigma, NEGATIVE
from models.blanking import BlankResult
from models.filter_config import FilterConfig, FilterType
from models.spikes import SpikeResult
from tests.test_blackrock import write_nsx_23

FS = 30000.0
N = 90000  # 3 s
CHUNK_N = 9000  # 0.3 s chunks in the tests
SPIKES = [1000, 8999, 18001, 39000, 45000, 60000, 75000]  # includes chunk joins (8999/9000, 18001)
# Channel 1: two spikes 1 ms apart straddling the 9000 chunk join (refractory must merge them)
PAIR = [8990, 9020]
BLANKED = 60000  # this spike falls inside a blank span


class TestSpikeDetect(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        rng = np.random.default_rng(7)
        data = rng.normal(0, 40, (N, 2))  # 40 counts = 10 µV noise
        for i in SPIKES:
            data[i:i + 15, 0] = -800  # -200 µV, 0.5 ms
        for i in PAIR:
            data[i:i + 5, 1] = -800
        write_nsx_23(os.path.join(cls.tmp, "rec.ns5"), [(0, np.clip(data, -32000, 32000).astype(np.int16))])
        cls.reader = BlackrockReader()
        cls.reader.load_block(cls.tmp)
        cls.blank = BlankResult("ICMS", "ns5", [0, 1], [1],
                                np.array([[1.99, 1.99]]), np.array([[2.01, 2.01]]))

    @classmethod
    def tearDownClass(cls):
        remove_dir(cls.tmp)

    def _detect(self, **kw):
        sigma = freeze_sigma(self.reader, "ns5", [0, 1], 0, 30000, FS, 2, kw.get("blank_results", ()), kw.get("filters"))
        times = detect_spikes(
            self.reader, "ns5", [0, 1], 0, N, FS, 2, 5.0 * sigma, NEGATIVE, 2.0,
            chunk_s=CHUNK_N / FS, pad_s=0.05, **kw,
        )
        return sigma, times

    def test_finds_injected_spikes_across_chunk_joins(self):
        sigma, times = self._detect()
        np.testing.assert_allclose(times[0] * FS, SPIKES, atol=1)
        np.testing.assert_allclose(times[1] * FS, [8990], atol=1)  # 9020 is within the 2 ms refractory
        self.assertAlmostEqual(sigma[0] * 1e6, 10.0, delta=0.6)

    def test_no_spikes_in_blanked_span(self):
        _, times = self._detect(blank_results=[self.blank])
        expected = [s for s in SPIKES if s != BLANKED]
        np.testing.assert_allclose(times[0] * FS, expected, atol=1)

    def test_with_filter_chain(self):
        filters = [FilterConfig(FilterType.BANDPASS, cutoff_low=250.0, cutoff_high=5000.0)]
        _, times = self._detect(filters=filters)
        # band-passing shifts the first crossing by a few samples at most
        self.assertEqual(len(times[0]), len(SPIKES))
        self.assertLess(np.abs(times[0] * FS - SPIKES).max(), 10)

    def test_frozen_sigma_matches_view(self):
        data, _ = self.reader.get_stream_data("ns5", channels=[0, 1], start_time=(30000 + .5) / FS,
                                              end_time=(60000 + .5) / FS)
        expected = robust_sigma(data)
        got = freeze_sigma(self.reader, "ns5", [0, 1], 30000, 60000, FS, 2)
        np.testing.assert_allclose(got, expected)

    def test_chunked_sigma_matches_one_chunk(self):
        bp = FilterConfig(FilterType.BANDPASS, cutoff_low=250.0, cutoff_high=5000.0)
        car = FilterConfig(FilterType.CAR, channel_groups=[[0, 1]])
        i0, i1 = 45000, 75000  # holds the blank span at 2 s
        for blank, filters in (((), [bp]), ((), [bp, car]), ([self.blank], [bp])):
            *_, data, mask = next(iter_processed_chunks(
                self.reader, "ns5", [0, 1], i0, i1, FS, 2, blank, filters,
                chunk_s=(i1 - i0) / FS + 1.0, with_mask=True,
            ))
            got = freeze_sigma(self.reader, "ns5", [0, 1], i0, i1, FS, 2, blank, filters, chunk_s=0.3)
            np.testing.assert_allclose(got, robust_sigma(data, mask), rtol=1e-5)

    def test_cancelled_freeze_returns_none(self):
        self.assertIsNone(freeze_sigma(self.reader, "ns5", [0, 1], 0, 30000, FS, 2, cancelled=lambda: True))

    def test_refractory_off_keeps_both(self):
        sigma = freeze_sigma(self.reader, "ns5", [1], 0, 30000, FS, 2)
        times = detect_spikes(self.reader, "ns5", [1], 0, 18000, FS, 2, 5.0 * sigma, NEGATIVE,
                              0.0, chunk_s=CHUNK_N / FS, pad_s=0.05)
        np.testing.assert_allclose(times[1] * FS, PAIR, atol=1)

    def test_result_round_trip(self):
        res = SpikeResult("ns5", FS, [0, 1], {0: np.array([0.1, 0.2]), 1: np.zeros(0)},
                          {0: 1e-5, 1: float("nan")}, 4.75, NEGATIVE, 2.0, (1.0, 2.0), "BP", 3.0)
        back = SpikeResult.from_dict(json.loads(json.dumps(res.to_dict())))
        np.testing.assert_allclose(back.times[0], [0.1, 0.2])
        self.assertTrue(np.isnan(back.sigma[1]))
        self.assertEqual((back.total(), back.rate(0)), (2, 2 / 3.0))


if __name__ == "__main__":
    unittest.main()
