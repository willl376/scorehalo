"""Opt-in slur repair: pair honestly, drop orphans, never renumber or edit notes.

The converter evidence behind the "preserve numbers" rule lives in
``reports/slur-honesty.md``. These tests pin the rules themselves with tiny
synthetic scores so they run in a bare checkout (no data/ directory needed).
"""

import os
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pysrc"))

from scorehalo.slurs import _note_fingerprint, repair_file, summary  # noqa: E402
from scorehalo.validate import validate  # noqa: E402

DOCTYPE = ('<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 4.0 '
           'Partwise//EN" "http://www.musicxml.org/dtds/partwise.dtd">')


def note(slurs=(), *, pitch="C", octave=4, duration=1, staff=None, voice=None):
    attrs = ""
    if voice is not None:
        attrs += f"<voice>{voice}</voice>"
    if staff is not None:
        attrs += f"<staff>{staff}</staff>"
    notations = ""
    if slurs:
        marks = "".join('<slur type="%s" number="%s"/>' % (kind, num)
                        for kind, num in slurs)
        notations = f"<notations>{marks}</notations>"
    return (f'<note>{attrs}<pitch><step>{pitch}</step><octave>{octave}</octave>'
            f'</pitch><duration>{duration}</duration>{notations}</note>')


def score(parts):
    """parts: sequence of (part_id, [note_xml, ...])."""
    plist = "".join(f'<score-part id="{pid}"><part-name>{pid}</part-name></score-part>'
                    for pid, _ in parts)
    body = "".join(
        f'<part id="{pid}"><measure number="1">{notes}</measure></part>'
        for pid, notes in parts)
    return (DOCTYPE + "\n"
            '<score-partwise version="4.0">'
            f'<part-list>{plist}</part-list>{body}'
            '</score-partwise>\n')


class SlurRepairTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name

    def run_repair(self, xml, name="score"):
        src = os.path.join(self.dir, name + ".musicxml")
        out = os.path.join(self.dir, name + ".fixed.musicxml")
        rep = os.path.join(self.dir, name + ".json")
        with open(src, "w", encoding="utf-8") as fh:
            fh.write(xml)
        result = repair_file(src, out, rep)
        with open(out, encoding="utf-8") as fh:
            fixed = fh.read()
        return src, out, rep, result, fixed

    def test_balanced_pair_is_preserved(self):
        xml = score([("P1", [note([("start", "1")]), note([("stop", "1")])])])
        _, out, _, result, fixed = self.run_repair(xml)
        self.assertEqual((result["pairs"], result["orphans"]), (1, 0))
        self.assertTrue(result["notes_unchanged"])
        self.assertEqual(fixed.count('<slur type='), 2)
        self.assertIn('number="1"', fixed)
        ok, report = validate(out)
        self.assertTrue(ok, report["errors"])

    def test_orphan_stop_and_orphan_start_are_dropped(self):
        # Different parts: the stop must NOT be paired with the P1 start.
        xml = score([
            ("P1", [note([("start", "1")])]),
            ("P2", [note([("stop", "1")])]),
        ])
        _, out, _, result, fixed = self.run_repair(xml)
        self.assertEqual((result["pairs"], result["orphans"]), (0, 2))
        self.assertEqual(result["starts_after"], 0)
        self.assertEqual(result["stops_after"], 0)
        self.assertNotIn("<slur ", fixed)
        self.assertTrue(result["notes_unchanged"])
        ok, report = validate(out)
        self.assertTrue(ok, report["errors"])
        reasons = {o["reason"] for o in result["orphan_detail"]}
        self.assertEqual(reasons, {"start_without_stop", "stop_without_start"})

    def test_number_is_part_of_slur_identity(self):
        # A stop on number 2 must not close a start on number 1.
        xml = score([("P1", [note([("start", "1")]), note([("stop", "2")])])])
        _, _, _, result, fixed = self.run_repair(xml)
        self.assertEqual((result["pairs"], result["orphans"]), (0, 2))
        self.assertNotIn("<slur ", fixed)

    def test_voice_streams_are_independent(self):
        xml = score([("P1", [
            note([("start", "1")], voice=1),
            note([("start", "1")], voice=2),
            note([("stop", "1")], voice=1),
            note([("stop", "1")], voice=2),
        ])])
        _, _, _, result, _ = self.run_repair(xml)
        self.assertEqual((result["pairs"], result["orphans"]), (2, 0))
        self.assertEqual([p["voice"] for p in result["pair_detail"]], ["1", "2"])
        self.assertEqual(result["pair_detail"][0]["start"]["note"], 1)
        self.assertEqual(result["pair_detail"][0]["stop"]["note"], 3)

    def test_nested_pairs_use_lifo(self):
        xml = score([("P1", [
            note([("start", "1")]),
            note([("start", "1")]),
            note([("stop", "1")]),
            note([("stop", "1")]),
        ])])
        _, _, _, result, _ = self.run_repair(xml)
        self.assertEqual((result["pairs"], result["orphans"]), (2, 0))
        self.assertEqual(result["pair_detail"][0]["start"]["note"], 2)
        self.assertEqual(result["pair_detail"][0]["stop"]["note"], 3)
        self.assertEqual(result["pair_detail"][1]["start"]["note"], 1)
        self.assertEqual(result["pair_detail"][1]["stop"]["note"], 4)

    def test_doctype_survives_and_empty_notations_is_removed(self):
        xml = score([("P1", [note([("start", "1")])])])
        _, _, _, result, fixed = self.run_repair(xml)
        self.assertTrue(fixed.startswith(DOCTYPE))
        self.assertNotIn("<notations", fixed)
        self.assertEqual((result["pairs"], result["orphans"]), (0, 1))

    def test_validator_flips_from_bad_to_ok(self):
        xml = score([("P1", [
            note([("start", "1")]), note([("stop", "1")]), note([("stop", "1")]),
        ])])
        src, out, _, result, _ = self.run_repair(xml)
        ok_before, before = validate(src)
        ok_after, after = validate(out)
        self.assertFalse(ok_before)
        self.assertTrue(any("unbalanced slurs" in e for e in before["errors"]))
        self.assertTrue(ok_after, after["errors"])
        self.assertEqual((result["pairs"], result["orphans"]), (1, 1))

    def test_second_run_is_idempotent(self):
        xml = score([("P1", [
            note([("start", "1")]), note([("stop", "1")]), note([("stop", "1")]),
        ])])
        _, out, _, first, fixed_once = self.run_repair(xml)
        out2 = os.path.join(self.dir, "twice.musicxml")
        second = repair_file(out, out2)
        self.assertEqual((second["pairs"], second["orphans"]), (1, 0))
        self.assertEqual(first["note_hash_after"], second["note_hash_before"])
        with open(out2, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), fixed_once)

    def test_fingerprint_notices_a_note_edit(self):
        xml = score([("P1", [note([("start", "1")]), note([("stop", "1")])])])
        _, out, _, result, _ = self.run_repair(xml)
        root = ET.parse(out).getroot()
        root.find(".//pitch/step").text = "D"
        self.assertNotEqual(_note_fingerprint(root), result["note_hash_after"])

    def test_summary_omits_full_detail(self):
        xml = score([("P1", [note([("start", "1")]), note([("stop", "1")])])])
        _, _, _, result, _ = self.run_repair(xml)
        compact = summary(result)
        self.assertNotIn("pair_detail", compact)
        self.assertNotIn("orphan_detail", compact)
        self.assertEqual(compact["pairs"], 1)
        self.assertEqual(compact["policy"],
                         "pair_per_part_staff_voice_number; preserve_numbers; drop_orphans")


if __name__ == "__main__":
    unittest.main()
