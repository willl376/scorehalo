"""Tests for the two staff-level repairs that keep the singer on the page.

The bug this exists to prevent: on the pages of "We've Only Just Begun" the
voice staff's bar lines are ~70 px long, while the vertical opening that finds
systems runs a kernel 4% of the page height (~132 px), so they are erased. The
voice staff is left standing alone above the accompaniment instead of inside
its system. Two things then go wrong quietly, and both lose the singer:

* dense notation can weld staff lines together, so the horizontal opening never
  separates them and the whole staff is dropped (on p0035 the piano's staves
  came back as blobs 15-22 px tall, and 2 of 15 staves went missing);
* a lone staff becomes a one-staff "system", and the grid -- which fits every
  system onto the page's tallest layout -- then sees alternating one- and
  two-staff systems and calls the piece two-staff music.

Measured truth for the pages this was written against (300dpi
``data/out-perstaff/pNNNN.deskew.png``, staff tops by row darkness):

* p0020 -- 8 staves, systems 2+3+3 (the piano introduction has no singer);
* p0033 -- 12 staves, systems 3+3+3+3;
* p0035 -- 15 staves, systems 3+3+3+3+3.

Before the repairs p0033 read as 12 staves in 7 systems (shape 3,1,2,1,2,1,2)
and p0035 as 13 in 9 (1,1,1,2,1,2,1,2,2). Those pages are checked by a probe
script, not here, so these tests are synthetic and run anywhere.

Run: .venv/bin/python -m unittest discover -s tests -v
"""

import os
import sys
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pysrc"))

from scorehalo import layout


def system(*tops, x0=100, x1=900, height=80):
    """A system as :func:`layout._finish_system` builds it, from staff tops."""
    staves = [{"y_top": float(t), "y_bot": float(t) + height,
               "y_center": float(t) + height / 2,
               "x0": float(x0), "x1": float(x1)} for t in tops]
    return layout._finish_system(staves)


def tops_of(systems):
    return [[round(s["y_top"]) for s in sysd["staves"]] for sysd in systems]


def lines(ink):
    """The y-centres :func:`layout.find_ink_lines` reports for an ink image."""
    return [y for y, _, _, _ in layout.find_ink_lines(ink)]


class AttachOrphanStavesTests(unittest.TestCase):
    def test_a_full_system_is_left_alone(self):
        systems = [system(100, 400, 500), system(900, 1200, 1300)]
        self.assertEqual(tops_of(layout.attach_orphan_staves(systems)),
                         tops_of(systems))

    def test_a_lone_staff_joins_the_system_below_it(self):
        # What p0033 comes back as before the repair: the voice alone, then the
        # braced piano pair, alternating down the page.
        out = layout.attach_orphan_staves(
            [system(100), system(400, 500), system(900), system(1200, 1300)])
        self.assertEqual(tops_of(out), [[100, 400, 500], [900, 1200, 1300]])

    def test_a_lone_staff_at_the_foot_of_the_page_joins_the_system_above(self):
        out = layout.attach_orphan_staves([system(100, 200), system(500)])
        self.assertEqual(tops_of(out), [[100, 200, 500]])

    def test_two_lone_staves_in_a_row_are_left_alone(self):
        # Nothing here says which of them is the other's top line.
        out = layout.attach_orphan_staves([system(100), system(300)])
        self.assertEqual([s["n_staves"] for s in out], [1, 1])

    def test_a_join_keeps_the_staves_in_printed_order(self):
        # Staves are read top to bottom; a join must not seat the accompaniment
        # above the singer.
        joined = layout._join_systems(system(500), system(100, 400))
        self.assertEqual([round(s["y_top"]) for s in joined["staves"]],
                         [100, 400, 500])

    def test_no_staff_is_lost_or_invented(self):
        systems = [system(100), system(300, 400), system(800), system(1000, 1100)]
        out = layout.attach_orphan_staves(systems)
        before = sorted(s["y_top"] for sysd in systems for s in sysd["staves"])
        after = sorted(s["y_top"] for sysd in out for s in sysd["staves"])
        self.assertEqual(before, after)


def staff_page(spacing=20, first=100, count=5, width=1000, height=400):
    """An ink image of ``count`` staff lines spanning most of the page."""
    ink = np.zeros((height, width), dtype=np.uint8)
    for i in range(count):
        ink[first + i * spacing, 20:width - 20] = 255
    return ink


def weld(ink, first, count, spacing=20, run=140):
    """Fill every row between the lines with short-run ink.

    A staff line carries a run of most of the page; the ink between lines on a
    dense page carries only short runs. Both survive the horizontal opening
    (the kernel is a tenth of the page here) but only the first is a line, and
    the short ink is what welds the lines into one blob.
    """
    for i in range(count - 1):
        top = first + i * spacing + 1
        for y in range(top, top + spacing - 1):
            ink[y, 30:30 + run] = 255
    return ink


class FindInkLinesTests(unittest.TestCase):
    def test_five_clean_lines_are_five_candidates(self):
        found = lines(staff_page())
        self.assertEqual([round(y) for y in found], [100, 120, 140, 160, 180])

    def test_lines_welded_by_dense_ink_are_still_all_reported(self):
        # Without the repair this whole staff arrives as one blob 81 px tall,
        # taller than a staff line may be, and is thrown away.
        found = lines(weld(staff_page(), 100, 5))
        self.assertEqual([round(y) for y in found], [100, 120, 140, 160, 180])

    def test_a_page_border_is_not_read_as_a_welded_staff(self):
        ink = staff_page(first=200)
        ink[0:21, :] = 255  # a thick scan edge across the top of the paper
        found = lines(ink)
        self.assertEqual([round(y) for y in found], [200, 220, 240, 260, 280])

    def test_a_solid_band_does_not_become_a_staff(self):
        # A shaded block has long runs in every row, so it can only offer one
        # candidate at its centre -- not the three a staff is made of.
        ink = np.zeros((400, 1000), dtype=np.uint8)
        ink[100:140, 100:900] = 255
        self.assertLessEqual(len(lines(ink)), 1)


