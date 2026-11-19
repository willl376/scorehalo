"""``scorehalo validate`` must not invent a schema rule that does not exist.

MusicXML puts a tuplet note's ``<type>`` *beside* ``<time-modification>``,
never inside it (the element's children are actual-notes, normal-notes and
optionally normal-type/normal-dot). The old check looked for a ``<type>``
child, so every correctly written triplet was reported invalid: 0/6 on the
shipped band set while MuseScore, musicxml2ly and LilyPond all accepted the
same files.

The rule that matters is the note's own sibling ``<type>``. Without it the
printed value of the tuplet is ambiguous, which is what this pins.
"""

import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pysrc"))

from scorehalo.validate import validate  # noqa: E402


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


TUPLET = ('<time-modification><actual-notes>3</actual-notes>'
          '<normal-notes>2</normal-notes></time-modification>')


class TupletTypeTests(unittest.TestCase):

    def _check(self, xml):
        with tempfile.NamedTemporaryFile(
                "w", suffix=".musicxml", delete=False) as fh:
            fh.write(xml)
            path = fh.name
        try:
            return validate(path)
        finally:
            os.unlink(path)

    def test_a_sibling_type_makes_the_tuplet_valid(self):
        note = ('<note><pitch><step>C</step><octave>4</octave></pitch>'
                '<duration>2</duration><voice>1</voice><type>eighth</type>'
                + TUPLET + '</note>')
        ok, report = self._check(_score(note))
        self.assertTrue(ok, report["errors"])
        self.assertEqual(report["errors"], [])

    def test_a_tuplet_note_without_a_type_is_reported(self):
        note = ('<note><pitch><step>C</step><octave>4</octave></pitch>'
                '<duration>2</duration><voice>1</voice>'
                + TUPLET + '</note>')
        ok, report = self._check(_score(note))
        self.assertFalse(ok)
        self.assertTrue(
            any("tuplet note without a <type>" in e for e in report["errors"]),
            report["errors"])

    def test_a_type_child_of_time_modification_does_not_count(self):
        # A <type> in the wrong place is not the note's printed value; it is
        # the non-schema shape the old repair wrote.
        note = ('<note><pitch><step>C</step><octave>4</octave></pitch>'
                '<duration>2</duration><voice>1</voice>'
                '<time-modification><actual-notes>3</actual-notes>'
                '<normal-notes>2</normal-notes><type>eighth</type>'
                '</time-modification></note>')
        ok, report = self._check(_score(note))
        self.assertFalse(ok)
        self.assertTrue(
            any("tuplet note without a <type>" in e for e in report["errors"]),
            report["errors"])


def _score_ids(part_list, parts):
    """A minimal score with chosen <score-part>/<part> ids."""
    declarations = "".join(
        f'<score-part id="{pid}"><part-name>{pid}</part-name></score-part>'
        for pid in part_list)
    body = "".join(
        f'<part id="{pid}"><measure number="1">'
        '<note><pitch><step>C</step><octave>4</octave></pitch>'
        '<duration>4</duration><type>whole</type></note>'
        '</measure></part>' for pid in parts)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<score-partwise version="4.0">'
        f'<part-list>{declarations}</part-list>{body}'
        '</score-partwise>\n')


class PartIdTests(unittest.TestCase):

    def _check(self, xml):
        with tempfile.NamedTemporaryFile(
                "w", suffix=".musicxml", delete=False) as fh:
            fh.write(xml)
            path = fh.name
        try:
            return validate(path)
        finally:
            os.unlink(path)

    def test_matching_ids_are_valid(self):
        ok, report = self._check(_score_ids(["P1", "P2"], ["P1", "P2"]))
        self.assertTrue(ok, report["errors"])

    def test_part_without_declaration_is_reported(self):
        ok, report = self._check(_score_ids(["P1"], ["P1", "P2"]))
        self.assertFalse(ok)
        self.assertTrue(any("part id 'P2' has no" in e for e in report["errors"]),
                        report["errors"])

    def test_declaration_without_part_is_reported(self):
        ok, report = self._check(_score_ids(["P1", "P3"], ["P1"]))
        self.assertFalse(ok)
        self.assertTrue(any("<score-part id='P3'> has no" in e
                            for e in report["errors"]), report["errors"])

    def test_duplicate_ids_are_reported(self):
        ok, report = self._check(_score_ids(["P1", "P1"], ["P1", "P1"]))
        self.assertFalse(ok)
        self.assertTrue(any("duplicate score-part id 'P1'" in e
                            for e in report["errors"]), report["errors"])
        self.assertTrue(any("duplicate part id 'P1'" in e
                            for e in report["errors"]), report["errors"])


if __name__ == "__main__":
    unittest.main()
