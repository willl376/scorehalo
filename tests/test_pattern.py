"""Tests for the pattern-recognition module.

The module's whole purpose is to say "I do not recognise this" instead of
guessing, so the tests are mostly about the ways it could be wrong in the
direction of *false confidence*: quietly matching things that differ, and
quietly flagging things that legitimately vary.

The p0020 tests run against real homr output under ``data/out-perstaff``. They
skip if that data is absent rather than failing, since a fixture missing from a
checkout is not a defect in the code.
"""

import os
import unittest

from scorehalo import pattern
from scorehalo.graph import Measure, Note, Page, Part, Pitch

DATA = os.path.join("data", "out-perstaff", "homr", "p0020")

#: s1+s2 = system 1, s3+s4+s5 = system 2, s6+s7+s8 = system 3, per the brace
#: measurements in scorehalo.brace and Wilbur's reading of the page.
P0020_LAYOUT = {1: (1, 2), 2: (3, 4, 5), 3: (6, 7, 8)}


def notes(measure_number, staff, octave, count, step="C"):
    return [
        Note(staff=staff, voice=1, onset=index * 4, duration=4,
             pitch=Pitch(step, 0, octave), measure=measure_number)
        for index in range(count)
    ]


def piano_part(system, staff_index=0, id_suffix=None, extra_staff=False):
    measures = [
        Measure(number=1, clef=("G", 2), notes=notes(1, 1, 4, 2)),
        Measure(number=1, clef=("F", 4), notes=notes(1, 2, 2, 2)),
    ]
    clefs = {1: ("G", 2), 2: ("F", 4)}
    if extra_staff:
        measures.append(Measure(number=1, clef=("C", 3), notes=notes(1, 3, 3, 1)))
        clefs[3] = ("C", 3)
    return Part(id="p%s%s" % (system, id_suffix or ""), name="Piano",
                system=system, staff_index=staff_index,
                measures=measures, clef_by_staff=clefs)


def voice_part(system, staff_index=1):
    return Part(id="v%s" % system, name="Voice", system=system,
                staff_index=staff_index,
                measures=[Measure(number=1, clef=("G", 2),
                                  notes=notes(1, 1, 5, 1))],
                clef_by_staff={1: ("G", 2)})


def three_system_page(**kwargs):
    parts = []
    for system in (1, 2, 3):
        parts.append(piano_part(system, **kwargs))
        parts.append(voice_part(system))
    return Page(id="t", parts=parts)


class TestSignature(unittest.TestCase):
    def test_counts_one_entry_per_staff_not_per_part(self):
        signature = pattern.signature_of([piano_part(1)])
        self.assertEqual(signature.staff_count, 2)
        self.assertEqual(signature.clefs, (("G", 2), ("F", 4)))

    def test_piano_and_voice_are_different_shapes(self):
        """A two-staff part and a one-staff part must never share a signature.

        This is the confusion that put a singer's staff inside the piano; the
        signature exists to make it impossible to commit again.
        """
        piano = pattern.signature_of([piano_part(1)])
        voice = pattern.signature_of([voice_part(1)])
        self.assertNotEqual(piano.staff_count, voice.staff_count)

    def test_orders_staves_by_index_not_by_input_order(self):
        """Same indices, opposite input order, same signature.

        The indices must be *identical* in both calls: giving them different
        values tests a different thing, and testing that different thing is how
        the first version of this test passed for the wrong reason.
        """
        forwards = pattern.signature_of([piano_part(1, 0), voice_part(1, 2)])
        backwards = pattern.signature_of([voice_part(1, 2), piano_part(1, 0)])
        self.assertEqual(forwards.clefs, backwards.clefs)

    def test_uses_per_staff_note_counts(self):
        part = piano_part(1)
        part.measures[0].notes = notes(1, 1, 4, 5)
        signature = pattern.signature_of([part])
        self.assertEqual(signature.pitch_counts, (5, 2))

    def test_meter_comes_from_the_measures(self):
        part = voice_part(1)
        part.measures[0].beats = 3
        self.assertEqual(pattern.signature_of([part]).meter, (3, 4))

    def test_bar_lengths_track_the_measures(self):
        part = voice_part(1)
        part.measures.append(Measure(number=2, notes=notes(2, 1, 5, 1)))
        self.assertEqual(pattern.signature_of([part]).bar_lengths, ((16, 16),))

    def test_missing_clef_is_marked_unknown_not_guessed(self):
        part = voice_part(1)
        part.measures[0].clef = None
        part.clef_by_staff = {}
        self.assertEqual(pattern.signature_of([part]).clefs, (("?", 0),))


