"""MusicXML content-model invariants: element ORDER, not element values.

Every bug this file pins was invisible to the rest of the suite. The numbers in
the file were correct, the tests were green, and MuseScore still refused the
document with ``importMusicXml() ... is not a valid MusicXML file``. Element
order inside ``<note>`` is fixed by the MusicXML content model and a validating
importer rejects the whole file when it is violated -- so a checker that only
counts notes, sums durations or compares pitches cannot see the defect at all.

Three separate defects shipped together:

* ``<tie>`` was written BEFORE ``<pitch>`` (the model wants it after
  ``<duration>``);
* ``<voice>`` was written AFTER ``<type>``/``<dot>``/``<time-modification>``
  (it belongs immediately after ``<duration>``), which is what homr's own
  fragments do correctly and emit did not;
* a padded measure in a two-staff part emitted one measure rest per staff with
  no ``<backup>``, so a strict cursor summed them into a bar twice as long as
  its meter -- our own validator said ``bar overflows: 96 > 48`` while MuseScore
  said the file was invalid.

Each test rebuilds the broken shape and asserts the checker CATCHES it, because
a checker that cannot fail is not a checker.

Run: .venv/bin/python -m unittest discover -s tests -v
"""

import os
import sys
import unittest
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pysrc"))

from scorehalo.emit import GLOBAL_DIVISIONS, emit_page  # noqa: E402
from scorehalo.graph import Measure, Note, Page, Part, Pitch  # noqa: E402

#: Children of ``<note>`` in the order the MusicXML content model requires.
#: Indices must be non-decreasing across a note's children; anything else makes
#: a validating importer reject the document.
NOTE_ORDER = [
    "chord",
    "pitch", "unpitched", "rest",
    "duration",
    "tie", "instrument", "footnote", "level",
    "voice",
    "type",
    "dot",
    "accidental", "time-modification", "stem", "notehead", "notehead-text",
    "staff",
    "beam", "notations", "lyric",
]
_RANK = {tag: i for i, tag in enumerate(NOTE_ORDER)}


def note_order_problems(root):
    """Every ``<note>`` whose children leave the content-model order."""
    problems = []
    for note in root.iter("note"):
        tags = [c.tag for c in note]
        ranks = [_RANK[t] for t in tags if t in _RANK]
        if ranks != sorted(ranks):
            where = "measure %s" % (note.getparent().get("number")
                                    if hasattr(note, "getparent") else "?")
            problems.append("%s: children out of order: %s" % (where, tags))
    return problems


def _pitch():
    return Pitch(step="G", alter=0, octave=4)


def _note(onset, duration, staff=1, **kw):
    return Note(staff=staff, voice=kw.pop("voice", 1), onset=onset,
                duration=duration, pitch=_pitch(), measure=1, **kw)


def _two_staff_page():
    """One part, two staves, one padded measure: the shape that broke."""
    part = Part(id="P1", name="Piano", clef_by_staff={1: ("G", 2),
                                                      2: ("F", 4)})
    m1 = Measure(number=1, divisions=GLOBAL_DIVISIONS)
    m1.notes = [
        _note(0, 12, tie="start"),                 # <tie> must follow duration
        _note(0, 12, staff=2),
        _note(0, 12, staff=1),                     # same onset -> <chord/>
        _note(12, 18, slur="start"),               # dotted -> <dot/>
        _note(30, 8, tuplet=(3, 2)),               # -> <time-modification/>
    ]
    m2 = Measure(number=2, divisions=GLOBAL_DIVISIONS, is_empty=True)
    part.measures = [m1, m2]
    page = Page(id="p", divisions=GLOBAL_DIVISIONS)
    page.parts = [part]
    return page


class TestNoteContentModel(unittest.TestCase):
    """Element order inside <note> is part of the format, not a style choice."""

    def setUp(self):
        self.root = ET.fromstring(emit_page(_two_staff_page()))

    def test_note_children_follow_the_content_model(self):
        self.assertEqual(note_order_problems(self.root), [])

    def test_tie_comes_after_duration_and_before_type(self):
        tags = []
        for note in self.root.iter("note"):
            if note.find("tie") is not None:
                tags = [c.tag for c in note]
                break
        self.assertIn("tie", tags, "fixture never emitted a tie; test is vacuous")
        self.assertGreater(tags.index("tie"), tags.index("duration"),
                           "<tie> must come after <duration>")
        self.assertLess(tags.index("tie"), tags.index("type"),
                        "<tie> must come before <voice>/<type>")

    def test_voice_precedes_type(self):
        for note in self.root.iter("note"):
            tags = [c.tag for c in note]
            if "voice" in tags and "type" in tags:
                self.assertLess(tags.index("voice"), tags.index("type"),
                                "<voice> after <type> makes a validating "
                                "importer reject the whole file: %s" % tags)
                return
        self.fail("no note carried both <voice> and <type>; test is vacuous")

    def test_dotted_note_keeps_dot_after_type(self):
        for note in self.root.iter("note"):
            tags = [c.tag for c in note]
            if "dot" in tags:
                self.assertLess(tags.index("type"), tags.index("dot"))
                self.assertLess(tags.index("dot"), tags.index("staff"))
                return
        self.fail("no dotted note; test is vacuous")

    def test_checker_catches_voice_after_type(self):
        """The regression form: reorder a note and the checker must fail."""
        root = self.root
        for note in root.iter("note"):
            tags = [c.tag for c in note]
            if "voice" in tags and "type" in tags:
                children = list(note)
                vi = tags.index("voice")
                voice = children.pop(vi)
                note.insert(tags.index("type") + 1, voice)  # put it back late
                self.assertTrue(note_order_problems(root),
                                "checker did not notice <voice> after <type>")
                return
        self.fail("no note to corrupt; test is vacuous")


