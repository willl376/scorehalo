"""Tests for dual-count reconciliation.

The interesting failures here are false *confidence*: a comparison that reports
agreement when the two counts actually differ, or that compares fields it has
no business comparing. So most of these tests build two tallies that disagree
in one specific way and assert the verdict names it.

The p0020 tests need both homr runs on disk and skip without them.
"""

import os
import shutil
import tempfile
import unittest
from collections import Counter

from scorehalo import reconcile

PER_STAFF = os.path.join("data", "out-perstaff", "homr", "p0020")
PER_SYSTEM = os.path.join("data", "out-system", "homr", "p0020")


def tally(label="x", notes=10, rests=2, pitched=8, pitches=None,
          measures=4, comparable=reconcile.COUNT_FIELDS):
    result = reconcile.Tally(label=label, measures=measures, notes=notes,
                             rests=rests, pitched=pitched,
                             pitches=Counter(pitches or {}),
                             comparable=tuple(comparable))
    return result


class TestTallyMusicxml(unittest.TestCase):
    def test_counts_notes_rests_and_pitches(self):
        path = os.path.join(PER_STAFF, "p0020.s1_in.musicxml")
        if not os.path.exists(path):
            self.skipTest("p0020 fragments not present")
        counted = reconcile.tally_musicxml(path)
        self.assertEqual(counted.notes, 18)
        self.assertEqual(counted.rests, 4)
        self.assertEqual(counted.pitched, 14)
        self.assertEqual(counted.measures, 4)

    def test_rests_and_pitches_partition_the_notes(self):
        path = os.path.join(PER_STAFF, "p0020.s1_in.musicxml")
        if not os.path.exists(path):
            self.skipTest("p0020 fragments not present")
        counted = reconcile.tally_musicxml(path)
        self.assertEqual(counted.rests + counted.pitched, counted.notes)

    def test_pitch_multiset_size_matches_the_pitched_count(self):
        path = os.path.join(PER_STAFF, "p0020.s1_in.musicxml")
        if not os.path.exists(path):
            self.skipTest("p0020 fragments not present")
        counted = reconcile.tally_musicxml(path)
        self.assertEqual(sum(counted.pitches.values()), counted.pitched)


class TestCombine(unittest.TestCase):
    def test_sums_every_field(self):
        total = reconcile.combine([tally("a", notes=3, rests=1, pitched=2, measures=2),
                                   tally("b", notes=4, rests=0, pitched=4, measures=1)])
        self.assertEqual(total.notes, 7)
        self.assertEqual(total.rests, 1)
        self.assertEqual(total.pitched, 6)
        self.assertEqual(total.measures, 3)

    def test_merges_pitch_multisets(self):
        total = reconcile.combine([tally("a", pitches={("C", "", 4): 2}),
                                   tally("b", pitches={("C", "", 4): 1, ("E", "", 5): 1})])
        self.assertEqual(total.pitches[("C", "", 4)], 3)
        self.assertEqual(sum(total.pitches.values()), 4)

    def test_comparable_fields_intersect(self):
        """A field only one side marks comparable cannot be compared.

        This is the guard against confidently reporting a measure-count
        difference between a per-staff and a per-system crop, where the
        difference is an artefact of crop granularity and means nothing.
        """
        total = reconcile.combine([
            tally("a", comparable=("notes", "rests", "pitched")),
            tally("b", comparable=("notes",)),
        ])
        self.assertEqual(total.comparable, ("notes",))

    def test_empty_input_does_not_raise(self):
        total = reconcile.combine([])
        self.assertEqual(total.notes, 0)


