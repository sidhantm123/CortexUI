"""Long-job workers: a job cancelled during its last read reports cancelled, never a result."""
import os
import shutil
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from blackrock_io import BlackrockReader
from blackrock_io.blackrock_reader import NEO_AVAILABLE
from core.workers import BlankingWorker, EventDetectWorker, ExportWorker, SpikeDetectWorker
from models.analog import AnalogAssignment, DetectionSettings
from models.blanking import ADVANCED, BlankingSettings
from models.spikes import SpikeSettings
from tests.test_blackrock import write_nsx_23

FS = 30000.0
N_CH = 4
EVENTS = ((10.0, 10.5), (20.0, 20.5))  # on the analog line, channel 3


class CountingReader:
    """Wraps a reader: counts reads, cancels the worker right after read number `cancel_at`
    and raises on read number `fail_at`."""

    def __init__(self, reader, cancel_at=None, fail_at=None):
        self.reader = reader
        self.cancel_at = cancel_at
        self.fail_at = fail_at
        self.calls = 0
        self.worker = None

    def get_stream_data(self, *args, **kwargs):
        self.calls += 1
        if self.calls == self.fail_at:
            raise OSError("disk went away")
        out = self.reader.get_stream_data(*args, **kwargs)
        if self.calls == self.cancel_at:
            self.worker.cancel()
        return out


@unittest.skipUnless(NEO_AVAILABLE, "neo not installed")
class TestCancelAfterLastRead(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        rng = np.random.default_rng(3)
        n = int(30 * FS)
        counts = rng.integers(-40, 40, (n, N_CH), dtype=np.int16)
        counts[:, 3] = 0
        for on, off in EVENTS:
            counts[int(on * FS):int(off * FS), 3] = 1000
        write_nsx_23(os.path.join(cls.tmp, "rec.ns5"), [(0, counts)])
        cls.reader = BlackrockReader()
        block = cls.reader.load_block(cls.tmp)
        cls.stream = sorted(block.streams)[0]
        cls.n = n

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def _run(self, make, cancel_at=None, fail_at=None):
        """Run make(reader) synchronously; returns (emitted signal names, result, reads)."""
        reader = CountingReader(self.reader, cancel_at, fail_at)
        worker = make(reader)
        reader.worker = worker
        out = {}
        worker.signals.result.connect(lambda r: out.setdefault("result", r))
        worker.signals.error.connect(lambda e: out.setdefault("error", e))
        worker.signals.cancelled.connect(lambda: out.setdefault("cancelled", True))
        worker.run()
        return out, reader.calls

    def _check(self, make):
        """Uncancelled: a result. Cancelled during the last read: cancelled and nothing else."""
        out, n_reads = self._run(make)
        self.assertEqual(set(out), {"result"}, out.get("error"))
        cancelled, _ = self._run(make, cancel_at=n_reads)
        self.assertEqual(set(cancelled), {"cancelled"})
        return out["result"]

    def _events(self):
        def make(reader):
            a = AnalogAssignment(self.stream, 3, "ICMS", DetectionSettings(merge_gap_ms=333.0))
            w = EventDetectWorker(reader, [a], {self.stream: (FS, self.n / FS)}, {})
            w.CHUNK_SECONDS = 10.0  # several reads
            return w
        return make

    def test_event_detection(self):
        result = self._check(self._events())
        self.assertEqual([(round(e.onset, 3), round(e.offset, 3)) for e in result["events"]], list(EVENTS))

    def test_advanced_blanking(self):
        events, _ = self._run(self._events())
        events = events["result"]["events"]
        settings = BlankingSettings(mode=ADVANCED)
        self._check(lambda reader: BlankingWorker(
            reader, self.stream, FS, self.n / FS, [0, 1, 2], "ICMS", events, events, settings))

    def test_spike_detection(self):
        self._check(lambda reader: SpikeDetectWorker(
            reader, self.stream, [0, 1, 2], FS, N_CH, self.n, (0.0, 5.0), SpikeSettings()))

    def test_export_removes_its_file_when_cancelled_or_failed(self):
        path = os.path.join(self.tmp, "out.i16")

        def make(reader):
            return ExportWorker(reader=reader, stream=self.stream, channels=[0, 1], i_start=0,
                                i_stop=self.n, fs=FS, num_channels=N_CH, filepath=path, scale=1e6)

        result = self._check(make)
        self.assertEqual(result["n_samples"], self.n)
        self.assertFalse(os.path.exists(path))  # the cancelled run deleted the file
        out, _ = self._run(make)
        self.assertEqual(os.path.getsize(path), 2 * 2 * self.n)
        out, _ = self._run(make, fail_at=2)
        self.assertEqual(set(out), {"error"})
        self.assertFalse(os.path.exists(path))  # no partial file left behind


if __name__ == "__main__":
    unittest.main()