class TestPatternKey(unittest.TestCase):
    def test_ignores_notes_so_two_verses_match(self):
        """The bug this module was written to avoid.

        Including note counts made every real page report "nothing repeats",
        which is the detector failing at the one job it exists for.
        """
        first = piano_part(1)
        second = piano_part(2)
        second.measures[0].notes = notes(1, 1, 6, 9)  # different music entirely
        self.assertEqual(
            pattern.signature_of([first]).pattern_key,
            pattern.signature_of([second]).pattern_key,
        )

    def test_ignores_bar_count_so_different_verses_match(self):
        first = piano_part(1)
        second = piano_part(2)
        second.measures.append(Measure(number=2, clef=("G", 2),
                                       notes=notes(2, 1, 4, 2)))
        self.assertEqual(
            pattern.signature_of([first]).pattern_key,
            pattern.signature_of([second]).pattern_key,
        )

    def test_catches_a_changed_clef(self):
        first = piano_part(1)
        second = piano_part(2)
        second.clef_by_staff = {1: ("G", 2), 2: ("C", 3)}
        second.measures[1].clef = ("C", 3)
        self.assertNotEqual(
            pattern.signature_of([first]).pattern_key,
            pattern.signature_of([second]).pattern_key,
        )

    def test_catches_a_changed_meter(self):
        first = voice_part(1)
        second = voice_part(2)
        second.measures[0].beats = 3
        self.assertNotEqual(
            pattern.signature_of([first]).pattern_key,
            pattern.signature_of([second]).pattern_key,
        )

    def test_catches_a_gained_staff(self):
        self.assertNotEqual(
            pattern.signature_of([piano_part(1)]).pattern_key,
            pattern.signature_of([piano_part(2, extra_staff=True)]).pattern_key,
        )


class TestFindDeviations(unittest.TestCase):
    def test_regular_page_reports_nothing(self):
        report = pattern.find_deviations(three_system_page())
        self.assertEqual(report.deviations, [])

    def test_reports_the_broken_system(self):
        parts = []
        for system in (1, 2, 3):
            parts.append(piano_part(system, extra_staff=(system == 3)))
            parts.append(voice_part(system))
        report = pattern.find_deviations(Page(id="t", parts=parts))
        self.assertEqual([d.system for d in report.deviations], [3])

    def test_names_the_feature_that_broke(self):
        parts = []
        for system in (1, 2, 3):
            parts.append(piano_part(system, extra_staff=(system == 3)))
            parts.append(voice_part(system))
        text = str(pattern.find_deviations(Page(id="t", parts=parts)).deviations[0])
        self.assertIn("clefs", text)
        self.assertIn("C3", text)

    def test_expected_phrases_carry_real_values_not_empty_placeholders(self):
        """Regression: a stub representative printed "expected []" for every
        count field, burying the two phrases a reader actually needs."""
        parts = []
        for system in (1, 2, 3):
            parts.append(piano_part(system, extra_staff=(system == 3)))
            parts.append(voice_part(system))
        text = str(pattern.find_deviations(Page(id="t", parts=parts)).deviations[0])
        self.assertNotIn("expected []", text)
        self.assertNotIn("expected ()", text)

    def test_an_opening_system_can_still_be_flagged(self):
        """Evidence outranks the opening of the page.

        Systems 2 and 3 agreeing is a fact about the page; system 1 being first
        does not exempt it from being wrong.
        """
        parts = []
        for system in (1, 2, 3):
            parts.append(piano_part(system, extra_staff=(system == 1)))
            parts.append(voice_part(system))
        report = pattern.find_deviations(Page(id="t", parts=parts))
        self.assertEqual([d.system for d in report.deviations], [1])

    def test_explicit_reference_is_honoured(self):
        page = three_system_page()
        report = pattern.find_deviations(page, reference=[1])
        self.assertEqual(report.reference, [1])

    def test_reference_naming_nothing_falls_back(self):
        report = pattern.find_deviations(three_system_page(), reference=[99])
        self.assertTrue(report.reference)

    def test_empty_page_does_not_raise(self):
        report = pattern.find_deviations(Page(id="t", parts=[]))
        self.assertEqual(report.deviations, [])
        self.assertEqual(report.observed, {})

    def test_page_where_nothing_repeats_is_marked_weak(self):
        """Three systems of three different shapes: no evidence to compare to.

        Getting this wrong is easy -- my first fixture broke only system 1, so
        systems 2 and 3 still repeated, the page was *not* weak, and the test
        failed for a reason that had nothing to do with what it claimed.
        """
        parts = [
            piano_part(1, 0),                                   # 2 staves
            piano_part(2, 0), voice_part(2, 2),                 # 3 staves
            piano_part(3, 0, extra_staff=True), voice_part(3, 4),  # 4 staves
        ]
        report = pattern.find_deviations(Page(id="t", parts=parts))
        self.assertEqual(report.repeats(), [])
        self.assertTrue(report.weak)

    def test_strong_page_is_not_weak(self):
        self.assertFalse(pattern.find_deviations(three_system_page()).weak)

    def test_matched_lists_the_unbroken_systems(self):
        parts = []
        for system in (1, 2, 3):
            parts.append(piano_part(system, extra_staff=(system == 3)))
            parts.append(voice_part(system))
        report = pattern.find_deviations(Page(id="t", parts=parts))
        self.assertEqual(report.matched(), [1, 2])


