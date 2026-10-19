"""Tests for brace detection and brace-derived staff grouping.

The bug this exists to prevent: pairing staves by clef guesses that the singer
is on top. It is wrong whenever the singer sits between or below the
accompaniment, and it fails *silently* -- the score still engraves, just with
the vocal line folded into the piano.

All the geometry here is measured from p0020 at 300dpi
(``data/out-band/render/p0020.png``) and confirmed by Wilbur reading the page:

* sys1 is a piano introduction -- brace over both staves, no singer;
* sys2 has "Slowly" above staff 3, the voice, with the piano braced below;
* sys3 repeats that shape -- voice on staff 6, piano braced on staves 7 and 8;
* one singer throughout; the piano plays under her in every system.

Tests needing a real image read ``data/out-band/render/p0020.png`` and are
skipped when it is absent, so the synthetic ones below still run anywhere.

Run: .venv/bin/python -m unittest discover -s tests -v
"""

import json
import os
import sys
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pysrc"))

from scorehalo.brace import (BRACE_MIN_WIDTH, brace_groups, brace_strokes)

# The measured truth for p0020, as GLOBAL staff indices (0-based, reading
# order over the whole page): which staves a brace covers, per system.
P0020_BRACED = {"sys1": [0, 1], "sys2": [3, 4], "sys3": [6, 7]}
# Which staff within its system carries the singer (local indices), or None.
P0020_SINGER = {"sys1": None, "sys2": 0, "sys3": 0}


def synth_system(n_staves, brace=None, stem=False):
    """Build a synthetic system: staves plus a drawn brace/stem.

    ``brace`` is the 0-based inclusive staff range the brace spans, or None.
    ``stem`` draws a 1px-wide mark beside the first staff -- the shape that a
    first pass mistook for a brace.
    """
    ink = np.zeros((1000, 400), dtype=bool)
    staves = []
    for i in range(n_staves):
        top = 100 + i * 200
        staves.append({"y_top": float(top), "y_bot": float(top + 80),
                       "x0": 120})
        ink[top:top + 81, 120:300] = True
    if brace is not None:
        lo, hi = brace
        a = staves[lo]["y_top"]
        b = staves[hi]["y_bot"]
        ink[int(a):int(b) + 1, 100:112] = True  # 12px wide -> a brace
    if stem:
        a = staves[0]["y_top"]
        ink[int(a):int(a) + 160, 112:113] = True  # 1px -> a stem, not a brace
    return ink, staves


class BraceWidthTests(unittest.TestCase):
    """Width is what separates a brace from a stem. It must not be guessed."""

    def test_wide_stroke_is_detected_as_a_brace(self):
        ink, staves = synth_system(2, brace=(0, 1))
        self.assertEqual(brace_strokes(ink, staves), [(100, 380)])

    def test_thin_stroke_is_not_a_brace(self):
        """A 1px note stem must not become a brace.

        Regression: on p0020 a 1px mark beside staff 6 of sys3 was first read as
        a brace stroke, which would have invented a voice part there.
        """
        ink, staves = synth_system(2, stem=True)
        self.assertEqual(brace_strokes(ink, staves), [])

    def test_threshold_rejects_sub_width_ink(self):
        ink, staves = synth_system(2)
        ink[100:381, 100:100 + BRACE_MIN_WIDTH - 1] = True
        self.assertEqual(brace_strokes(ink, staves), [])

    def test_threshold_accepts_exactly_width(self):
        ink, staves = synth_system(2)
        ink[100:381, 100:100 + BRACE_MIN_WIDTH] = True
        self.assertEqual(len(brace_strokes(ink, staves)), 1)

    def test_short_speck_is_ignored(self):
        ink, staves = synth_system(2)
        ink[100:140, 100:120] = True  # wide but far too short to be a brace
        self.assertEqual(brace_strokes(ink, staves), [])


