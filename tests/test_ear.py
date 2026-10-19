"""Tests for ear.py. Every check gets a hand-built example of its own defect.

The trap these guard against is a check that reports green because it is looking
for nothing. Each positive control is a melody built to trip exactly one ear, and
each negative control is the same melody with the defect removed.
"""

import sys
import unittest
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pysrc"))

from scorehalo.ear import PIANO_HI, PIANO_LO, check_streams, midi


def stream(notes, part=0, staff=1, voice=1):
    """notes: [(measure, onset, pitch)] -> a stream in score.py's real 7-tuple shape.

    (measure_no, onset, local, kind, pitch, dur, chord). 'local' is the
    within-measure position; using it for simultaneity matters (§26).
    """
    return {
        (part, staff, voice): [
            (m, o, o, "pitched", p, Fraction(1, 4), False) for m, o, p in notes
        ]
    }


class TestMidi(unittest.TestCase):
    def test_pitch_conversion(self):
        self.assertEqual(midi("C4"), 60)
        self.assertEqual(midi("A0"), 21)  # lowest piano key
        self.assertEqual(midi("C8"), 108)  # highest piano key
        self.assertEqual(midi("F#4"), 66)
        self.assertEqual(midi("Bb3"), 58)

    def test_rejects_malformed(self):
        # A differ that cannot parse returns None, not a wrong number.
        for bad in ("", None, "H4", "Cb", "4", "C04", "C-1"):
            self.assertIsNone(midi(bad), f"{bad!r} should not parse")


class TestVocalLeap(unittest.TestCase):
    def test_fires_on_a_hand_built_two_octave_leap(self):
        # The positive control: a melody that teleports up two octaves.
        s = stream([(1, 0, "C4"), (1, 1, "C6")])
        suspects, counts = check_streams(s)
        self.assertEqual(counts["vocal_leap"], 1, "the leap must be caught")
        self.assertIn("24 semitones", suspects[0]["detail"])

    def test_silent_on_a_stepwise_melody(self):
        # Same shape, defect removed: an ordinary ascending scale.
        s = stream([(1, 0, "C4"), (1, 1, "D4"), (1, 2, "E4"), (1, 3, "F4")])
        _suspects, counts = check_streams(s)
        self.assertEqual(counts["vocal_leap"], 0, "a scale is not a leap")

    def test_exactly_one_octave_is_allowed(self):
        # Boundary: the limit is "> 12", so 12 must pass.
        s = stream([(1, 0, "C4"), (1, 1, "C5")])
        _suspects, counts = check_streams(s)
        self.assertEqual(counts["vocal_leap"], 0, "an octave is within the limit")

    def test_ignores_lower_staves(self):
        # A bass line legitimately leaps; only the TOP staff is a melody.
        # Both staffs must be present, or staff 2 genuinely IS the top staff.
        # A realistic bass staff: consistently low, as a left hand actually is.
        # (Two notes, one of them a high C, would give the median no information --
        # the melody cannot be identified from a sample that size, by anyone.)
        s = stream([(1, 0, "C4"), (1, 1, "D4"), (1, 2, "E4")], staff=1)
        s.update(stream([(1, 0, "C2"), (1, 1, "E2"), (1, 2, "G2")], staff=2))
        _suspects, counts = check_streams(s)
        self.assertEqual(counts["vocal_leap"], 0, "only the top voice is checked")

    def test_fires_when_the_leap_is_in_the_top_staff(self):
        # Same two staffs, defect moved to staff 1: now it must fire.
        s = stream([(1, 0, "C4"), (1, 1, "C6")], staff=1)
        s.update(stream([(1, 0, "C2"), (1, 1, "E2"), (1, 2, "G2")], staff=2))
        _suspects, counts = check_streams(s)
        self.assertEqual(counts["vocal_leap"], 1, "the top staff leap must be caught")


class TestOutOfRange(unittest.TestCase):
    def test_fires_above_the_piano(self):
        s = stream([(1, 0, "C9")])
        _suspects, counts = check_streams(s)
        self.assertEqual(counts["out_of_range"], 1)

    def test_silent_inside_the_piano(self):
        s = stream([(1, 0, "A0"), (1, 1, "C8")])
        _suspects, counts = check_streams(s)
        self.assertEqual(counts["out_of_range"], 0)
        self.assertLessEqual(PIANO_LO, midi("A0"))
        self.assertLessEqual(midi("C8"), PIANO_HI)


class TestSilenceRun(unittest.TestCase):
    def test_fires_on_three_empty_measures(self):
        s = stream([(1, 0, "C4"), (5, 0, "C4")])
        idx = {0: [1, 2, 3, 4, 5]}  # measures 2,3,4 exist in the file and are empty
        _suspects, counts = check_streams(s, measure_index=idx)
        self.assertGreaterEqual(counts["silence_run"], 1, "a 3-bar hole must be heard")

    def test_hole_spanning_a_digit_boundary(self):
        # Measures 8,9,10 empty. Sorting as STRINGS puts '10' before '8', and any
        # measure between them with notes resets the run -- this hid every real
        # hole in the joined Carpenters score.
        s = stream([(7, 0, "C4"), (11, 0, "C4")])
        idx = {0: [str(n) for n in range(1, 13)]}
        _suspects, counts = check_streams(s, measure_index=idx)
        self.assertGreaterEqual(counts["silence_run"], 1, "8-9-10 is a hole")

    def test_hole_after_a_note_named_four(self):
        # The exact failure: real hole at 38-40, but measure '4' has notes and
        # sorts between '39' and '40'.
        s = stream([(1, 0, "C4"), (4, 0, "C4"), (37, 0, "C4"), (41, 0, "C4")])
        idx = {0: [str(n) for n in range(1, 42)]}
        _suspects, counts = check_streams(s, measure_index=idx)
        self.assertGreaterEqual(counts["silence_run"], 1, "38-40-41 boundary")

    def test_blind_without_the_measure_index(self):
        # The control that found the bug: with no index the hole is invisible,
        # because empty measures contribute no stream entries at all.
        s = stream([(1, 0, "C4"), (5, 0, "C4")])
        _suspects, counts = check_streams(s)
        self.assertEqual(counts["silence_run"], 0, "documented blind spot")

    def test_silent_on_rests_but_no_hole(self):
        # Two empty measures is not a hole; a fermata is one bar.
        s = stream([(1, 0, "C4"), (2, 0, "C4"), (5, 0, "C4")])
        idx = {0: [1, 2, 3, 4, 5]}
        _suspects, counts = check_streams(s, measure_index=idx)
        self.assertEqual(counts["silence_run"], 0, "two bars is not three")


if __name__ == "__main__":
    unittest.main()
