"""Tests for the per-page review: pattern breaks + two-count reconciliation.

``scorehalo review`` is where Wilbur's two ideas meet, so the tests here are as
much about "does it say something true" as about "does it run": a review that
reports agreement because it measured nothing is worse than no review, and one
that reports twelve spurious flags because it paired files that cannot pair is
just noise with a severity number.
"""

import os
import shutil
import tempfile
import unittest

from scorehalo import review


def _fragment(directory, name, parts):
    """Write one MusicXML fragment. ``parts`` is {role: [pitches per measure]}."""
    body = ['<?xml version="1.0" encoding="UTF-8"?>',
            '<score-partwise version="4.0">', "  <part-list>"]
    for index, role in enumerate(parts, start=1):
        body.append('    <score-part id="P%d"><part-name>%s</part-name>'
                    "</score-part>" % (index, role))
    body.append("  </part-list>")
    for index, (role, measures) in enumerate(parts.items(), start=1):
        body.append('  <part id="P%d">' % index)
        for number, pitches in enumerate(measures, start=1):
            body.append('    <measure number="%d">' % number)
            body.append('      <attributes><divisions>4</divisions>'
                        '<time><beats>4</beats><beat-type>4</beat-type></time>'
                        "</attributes>")
            for step in pitches:
                body.append('      <note><pitch><step>%s</step><octave>4</octave>'
                            "</pitch><duration>4</duration><voice>1</voice>"
                            "</note>" % step)
            body.append("    </measure>")
        body.append("  </part>")
    body.append("</score-partwise>")
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, name), "w") as fh:
        fh.write("\n".join(body))


