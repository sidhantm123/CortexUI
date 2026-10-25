"""Tests for typed channel selections."""
import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui.channel_spec import parse_channel_spec, format_channel_spec

VALID = range(1, 129)


class TestChannelSpec(unittest.TestCase):

    def test_valid_inputs(self):
        self.assertEqual(parse_channel_spec("1-24, 37-45", VALID)[0], list(range(1, 25)) + list(range(37, 46)))
        self.assertEqual(parse_channel_spec("1,2,3", VALID), ([1, 2, 3], ""))
        self.assertEqual(parse_channel_spec("33", VALID), ([33], ""))
        self.assertEqual(parse_channel_spec("3, 1-2, 2", VALID)[0], [1, 2, 3])
        self.assertEqual(parse_channel_spec(" 5 - 7  9 ", VALID)[0], [5, 6, 7, 9])
        self.assertEqual(parse_channel_spec("10–12", VALID)[0], [10, 11, 12])  # en dash

    def test_invalid_inputs(self):
        for text, fragment in [
            ("5-3", "backwards"),
            ("abc", "isn't a number"),
            ("5-", "isn't a number"),
            ("200", "out of range (1–128)"),
            ("", "Type channel"),
        ]:
            numbers, error = parse_channel_spec(text, VALID)
            self.assertEqual(numbers, [], text)
            self.assertIn(fragment, error, text)

    def test_reasons_and_gaps(self):
        _, error = parse_channel_spec("130", range(1, 129), {130: "an analog channel (ainp2)"})
        self.assertEqual(error, "130 is an analog channel (ainp2)")
        _, error = parse_channel_spec("3", [1, 2, 4], noun="electrode")
        self.assertEqual(error, "There is no electrode 3 in this recording")

    def test_format(self):
        self.assertEqual(format_channel_spec([1, 2, 3, 6, 8, 9]), "1-3, 6, 8-9")
        self.assertEqual(format_channel_spec([5]), "5")
        self.assertEqual(format_channel_spec([]), "")
        self.assertEqual(parse_channel_spec(format_channel_spec(range(1, 129)), VALID)[0], list(VALID))


if __name__ == "__main__":
    unittest.main()