class TestReconcile(unittest.TestCase):
    def test_identical_counts_are_twins(self):
        report = reconcile.reconcile(tally(), tally())
        self.assertTrue(report.twin_counts)
        self.assertTrue(report.twin_pitches)
        self.assertEqual(report.disagreements, [])

    def test_a_pitched_difference_is_reported(self):
        report = reconcile.reconcile(tally(pitched=8), tally(pitched=7))
        self.assertFalse(report.agree("pitched"))
        self.assertEqual(report.disagreements[0].field_name, "pitched")
        self.assertEqual(report.disagreements[0].delta, -1)

    def test_every_differing_field_is_listed(self):
        report = reconcile.reconcile(tally(notes=10, rests=2, pitched=8),
                                     tally(notes=11, rests=3, pitched=8))
        names = {d.field_name for d in report.disagreements}
        self.assertEqual(names, {"notes", "rests"})

    def test_counts_can_match_while_the_notes_differ(self):
        """The case a bare count cannot see.

        Two runs both report 8 pitched notes, and they are not the same 8.
        Reporting this as agreement would be the exact false confidence this
        module exists to prevent.
        """
        left = tally(pitches={("C", "", 4): 8})
        right = tally(pitches={("C", "", 4): 7, ("E", "", 5): 1})
        report = reconcile.reconcile(left, right)
        self.assertTrue(report.twin_counts)
        self.assertFalse(report.twin_pitches)

    def test_pitch_difference_names_the_notes_and_the_direction(self):
        """Positive means the left tally has notes the right lacks.

        left: C4x2, E5x1.  right: C4x1, G4x1.
        So C4 is +1 (left has one spare), E5 is +1 (left has it, right does
        not), G4 is -1 (right has it, left does not).
        """
        left = tally(pitches={("C", "", 4): 2, ("E", "", 5): 1})
        right = tally(pitches={("C", "", 4): 1, ("G", "", 4): 1})
        text = reconcile.reconcile(left, right).describe_pitches()
        self.assertIn("C4 +1", text)
        self.assertIn("E5 +1", text)
        self.assertIn("G4 -1", text)

    def test_pitch_comparison_can_be_declined(self):
        left = tally(pitches={("C", "", 4): 2})
        right = tally(pitches={("C", "", 4): 1})
        report = reconcile.reconcile(left, right, compare_pitches=False)
        self.assertIsNone(report.pitch_difference)
        self.assertTrue(report.twin_pitches)

    def test_incomparable_field_is_skipped_not_failed(self):
        """An incomparable field must not read as agreement either.

        ``twin_counts`` requires that nothing was skipped, so a report that
        could not check everything does not claim everything checked.
        """
        left = tally(comparable=("notes",))
        right = tally(comparable=("notes",))
        report = reconcile.reconcile(left, right)
        self.assertEqual(report.skipped, ("rests", "pitched"))
        self.assertFalse(report.twin_counts)
        self.assertTrue(report.agree("notes"))

    def test_agree_is_true_for_a_skipped_field(self):
        left = tally(comparable=("notes",))
        right = tally(comparable=("notes",))
        self.assertTrue(reconcile.reconcile(left, right).agree("pitched"))

    def test_summarise_leads_with_the_verdict(self):
        report = reconcile.reconcile(tally("per-staff"), tally("per-staff copy"))
        self.assertIn("IDENTICAL TWINS", report.summarise())

    def test_summarise_says_do_not_agree(self):
        report = reconcile.reconcile(tally("a", pitched=8), tally("b", pitched=7))
        self.assertIn("DO NOT AGREE", report.summarise())
        self.assertIn("pitched: 8 vs 7", report.summarise())

    def test_summarise_names_skipped_fields(self):
        report = reconcile.reconcile(tally(comparable=("notes",)),
                                     tally(comparable=("notes",)))
        self.assertIn("not compared", report.summarise())


class TestReconcileDirectories(unittest.TestCase):
    def test_pairs_by_basename(self):
        if not (os.path.isdir(PER_STAFF) and os.path.isdir(PER_SYSTEM)):
            self.skipTest("both homr runs not present")
        reports = reconcile.reconcile_directories(PER_STAFF, PER_SYSTEM)
        self.assertTrue(reports)

    def test_a_missing_file_on_one_side_is_reported(self):
        """Silently pairing nothing would read as agreement.

        That is the failure mode this guards: a crop that exists in one run and
        not the other is a *disagreement*, not an absence.
        """
        import tempfile

        with tempfile.TemporaryDirectory() as left, tempfile.TemporaryDirectory() as right:
            xml = ('<?xml version="1.0"?><score-partwise version="3.1">'
                   '<part-list><score-part id="P1"><part-name>X</part-name>'
                   '</score-part></part-list><part id="P1"><measure number="1">'
                   '<note><pitch><step>C</step><octave>4</octave></pitch>'
                   '<duration>4</duration></note></measure></part>'
                   '</score-partwise>')
            for directory in (left, right):
                with open(os.path.join(directory, "a_in.musicxml"), "w") as handle:
                    handle.write(xml)
            with open(os.path.join(right, "b_in.musicxml"), "w") as handle:
                handle.write(xml)
            reports = reconcile.reconcile_directories(left, right)
            summary = "\n".join(r.summarise() for r in reports)
            self.assertIn("b_in.musicxml", summary)
            self.assertIn("DO NOT AGREE", summary)


