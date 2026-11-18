"""Tests for electrode vs channel numbering."""
import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models import StreamState, NUMBER_BY_ELECTRODE, NUMBER_BY_CHANNEL
from models.stream_state import array_name, group_by_array, parse_electrode_numbers

# Rows like RP01: row 0 = electrode 63, row 1 = 64, row 2 = electrode 10, row 3 = elec2-84, row 4 = analog
LABELS = ["elec1-63", "elec1-64", "elec1-10", "elec2-84", "ainp2"]


def make_state(labels=LABELS):
    return StreamState("ns5", len(labels), 30000.0, 10.0, channel_labels=list(labels), analog_channels=[4])


class TestNumbering(unittest.TestCase):

    def setUp(self):
        self._saved = StreamState.numbering
        StreamState.numbering = NUMBER_BY_ELECTRODE

    def tearDown(self):
        StreamState.numbering = self._saved

    def test_parse(self):
        nums, note = parse_electrode_numbers(LABELS, [0, 1, 2, 3])
        self.assertEqual(nums, {0: 63, 1: 64, 2: 10, 3: 84})
        self.assertEqual(note, "")
        self.assertIsNone(parse_electrode_numbers(["elec1", "noname"], [0, 1])[0])
        self.assertIn("unique", parse_electrode_numbers(["elec1-5", "elec2-5"], [0, 1])[1])
        self.assertIsNone(parse_electrode_numbers(None, [0])[0])

    def test_electrode_mode(self):
        st = make_state()
        self.assertTrue(st.uses_electrode_numbers)
        self.assertEqual(st.display_number(2), 10)
        self.assertEqual(st.channel_for_number(10), 2)
        self.assertIsNone(st.channel_for_number(11))
        self.assertEqual(st.ordered_neural_channels(), [2, 0, 1, 3])
        self.assertEqual(st.channel_label(2), "Elec 10 (Ch 3)")
        self.assertEqual(st.channel_label(4), "Ch 5 (ainp2)")  # analog keeps its label
        self.assertEqual(st.selected_channel, 2)  # starts on the lowest electrode number

    def test_channel_mode(self):
        StreamState.numbering = NUMBER_BY_CHANNEL
        st = make_state()
        self.assertFalse(st.uses_electrode_numbers)
        self.assertEqual(st.display_number(2), 3)
        self.assertEqual(st.channel_for_number(3), 2)
        self.assertEqual(st.ordered_neural_channels(), [0, 1, 2, 3])
        self.assertEqual(st.channel_label(2), "Ch 3 (elec1-10)")

    def test_falls_back_without_electrode_numbers(self):
        st = make_state(["a", "b", "c"])
        self.assertIsNone(st.electrode_numbers)
        self.assertFalse(st.uses_electrode_numbers)
        self.assertEqual(st.display_number(1), 2)

    def test_editing_analog_list_updates_numbers(self):
        st = make_state()
        st.set_analog_channels([3, 4])
        self.assertEqual(set(st.electrode_numbers), {0, 1, 2})


class TestArrays(unittest.TestCase):

    def test_array_name(self):
        for label, name in (("elec1-10", "elec1"), ("elec2-64", "elec2"), ("chan7", "chan"), ("A_12", "A"),
                            ("ainp", ""), ("", ""), (None, "")):
            self.assertEqual(array_name(label), name, label)

    def test_group_by_array(self):
        labels = {0: "elec2-1", 1: "elec1-1", 2: "elec2-2", 3: "elec1-2", 4: "odd5", 5: "nonumber"}
        self.assertEqual(group_by_array([1, 3, 0, 2, 4, 5], labels), [("elec1", [1, 3]), ("elec2", [0, 2])])
        self.assertEqual(group_by_array([0, 1], {}), [])


if __name__ == "__main__":
    unittest.main()