class TestColumnAnalysis(unittest.TestCase):
    def test_finds_the_recurring_clef_by_position(self):
        report = pattern.find_deviations(three_system_page())
        self.assertEqual(len(report.columns), 3)
        for column in report.columns:
            self.assertTrue(column.consistent, str(column))

    def test_flags_a_clef_that_breaks_its_column(self):
        """The p0020 shape: the bass position is F4 twice, so a G2 is wrong.

        Only the *bass* staff is disturbed. My first fixture changed the third
        staff instead and asserted the wrong column, so it passed or failed for
        reasons unrelated to the claim.
        """
        parts = []
        for system in (1, 2, 3):
            part = piano_part(system, staff_index=0)
            if system == 3:
                part.clef_by_staff = {1: ("G", 2), 2: ("G", 2)}
                part.measures[1].clef = ("G", 2)
            parts.append(part)
            parts.append(voice_part(system, staff_index=2))
        report = pattern.find_deviations(Page(id="t", parts=parts))
        column = report.columns[1]
        self.assertEqual(column.agreed, ("F", 4))
        self.assertFalse(column.consistent)
        self.assertEqual([s for s, _ in column.outliers], [3])

    def test_reports_bar_length_disagreement_per_position(self):
        parts = []
        for system in (1, 2, 3):
            part = voice_part(system)
            if system == 2:
                part.measures[0].beats = 3   # 36 ticks where the others are 48
            parts.append(part)
        report = pattern.find_deviations(Page(id="t", parts=parts))
        self.assertEqual(report.columns[0].agreed_bar_length, 16)
        self.assertEqual(report.columns[0].bar_outliers, [2])

    def test_a_position_seen_once_predicts_nothing(self):
        """One reading is not a majority; claiming otherwise is how a detector
        invents certainty it does not have."""
        report = pattern.find_deviations(three_system_page())
        self.assertTrue(all(c.established for c in report.columns))

    def test_established_accepts_a_two_of_three_majority(self):
        """The boundary: 2 of 3 is enough to be evidence, 1 of 3 is not.

        Pinning both sides matters, because the threshold is what stops this
        from inventing a pattern out of a single reading.
        """
        parts = []
        for system in (1, 2, 3):
            part = piano_part(system, staff_index=0)
            if system == 1:
                part.clef_by_staff = {1: ("G", 2), 2: ("G", 2)}
                part.measures[1].clef = ("G", 2)
            parts.append(part)
            parts.append(voice_part(system, staff_index=2))
        column = pattern.find_deviations(Page(id="t", parts=parts)).columns[1]
        self.assertTrue(column.established)
        self.assertEqual(column.votes, 2)
        self.assertEqual(column.total, 3)

    def test_all_readings_differing_reports_no_agreement(self):
        parts = []
        for system in (1, 2, 3):
            part = voice_part(system)
            part.measures[0].clef = (("G", "F", "C")[system - 1], 2)
            part.clef_by_staff = {1: part.measures[0].clef}
            parts.append(part)
        column = pattern.find_deviations(Page(id="t", parts=parts)).columns[0]
        self.assertFalse(column.established)