@unittest.skipUnless(os.path.isdir(PER_STAFF) and os.path.isdir(PER_SYSTEM),
                     "both homr runs not present")
class TestP0020TwoRuns(unittest.TestCase):
    """The real corpus: per-staff crops against per-system crops.

    Two independent counts of one page. System 1 is expected to reconcile
    exactly; systems 2 and 3 are expected not to, which is the finding the
    whole module was built to make visible.
    """

    #: s1+s2 = system 1, s3+s4+s5 = system 2, s6+s7+s8 = system 3.
    LAYOUT = {1: (1, 2), 2: (3, 4, 5), 3: (6, 7, 8)}

    def _per_staff(self, system):
        tallies = []
        for staff in self.LAYOUT[system]:
            tallies.append(reconcile.tally_musicxml(
                os.path.join(PER_STAFF, "p0020.s%d_in.musicxml" % staff),
                "s%d" % staff))
        return reconcile.combine(tallies, "per-staff sys%d" % system)

    def _per_system(self, system):
        return reconcile.tally_musicxml(
            os.path.join(PER_SYSTEM, "p0020.sys%d_in.musicxml" % system),
            "per-system sys%d" % system)

    def _report(self, system):
        # Measure counts are not comparable across crop granularities.
        left, right = self._per_staff(system), self._per_system(system)
        left.comparable = right.comparable = ("notes", "rests", "pitched")
        return reconcile.reconcile(left, right)

    def test_system_one_reconciles_exactly(self):
        report = self._report(1)
        self.assertTrue(report.twin_counts, report.summarise())
        self.assertTrue(report.twin_pitches, report.summarise())

    def test_system_two_disagrees_on_rests_only(self):
        """Pitched notes agree; the rest difference is homr's padding bars."""
        report = self._report(2)
        self.assertTrue(report.agree("pitched"), report.summarise())
        self.assertFalse(report.agree("rests"), report.summarise())

    def test_system_three_is_missing_a_pitched_note(self):
        report = self._report(3)
        self.assertFalse(report.agree("pitched"), report.summarise())
        self.assertEqual(report.disagreements[0].delta, -1)

    def test_system_three_names_the_differing_pitches(self):
        text = self._report(3).describe_pitches()
        self.assertIn("pitches differ", text)

    def test_whole_page_totals_do_not_reconcile(self):
        left = reconcile.combine(
            [reconcile.tally_musicxml(
                os.path.join(PER_STAFF, "p0020.s%d_in.musicxml" % staff), "s%d" % staff)
             for staff in range(1, 9)], "per-staff")
        right = reconcile.combine(
            [reconcile.tally_musicxml(
                os.path.join(PER_SYSTEM, "p0020.sys%d_in.musicxml" % system),
                "sys%d" % system)
             for system in range(1, 4)], "per-system")
        left.comparable = right.comparable = ("notes", "rests", "pitched")
        report = reconcile.reconcile(left, right)
        self.assertFalse(report.twin_counts, report.summarise())

    def test_measures_are_not_compared_across_granularities(self):
        left = reconcile.combine(
            [reconcile.tally_musicxml(
                os.path.join(PER_STAFF, "p0020.s%d_in.musicxml" % staff), "s%d" % staff)
             for staff in range(1, 9)], "per-staff")
        right = reconcile.combine(
            [reconcile.tally_musicxml(
                os.path.join(PER_SYSTEM, "p0020.sys%d_in.musicxml" % system),
                "sys%d" % system)
             for system in range(1, 4)], "per-system")
        left.comparable = ("notes", "rests", "pitched")
        right.comparable = ("notes", "rests", "pitched", "measures")
        report = reconcile.reconcile(left, right)
        self.assertEqual(report.skipped, ())


