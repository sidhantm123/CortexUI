"""Block info text (left panel)."""
import unittest

from ui.stream_panel import format_duration


class TestBlockInfo(unittest.TestCase):
    def test_format_duration(self):
        self.assertEqual(format_duration(12.34), "12.3 s")
        self.assertEqual(format_duration(966.6), "16 min 6.6 s")
        self.assertEqual(format_duration(3725.0), "1 h 2 min 5 s")


if __name__ == "__main__":
    unittest.main()