class SplitSystemByBraceTests(unittest.TestCase):
    """Which staves of one system get cropped (and read) together.

    The bug this holds still: homr reads a whole three-staff system on p0035's
    dense first two systems, finds the three staves, and merges them into one
    part, so the singer's 19 notes become piano music. The brace is the printed
    boundary between the parts and is what this split follows.
    """

    def test_a_braced_pair_under_a_lone_voice_is_two_groups(self):
        # The printed layout of "We've Only Just Begun": the singer's staff
        # alone on top, the braced piano under it.
        rule = [(200.0, 300.0, 480.0, 3.0)]  # spans the two piano staves only
        groups = layout.split_system_by_brace(system(100, 300, 400), rule, 20.0)
        self.assertEqual(tops_of(groups), [[100], [300, 400]])

    def test_a_rule_over_every_staff_leaves_the_system_whole(self):
        # A system bracket over all three: nothing here says where the parts
        # divide, so the system is read as one crop -- as it was before.
        groups = layout.split_system_by_brace(system(100, 300, 400),
                                             [(200.0, 100.0, 480.0, 3.0)], 20.0)
        self.assertEqual([g["n_staves"] for g in groups], [3])

    def test_a_system_with_no_rules_is_left_whole(self):
        # No rule measured anything about the part boundary. Splitting into
        # singletons here would claim every staff is its own instrument, which
        # for a piano is a worse error than the merge this avoids.
        groups = layout.split_system_by_brace(system(100, 300, 400), [], 20.0)
        self.assertEqual([g["n_staves"] for g in groups], [3])

    def test_a_page_edge_rule_is_not_a_brace(self):
        # A scan edge down the paper is not a rule in the music.
        groups = layout.split_system_by_brace(system(100, 300, 400),
                                              [(0.0, 100.0, 480.0, 3.0)], 20.0)
        self.assertEqual([g["n_staves"] for g in groups], [3])

    def test_groups_come_back_top_first_with_their_staves_in_order(self):
        groups = layout.split_system_by_brace(system(100, 300, 400),
                                              [(200.0, 300.0, 480.0, 3.0)], 20.0)
        self.assertEqual(tops_of(groups), [[100], [300, 400]])

    def test_a_rule_over_two_of_three_staves_joins_only_those_two(self):
        # Honest geometry, stated rather than hidden: a bracket over the top
        # two staves reads as a pair. Which instrument it pairs is then a
        # recognition question, not one this function can answer.
        groups = layout.split_system_by_brace(system(100, 300, 400),
                                              [(200.0, 100.0, 350.0, 3.0)], 20.0)
        self.assertEqual(tops_of(groups), [[100, 300], [400]])


class HealLineGapsTests(unittest.TestCase):
    """A staff line broken by a blank patch is drawn back together.

    p0022 prints a blank band through its third system: every staff line
    arrives in two halves, the engine reports *six* staffs where three are
    printed, all six collide into one part, and the singer's staff vanishes
    from that system (measured: 6 staffs, 1 part, 9 notes -- after healing,
    3 staffs, 2 parts, 48 notes). Closing the break is the whole repair.
    """

    def _page(self, w=400, h=40):
        return np.full((h, w), 255, np.uint8)

    def test_a_break_is_closed_without_mutating_the_input(self):
        gray = self._page()
        gray[20, 0:150] = 0
        gray[20, 250:400] = 0
        healed, filled = layout.heal_line_gaps(
            gray, [(20.0, 0, 150, 1), (20.0, 250, 400, 1)])
        self.assertTrue((healed[20, :] == 0).all(), "break not closed")
        self.assertTrue((healed[19, 151:250] == 0).all(), "line has no thickness")
        self.assertGreater(filled, 0)
        self.assertTrue((gray[20, 150:250] == 255).all(), "input was modified")

    def test_an_unbroken_line_is_returned_as_is(self):
        gray = self._page()
        gray[20, :] = 0
        healed, filled = layout.heal_line_gaps(gray, [(20.0, 0, 400, 1)])
        self.assertEqual(filled, 0)
        self.assertTrue((healed == gray).all())

    def test_a_gap_smaller_than_min_gap_is_left_alone(self):
        gray = self._page()
        gray[20, 0:150] = 0
        gray[20, 155:400] = 0
        healed, filled = layout.heal_line_gaps(
            gray, [(20.0, 0, 150, 1), (20.0, 155, 400, 1)])
        self.assertEqual(filled, 0)
        self.assertTrue((healed == gray).all())

    def test_a_sliver_does_not_extend_a_line(self):
        # 3 px of anything is not half a staff line; a break is only closed
        # between pieces that are long enough to have been a staff line.
        gray = self._page()
        gray[20, 0:150] = 0
        healed, filled = layout.heal_line_gaps(
            gray, [(20.0, 0, 150, 1), (20.0, 250, 253, 1)])
        self.assertEqual(filled, 0)

    def test_two_staff_lines_are_not_joined_to_each_other(self):
        gray = self._page()
        gray[10, 0:150] = 0
        gray[20, 250:400] = 0
        healed, filled = layout.heal_line_gaps(
            gray, [(20.0, 0, 150, 1), (20.0 + 14, 250, 400, 1)])
        self.assertEqual(filled, 0)


if __name__ == "__main__":
    unittest.main()