class TestPitchName(unittest.TestCase):
    """Pitch keys arrive as TEXT from ElementTree, not ints.

    Keying an accidental lookup on ints misses every time and prints "B-13"
    where it meant "Bb3" -- a pitch disagreement that reads as gibberish rather
    than as a note.
    """

    def test_flat_sharp_and_natural(self):
        self.assertEqual(reconcile.pitch_name("B", "-1", "3"), "Bb3")
        self.assertEqual(reconcile.pitch_name("C", "1", "4"), "C#4")
        self.assertEqual(reconcile.pitch_name("E", "0", "5"), "E5")

    def test_absent_alter_is_natural(self):
        self.assertEqual(reconcile.pitch_name("G", "", "4"), "G4")

    def test_accepts_int_alter_too(self):
        self.assertEqual(reconcile.pitch_name("B", -1, 3), "Bb3")
        self.assertEqual(reconcile.pitch_name("C", 0, 4), "C4")

    def test_unknown_alter_is_marked_rather_than_dropped(self):
        """A value we cannot read must be visible, not silently flattened."""
        self.assertEqual(reconcile.pitch_name("B", "9", "3"), "B?93")


class TestTallyDirectory(unittest.TestCase):
    """Counting a whole crop, which is the only way two crops can be compared.

    The two crops of p0020 have different granularity: three per-system files
    against eight per-staff files, with nothing in common to pair on. Reducing
    each side to one tally first is what makes the comparison meaningful.
    """

    def setUp(self):
        self.work = tempfile.mkdtemp(prefix="tallydir-")
        self.addCleanup(shutil.rmtree, self.work, ignore_errors=True)

    def _write(self, name, notes):
        body = ['<?xml version="1.0" encoding="UTF-8"?>',
                '<score-partwise version="4.0">',
                '  <part-list><score-part id="P1"><part-name>X</part-name>'
                '</score-part></part-list>',
                '  <part id="P1">']
        for i, (step, octave, dur) in enumerate(notes, start=1):
            body.append('    <measure number="%d"><note><pitch><step>%s</step>'
                        '<octave>%s</octave></pitch><duration>%d</duration>'
                        '</note></measure>' % (i, step, octave, dur))
        body.append("  </part>\n</score-partwise>")
        with open(os.path.join(self.work, name), "w") as fh:
            fh.write("\n".join(body))

    def test_sums_every_fragment_in_the_directory(self):
        self._write("a_in.musicxml", [("C", 4, 4), ("D", 4, 4)])
        self._write("b_in.musicxml", [("E", 4, 4)])
        total = reconcile.tally_directory(self.work, "both")
        self.assertEqual(total.notes, 3)
        self.assertEqual(total.pitched, 3)

    def test_ignores_files_that_are_not_fragments(self):
        self._write("a_in.musicxml", [("C", 4, 4)])
        self._write("a_in.png", [])       # must not be counted
        with open(os.path.join(self.work, "a.homr.log"), "w") as fh:
            fh.write("log")
        self.assertEqual(reconcile.tally_directory(self.work).notes, 1)

    def test_empty_directory_counts_nothing_but_is_comparable_with_nothing(self):
        empty = tempfile.mkdtemp(prefix="tallydir-empty-")
        self.addCleanup(shutil.rmtree, empty, ignore_errors=True)
        total = reconcile.tally_directory(empty)
        self.assertEqual(total.notes, 0)
        self.assertEqual(total.comparable, (),
                         "a run that counted nothing must not claim to agree")


