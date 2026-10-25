"""Tests for chunked int16 binary export."""
import sys
import os
import shutil
import tempfile
import unittest
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from blackrock_io import BlackrockReader
from core.export import export_binary, chunk_bounds, blank_sample_spans
from models.blanking import BlankResult
from models.filter_config import FilterConfig, FilterType
from tests.test_blackrock import write_nsx_23

FS = 30000.0
N = 60000  # 2 s
N_CH = 4
SCALE = 1e6  # volts -> µV


class TestExport(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        rng = np.random.default_rng(3)
        t = np.arange(N) / FS
        data = (200 * np.sin(2 * np.pi * 5 * t)[:, None] + rng.normal(0, 40, (N, N_CH)))
        data[30000:31500] += 900  # an "artifact" at 1.0-1.05 s
        write_nsx_23(os.path.join(cls.tmp, "rec.ns5"), [(0, data.astype(np.int16))])
        cls.reader = BlackrockReader()
        cls.reader.load_block(cls.tmp)
        cls.blank = BlankResult(
            "ICMS", "ns5", list(range(N_CH)), [1],
            np.full((1, N_CH), 0.999), np.array([[1.06, 1.10, 1.06, 1.20]]),
        )

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def _export(self, name, layout="interlaced", channels=(0, 1, 2, 3), i0=0, i1=N, **kw):
        path = os.path.join(self.tmp, name)
        res = export_binary(self.reader, "ns5", list(channels), i0, i1, FS, N_CH, path,
                            SCALE, layout=layout, **kw)
        raw = np.fromfile(path, dtype=np.int16)
        n_ch = len(channels)
        arr = raw.reshape(-1, n_ch).T if layout == "interlaced" else raw.reshape(n_ch, -1)
        return arr, res

    def test_raw_matches_reader_exactly(self):
        arr, res = self._export("raw.bin")
        data, _ = self.reader.get_stream_data("ns5")
        np.testing.assert_array_equal(arr, np.rint(data * SCALE).astype(np.int16))
        self.assertEqual(res["n_samples"], N)

    def test_exact_sample_range_and_sequential_layout(self):
        arr, _ = self._export("view.bin", layout="sequential", channels=(2, 0), i0=12345, i1=23456)
        self.assertEqual(arr.shape, (2, 23456 - 12345))
        data, _ = self.reader.get_stream_data("ns5", channels=[2, 0])
        np.testing.assert_array_equal(arr, np.rint(data[:, 12345:23456] * SCALE).astype(np.int16))

    def test_chunked_equals_single_chunk(self):
        filters = [
            FilterConfig(FilterType.CAR, channel_groups=[[0, 1, 2, 3]]),
            FilterConfig(FilterType.HIGHPASS, cutoff_low=300.0),
        ]
        for name, kw in [
            ("raw", {}),
            ("blanked", {"blank_results": [self.blank]}),
            ("blanked+filtered", {"blank_results": [self.blank], "filters": filters}),
        ]:
            whole, _ = self._export(f"{name}_whole.bin", chunk_s=100.0, **kw)
            chunked, _ = self._export(f"{name}_chunked.bin", chunk_s=0.3, pad_s=0.2, **kw)
            # 1 count = 1 µV; filtered chunk joins may differ by rounding only
            self.assertLessEqual(np.abs(whole.astype(int) - chunked.astype(int)).max(), 1, name)

    def test_blanked_interval_is_interpolated(self):
        arr, _ = self._export("blank.bin", blank_results=[self.blank])
        # Channel 0 is blanked over samples [29970, 31801): straight line between the neighbours
        seg = arr[0, 29970:31801].astype(float)
        line = np.linspace(arr[0, 29969], arr[0, 31801], 31801 - 29969 + 1)[1:-1]
        np.testing.assert_allclose(seg, line, atol=1)

    def test_clipping_counted(self):
        path = os.path.join(self.tmp, "clip.bin")
        res = export_binary(self.reader, "ns5", [0], 0, N, FS, N_CH, path, 1e9)
        self.assertGreater(res["n_clipped"], 0)

    def test_chunk_boundary_moves_out_of_blank(self):
        bounds = chunk_bounds(0, 100, 30, avoid=[(25, 40)])
        self.assertEqual(bounds[0], (0, 40))
        for a, b in bounds:
            self.assertFalse(25 < b < 40)
        self.assertEqual(bounds[-1][1], 100)
        spans = blank_sample_spans([self.blank], FS)
        m = 6  # interpolation anchor margin
        self.assertEqual(spans, [(int(np.floor(0.999 * FS)) - m, int(np.ceil(1.20 * FS)) + 1 + m)])


if __name__ == "__main__":
    unittest.main()
