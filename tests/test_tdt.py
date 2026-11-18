"""TDT reader on synthetic blocks (a .tsq index and .tev data written to the format tdt reads)."""
import os
import sys
import tempfile
import unittest
import warnings

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import remove_dir  # noqa: E402

from tdt_io.tdt_reader import TDT_AVAILABLE, TDTReader

FS = 24414.0625  # a typical TDT rate: n / FS * FS is often not n
TSQ = np.dtype([("size", "<u4"), ("type", "<u4"), ("code", "<u4"), ("chan", "<u2"), ("sort", "<u2"),
                ("ts", "<f8"), ("offset", "<u8"), ("format", "<u4"), ("freq", "<f4")])
EVTYPE_MARK, EVTYPE_STREAM = 0x8801, 0x8101
DFORM = {np.dtype("<f4"): 0, np.dtype("<i2"): 2}


def write_tdt_block(folder, stores, fs, per_record=256, t0=1.7e9):
    """
    stores: {4-char name: (channels, samples) array, float32 or int16}. Records of all stores
    are interleaved in time order, one per channel and time block, as a recording writes them.
    """
    os.makedirs(folder, exist_ok=True)
    name = os.path.basename(folder)
    recs = [(0, 0, 0, 0, 0, 0.0, 0, 0, 0.0), (10, EVTYPE_MARK, 1, 0, 0, t0, 0, 0, 0.0)]
    tev = bytearray()
    n = max(d.shape[1] for d in stores.values())
    for k0 in range(0, n, per_record):
        for store, data in stores.items():
            npts = per_record * 4 // data.dtype.itemsize if data.dtype.itemsize < 4 else per_record
            if k0 % npts:
                continue
            for c in range(data.shape[0]):
                block = data[c, k0:k0 + npts]
                if block.size < npts:
                    continue
                off = len(tev)
                tev += block.tobytes()
                recs.append((10 + block.nbytes // 4, EVTYPE_STREAM, int.from_bytes(store.encode(), "little"),
                             c + 1, 0, t0 + k0 / fs, off, DFORM[data.dtype], fs))
    recs.append((10, EVTYPE_MARK, 2, 0, 0, t0 + n / fs, 0, 0, 0.0))
    np.array(recs, dtype=TSQ).tofile(os.path.join(folder, name + ".tsq"))
    with open(os.path.join(folder, name + ".tev"), "wb") as f:
        f.write(tev)


@unittest.skipUnless(TDT_AVAILABLE, "tdt not installed")
class TestTDTReader(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.folder = os.path.join(cls.tmp, "Block-1")
        rng = np.random.default_rng(4)
        cls.wav = rng.standard_normal((3, 256 * 300)).astype("<f4")  # 3.1 s
        cls.lfp = rng.integers(-3000, 3000, (1, 512 * 150)).astype("<i2")
        write_tdt_block(cls.folder, {"Wav1": cls.wav, "LFP1": cls.lfp}, FS)
        cls.reader = TDTReader()
        cls.block = cls.reader.load_block(cls.folder)

    @classmethod
    def tearDownClass(cls):
        remove_dir(cls.tmp)

    def test_synthetic_block_matches_tdt_itself(self):
        import tdt
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            d = tdt.read_block(self.folder)
        np.testing.assert_array_equal(d.streams.Wav1.data, self.wav)
        np.testing.assert_array_equal(d.streams.LFP1.data, self.lfp[0])

    def test_metadata(self):
        wav, lfp = self.block.streams["Wav1"], self.block.streams["LFP1"]
        self.assertEqual((wav.num_channels, wav.num_samples, wav.sample_rate), (3, self.wav.shape[1], FS))
        self.assertEqual((lfp.num_channels, lfp.num_samples), (1, self.lfp.shape[1]))
        self.assertAlmostEqual(wav.duration, self.wav.shape[1] / FS)

    def test_windows_are_sample_exact(self):
        n = self.wav.shape[1]
        for i0, i1 in ((0, 1000), (12345, 23456), (255, 257), (n - 300, n), (100, 100)):
            for t0, t1 in (((i0 + 0.5) / FS, (i1 + 0.5) / FS), (i0 / FS, i1 / FS)):
                data, t = self.reader.get_stream_data("Wav1", [2, 0], t0, t1)
                np.testing.assert_array_equal(data, self.wav[[2, 0], i0:i1])
                np.testing.assert_allclose(t, (i0 + np.arange(i1 - i0)) / FS)
        data, _ = self.reader.get_stream_data("LFP1", None, (777 + 0.5) / FS, (5000 + 0.5) / FS, dtype=np.float32)
        np.testing.assert_array_equal(data, self.lfp[:, 777:5000].astype(np.float32))

    def test_chunked_reads_equal_one_read(self):
        from core.export import iter_processed_chunks
        whole, _ = self.reader.get_stream_data("Wav1", [0, 1])
        parts = [out for *_, out, _ in iter_processed_chunks(
            self.reader, "Wav1", [0, 1], 0, self.wav.shape[1], FS, 3, chunk_s=0.37)]
        np.testing.assert_array_equal(np.concatenate(parts, axis=1), whole)
        np.testing.assert_array_equal(whole, self.wav[:2])

    def test_reads_do_not_reparse_the_block(self):
        import tdt
        calls = []
        orig = tdt.read_block
        tdt.read_block = lambda *a, **k: calls.append(k) or orig(*a, **k)
        try:
            for i in range(5):
                self.reader.get_stream_data("Wav1", [1], i * 0.1, i * 0.1 + 0.05)
        finally:
            tdt.read_block = orig
        self.assertEqual(calls, [])
        self.assertIsNone(getattr(self.reader, "_raw_data", None))  # no whole-block copy

    def test_past_the_end_is_clamped(self):
        n = self.wav.shape[1]
        data, _ = self.reader.get_stream_data("Wav1", [0], (n - 10 + 0.5) / FS, n / FS + 5.0)
        np.testing.assert_array_equal(data, self.wav[:1, n - 10:])


if __name__ == "__main__":
    unittest.main()
