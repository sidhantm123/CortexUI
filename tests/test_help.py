"""Every editable setting is explained (tooltips and the Settings Guide come from ui.help_text)."""
import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import (  # noqa: E402
    QAbstractSpinBox, QApplication, QCheckBox, QComboBox, QLineEdit, QRadioButton, QWidget,
)

app = QApplication.instance() or QApplication([])

from core.app_state import AppState  # noqa: E402
from models import StreamState  # noqa: E402
from models.recording import RecordingBlock  # noqa: E402
from ui.analog_panel import AnalogPanel, AssignChannelDialog  # noqa: E402
from ui.blanking_panel import ModalityBlankingPage  # noqa: E402
from ui.control_panel import ControlPanel  # noqa: E402
from ui.export_binary_dialog import ExportBinaryDialog  # noqa: E402
from ui.filter_panel import FilterEditDialog, FilterPanel  # noqa: E402
from ui.help_text import HELP, SECTIONS, tooltip  # noqa: E402
from ui.probe_import_dialog import ProbeImportDialog  # noqa: E402
from ui.recording_info_dialog import RecordingInfoDialog  # noqa: E402
from ui.settings_guide import SettingsGuide  # noqa: E402
from ui.spectrum_panel import SpectrumPanel  # noqa: E402
from ui.spike_panel import SpikePanel  # noqa: E402

EDITABLE = (QAbstractSpinBox, QComboBox, QCheckBox, QRadioButton, QLineEdit)


def unexplained(root: QWidget):
    """Editable controls under root with no tooltip (a spin box's or combo's own line edit doesn't count)."""
    missing = []
    for w in root.findChildren(QWidget):
        if not isinstance(w, EDITABLE):
            continue
        if isinstance(w, QLineEdit) and isinstance(w.parent(), (QAbstractSpinBox, QComboBox)):
            continue
        if not w.toolTip().strip():
            missing.append(f"{type(w).__name__} {getattr(w, 'text', lambda: '')() or w.objectName()!r}")
    return missing


class TestHelpText(unittest.TestCase):

    def test_every_entry_is_in_the_guide_once(self):
        keys = [k for _, ks in SECTIONS for k in ks]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(set(keys), set(HELP))

    def test_tooltip_has_title_text_and_default(self):
        tip = tooltip("assign.merge_gap")
        self.assertIn("<b>Merge gap</b>", tip)
        self.assertIn("Default: 20 ms", tip)
        self.assertIn("50 pulses", tip)


class TestEveryControlIsExplained(unittest.TestCase):

    def _roots(self):
        state = StreamState("ns5", 4, 30000.0, 10.0, channel_labels=[f"elec1-{i + 1}" for i in range(4)])
        block = RecordingBlock("/tmp/rec", "rec", 0.0, 10.0, 10.0, {}, source="blackrock", metadata={
            "ccf": {"path": "/tmp/rec/rec.ccf", "channels": [{"chan": 1, "fields": {"a": "1"}}]},
        })
        return {
            "Channels tab": ControlPanel(),
            "Analog panel": AnalogPanel(AppState()),
            "Assign dialog": AssignChannelDialog("ainp2", ["ICMS", "PNS"], None),
            "Blanking page": ModalityBlankingPage(AppState(), "ICMS"),
            "Filters tab": FilterPanel(),
            "Filter dialog": FilterEditDialog(sample_rate=30000.0, channels=[0, 1, 2, 3],
                                              labels={0: "elec1-1"}, numbering="electrode"),
            "Spike tab": SpikePanel(),
            "Analysis tab": SpectrumPanel(),
            "Export dialog": ExportBinaryDialog(state, blanking="ICMS", filters="BP"),
            "Probe dialog": ProbeImportDialog(),
            "Recording info": RecordingInfoDialog(block),
        }

    def test_no_editable_control_without_an_explanation(self):
        for name, root in self._roots().items():
            with self.subTest(name):
                self.assertEqual(unexplained(root), [])


class TestSettingsGuide(unittest.TestCase):

    def test_lists_every_setting_and_searches(self):
        guide = SettingsGuide()
        text = guide._browser.toPlainText()
        for h in HELP.values():
            self.assertIn(h.title, text)
        self.assertEqual(SettingsGuide.matching("merge"), ["assign.merge_gap"])
        guide._search.setText("notch")
        shown = guide._browser.toPlainText()
        self.assertIn("Notch Freq", shown)
        self.assertNotIn("Merge gap", shown)
        self.assertIn(f"of {len(HELP)} settings", guide._count.text())
        guide._search.setText("zzz-no-such-setting")
        self.assertIn("No setting matches", guide._browser.toPlainText())


if __name__ == "__main__":
    unittest.main()
