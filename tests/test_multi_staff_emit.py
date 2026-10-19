"""Tests for emitting a genuinely multi-staff part.

The defect these exist to prevent
---------------------------------
``Part.clef_by_staff`` said which clef belonged to which staff, and the graph
carried it correctly -- but the *emitter* never read it. Every note was written
with ``<staff>1</staff>``, no ``<staves>`` element was emitted, and a single
scalar clef was declared. So a piano's two staves came out as one staff with
both hands on it, which is what ``musicxml2ly`` was handed and the reason the
engraved score collapsed.

Mutation testing is why this file exists rather than a comment in ``emit.py``:
three separate mutations of the emit path -- dropping the note restamping,
dropping ``<staves>``, and collapsing the per-staff clefs to one -- all left the
whole suite green. The graph-level tests passed because the graph was never
wrong; only the file on disk was. A test that cannot see the bug is not a test
of it, so these assert on the emitted XML text.

Also pinned here: a padded bar of a two-staff part needs a rest on *every*
staff. One staff's worth of silence leaves the other staff with no bar content
at all, which importers read as a short bar -- the exact "looks like success"
failure mode already documented in :func:`scorehalo.emit.emit_page`.

Run: .venv/bin/python -m unittest tests.test_multi_staff_emit -v
"""

import os
import re
import sys
import unittest
from dataclasses import replace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pysrc"))

from scorehalo.emit import GLOBAL_DIVISIONS, emit_page
from scorehalo.graph import Measure, Note, Page, Part, Pitch
from scorehalo.grouping import assemble_page

G = ("G", 2)
F = ("F", 4)


def staff(measures=1, notes_per_measure=2, step="c", octave=4, clef=None):
    """One single-staff fragment, exactly as ``parse_part`` returns one.

    Every note carries ``staff=1``, because that is true of each fragment in
    isolation -- homr recognised it as its own staff 1.
    """
    part = Part(id="S", name="Staff")
    for number in range(1, measures + 1):
        notes = [Note(staff=1, voice=1, onset=i, duration=1,
                      pitch=Pitch(step=step, alter=0, octave=octave),
                      measure=number) for i in range(notes_per_measure)]
        part.measures.append(Measure(number=number, divisions=GLOBAL_DIVISIONS,
                                     notes=notes, clef=clef))
    return part


def part_xml(xml, index=1):
    """The text of one <part>, so assertions cannot be confused by a sibling.

    Every page here holds a piano AND a singer, and only the piano is
    multi-staff. Asserting on the whole document cannot tell "the singer is
    also two staves" from "the piano is", which is the mistake that made the
    first draft of these tests fail for the wrong reason.
    """
    body = xml.split('<part id="P%d">' % index)[1].split("</part>")[0]
    return body


def bars(part):
    """Each <measure> of a part, keyed by its number."""
    out = {}
    for num, body in re.findall(r'<measure number="(\d+)">(.*?)</measure>',
                                part, re.S):
        out[int(num)] = body
    return out


def piano_page(singer_bars=2, piano_bars=2):
    """A 2-staff piano with a one-staff singer above it, all in system 1."""
    staves = [staff(piano_bars, step="e", octave=5),   # staff 1, treble
              staff(piano_bars, step="g", octave=3),   # staff 2, bass
              staff(singer_bars, step="c", octave=5)]   # the singer
    return assemble_page(staves, [1, 1, 1], [G, F, G],
                         brace_ids=["p", "p", None], id="multi")


class SingleStaffEmissionTests(unittest.TestCase):
    """A one-staff part must be unchanged: no <staves>, one plain clef."""

    def setUp(self):
        self.voice = part_xml(emit_page(piano_page()), index=2)

    def test_no_staves_element_for_one_staff_part(self):
        self.assertNotIn("<staves>", self.voice)

    def test_one_staff_part_has_a_scalar_clef(self):
        """The voice keeps the old form, with no ``number`` attribute."""
        self.assertNotIn("<clef number=", self.voice)
        self.assertIn("<clef><sign>G</sign><line>2</line></clef>", self.voice)

    def test_the_piano_still_declares_two_staves(self):
        """Sanity: the assertion above is about the singer, not the document."""
        piano = part_xml(emit_page(piano_page()), index=1)
        self.assertIn("<staves>2</staves>", piano)