class TestReconcileCrops(unittest.TestCase):
    """Two crops of one page, compared as totals."""

    def setUp(self):
        self.left = tempfile.mkdtemp(prefix="crops-l-")
        self.right = tempfile.mkdtemp(prefix="crops-r-")
        self.addCleanup(shutil.rmtree, self.left, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.right, ignore_errors=True)

    def _write(self, directory, name, pitches):
        body = ['<?xml version="1.0" encoding="UTF-8"?>',
                '<score-partwise version="4.0">',
                '  <part-list><score-part id="P1"><part-name>X</part-name>'
                '</score-part></part-list>', '  <part id="P1">']
        for i, step in enumerate(pitches, start=1):
            body.append('    <measure number="%d"><note><pitch><step>%s</step>'
                        '<octave>4</octave></pitch><duration>4</duration>'
                        '</note></measure>' % (i, step))
        body.append("  </part>\n</score-partwise>")
        with open(os.path.join(directory, name), "w") as fh:
            fh.write("\n".join(body))

    def test_different_granularity_but_the_same_notes_agree(self):
        """Three files vs one file, same pitches: identical twins.

        This is the case per-file pairing cannot express at all. It used to
        report every fragment as "present on one side only".
        """
        for i, step in enumerate("CDE", start=1):
            self._write(self.left, "sys%d_in.musicxml" % i, [step])
        self._write(self.right, "s1_in.musicxml", ["C", "D", "E"])
        report = reconcile.reconcile_crops(self.left, self.right,
                                           left_label="system", right_label="staff")
        self.assertTrue(report.twin_counts)
        self.assertTrue(report.twin_pitches)

    def test_a_note_missing_from_one_crop_is_caught(self):
        self._write(self.left, "sys1_in.musicxml", ["C", "D"])
        self._write(self.right, "s1_in.musicxml", ["C"])
        report = reconcile.reconcile_crops(self.left, self.right)
        self.assertFalse(report.twin_counts)
        self.assertFalse(report.twin_pitches)
        self.assertFalse(report.agree("notes"))

    def test_counts_matching_while_pitches_differ_is_not_twin_pitches(self):
        """Trading one note for another is invisible to a headcount."""
        self._write(self.left, "sys1_in.musicxml", ["C", "D"])
        self._write(self.right, "s1_in.musicxml", ["C", "E"])
        report = reconcile.reconcile_crops(self.left, self.right)
        self.assertTrue(report.twin_counts, "counts do match here")
        self.assertFalse(report.twin_pitches, "but the notes do not")
        self.assertIn("pitches differ", report.summarise())

    def test_a_missing_crop_disagrees_loudly(self):
        self._write(self.left, "sys1_in.musicxml", ["C", "D", "E"])
        report = reconcile.reconcile_crops(self.left, self.right)
        self.assertFalse(report.twin_counts)
        self.assertFalse(report.agree("notes"))


if __name__ == "__main__":
    unittest.main()

class TestUnmeasuredIsNotAgreement(unittest.TestCase):
    """The worst failure mode for a double-count check.

    A run that measured nothing came back as a clean pass: the comparable-field
    intersection is empty, so nothing is compared, no disagreement is recorded,
    and ``agree()`` returns True. The run that found nothing is precisely the one
    that has to be caught.
    """

    def setUp(self):
        self.empty = tempfile.mkdtemp(prefix="unmeasured-")
        self.addCleanup(shutil.rmtree, self.empty, ignore_errors=True)

    def test_two_empty_directories_are_not_twins(self):
        report = reconcile.reconcile_crops(self.empty, self.empty)
        self.assertFalse(report.measured)
        self.assertFalse(report.twin_counts)
        self.assertFalse(report.twin_pitches)

    def test_agree_is_false_when_nothing_was_measured(self):
        report = reconcile.reconcile_crops(self.empty, self.empty)
        self.assertFalse(report.agree("notes"))
        self.assertFalse(report.agree("pitched"))

    def test_says_so_in_words(self):
        report = reconcile.reconcile_crops(self.empty, self.empty)
        self.assertIn("NEITHER RUN MEASURED", report.summarise())
        self.assertIn("not agreement", report.summarise())

    def test_an_empty_side_against_a_counted_one_disagrees(self):
        counted = tempfile.mkdtemp(prefix="counted-")
        self.addCleanup(shutil.rmtree, counted, ignore_errors=True)
        body = ['<?xml version="1.0" encoding="UTF-8"?>',
                '<score-partwise version="4.0">',
                '  <part-list><score-part id="P1"><part-name>X</part-name>'
                '</score-part></part-list>', '  <part id="P1">',
                '    <measure number="1"><note><pitch><step>C</step><octave>4'
                '</octave></pitch><duration>4</duration></note></measure>',
                '  </part>', '</score-partwise>']
        with open(os.path.join(counted, "a_in.musicxml"), "w") as fh:
            fh.write("\n".join(body))

        report = reconcile.reconcile_crops(counted, self.empty)
        self.assertTrue(report.measured, "one side did measure")
        self.assertFalse(report.twin_counts)
        self.assertFalse(report.agree("notes"))
