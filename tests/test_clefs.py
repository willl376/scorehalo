"""Tests for scorehalo.clefs — read-only clef/staff audit.

No repair occurs; unknown timing produces unknown assignments rather than
guessed clefs. These tests exercise the audit on synthetic MusicXML and on
the real saved run at Documents/scorehalo-runs/... (when present).
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pysrc"))

from scorehalo.clefs import audit_clefs, declaration_inventory, compare_clef_maps  # noqa: E402


def _score(measures_xml):
    """Wrap measure XML in a minimal score-partwise document."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<score-partwise version="4.0">\n'
        '  <part-list><score-part id="P1"><part-name>P1</part-name>'
        '</score-part></part-list>\n'
        '  <part id="P1">%s</part>\n'
        '</score-partwise>\n' % measures_xml)


def _measure(num, attrs_xml, notes_xml=""):
    return '<measure number="%d">%s%s</measure>' % (num, attrs_xml, notes_xml)


def _clef_attrs(staff="1", sign="G", line="2"):
    return ('<attributes><divisions>1</divisions>'
            '<time><beats>4</beats><beat-type>4</beat-type></time>'
            '<clef number="%s"><sign>%s</sign><line>%s</line></clef>'
            '</attributes>' % (staff, sign, line))


def _note_xml(dur="4", staff="1", voice="1", pitched=True):
    if pitched:
        return ('<note><pitch><step>C</step><alter>0</alter><octave>4</octave></pitch>'
                '<duration>%s</duration><voice>%s</voice><staff>%s</staff></note>' % (dur, voice, staff))
    return ('<note><rest/><duration>%s</duration><voice>%s</voice><staff>%s</staff></note>' % (dur, voice, staff))


class ClefAuditTests(unittest.TestCase):

    def test_clean_score_no_findings(self):
        xml = _score(_measure(1, _clef_attrs(), _note_xml() + _note_xml()))
        report = audit_clefs(xml)
        self.assertEqual(report["findings"], [])
        self.assertEqual(len(report["declarations"]), 1)
        self.assertEqual(report["declarations"][0]["sign"], "G")
        self.assertEqual(report["declarations"][0]["line"], "2")

    def test_clef_inherited_across_measures(self):
        xml = _score(
            _measure(1, _clef_attrs(), _note_xml()) +
            _measure(2, "", _note_xml()))
        report = audit_clefs(xml)
        # Measure 2 inherits clef from measure 1; no unresolved assignments.
        unresolved = [n for n in report["note_assignments"] if n["clef_origin"] == "unresolved"]
        self.assertEqual(unresolved, [])

    def test_clef_conflict_dedupe_one_per_time(self):
        # Two different clef declarations at the same onset in measure 1.
        attrs = (_clef_attrs(staff="1", sign="G", line="2") +
                 _clef_attrs(staff="1", sign="F", line="4"))
        xml = _score(_measure(1, attrs, _note_xml() + _note_xml()))
        report = audit_clefs(xml)
        conflicts = [f for f in report["findings"] if f["code"] == "clef_conflict"]
        # One conflict per conflicting time, not one per note.
        self.assertEqual(len(conflicts), 1)

    def test_clef_staff_invalid_flagged(self):
        attrs = _clef_attrs(staff="3")  # staff 3 but only 1 staff declared
        xml = _score(_measure(1, attrs, _note_xml(staff="3")))
        report = audit_clefs(xml)
        codes = {f["code"] for f in report["findings"]}
        self.assertIn("clef_staff_invalid", codes)

    def test_clef_sign_invalid_flagged(self):
        attrs = '<attributes><divisions>1</divisions>' \
                '<time><beats>4</beats><beat-type>4</beat-type></time>' \
                '<clef number="1"><sign>X</sign><line>2</line></clef>' \
                '</attributes>'
        xml = _score(_measure(1, attrs, _note_xml()))
        report = audit_clefs(xml)
        codes = {f["code"] for f in report["findings"]}
        self.assertIn("clef_sign_invalid", codes)

    def test_clef_unresolved_for_pitched_note(self):
        # A pitched note with no clef declaration at all.
        xml = _score(_measure(1, '<attributes><divisions>1</divisions></attributes>', _note_xml()))
        report = audit_clefs(xml)
        unresolved = [f for f in report["findings"] if f["code"] == "clef_unresolved"]
        self.assertGreaterEqual(len(unresolved), 1)

    def test_note_assignments_have_clef_origin(self):
        xml = _score(_measure(1, _clef_attrs(), _note_xml() + _note_xml()))
        report = audit_clefs(xml)
        for n in report["note_assignments"]:
            self.assertIn("clef_origin", n)
            self.assertIn(n["clef_origin"], ("declared_this_measure", "inherited", "unresolved"))

    def test_declaration_inventory_structure(self):
        xml = _score(_measure(1, _clef_attrs(), _note_xml()))
        inv = declaration_inventory(xml)
        self.assertIn("P1", inv)
        self.assertEqual(len(inv["P1"]), 1)

    def test_compare_clef_maps_identical(self):
        xml = _score(_measure(1, _clef_attrs(), _note_xml()))
        result = compare_clef_maps(xml, xml)
        self.assertTrue(result["declarations_identical"])
        self.assertEqual(result["a_count"], result["b_count"])

    def test_parse_error_handled(self):
        report = audit_clefs("not xml at all")
        self.assertEqual(report["findings"][0]["code"], "clef_xml_parse")
        self.assertEqual(report["source_review"], "not_performed")


class RealRunTests(unittest.TestCase):
    """If the saved real run exists, verify its clef audit matches expectations."""

    def _run_path(self):
        return "/home/wburt59/Documents/scorehalo-runs/2026-11-05-homr-carpenters-p12/score.musicxml"

    def test_real_run_clef_declarations(self):
        path = self._run_path()
        if not os.path.exists(path):
            self.skipTest("real run not present")
        xml = open(path, encoding="utf-8").read()
        report = audit_clefs(xml)
        # 3 clef declarations: P1 staff 1 G2, P1 staff 2 F4, P2 staff 1 G2
        decls = report["declarations"]
        self.assertEqual(len(decls), 3)
        p1_staff1 = [d for d in decls if d["part"] == "P1" and d["staff"] == 1]
        p1_staff2 = [d for d in decls if d["part"] == "P1" and d["staff"] == 2]
        p2_staff1 = [d for d in decls if d["part"] == "P2" and d["staff"] == 1]
        self.assertEqual(len(p1_staff1), 1)
        self.assertEqual(len(p1_staff2), 1)
        self.assertEqual(len(p2_staff1), 1)
        self.assertEqual(p1_staff1[0]["sign"], "G")
        self.assertEqual(p1_staff1[0]["line"], "2")
        self.assertEqual(p1_staff2[0]["sign"], "F")
        self.assertEqual(p1_staff2[0]["line"], "4")
        self.assertEqual(p2_staff1[0]["sign"], "G")
        self.assertEqual(p2_staff1[0]["line"], "2")

    def test_real_run_note_assignments_count(self):
        path = self._run_path()
        if not os.path.exists(path):
            self.skipTest("real run not present")
        xml = open(path, encoding="utf-8").read()
        report = audit_clefs(xml)
        # 205 note assignments (matches the MusicXML note count)
        self.assertEqual(len(report["note_assignments"]), 205)


if __name__ == "__main__":
    unittest.main()
