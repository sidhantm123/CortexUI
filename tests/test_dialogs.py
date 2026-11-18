"""Dialog behaviour (offscreen Qt): probe Table Editor, CAR/CMR filter validation."""
import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton

app = QApplication.instance() or QApplication([])

from models.filter_config import FilterConfig, FilterType  # noqa: E402
from ui.filter_panel import EXCLUDED, ChannelGroupWidget, FilterEditDialog  # noqa: E402
from ui.probe_import_dialog import ProbeImportDialog  # noqa: E402
from ui.spectrum_panel import SpectrumPanel  # noqa: E402
from models import StreamState  # noqa: E402


class TestProbeTableEditor(unittest.TestCase):

    def test_add_row_and_accept_from_the_table(self):
        dialog = ProbeImportDialog()
        ok = dialog._ok_btn
        self.assertFalse(ok.isEnabled())  # JSON tab, nothing loaded
        dialog._tabs.setCurrentIndex(1)
        self.assertFalse(ok.isEnabled())  # empty table
        add = next(b for b in dialog.findChildren(QPushButton) if b.text() == "+ Add Row")
        add.click()  # used to pass clicked(False) into append_row: TypeError
        add.click()
        self.assertEqual(dialog._edit_table.rowCount(), 2)
        self.assertTrue(ok.isEnabled())
        dialog._edit_table.item(1, 1).setText("50.0")  # x of the second contact
        ok.click()
        geo = dialog.geometry()
        self.assertEqual(len(geo.contacts), 2)
        dialog._tabs.setCurrentIndex(0)
        self.assertTrue(ok.isEnabled())  # a geometry now exists


class TestFilterDialog(unittest.TestCase):

    def setUp(self):
        self.warnings = []
        self._orig = QMessageBox.warning
        QMessageBox.warning = staticmethod(lambda *a, **k: self.warnings.append(a[2]))

    def tearDown(self):
        QMessageBox.warning = self._orig

    def test_car_with_single_channel_groups_is_refused(self):
        config = FilterConfig(FilterType.CAR, channel_groups=[[0, 1, 2]])
        dialog = FilterEditDialog(filter_config=config, sample_rate=30000.0, channels=[0, 1, 2])
        dialog._group_widget.reset()
        dialog._group_widget.set_channel_groups([[0], [1], [2]])  # every channel assigned, no group of 2
        dialog.accept()
        self.assertEqual(dialog.result(), 0)  # still open
        self.assertEqual(len(self.warnings), 1)
        dialog._group_widget.reset()
        dialog._group_widget.set_channel_groups([[0, 1], [2]])
        dialog.accept()
        self.assertEqual(dialog.result(), 1)
        self.assertEqual(dialog.get_config().channel_groups, [[0, 1], [2]])


class TestFrequencyRange(unittest.TestCase):

    def test_band_follows_the_boxes_and_refuses_min_above_max(self):
        state = StreamState("ns5", 4, 30000.0, 10.0)
        panel = SpectrumPanel()
        bands = []
        panel.band_changed.connect(lambda a, b: bands.append((a, b)))
        panel.set_state(state)
        self.assertEqual(bands[-1], (0.0, 15000.0))  # full range by default
        self.assertEqual((state.fft_fmin, state.fft_fmax), (None, None))
        panel._fmin_spin.setValue(250.0)
        panel._fmax_spin.setValue(5000.0)
        self.assertEqual(bands[-1], (250.0, 5000.0))
        self.assertEqual((state.fft_fmin, state.fft_fmax), (250.0, 5000.0))
        n = len(bands)
        panel._fmin_spin.setValue(6000.0)  # above max
        self.assertEqual(len(bands), n)
        self.assertFalse(panel._analyze_btn.isEnabled())
        self.assertIsNone(panel.band())
        panel._fmin_spin.setValue(300.0)
        self.assertTrue(panel._analyze_btn.isEnabled())
        panel.set_state(state)  # e.g. switching back to this stream
        self.assertEqual(panel.band(), (300.0, 5000.0))