class TwoStaffEmissionTests(unittest.TestCase):
    """The piano must reach the file as a piano."""

    def setUp(self):
        self.piano = part_xml(emit_page(piano_page()))

    def test_staves_element_is_emitted(self):
        self.assertIn("<staves>2</staves>", self.piano)

    def test_notes_land_on_both_staves(self):
        self.assertEqual(self.piano.count("<staff>1</staff>"), 4)
        self.assertEqual(self.piano.count("<staff>2</staff>"), 4)

    def test_each_staff_declares_its_own_clef(self):
        self.assertIn('<clef number="1"><sign>G</sign><line>2</line></clef>',
                      self.piano)
        self.assertIn('<clef number="2"><sign>F</sign><line>4</line></clef>',
                      self.piano)

    def test_the_piano_is_not_all_treble(self):
        """The whole point: a bass clef on the lower staff.

        Both clefs coming out as treble was the audible symptom -- the left hand
        an octave and a third too high.
        """
        signs = re.findall(r'<clef number="\d"><sign>([A-Z])</sign>',
                           self.piano)
        self.assertEqual(signs, ["G", "F"])

    def test_staves_element_precedes_the_clefs(self):
        """Schema order matters: <staves> belongs between <time> and <clef>."""
        self.assertLess(self.piano.index("<time>"), self.piano.index("<staves>"))
        self.assertLess(self.piano.index("<staves>"), self.piano.index("<clef"))

    def test_every_note_in_the_part_carries_a_staff_number(self):
        notes = self.piano.count("<note>")
        staves = self.piano.count("<staff>")
        self.assertEqual(notes, staves,
                         "a <note> without <staff> is read as staff 1")

    def test_the_two_hands_are_not_written_as_one_chord(self):
        """A restamp bug shows up as both hands sounding as one chord.

        Notes on different staves are simultaneous, so pairing them would both
        misread the page and engrave one five-finger chord instead of two hands.
        """
        for body in bars(self.piano).values():
            chords = body.count("<chord/>")
            self.assertEqual(chords, 0,
                             "a <chord/> spanning staves 1 and 2 is a defect")


class VoiceNumberingTests(unittest.TestCase):
    """Voice numbers are part-scoped, so each staff needs its own.

    MusicXML's ``<voice>`` runs per *part*, not per staff. Two staves of a piano
    that both number their single voice "1" are read as one voice hopping
    between staves: ``musicxml2ly`` emits a ``\\change Staff`` for every switch
    and LilyPond answers "cannot find context to change to: Staff = 1". This is
    asserted on the emitted XML because that is where the collision exists.
    """

    def voices_in(self, part_index=1, **kw):
        xml = emit_page(piano_page(**kw))
        return re.findall(r"<voice>(\d+)</voice>", part_xml(xml, part_index))

    def test_the_two_staves_of_a_piano_get_different_voice_numbers(self):
        voices = self.voices_in()
        self.assertGreater(len(set(voices)), 1,
                           "both staves are voice 1: they will collide")

    def test_a_voice_pair_keeps_its_number_in_every_bar(self):
        """The whole point of building the map page-wide, not bar-by-bar.

        Numbering each bar independently looks equivalent and is not: a bar
        where only the treble plays numbers it 1, the next bar with both hands
        numbers treble 1 and bass 2, and a third bar with only the bass numbers
        it 1 -- so bass 2 and bass 1 alternate and the voice migrates between
        staves mid-part. p0020 hits exactly this: bars 7-10 have one hand silent.
        """
        page = piano_page()
        piano = page.parts[0]
        pairs = {}
        for m in piano.measures:
            for n in m.notes:
                pairs.setdefault((n.staff, n.voice), set()).add(n.voice)
        for key, seen in pairs.items():
            self.assertEqual(len(seen), 1, "pair %s renumbered mid-part" % (key,))

    def test_a_restaff_makes_the_two_hands_share_a_voice_number(self):
        """Negative control for the test above, so it cannot pass vacuously.

        A part where both staves carry ``voice=1`` is exactly the collision the
        page-wide map exists to prevent. If the map ever stopped being applied,
        this is the shape the file would have -- and it must be *detectable*.
        """
        staves = [staff(2, step="e", octave=5), staff(2, step="g", octave=3)]
        page = assemble_page(staves, [1, 1], [G, F], brace_ids=["p", "p"],
                             id="collide")
        # Bypass the emit-time map the way a regression would. Note is frozen,
        # which is why the collision cannot even be introduced by accident here.
        collided = [replace(n, voice=1) for m in page.parts[0].measures
                    for n in m.notes]
        self.assertEqual({n.voice for n in collided}, {1})
        self.assertEqual({n.staff for n in collided}, {1, 2})

    def test_one_staff_parts_are_left_alone(self):
        """The singer's voice stays 1.

        Renumbering it would be semantically harmless but would churn the file
        for every vocal page, and it would mean a one-staff part and a
        two-staff part are treated by different rules.
        """
        self.assertEqual(set(self.voices_in(part_index=2)), {"1"})

    def test_sparse_voice_numbers_on_a_one_staff_part_are_preserved(self):
        """The guard that makes the previous test meaningful.

        With one staff there is nothing to collide, so ``voice_map`` returns {}
        and the source numbering survives verbatim -- including the gaps homr
        sometimes leaves. Renumbering {3, 7} to {1, 2} would be valid MusicXML
        and a silent edit of every voice number on every vocal page, which is
        exactly the kind of change that makes a diff unreadable.
        """
        frag = staff(1)
        frag.measures[0].notes = [
            replace(frag.measures[0].notes[0], voice=3),
            replace(frag.measures[0].notes[1], voice=7)]
        xml = emit_page(assemble_page([frag], [1], [G], brace_ids=[None],
                                      id="sparse"))
        self.assertEqual(re.findall(r"<voice>(\d+)</voice>", xml), ["3", "7"])

    def test_a_part_with_no_multi_staff_pitches_is_untouched(self):
        staves = [staff(2, step="e", octave=5), staff(2, step="g", octave=3)]
        xml = emit_page(assemble_page(staves, [1, 1], [G, F],
                                      brace_ids=["p", "p"], id="mono"))
        # Both staves carry notes, so this is still renumbered; the point of the
        # test is that voice_map returns numbers rather than {} or raising.
        self.assertEqual(len(set(re.findall(r"<voice>(\d+)</voice>", xml))), 2)


