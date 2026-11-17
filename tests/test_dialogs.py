"""Dialog behaviour (offscreen Qt): probe Table Editor, CAR/CMR filter validation."""
import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton

app = QApplication.instance() or QApplication([])

from models.filter_config import FilterConfig, FilterType  # noqa: E402
from ui.filter_panel import FilterEditDialog  # noqa: E402
from ui.probe_import_dialog import ProbeImportDialog  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
