"""A tuplet's <type> is a NOTE sibling, never a child of <time-modification>.

The old repair read the schema backwards: it appended <type> (and <dot>) inside
<time-modification>, so every triplet in the corpus carried a non-schema child
while the note's real printed value sat correctly beside the tuplet element.
This file pins the corrected behaviour and, crucially, includes tests that
would FAIL against the old implementation (a checker that cannot fail is not a
checker).
"""

import os
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pysrc"))

from scorehalo.repair import TUPLET_TYPE, repair_time_modifications  # noqa: E402


def _read(path):
    with open(path) as fh:
        return fh.read()


def _score(note):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<score-partwise version="4.0">\n'
        ' <part-list><score-part id="P1"><part-name>S</part-name>'
        '</score-part></part-list>\n'
        ' <part id="P1"><measure number="1">\n'
        '  <attributes><divisions>1</divisions>'
        '<time><beats>4</beats><beat-type>4</beat-type></time></attributes>\n'
        '  %s\n'
        ' </measure></part>\n'
        '</score-partwise>\n' % note
    )


def _tuplet_note(ratio=(3, 2), sibling_type=True, child_type=False,
                 dotted_child=False, with_voice=True, with_accidental=False):
    a, n = ratio
    parts = ['<note><pitch><step>C</step><octave>4</octave></pitch>',
             '<duration>2</duration>']
    if with_voice:
        parts.append('<voice>1</voice>')
    if sibling_type:
        parts.append('<type>eighth</type>')
    if with_accidental:
        parts.append('<accidental>sharp</accidental>')
    tm = ['<time-modification><actual-notes>%s</actual-notes>'
          '<normal-notes>%s</normal-notes>' % (a, n)]
    if child_type:
        tm.append('<type>eighth</type>')
    if dotted_child:
        tm.append('<dot/>')
    tm.append('</time-modification>')
    parts.append(''.join(tm))
    parts.append('<staff>1</staff></note>')
    return ''.join(parts)


NOTE_ORDER = ["pitch", "duration", "voice", "type", "dot", "accidental",
              "time-modification", "staff"]


class RepairTupletTests(unittest.TestCase):

    def _repair(self, xml):
        """Write xml to a temp file, run the repair, return (count, root)."""
        with tempfile.NamedTemporaryFile(
                "w", suffix=".musicxml", delete=False) as fh:
            fh.write(xml)
            path = fh.name
        try:
            count = repair_time_modifications(path)
            root = ET.parse(path).getroot()
            return count, root, _read(path)
        finally:
            os.unlink(path)

    def _note(self, root):
        notes = list(root.iter("note"))
        self.assertEqual(len(notes), 1)
        return notes[0]

    # -- the bug the old repair introduced ---------------------------------

    def test_strips_child_type_from_time_modification(self):
        xml = _score(_tuplet_note(sibling_type=True, child_type=True))
        count, root, _ = self._repair(xml)
        tm = self._note(root).find("time-modification")
        self.assertIsNone(tm.find("type"),
                          "a <type> child of <time-modification> is not schema")
        self.assertEqual(count, 1, "a healed note must be counted")

    def test_strips_child_dot_from_time_modification(self):
        xml = _score(_tuplet_note(sibling_type=True, child_type=True,
                                  dotted_child=True))
        _, root, _ = self._repair(xml)
        tm = self._note(root).find("time-modification")
        self.assertIsNone(tm.find("dot"))

    def test_existing_sibling_type_is_not_overwritten(self):
        # 3/2 normally implies "eighth", but if a note already carries a type
        # we must not invent a different one -- printed value is source truth.
        xml = _score(_tuplet_note(sibling_type=True, child_type=True))
        _, root, _ = self._repair(xml)
        self.assertEqual(self._note(root).findtext("type"), "eighth")

    # -- adding a missing sibling type -------------------------------------

    def test_adds_sibling_type_when_missing(self):
        xml = _score(_tuplet_note(sibling_type=False))
        count, root, _ = self._repair(xml)
        note = self._note(root)
        self.assertEqual(note.findtext("type"), "eighth")
        self.assertIsNone(note.find("time-modification/type"),
                          "repair must not reintroduce the bad child shape")
        self.assertEqual(count, 1)

    def test_inferred_type_uses_ratio(self):
        for ratio, expected in ((("2", "3"), "quarter"),
                                (("3", "4"), "quarter"),
                                (("5", "4"), "sixteenth"),
                                (("7", "4"), "sixteenth"),
                                (("9", "8"), "thirty-second")):
            with self.subTest(ratio=ratio):
                xml = _score(_tuplet_note(ratio=ratio, sibling_type=False))
                _, root, _ = self._repair(xml)
                self.assertEqual(self._note(root).findtext("type"), expected)

    def test_dotted_ratio_gets_a_dot_sibling(self):
        xml = _score(_tuplet_note(ratio=(6, 4), sibling_type=False))
        _, root, _ = self._repair(xml)
        note = self._note(root)
        self.assertEqual(note.findtext("type"), "eighth")
        self.assertIsNotNone(note.find("dot"),
                             "a dotted tuplet requires a note-level <dot>")
        self.assertIsNone(note.find("time-modification/dot"))

    def test_dotted_ratio_does_not_duplicate_existing_dot(self):
        note = _tuplet_note(ratio=(6, 4), sibling_type=False)
        note = note.replace("</note>", "<dot/></note>")  # dot but no type
        xml = _score(note)
        _, root, _ = self._repair(xml)
        self.assertEqual(len(self._note(root).findall("dot")), 1)

    def test_unknown_ratio_is_left_alone(self):
        xml = _score(_tuplet_note(ratio=(11, 7), sibling_type=False))
        count, root, out = self._repair(xml)
        self.assertEqual(count, 0)
        self.assertIsNone(self._note(root).find("type"),
                          "an unknown ratio must not be guessed at")

    # -- structural properties ---------------------------------------------

    def test_note_children_keep_content_model_order(self):
        for note_xml in (_tuplet_note(sibling_type=False),
                         _tuplet_note(sibling_type=False, with_accidental=True),
                         _tuplet_note(sibling_type=True, child_type=True)):
            with self.subTest(note=note_xml[:40]):
                _, root, _ = self._repair(_score(note_xml))
                tags = [c.tag for c in self._note(root)]
                ranks = [NOTE_ORDER.index(t) for t in tags if t in NOTE_ORDER]
                self.assertEqual(ranks, sorted(ranks),
                                 "out of content-model order: %s" % tags)

    def test_idempotent_second_run_does_nothing(self):
        xml = _score(_tuplet_note(sibling_type=True, child_type=True))
        first_count, _, first_out = self._repair(xml)
        self.assertEqual(first_count, 1)
        with tempfile.NamedTemporaryFile(
                "w", suffix=".musicxml", delete=False) as fh:
            fh.write(first_out)
            path = fh.name
        try:
            self.assertEqual(repair_time_modifications(path), 0)
            self.assertEqual(_read(path), first_out)
        finally:
            os.unlink(path)

    def test_healthy_file_is_untouched(self):
        xml = _score(_tuplet_note(sibling_type=True))
        count, _, out = self._repair(xml)
        self.assertEqual(count, 0)
        self.assertNotIn("<type>eighth</type><dot/>", out)

    def test_all_known_ratios_are_inferred(self):
        self.assertEqual(set(TUPLET_TYPE), {
            ("2", "3"), ("3", "2"), ("3", "4"), ("5", "4"),
            ("6", "4"), ("7", "4"), ("9", "8"),
        })


if __name__ == "__main__":
    unittest.main()