class RestampTests(unittest.TestCase):
    """Grouping must move notes onto the staff they now belong to."""

    def test_two_staves_of_notes_do_not_collapse_to_staff_one(self):
        page = piano_page()
        piano = [p for p in page.parts if len(p.clef_by_staff) == 2][0]
        by_staff = {n.staff for m in piano.measures for n in m.notes}
        self.assertEqual(by_staff, {1, 2})

    def test_restamping_does_not_mutate_the_callers_fragments(self):
        """Fragments are reused across systems; stamping in place corrupts them."""
        fragments = [staff(2, step="e", octave=5), staff(2, step="g", octave=3)]
        assemble_page(fragments, [1, 1], [G, F], brace_ids=["p", "p"],
                      id="reuse")
        for frag in fragments:
            self.assertEqual({n.staff for m in frag.measures for n in m.notes},
                             {1}, "the input fragment was modified in place")

    def test_a_one_staff_part_is_left_on_staff_one(self):
        page = piano_page()
        voice = [p for p in page.parts if len(p.clef_by_staff) == 1][0]
        self.assertEqual({n.staff for m in voice.measures for n in m.notes},
                         {1})


class PaddedTwoStaffTests(unittest.TestCase):
    """A padded bar of a two-staff part needs silence on BOTH staves."""

    def setUp(self):
        # Piano plays 2 bars; the singer plays 1, so the piano is NOT the short
        # part here -- the singer's bar 2 is the padding. To pad a TWO-staff
        # part the singer must be the long one, so: singer 3 bars, piano 2.
        self.page = piano_page(singer_bars=3, piano_bars=2)
        self.xml = emit_page(self.page)

    def test_the_padded_bar_of_a_two_staff_part_rests_both_staves(self):
        """Piano plays 2 bars, singer 3: the piano's bar 3 is padded."""
        piano = bars(part_xml(self.xml, index=1))
        self.assertIn(3, piano, "expected a padded bar 3 on the piano")
        self.assertEqual(piano[3].count('<rest measure="yes"/>'), 2,
                         "a two-staff bar needs a rest per staff")

    def test_the_padded_bar_names_both_staves(self):
        piano = bars(part_xml(self.xml, index=1))
        self.assertEqual(sorted(re.findall(r"<staff>(\d)</staff>", piano[3])),
                         ["1", "2"])

    def test_a_one_staff_part_still_pads_with_one_rest(self):
        """The singer is the long part, so the piano is padded, not the singer."""
        piano = bars(part_xml(self.xml, index=1))
        self.assertEqual(piano[3].count('<rest measure="yes"/>'), 2)

    def test_the_singer_is_not_padded_at_all(self):
        voice = bars(part_xml(self.xml, index=2))
        self.assertEqual(len(voice), 3)
        self.assertFalse(any('<rest measure="yes"/>' in b
                             for b in voice.values()))


class ThreeStaffBraceTests(unittest.TestCase):
    """A three-staff instrument: the emitter must declare all three."""

    def test_three_staves_are_declared_with_three_clefs(self):
        staves = [staff(1, step="e", octave=5), staff(1, step="c", octave=5),
                  staff(1, step="g", octave=3)]
        xml = emit_page(assemble_page(staves, [1, 1, 1], [G, G, F],
                                      brace_ids=["o", "o", "o"], id="organ"))
        self.assertIn("<staves>3</staves>", xml)
        self.assertEqual(len(re.findall(r"<clef number=", xml)), 3)
        # Two notes per staff, one measure each.
        self.assertEqual(xml.count("<staff>3</staff>"), 2)
        self.assertEqual(xml.count("<staff>2</staff>"), 2)


if __name__ == "__main__":
    unittest.main()