class TestMultiStaffPaddedMeasure(unittest.TestCase):
    """A rest on every staff is right; the cursor has to be rewound between."""

    def setUp(self):
        self.measure = None
        for m in ET.fromstring(emit_page(_two_staff_page())).iter("measure"):
            if m.find("attributes/staves") is not None or \
                    any(n.findtext("staff") == "2" for n in m.findall("note")):
                if all(n.find("rest") is not None for n in m.findall("note")):
                    self.measure = m
                    break

    def test_finds_the_all_rest_measure(self):
        self.assertIsNotNone(self.measure,
                             "fixture produced no all-rest measure to inspect")

    def test_each_staff_rest_is_reached_by_a_backup(self):
        """Cursor model: every staff's content starts at tick 0."""
        m = self.measure
        cursor, worst = 0, 0
        for el in m:
            dur = int(el.findtext("duration") or 0)
            if el.tag == "backup":
                cursor -= dur
            elif el.tag == "note" and el.find("chord") is None:
                cursor += dur
            worst = min(worst, cursor)
        self.assertGreaterEqual(worst, 0, "<backup> overshoots the bar start")
        self.assertLessEqual(cursor, GLOBAL_DIVISIONS * 4,
                             "staves summed past the barline: %d ticks" % cursor)

    def test_checker_catches_missing_backup(self):
        """Drop the <backup> and the same cursor walk must overflow."""
        m = self.measure
        for el in list(m):
            if el.tag == "backup":
                m.remove(el)
                break
        else:
            self.fail("no <backup> to remove; test is vacuous")
        cursor = 0
        for el in m:
            dur = int(el.findtext("duration") or 0)
            if el.tag == "note" and el.find("chord") is None:
                cursor += dur
        self.assertGreater(cursor, GLOBAL_DIVISIONS * 4,
                           "cursor did not overflow without the <backup>")


class TestPartListDeclaresInstruments(unittest.TestCase):
    """MuseScore warns "no instrument found for part" without a label."""

    def test_every_score_part_carries_a_score_instrument(self):
        root = ET.fromstring(emit_page(_two_staff_page()))
        for sp in root.iter("score-part"):
            name = sp.findtext("part-name")
            inst = sp.find("score-instrument/instrument-name")
            self.assertIsNotNone(inst, "score-part %s has no instrument label"
                                 % sp.get("id"))
            self.assertEqual(inst.text, name,
                             "the label must repeat the name homr read, not "
                             "invent one")

    def test_no_midi_program_is_invented(self):
        """Choosing a sound is the same class of error as guessing a clef."""
        root = ET.fromstring(emit_page(_two_staff_page()))
        self.assertIsNone(root.find("part-list/score-part/midi-instrument"))


if __name__ == "__main__":
    unittest.main()


class TestAttributesDeclaredOnce(unittest.TestCase):
    """Restating <time> in every silent bar paints signatures the book lacks.

    The engraver caught a stray 4/4 at the right end of the top system:
    renderers print the signatures they are SHOWN, and every padded bar used
    to carry a fresh <attributes> block with key/time/clef. Inheritance is
    the format's own rule -- declare once at the part's first bar and let
    the rest read against it.
    """

    def test_an_empty_bar_mid_part_declares_nothing(self):
        root = ET.fromstring(emit_page(_two_staff_page()))
        measures = root.find("part").findall("measure")
        self.assertIsNotNone(measures[0].find("attributes"))
        self.assertIsNone(
            measures[1].find("attributes"),
            "the empty second bar must inherit, not redeclare")

    def test_the_time_signature_is_declared_exactly_once_per_part(self):
        short = Part(id="P1", name="Voice")
        short.measures = [
            Measure(number=1, divisions=GLOBAL_DIVISIONS, beats=4,
                    beat_type=4, notes=[_note(0, 12)]),
        ]
        long = Part(id="P2", name="Piano")
        long.measures = [
            Measure(number=1, divisions=GLOBAL_DIVISIONS, beats=4,
                    beat_type=4, notes=[_note(0, 12)]),
            Measure(number=2, divisions=GLOBAL_DIVISIONS, beats=4,
                    beat_type=4, notes=[_note(0, 12)]),
        ]
        page = Page(id="p", divisions=GLOBAL_DIVISIONS)
        page.parts = [short, long]
        xml = emit_page(page)   # P1 gets a padded bar 2 from the width pad
        self.assertEqual(xml.count("<measure"), 4)
        self.assertEqual(
            xml.count("<time>"), 2,
            "one <time> per part, not one per padded bar:\n%s" % xml)