class BraceGroupTests(unittest.TestCase):
    """A brace covers every staff between its endpoints, inclusive."""

    def test_two_staves_under_one_brace(self):
        ink, staves = synth_system(2, brace=(0, 1))
        self.assertEqual(brace_groups(ink, staves), [[0, 1]])

    def test_unbraced_top_staff_is_the_singer(self):
        ink, staves = synth_system(3, brace=(1, 2))
        groups = brace_groups(ink, staves)
        self.assertEqual(groups, [[1, 2]])
        covered = {i for g in groups for i in g}
        self.assertEqual([i for i in range(3) if i not in covered], [0])

    def test_unbraced_bottom_staff_is_the_singer(self):
        """The layout that defeats clef-based pairing."""
        ink, staves = synth_system(3, brace=(0, 1))
        groups = brace_groups(ink, staves)
        self.assertEqual(groups, [[0, 1]])
        covered = {i for g in groups for i in g}
        self.assertEqual([i for i in range(3) if i not in covered], [2])

    def test_piano_only_system_has_no_singer(self):
        ink, staves = synth_system(2, brace=(0, 1))
        groups = brace_groups(ink, staves)
        covered = {i for g in groups for i in g}
        self.assertEqual([i for i in range(2) if i not in covered], [])

    def test_brace_endpoints_inside_staff_bounds(self):
        """Endpoints land a few px inside the staff; a tight test finds nothing.

        Regression: measuring sys1/sys3 with a +-12px endpoint tolerance found
        NO brace, silently turning a piano-only intro into two unbraced staves.
        """
        ink = np.zeros((1000, 400), dtype=bool)
        staves = [{"y_top": 100.0, "y_bot": 180.0, "x0": 120},
                  {"y_top": 300.0, "y_bot": 380.0, "x0": 120}]
        ink[100:181, 120:300] = True
        ink[300:381, 120:300] = True
        ink[106:377, 100:112] = True  # starts y_top+6, ends y_bot-3
        self.assertEqual(brace_groups(ink, staves), [[0, 1]])

    def test_brace_covering_no_staff_raises(self):
        """Loudly, never a silent 'everything is a voice'."""
        # Two far-apart staves, and a brace-like stroke stranded in the gap
        # between them: wide and tall enough to pass both thresholds, yet
        # touching neither staff.
        ink = np.zeros((1000, 400), dtype=bool)
        staves = [{"y_top": 100.0, "y_bot": 180.0, "x0": 120},
                  {"y_top": 700.0, "y_bot": 780.0, "x0": 120}]
        ink[100:181, 120:300] = True
        ink[700:781, 120:300] = True
        ink[400:501, 100:112] = True
        with self.assertRaises(ValueError):
            brace_groups(ink, staves)

    def test_three_brace_groups_are_kept_separate(self):
        ink = np.zeros((1200, 400), dtype=bool)
        staves = []
        for i in range(6):
            top = 100 + i * 180
            staves.append({"y_top": float(top), "y_bot": float(top + 80),
                           "x0": 120})
            ink[top:top + 81, 120:300] = True
        for lo, hi in ((0, 1), (2, 3), (4, 5)):
            ink[int(staves[lo]["y_top"]):int(staves[hi]["y_bot"]) + 1,
                100:112] = True
        self.assertEqual(brace_groups(ink, staves), [[0, 1], [2, 3], [4, 5]])

    def test_brace_spanning_three_staves_covers_the_middle_one(self):
        """A brace spans a range, not merely its two endpoints.

        Three-staff braces are ordinary -- an organ part, or a vocal score that
        braces the singer inside the piano's group. Reading only the endpoints
        would report the middle staff as unbraced, inventing a second singer.
        """
        ink, staves = synth_system(3, brace=(0, 2))
        self.assertEqual(brace_groups(ink, staves), [[0, 1, 2]])

    def test_three_staff_brace_leaves_nothing_unbraced(self):
        ink, staves = synth_system(3, brace=(0, 2))
        covered = {i for g in brace_groups(ink, staves) for i in g}
        self.assertEqual([i for i in range(3) if i not in covered], [])


class P0020RealImageTests(unittest.TestCase):
    """The measured page, if it is on disk."""

    IMAGE = os.path.join(ROOT, "data", "out-band", "render", "p0020.png")
    BOXES = os.path.join(ROOT, "data", "out-perstaff", "p0020.boxes.json")

    def setUp(self):
        if not (os.path.exists(self.IMAGE) and os.path.exists(self.BOXES)):
            self.skipTest("p0020 render or boxes not present")

    def _load(self):
        from PIL import Image
        with open(self.BOXES) as fh:
            boxes = json.load(fh)
        ink = np.asarray(Image.open(self.IMAGE).convert("L")) < 128
        return ink, boxes["staves"]

    def test_system_membership_matches_the_scan(self):
        ink, staves = self._load()
        systems = {"sys1": [0, 1], "sys2": [2, 3, 4], "sys3": [5, 6, 7]}
        for name, idxs in systems.items():
            sub = [staves[i] for i in idxs]
            groups = brace_groups(ink, sub)
            # Collect every staff a brace touches -- not just group starts,
            # which silently drops the second staff of a two-staff brace.
            got = sorted(idxs[i] for g in groups for i in g)
            self.assertEqual(got, P0020_BRACED[name], name)

    def test_sys1_is_piano_only(self):
        """Wilbur: the top of the song is piano hands alone."""
        ink, staves = self._load()
        sub = [staves[i] for i in (0, 1)]
        groups = brace_groups(ink, sub)
        covered = {i for g in groups for i in g}
        self.assertEqual(covered, {0, 1})

    def test_voice_is_the_unbraced_staff_in_sys2_and_sys3(self):
        """Wilbur: "Slowly" above staff 3; sys3 repeats the shape."""
        ink, staves = self._load()
        for sys_no, base, count in ((2, 2, 3), (3, 5, 3)):
            sub = [staves[i] for i in range(base, base + count)]
            groups = brace_groups(ink, sub)
            covered = {i for g in groups for i in g}
            singer = [i for i in range(count) if i not in covered]
            self.assertEqual(singer, [P0020_SINGER["sys%d" % sys_no]],
                             "sys%d" % sys_no)


if __name__ == "__main__":
    unittest.main()