class TestReporting(unittest.TestCase):
    def test_summarise_mentions_the_deviation(self):
        parts = []
        for system in (1, 2, 3):
            parts.append(piano_part(system, extra_staff=(system == 3)))
            parts.append(voice_part(system))
        text = pattern.summarise(Page(id="t", parts=parts))
        self.assertIn("deviation", text)
        self.assertIn("system 3", text)

    def test_summarise_explains_a_weak_page(self):
        parts = [
            piano_part(1, 0),
            piano_part(2, 0), voice_part(2, 2),
            piano_part(3, 0, extra_staff=True), voice_part(3, 4),
        ]
        self.assertIn("nothing repeats", pattern.summarise(Page(id="t", parts=parts)))

    def test_describe_key_renders_meter_and_clefs(self):
        self.assertEqual(
            pattern.describe_key((2, (4, 4), (("G", 2), ("F", 4)))),
            "2 staff(s), 4/4, G2 F4",
        )

    def test_describe_key_handles_absent_meter(self):
        self.assertIn("no meter", pattern.describe_key((1, None, (("G", 2),))))

    def test_describe_key_handles_absent_clefs(self):
        self.assertIn("no clef", pattern.describe_key((1, (4, 4), ())))


class TestBuiltInExample(unittest.TestCase):
    def test_example_page_finds_its_own_deviation(self):
        report = pattern.find_deviations(pattern.example_page())
        self.assertEqual([d.system for d in report.deviations], [4])


@unittest.skipUnless(os.path.isdir(DATA), "p0020 fragments not present")
class TestP0020RealFragments(unittest.TestCase):
    """The real corpus. This is where the module earns its place.

    Expected findings, from the measurements recorded in scorehalo.brace:
    systems 2 and 3 share a pattern (3 staves, G2 G2 F4); system 1 has two
    staves and homr gave it G2 G2, which is the ``i < 5`` clef rewrite.
    """

    def _page(self):
        from scorehalo.emit import parse_part

        parts = []
        for system, staves in sorted(P0020_LAYOUT.items()):
            for position, staff in enumerate(staves, start=1):
                path = os.path.join(DATA, "p0020.s%d_in.musicxml" % staff)
                parts.extend(parse_part(path, staff_index=position, system=system))
        return Page(id="p0020", parts=parts)

    def test_systems_two_and_three_share_a_pattern(self):
        report = pattern.find_deviations(self._page())
        repeating = report.repeats()
        self.assertTrue(repeating, "no pattern repeated on the real page")
        self.assertEqual(repeating[0][1], [2, 3])

    def test_system_one_is_reported_as_a_deviation(self):
        report = pattern.find_deviations(self._page())
        self.assertEqual([d.system for d in report.deviations], [1])

    def test_system_one_deviation_names_the_missing_bass_clef(self):
        report = pattern.find_deviations(self._page())
        broke = report.deviations[0].broke
        self.assertTrue(any("F4" in phrase for phrase in broke), broke)

    def test_bar_length_column_catches_the_three_four_bar(self):
        """System 2's piano staves hold 36-tick bars where every other system
        holds 48 -- homr's declared 3/4, which the pattern contradicts."""
        report = pattern.find_deviations(self._page())
        bar_outliers = [s for column in report.columns for s in column.bar_outliers]
        self.assertIn(2, bar_outliers)

    def test_the_real_page_is_not_weak(self):
        self.assertFalse(pattern.find_deviations(self._page()).weak)


if __name__ == "__main__":
    unittest.main()