class _TwoCrops(unittest.TestCase):
    """Two crop roots for one page, plus per-system and per-staff fragments."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="review-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.system_root = os.path.join(self.root, "system")
        self.perstaff_root = os.path.join(self.root, "perstaff")
        self.system_dir = os.path.join(self.system_root, "homr", "p0001")
        self.perstaff_dir = os.path.join(self.perstaff_root, "homr", "p0001")

    def _agreeing(self):
        """Three system files carrying one staff; eight staff files, same notes."""
        for i, step in enumerate("CDE", start=1):
            _fragment(self.system_dir, "p0001.sys%d_in.musicxml" % i,
                      {"Piano": [step]})
            _fragment(self.perstaff_dir, "p0001.s%d_in.musicxml" % i,
                      {"Piano": [step]})
        _fragment(self.system_dir, "p0001.sys4_in.musicxml", {"Piano": ["C"]})
        _fragment(self.perstaff_dir, "p0001.s4_in.musicxml", {"Piano": ["C"]})


class TestDiscoverPages(_TwoCrops, unittest.TestCase):

    def test_finds_a_page_present_under_both_roots(self):
        self._agreeing()
        self.assertEqual(
            review.discover_pages(self.system_root, self.perstaff_root),
            ["p0001"])

    def test_a_page_only_under_one_root_is_not_reviewable(self):
        """Only one crop exists, so there is nothing to count it against."""
        _fragment(self.system_dir, "p0001.sys1_in.musicxml", {"Piano": ["C"]})
        self.assertEqual(
            review.discover_pages(self.system_root, self.perstaff_root), [])

    def test_no_homr_subdirectory_is_no_pages(self):
        self.assertEqual(
            review.discover_pages(self.root, self.root), [])


class TestPageFromSystemFragments(_TwoCrops, unittest.TestCase):

    def test_keeps_one_system_per_fragment(self):
        """The assembled page cannot be used here: it stamps every part system=1.

        ``build_page_graph`` merges each role's measures across the page and sets
        ``system=1`` throughout, so grouping by system -- which is all
        ``find_deviations`` does -- finds one system and reports "too few" on
        every page in the corpus.
        """
        self._agreeing()
        page = review.page_from_system_fragments(self.system_dir)
        self.assertEqual(sorted(page.systems()), [1, 2, 3, 4])

    def test_parts_are_not_merged_by_role(self):
        """Role identity is not what pattern analysis compares."""
        self._agreeing()
        page = review.page_from_system_fragments(self.system_dir)
        self.assertEqual(len(page.parts), 4)

    def test_empty_directory_gives_a_page_with_no_parts(self):
        page = review.page_from_system_fragments(self.system_dir)
        self.assertEqual(page.parts, [])


class TestReviewPage(_TwoCrops, unittest.TestCase):

    def test_two_crops_with_the_same_notes_produce_no_reconciliation_flags(self):
        """Different granularity, identical music: identical twins."""
        self._agreeing()
        report = review.review_page("p0001", self.system_dir, self.perstaff_dir)
        checks = {f["check"] for f in report["flags"]}
        self.assertNotIn("count_disagreement", checks)
        self.assertNotIn("pitch_disagreement", checks)
        self.assertTrue(report["crops"].twin_pitches)

    def test_a_note_missing_from_one_crop_is_flagged_at_severity_3(self):
        _fragment(self.system_dir, "p0001.sys1_in.musicxml",
                  {"Piano": ["C", "D", "E"]})
        _fragment(self.perstaff_dir, "p0001.s1_in.musicxml", {"Piano": ["C"]})
        report = review.review_page("p0001", self.system_dir, self.perstaff_dir)
        flags = [f for f in report["flags"] if f["check"] == "count_disagreement"]
        self.assertTrue(flags)
        self.assertEqual(flags[0]["severity"], 3)

    def test_counts_matching_while_pitches_differ_is_still_flagged(self):
        """Trading C for E is invisible to a headcount and must not pass."""
        _fragment(self.system_dir, "p0001.sys1_in.musicxml", {"Piano": ["C", "D"]})
        _fragment(self.perstaff_dir, "p0001.s1_in.musicxml", {"Piano": ["C", "E"]})
        report = review.review_page("p0001", self.system_dir, self.perstaff_dir)
        checks = {f["check"] for f in report["flags"]}
        self.assertIn("pitch_disagreement", checks)
        # twin_counts is DEFINED as ignoring the pitch multiset, so it is
        # correctly True here; only twin_pitches catches the traded note. That
        # split is the whole point -- a headcount alone would call this a pass.
        self.assertTrue(report["crops"].twin_counts)
        self.assertFalse(report["crops"].twin_pitches)
        self.assertNotIn("count_disagreement", checks)

    def test_pitch_names_are_readable_not_arithmetic(self):
        """alter arrives as text; int-keyed accidentals print "B-13" for "Bb3"."""
        _fragment(self.system_dir, "p0001.sys1_in.musicxml", {"Piano": ["C", "D"]})
        _fragment(self.perstaff_dir, "p0001.s1_in.musicxml", {"Piano": ["C", "E"]})
        report = review.review_page("p0001", self.system_dir, self.perstaff_dir)
        details = " ".join(f["detail"] for f in report["flags"])
        self.assertIn("pitch", details)
        self.assertNotIn("C-4", details)

    def test_no_fragments_says_so_rather_than_reporting_a_pass(self):
        report = review.review_page("p0001", self.system_dir, self.perstaff_dir)
        checks = {f["check"] for f in report["flags"]}
        self.assertIn("missing_fragment", checks)
        self.assertIsNone(report["patterns"])

    def test_pattern_deviation_is_reported_when_a_system_breaks(self):
        """Two systems identical, one with a different staff count."""
        _fragment(self.system_dir, "p0001.sys1_in.musicxml",
                  {"Piano": ["C", "D"], "Voice": ["E"]})
        _fragment(self.system_dir, "p0001.sys2_in.musicxml",
                  {"Piano": ["C", "D"], "Voice": ["E"]})
        _fragment(self.system_dir, "p0001.sys3_in.musicxml", {"Piano": ["C", "D"]})
        _fragment(self.perstaff_dir, "p0001.s1_in.musicxml", {"Piano": ["C"]})
        report = review.review_page("p0001", self.system_dir, self.perstaff_dir)
        self.assertIsNotNone(report["patterns"])
        self.assertEqual(len(report["patterns"].observed), 3)
        deviations = [f for f in report["flags"] if f["check"] == "pattern_deviation"]
        self.assertTrue(deviations, "the odd system must be reported")
        self.assertIn("system 3", deviations[0]["where"])

    def test_report_is_json_shaped_for_the_cli(self):
        """The CLI serialises flags only; these must all be plain values."""
        self._agreeing()
        report = review.review_page("p0001", self.system_dir, self.perstaff_dir)
        for flag in report["flags"]:
            for key in ("check", "severity", "where", "detail"):
                self.assertIn(key, flag)
            self.assertIsInstance(flag["severity"], int)
            self.assertIsInstance(flag["detail"], str)


class TestCoverageSeparation(_TwoCrops, unittest.TestCase):
    """A crop that missed staves is a segmentation bug, not a reading one.

    Measured over the 16 real pages: the per-system run found every system on all
    16, while the per-staff run missed staves on 5 (p0022 lost 11 of 12). Folding
    that into "the two runs disagree" points the reader at the wrong layer of the
    pipeline and overstates how much recognition is at fault.
    """

    def _boxes(self, n_staves, n_systems):
        import json
        os.makedirs(self.system_root, exist_ok=True)
        path = os.path.join(self.system_root, "p0001.boxes.json")
        with open(path, "w") as fh:
            json.dump({"n_staves": n_staves, "n_systems": n_systems}, fh)
        return self.system_root

    def test_a_crop_that_found_everything_raises_nothing(self):
        self._agreeing()
        self._boxes(n_staves=4, n_systems=4)
        flags = review.coverage_flags("p0001", self.system_dir,
                                      self.perstaff_dir, self.system_root)
        self.assertEqual(flags, [])

    def test_a_missing_staff_is_reported_as_coverage_not_notes(self):
        self._agreeing()
        self._boxes(n_staves=9, n_systems=4)      # only 4 staves were written
        flags = review.coverage_flags("p0001", self.system_dir,
                                      self.perstaff_dir, self.system_root)
        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0]["check"], "coverage_miss")
        self.assertEqual(flags[0]["severity"], 3)
        self.assertIn("segmentation miss", flags[0]["detail"])

    def test_the_detail_names_both_counts_and_says_staves_not_staffs(self):
        self._agreeing()
        self._boxes(n_staves=9, n_systems=4)
        detail = review.coverage_flags("p0001", self.system_dir,
                                       self.perstaff_dir,
                                       self.system_root)[0]["detail"]
        self.assertIn("4 of 9", detail)
        self.assertIn("5 missing", detail)
        self.assertIn("staves", detail)
        self.assertNotIn("staffs", detail)

    def test_a_missing_system_is_reported_too(self):
        self._agreeing()
        self._boxes(n_staves=4, n_systems=4)
        flags = review.coverage_flags("p0001", self.system_dir,
                                      self.perstaff_dir, self.system_root)
        self.assertEqual(flags, [])
        # and with more systems expected than found
        self._boxes(n_staves=4, n_systems=7)
        flags = review.coverage_flags("p0001", self.system_dir,
                                      self.perstaff_dir, self.system_root)
        self.assertEqual(len(flags), 1)
        self.assertIn("systems", flags[0]["detail"])

    def test_no_boxes_file_means_no_coverage_claim_rather_than_a_guess(self):
        self._agreeing()
        self.assertEqual(
            review.coverage_flags("p0001", self.system_dir, self.perstaff_dir,
                                  self.system_root), [])

    def test_count_disagreements_carry_the_coverage_caveat(self):
        """A reader must not read a missed crop as a note-reading error."""
        self._boxes(n_staves=9, n_systems=4)
        report = review.review_page("p0001", self.system_dir,
                                    self.perstaff_dir, None, self.system_root)
        counts = [f for f in report["flags"] if f["check"] == "count_disagreement"]
        self.assertTrue(counts)
        for flag in counts:
            self.assertIn("a crop missed staves", flag["detail"])

    def test_no_caveat_when_coverage_is_complete(self):
        self._agreeing()
        self._boxes(n_staves=4, n_systems=4)
        _fragment(self.system_dir, "p0001.sys9_in.musicxml", {"Piano": ["C", "D"]})
        report = review.review_page("p0001", self.system_dir,
                                    self.perstaff_dir, None, self.system_root)
        counts = [f for f in report["flags"] if f["check"] == "count_disagreement"]
        self.assertTrue(counts)
        for flag in counts:
            self.assertNotIn("a crop missed staves", flag["detail"])


class TestFormatReview(_TwoCrops, unittest.TestCase):

    def test_no_pages_is_stated_plainly(self):
        self.assertEqual(review.format_review([]), "review: no pages found")

    def test_verdict_line_is_included(self):
        self._agreeing()
        text = review.format_review(
            [review.review_page("p0001", self.system_dir, self.perstaff_dir)])
        self.assertIn("IDENTICAL TWINS", text)

    def test_disagreement_is_stated_plainly(self):
        _fragment(self.system_dir, "p0001.sys1_in.musicxml", {"Piano": ["C", "D"]})
        _fragment(self.perstaff_dir, "p0001.s1_in.musicxml", {"Piano": ["C"]})
        text = review.format_review(
            [review.review_page("p0001", self.system_dir, self.perstaff_dir)])
        self.assertIn("DO NOT AGREE", text)


if __name__ == "__main__":
    unittest.main()