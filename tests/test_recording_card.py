"""Recording card text (left panel)."""
import unittest

from models.recording import StreamInfo
from ui.stream_panel import format_duration, format_rate, stream_summary


class TestRecordingCard(unittest.TestCase):
    def test_stream_summary_counts_analog_inputs(self):
        info = StreamInfo("ns5", 136, 30000.0, 966.6, 28998000, "int16", analog_channels=list(range(128, 136)))
        self.assertEqual(stream_summary(info), "ns5 · 136 ch (128 + 8 analog) · 30 kHz")
        # after the user edits the analog list
        self.assertEqual(stream_summary(info, n_analog=0), "ns5 · 136 ch · 30 kHz")

    def test_formats(self):
        self.assertEqual(format_rate(24414.0625), "24.4141 kHz")
        self.assertEqual(format_rate(500.0), "500 Hz")
        self.assertEqual(format_duration(966.6), "16 min 6.6 s")
        self.assertEqual(format_duration(12.34), "12.3 s")


if __name__ == "__main__":
    unittest.main()
