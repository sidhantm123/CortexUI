"""Probe geometry: probeinterface channel indices (0-based, -1 = not connected)."""
import json
import os
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import remove_dir  # noqa: E402

from blackrock_io.blackrock_reader import NEO_AVAILABLE
from core.app_state import AppState
from models.probe import UNWIRED, ProbeGeometry
from tests.test_blackrock import write_nsx_23


def probe_dict(device_channel_indices, shanks=None):
    n = len(device_channel_indices)
    return {"specification": "probeinterface", "probes": [{
        "contact_positions": [[i * 100.0, 0.0] for i in range(n)],
        "device_channel_indices": list(device_channel_indices),
        "contact_ids": [f"c{i}" for i in range(n)],
        "shank_ids": shanks or ["0"] * n,
    }]}


class TestProbeIndices(unittest.TestCase):

    def test_device_channel_indices_are_zero_based(self):
        geo = ProbeGeometry.from_probeinterface_dict(probe_dict([0, 5, -1, 2]))
        self.assertEqual([c.channel_idx for c in geo.contacts], [0, 5, UNWIRED, 2])

    def test_round_trip_keeps_unwired_contacts(self):
        geo = ProbeGeometry.from_probeinterface_dict(probe_dict([3, -1, 1]))
        d = json.loads(json.dumps(geo.to_probeinterface_dict()))
        self.assertEqual(d["probes"][0]["device_channel_indices"], [3, -1, 1])
        back = ProbeGeometry.from_probeinterface_dict(d)
        self.assertEqual([c.channel_idx for c in back.contacts], [3, UNWIRED, 1])

    def test_unwired_contacts_are_left_out_of_groups_and_lookups(self):
        geo = ProbeGeometry.from_probeinterface_dict(probe_dict([0, -1, 1, 2], ["A", "A", "B", "B"]))
        self.assertEqual(geo.get_shank_groups(), {"A": [0], "B": [1, 2]})
        self.assertIsNone(geo.get_contact_by_channel(UNWIRED))

    def test_table_rows_round_trip(self):
        from ui.probe_import_dialog import UNWIRED_TEXT, _geometry_to_rows, _rows_to_geometry
        geo = ProbeGeometry.from_probeinterface_dict(probe_dict([4, -1, 0]))
        rows = _geometry_to_rows(geo)
        self.assertEqual([r[0] for r in rows], ["1", "5", UNWIRED_TEXT])  # 1-based display, unwired last
        back = _rows_to_geometry(rows)
        self.assertEqual(sorted(c.channel_idx for c in back.contacts), [UNWIRED, 0, 4])


@unittest.skipUnless(NEO_AVAILABLE, "neo not installed")
class TestProbeInSessions(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        data = np.zeros((3000, 4), dtype=np.int16)
        write_nsx_23(os.path.join(self.tmp, "rec.ns5"), [(0, data)])
        self.app = AppState()
        self.app.load_block(self.tmp)
        self.session = os.path.join(self.tmp, "s.json")

    def tearDown(self):
        remove_dir(self.tmp)

    def test_new_sessions_round_trip(self):
        self.app.set_probe(ProbeGeometry.from_probeinterface_dict(probe_dict([0, 3, -1])))
        self.app.save_session(self.session)
        restored = AppState()
        restored.load_session(self.session)
        self.assertEqual([c.channel_idx for c in restored.probe.contacts], [0, 3, UNWIRED])

    def test_old_sessions_were_one_based(self):
        self.app.save_session(self.session)
        with open(self.session) as f:
            d = json.load(f)
        d.pop("probe_index_base")
        d["probe"] = probe_dict([1, 2, 4])  # written by the app as channel + 1
        with open(self.session, "w") as f:
            json.dump(d, f)
        restored = AppState()
        restored.load_session(self.session)
        self.assertEqual([c.channel_idx for c in restored.probe.contacts], [0, 1, 3])


if __name__ == "__main__":
    unittest.main()
