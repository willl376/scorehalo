"""The trace-paper primitive: detecting a systematic pitch error.

Run from the scorehalo repo root::

    .venv/bin/python -m unittest tests.test_tracing

The arithmetic that matters is ``_position_shift``. Given two
staves' notehead heights, it reports the shift that makes their
histograms overlap most. These tests pin the behaviour that
makes it worth having: a systematic error is found exactly,
and an exact copy is found as agreement.
"""

import unittest

from scorehalo import tracing


def _staff(positions):
    """A staff's noteheads as (x_fraction, height) pairs."""
    return [(float(i) / max(1, len(positions) - 1), p)
            for i, p in enumerate(positions)]


class PositionShiftTests(unittest.TestCase):

    def test_detects_an_exact_octave(self):
        source = _staff([-4, -2, 0, 1, 2, 3, 4, 5])
        octave_low = [(x, p - tracing.POSITIONS_PER_OCTAVE)
                      for x, p in source]
        shift, _hist, support = tracing._position_shift(source,
                                                         octave_low)
        self.assertEqual(shift, tracing.POSITIONS_PER_OCTAVE)
        self.assertEqual(support, len(source))

    def test_detects_agreement(self):
        source = _staff([-3, -1, 0, 1, 2, 3, 4])
        shift, _hist, support = tracing._position_shift(source, source)
        self.assertEqual(shift, 0)
        self.assertEqual(support, len(source))

    def test_detects_a_third(self):
        source = _staff([-4, -2, 0, 1, 2, 3, 4, 5, 6])
        a_third_low = [(x, p - 2) for x, p in source]
        shift, _hist, support = tracing._position_shift(source,
                                                         a_third_low)
        self.assertEqual(shift, 2)
        self.assertEqual(support, len(source))

    def test_empty_staves_agree_on_nothing(self):
        self.assertEqual(tracing._position_shift([], []), (0, {}, 0))
        self.assertEqual(tracing._position_shift([(0.5, 1.0)], []),
                         (0, {}, 0))

    def test_an_unrelated_staff_is_not_reported_as_a_shift(self):
        # Two staves that share a range but not a shape must not
        # claim a clean agreement: the overlap is partial, so the
        # support is well under the target count.
        source = _staff([-8, -7, -6, 6, 7, 8, 9, 10])
        other = _staff([-1, 0, 0, 1, 1, 2, 2, 3])
        _shift, _hist, support = tracing._position_shift(source, other)
        self.assertLess(support, len(other))


if __name__ == "__main__":
    unittest.main()