class TestChannelGroupDrag(unittest.TestCase):
    """Click and drag over the CAR/CMR electrode grid (electrodes 1-64, 8 per row)."""

    def setUp(self):
        self.w = ChannelGroupWidget(list(range(64)), {c: c + 1 for c in range(64)})
        self.w.resize(450, 400)
        self.w.show()
        app.processEvents()
        self.grid = self.w._grid
        self.changes = []
        self.w.assignment_changed.connect(lambda: self.changes.append(1))

    def tearDown(self):
        self.w.close()

    def _at(self, electrode) -> QPointF:
        return QPointF(self.w._channel_buttons[electrode - 1].geometry().center())

    def _send(self, kind, point, button, buttons):
        QApplication.sendEvent(self.grid, QMouseEvent(kind, QPointF(point), QPointF(self.grid.mapToGlobal(point.toPoint())),
                                                      button, buttons, Qt.KeyboardModifier.NoModifier))

    def _drag(self, *electrodes):
        """Press on the first, move straight to each next one, release on the last."""
        left, none = Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton
        self._send(QEvent.Type.MouseButtonPress, self._at(electrodes[0]), left, left)
        for e in electrodes[1:]:
            self._send(QEvent.Type.MouseMove, self._at(e), none, left)
        self._send(QEvent.Type.MouseButtonRelease, self._at(electrodes[-1]), left, none)

    def _group(self, sg):
        return sorted(c + 1 for c, v in self.w._assignments.items() if v == sg)

    def test_diagonal_drag_takes_the_run_between(self):
        self.w._on_add_subgroup()
        self._drag(1, 32)  # row 1 col 1 -> row 4 col 8, nothing in between touched
        self.assertEqual(self._group(0), list(range(1, 33)))
        self._drag(48, 60)  # a second drag adds to the same group
        self.assertEqual(self._group(0), list(range(1, 33)) + list(range(48, 61)))
        self.assertEqual(len(self.changes), 2)

    def test_new_group_takes_electrodes_over(self):
        self.w._on_add_subgroup()
        self._drag(1, 32)
        self._drag(48, 60)
        self.w._on_add_subgroup()
        self._drag(40, 20)  # backwards works too
        self.assertEqual(self._group(1), list(range(20, 41)))
        self.assertEqual(self._group(0), list(range(1, 20)) + list(range(48, 61)))
        self.assertEqual(self.w.get_channel_groups(),
                         [[c - 1 for c in list(range(1, 20)) + list(range(48, 61))], list(range(19, 40))])

    def test_dragging_back_shrinks_the_run(self):
        self.w._on_add_subgroup()
        self._drag(5, 6)
        self._drag(10, 30, 12)  # out to 30, back to 12
        self.assertEqual(self._group(0), [5, 6] + list(range(10, 13)))

    def test_click_still_toggles(self):
        self.w._on_add_subgroup()
        self._drag(7)
        self.assertEqual(self._group(0), [7])
        self._drag(7)
        self.assertEqual(self._group(0), [])

    def test_exclude_brush(self):
        self.w._on_add_subgroup()
        self._drag(1, 64)
        self.w._set_brush(EXCLUDED)
        self._drag(10)  # e.g. the stimulation electrode
        self._drag(62, 64)
        self.assertEqual(self._group(EXCLUDED), [10, 62, 63, 64])
        self.assertTrue(self.w.all_assigned())
        groups = self.w.get_channel_groups()
        self.assertEqual(len(groups), 1)
        self.assertNotIn(9, groups[0])
        self.assertIn("4 excluded", self.w._status_label.text())

    def test_gaps_and_edges_map_to_the_nearest_electrode(self):
        b1, b2 = (self.w._channel_buttons[i].geometry() for i in (0, 1))
        gap = QPoint((b1.right() + b2.left()) // 2, b1.center().y())
        self.assertIn(self.grid.position_at(gap), (0, 1))
        past_right = QPoint(self.w._channel_buttons[7].geometry().right() + 200, b1.center().y())
        self.assertEqual(self.grid.position_at(past_right), 7)  # electrode 8, end of row 1

    def test_nothing_happens_without_a_brush(self):
        self._drag(1, 10)
        self.assertEqual(self._group(0), [])
        self.assertIn("first", self.w._status_label.text())


class TestAutoGroupByArray(unittest.TestCase):

    def _dialog(self, labels, numbering="electrode"):
        config = None
        d = FilterEditDialog(sample_rate=30000.0, channels=list(range(16)), labels=labels, numbering=numbering)
        d._type_combo.setCurrentIndex(d._type_combo.findData(FilterType.CMR))
        return d

    def test_one_named_subgroup_per_array_keeping_exclusions(self):
        labels = {c: f"elec{1 + c // 8}-{1 + c % 8}" for c in range(16)}
        d = self._dialog(labels)
        w = d._group_widget
        self.assertTrue(d._auto_array_btn.isEnabled())
        w.exclude([3])  # e.g. the stimulation electrode, excluded first
        d._auto_array_btn.click()
        self.assertEqual(w._subgroups, ["elec1", "elec2"])
        self.assertEqual(w.get_channel_groups(), [[0, 1, 2, 4, 5, 6, 7], list(range(8, 16))])
        self.assertEqual(w.excluded(), [3])
        self.assertTrue(d._button_box.button(d._button_box.StandardButton.Ok).isEnabled())
        self.assertEqual(d._describe_channel(3), "elec1-4 · Ch 4")

    def test_disabled_without_arrays(self):
        self.assertFalse(self._dialog({})._auto_array_btn.isEnabled())
        unique = {c: f"{chr(65 + c)}{c}" for c in range(16)}  # A0, B1, ...: one per name
        self.assertFalse(self._dialog(unique)._auto_array_btn.isEnabled())

    def test_numbering_note(self):
        from PySide6.QtWidgets import QLabel
        texts = lambda d: " ".join(l.text() for l in d._group_widget.findChildren(QLabel))
        self.assertIn("electrode numbers", texts(self._dialog({}, "electrode")))
        self.assertIn("channel numbers", texts(self._dialog({}, "channel")))


class TestEditingCarKeepsExclusions(unittest.TestCase):

    def test_electrodes_outside_the_saved_groups_show_as_excluded(self):
        config = FilterConfig(FilterType.CAR, channel_groups=[[c for c in range(16) if c != 9]])
        dialog = FilterEditDialog(filter_config=config, sample_rate=30000.0, channels=list(range(16)))
        widget = dialog._group_widget
        self.assertEqual(widget._assignments[9], EXCLUDED)
        self.assertTrue(dialog._button_box.button(dialog._button_box.StandardButton.Ok).isEnabled())
        self.assertEqual(widget.get_channel_groups(), config.channel_groups)


if __name__ == "__main__":
    unittest.main()
