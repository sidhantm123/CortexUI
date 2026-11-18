"""Tests for the sliding plot buffer (core/view_buffer.py) and the direct Blackrock reads it relies on."""
import os
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import remove_dir  # noqa: E402

from blackrock_io import BlackrockReader
from blackrock_io.blackrock_reader import NEO_AVAILABLE
from core.export import _t
from core.view_buffer import missing_pieces, process_buffer, stitch, window_target
from core.workers import BufferLoadWorker
from dsp import apply_filter_chain
from dsp.blanking import apply_blanking, blank_mask
from models.blanking import BlankResult
from models.filter_config import FilterConfig, FilterType
from tests.test_blackrock import write_nsx_23

FS = 30000.0


class TestWindow(unittest.TestCase):

    def test_window_target_clamps_and_takes_two_margins(self):
        self.assertEqual(window_target(100, 200, 1000, 50), (50, 250))
        self.assertEqual(window_target(100, 200, 1000, 10, 300), (90, 500))
        self.assertEqual(window_target(20, 200, 220, 50), (0, 220))

    def test_missing_pieces(self):
        self.assertEqual(missing_pieces(None, (0, 10)), [(0, 10)])
        self.assertEqual(missing_pieces((0, 10), (2, 8)), [])
        self.assertEqual(missing_pieces((5, 10), (0, 15)), [(0, 5), (10, 15)])
        self.assertEqual(missing_pieces((5, 10), (7, 15)), [(10, 15)])
        self.assertEqual(missing_pieces((5, 10), (0, 7)), [(0, 5)])
        self.assertEqual(missing_pieces((20, 30), (0, 10)), [(0, 10)])  # no overlap: read it all

    def test_stitch_old_overlap_and_new_pieces(self):
        full = np.arange(2 * 100, dtype=np.float32).reshape(2, 100)
        old = full[:, 30:60].copy()
        pieces = [((10, 30), full[:, 10:30]), ((60, 80), full[:, 60:80])]
        out = stitch(old, (30, 60), pieces, (10, 80))
        np.testing.assert_array_equal(out, full[:, 10:80])
        self.assertFalse(np.shares_memory(out, old))  # trimmed copies don't keep the old buffer alive
        np.testing.assert_array_equal(stitch(old, (30, 60), [], (35, 50)), full[:, 35:50])

    def test_stitch_rejects_gaps(self):
        full = np.zeros((1, 100), np.float32)
        with self.assertRaises(ValueError):
            stitch(None, None, [((0, 10), full[:, :10]), ((12, 20), full[:, 12:20])], (0, 20))

    def test_process_buffer_is_blank_then_chain(self):
        rng = np.random.default_rng(0)
        t = np.arange(30000) / FS
        raw = (rng.standard_normal((2, t.size)) * 1e-5).astype(np.float32)
        res = BlankResult("ICMS", "ns5", [3, 7], [1], np.array([[0.4, 0.4]]), np.array([[0.45, 0.5]]))
        bp = FilterConfig(FilterType.BANDPASS, cutoff_low=250, cutoff_high=5000)
        out, mask, warnings = process_buffer(raw, None, t, [3, 7], 8, [bp], False, FS, [res])
        expect, _, _ = apply_filter_chain(apply_blanking(raw, t, [3, 7], [res]), [bp], FS, channel_indices=[3, 7])
        self.assertEqual(out.dtype, np.float32)
        np.testing.assert_allclose(out, expect, rtol=1e-5, atol=1e-12)
        np.testing.assert_array_equal(mask, blank_mask(t, [3, 7], [res]))
        self.assertEqual(warnings, [])
        raw_before = raw.copy()
        process_buffer(raw, None, t, [3, 7], 8, [bp], False, FS, [])
        np.testing.assert_array_equal(raw, raw_before)  # the raw buffer is never modified


@unittest.skipUnless(NEO_AVAILABLE, "neo not installed")
class TestBufferLoads(unittest.TestCase):
    """Stitched incremental loads equal one direct read; direct file reads equal neo's."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        rng = np.random.default_rng(5)
        cls.a = rng.integers(-1000, 1000, (60000, 5), dtype=np.int16)
        cls.b = rng.integers(-1000, 1000, (20000, 5), dtype=np.int16)
        write_nsx_23(os.path.join(cls.tmp, "rec.ns5"), [(0, cls.a), (100_000_000, cls.b)])  # two segments
        cls.reader = BlackrockReader()
        cls.block = cls.reader.load_block(cls.tmp)
        cls.stream = sorted(cls.block.streams)[0]

    @classmethod
    def tearDownClass(cls):
        remove_dir(cls.tmp)

    def _run(self, worker):
        out = {}
        worker.signals.result.connect(lambda r: out.setdefault("r", r))
        worker.signals.error.connect(lambda e: out.setdefault("e", e))
        worker.run()
        self.assertNotIn("e", out, out.get("e"))
        return out["r"]

    def test_direct_reads_match_neo(self):
        for name, counts in zip(sorted(self.block.streams), (self.a, self.b)):
            stream = self.reader._streams[name]
            self.assertIsNotNone(stream.file_layout())
            for i0, i1, chans in ((0, counts.shape[0], [0, 1, 2, 3, 4]), (123, 4567, [4, 1]), (999, 1000, [2])):
                neo = stream.rawio.get_analogsignal_chunk(0, stream.seg_index, i0, i1, 0, chans)
                np.testing.assert_array_equal(stream.read_counts(i0, i1, chans), neo)
                np.testing.assert_array_equal(neo, counts[i0:i1][:, chans])

    def test_incremental_loads_equal_one_read(self):
        chans = [1, 3]
        direct, _ = self.reader.get_stream_data(self.stream, chans, _t(5000, FS), _t(50000, FS), dtype=np.float32)
        first = self._run(BufferLoadWorker(self.reader, self.stream, chans, (20000, 30000), FS))
        grown = self._run(BufferLoadWorker(self.reader, self.stream, chans, (5000, 50000), FS,
                                           old=first["data"], old_range=first["range"]))
        self.assertEqual(grown["range"], (5000, 50000))
        np.testing.assert_array_equal(grown["data"], direct)
        np.testing.assert_allclose(grown["time"], (5000 + np.arange(45000)) / FS)
        moved = self._run(BufferLoadWorker(self.reader, self.stream, chans, (40000, 60000), FS,
                                           old=grown["data"], old_range=grown["range"]))
        expect, _ = self.reader.get_stream_data(self.stream, chans, _t(40000, FS), _t(60000, FS), dtype=np.float32)
        np.testing.assert_array_equal(moved["data"], expect)

    def test_worker_processes_with_all_channel_reference(self):
        car = FilterConfig(FilterType.CAR, channel_groups=[[0, 1, 2, 3]])
        hp = FilterConfig(FilterType.HIGHPASS, cutoff_low=300.0)
        process = {"rows": [2], "all_channels": True, "channels": [2], "num_channels": 5,
                   "filters": [hp, car], "global_bypass": False, "blank_results": []}
        r = self._run(BufferLoadWorker(self.reader, self.stream, list(range(5)), (0, 30000), FS, process=process))
        data, _ = self.reader.get_stream_data(self.stream, list(range(5)), _t(0, FS), _t(30000, FS))
        expect, _, _ = apply_filter_chain(data[[2]], [hp, car], FS, all_channel_data=data, channel_indices=[2])
        np.testing.assert_allclose(r["processed"], expect, rtol=1e-4, atol=1e-9)


if __name__ == "__main__":
    unittest.main()
