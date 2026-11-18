"""main.py --smoke-test: the check the CI runs on each built app (here on the source)."""
import os
import subprocess
import sys
import tempfile
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tests.test_blackrock import write_nsx_23  # noqa: E402


def write_recording(folder: str):
    """A small Blackrock recording: 4 electrodes and one analog input, 3 s at 30 kHz."""
    os.makedirs(folder, exist_ok=True)
    rng = np.random.default_rng(0)
    counts = rng.normal(0, 40, size=(90000, 5)).astype(np.int16)
    write_nsx_23(os.path.join(folder, "smoke.ns5"), [(0, counts)],
                 labels=["elec1", "elec2", "elec3", "elec4", "ainp1"])


def smoke_test(folder: str):
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    return subprocess.run(
        [sys.executable, os.path.join(ROOT, "main.py"), "--smoke-test", folder],
        capture_output=True, text=True, timeout=120, env=env,
    )


class TestSmokeTest(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_opens_a_recording_and_exits_0(self):
        folder = os.path.join(self.tmp, "rec")
        write_recording(folder)
        run = smoke_test(folder)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("smoke test ok: smoke, streams ns5, 5 channels at 30000 Hz", run.stdout)

    def test_a_folder_without_a_recording_exits_1(self):
        run = smoke_test(self.tmp)
        self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
        self.assertIn("smoke test failed: Error Loading Block", run.stderr)


if __name__ == "__main__":
    unittest.main()
