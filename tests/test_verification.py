"""Tests for scorehalo.verification — timing diagnostics + clef integration.

The verification report must be structurally sound: per-measure event timing,
overfull-measure flags, and a compact summary() that omits per-note detail
(so manifests stay small).
"""

import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pysrc"))

from scorehalo.verification import verify_musicxml, summary  # noqa: E402


def _score(measures_xml):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<score-partwise version="4.0">\n'
        '  <part-list><score-part id="P1"><part-name>P1</part-name>'
        '</score-part></part-list>\n'
        '  <part id="P1">%s</part>\n'
        '</score-partwise>\n' % measures_xml)


def _measure(num, attrs_xml, notes_xml=""):
    return '<measure number="%d">%s%s</measure>' % (num, attrs_xml, notes_xml)


def _attrs(divisions="1", beats="4", beat_type="4", staves=None):
    a = '<attributes><divisions>%s</divisions>' \
        '<time><beats>%s</beats><beat-type>%s</beat-type></time>' % (divisions, beats, beat_type)
    if staves is not None:
        a += '<staves>%d</staves>' % staves
    a += '</attributes>'
    return a


def _note_xml(dur="4", staff="1", voice="1", pitched=True):
    if pitched:
        return ('<note><pitch><step>C</step><alter>0</alter><octave>4</octave></pitch>'
                '<duration>%s</duration><voice>%s</voice><staff>%s</staff></note>' % (dur, voice, staff))
    return ('<note><rest/><duration>%s</duration><voice>%s</voice><staff>%s</staff></note>' % (dur, voice, staff))


class VerifyMusicXmlTests(unittest.TestCase):

    def test_clean_score_structure(self):
        xml = _score(_measure(1, _attrs(), _note_xml() + _note_xml()))
        report = verify_musicxml(xml)
        self.assertEqual(report["source_review"], "not_performed")
        self.assertIn("findings", report)
        self.assertIn("events", report)
        self.assertIn("clefs", report)
        self.assertEqual(len(report["events"]), 2)

    def test_overfull_measure_flagged(self):
        # 4/4 meter but 5 quarter notes -> extent 5 > meter 4
        xml = _score(_measure(1, _attrs(), "".join(_note_xml() for _ in range(5))))
        report = verify_musicxml(xml)
        overfull = [f for f in report["findings"] if f["code"] == "overfull_measure"]
        self.assertEqual(len(overfull), 1)

    def test_underfull_measure_not_error(self):
        # 4/4 meter but only 2 quarter notes -> underfull, not an error
        xml = _score(_measure(1, _attrs(), _note_xml() + _note_xml()))
        report = verify_musicxml(xml)
        errors = [f for f in report["findings"] if f["severity"] == "error"]
        self.assertEqual(errors, [])

    def test_parse_error_handled(self):
        report = verify_musicxml("not xml")
        self.assertEqual(report["findings"][0]["code"], "xml_parse")
        self.assertEqual(report["source_review"], "not_performed")

    def test_events_have_expected_fields(self):
        xml = _score(_measure(1, _attrs(), _note_xml(dur="2", staff="1", voice="1")))
        report = verify_musicxml(xml)
        ev = report["events"][0]
        self.assertEqual(ev["part"], "P1")
        self.assertEqual(ev["measure"], "1")
        self.assertEqual(ev["onset"], "0")
        self.assertEqual(ev["duration"], "2")
        self.assertEqual(ev["staff"], "1")
        self.assertEqual(ev["voice"], "1")
        self.assertFalse(ev["rest"])
        self.assertIsNotNone(ev["pitch"])

    def test_rest_event(self):
        xml = _score(_measure(1, _attrs(), _note_xml(dur="2", pitched=False)))
        report = verify_musicxml(xml)
        ev = report["events"][0]
        self.assertTrue(ev["rest"])
        self.assertIsNone(ev["pitch"])


class SummaryTests(unittest.TestCase):

    def test_summary_omits_per_note_detail(self):
        xml = _score(_measure(1, _attrs(), _note_xml() + _note_xml()))
        report = verify_musicxml(xml)
        s = summary(report)
        # summary has source_review, findings, clefs with counts only
        self.assertIn("source_review", s)
        self.assertIn("findings", s)
        self.assertIn("clefs", s)
        self.assertIn("declaration_count", s["clefs"])
        self.assertIn("note_assignment_count", s["clefs"])
        # No per-note assignments in summary
        self.assertNotIn("note_assignments", s.get("clefs", {}))
        self.assertNotIn("events", s)

    def test_summary_counts_match_report(self):
        xml = _score(_measure(1, _attrs(), _note_xml() + _note_xml()))
        report = verify_musicxml(xml)
        s = summary(report)
        self.assertEqual(s["clefs"]["declaration_count"], len(report["clefs"]["declarations"]))
        self.assertEqual(s["clefs"]["note_assignment_count"], len(report["clefs"]["note_assignments"]))

    def test_summary_small_manifest(self):
        # summary() should be much smaller than the full report
        xml = _score(_measure(1, _attrs(), _note_xml() + _note_xml() + _note_xml()))
        report = verify_musicxml(xml)
        full = len(str(report))
        compact = len(str(summary(report)))
        self.assertLess(compact, full)


class RealRunTests(unittest.TestCase):
    """If the saved real run exists, verify its verification report."""

    def _run_path(self):
        return "/home/wburt59/Documents/scorehalo-runs/2026-11-05-homr-carpenters-p12/score.musicxml"

    def test_real_run_verifies(self):
        path = self._run_path()
        if not os.path.exists(path):
            self.skipTest("real run not present")
        xml = open(path, encoding="utf-8").read()
        report = verify_musicxml(xml)
        self.assertEqual(len(report["events"]), 205)  # matches note count
        # Clef report embedded
        self.assertEqual(len(report["clefs"]["declarations"]), 3)
        self.assertEqual(len(report["clefs"]["note_assignments"]), 205)


if __name__ == "__main__":
    unittest.main()
