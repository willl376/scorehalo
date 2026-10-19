"""Tests for fitting every system of a piece onto one bottom-aligned staff grid.

A song changes how many staves it uses from system to system: "We've Only Just
Begun" opens with the piano alone on two staves and adds the singer on top for
three on every system after. Recognising each system on its own and trusting its
internal grouping gets this wrong, and quietly: homr pairs staves by clef, so a
voice-on-top page (clefs G, G, F) becomes a two-staff "Piano" holding the voice
and the right hand, plus a one-staff "Voice" holding the left hand.

The grid fixes it by re-seating whole staves onto the tallest layout, aligned to
the bottom, and by turning a clef that differs between systems into a genuine
clef change rather than a vote. These tests pin the three things that can go
wrong quietly:

* the phone book order: rows are read top to bottom and placed bottom-up, so the
  accompaniment stays put and the solo row is added above;
* no note is lost or invented, and system 2's bar 1 is the page's bar after
  system 1's last bar, not bar 1 again;
* the clef a system actually printed stays attached to its own row -- an intro
  written two-treble must not have a bass clef forced onto it, and a verse
  written treble+bass must not lose its bass clef.

Run: .venv/bin/python -m unittest discover -s tests -v
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pysrc"))

from scorehalo import grid
from scorehalo.graph import Measure, Note, Part, Pitch

G = ("G", 2)
F = ("F", 4)


def row(where, clef, bars=1, pitch="C", octave=4):
    """One single-staff Part: ``bars`` bars, two notes each, at one clef."""
    part = Part(id=where, name="x")
    part.clef_by_staff = {1: clef}
    for number in range(1, bars + 1):
        part.measures.append(Measure(number=number, notes=[
            Note(staff=1, voice=1, onset=i, duration=1,
                 pitch=Pitch(step=pitch, alter=0, octave=octave), measure=number)
            for i in range(2)]))
    return part


def grand(where, clef1, clef2, bars=1, top="G", bot="C", octave=(4, 3)):
    """A two-staff Part, the way homr writes a braced pair."""
    part = Part(id=where, name="Piano")
    part.clef_by_staff = {1: clef1, 2: clef2}
    for number in range(1, bars + 1):
        notes = [
            Note(staff=1, voice=1, onset=i, duration=1,
                 pitch=Pitch(step=top, alter=0, octave=octave[0]), measure=number)
            for i in range(2)]
        notes += [
            Note(staff=2, voice=1, onset=i, duration=1,
                 pitch=Pitch(step=bot, alter=0, octave=octave[1]), measure=number)
            for i in range(2)]
        part.measures.append(Measure(number=number, notes=notes))
    return part


def pitched(part):
    return [n for n in part.notes if not n.is_rest]


class SplitTests(unittest.TestCase):
    def test_grand_staff_splits_into_two_single_staves(self):
        parts = grid.split_part_by_staff(grand("P1", G, F, bars=2))
        self.assertEqual(len(parts), 2)
        self.assertEqual(parts[0].clef_by_staff, {1: G})
        self.assertEqual(parts[1].clef_by_staff, {1: F})
        self.assertEqual([n.staff for n in pitched(parts[0])], [1] * 4)
        self.assertEqual([n.staff for n in pitched(parts[1])], [1] * 4)

    def test_single_staff_part_survives_unchanged(self):
        parts = grid.split_part_by_staff(row("V", G, bars=3))
        self.assertEqual(len(parts), 1)
        self.assertEqual(len(parts[0].measures), 3)

    def test_system_rows_keep_reading_order(self):
        sys2 = [grand("P1", G, G, bars=2), row("P2", F, bars=2)]
        rows = grid.system_rows(sys2)
        self.assertEqual([r.id for r in rows], ["P1.s1", "P1.s2", "P2.s1"])
        self.assertEqual([r.clef_by_staff[1] for r in rows], [G, G, F])


class ShapeTests(unittest.TestCase):
    def test_two_three_three_is_a_three_row_grid(self):
        # The intro is the piano alone (2 braced rows); the verse adds the voice.
        intro = grid.system_rows([grand("P1", G, G, bars=4)])
        verse = grid.system_rows([grand("P1", G, G, bars=3), row("P2", F, bars=3)])
        total, accomp = grid.grid_shape([intro, verse, verse])
        self.assertEqual((total, accomp), (3, 2))

    def test_uniform_two_staff_page_has_no_solo_row(self):
        one = grid.system_rows([grand("P1", G, F, bars=2)])
        self.assertEqual(grid.grid_shape([one, one]), (2, 2))

    def test_a_page_that_stacks_two_parts_keeps_its_solo_row(self):
        # p0033 as the recogniser first reports it: the voice staff stands alone
        # above the braced piano, so the systems come back as alternating
        # one-row and two-row systems of DIFFERENT parts. The tallest layout is
        # then the piano's two rows, the voice row is not in the grid at all,
        # and the vocal part vanishes from the piece without a word -- a
        # two-staff page that is really three-staff music.
        voice = grid.system_rows([row("V1", G, bars=2)])
        piano = grid.system_rows([grand("P1", G, F, bars=2)])
        self.assertEqual(grid.grid_shape([voice, piano, voice, piano]), (3, 2))

    def test_a_two_staff_page_of_two_parts_keeps_both_rows_only(self):
        # Two independent single-staff parts stacked: the top row IS a solo row
        # already, so nothing is added.
        top = grid.system_rows([row("A", G, bars=2)])
        bottom = grid.system_rows([row("B", F, bars=2)])
        self.assertEqual(grid.grid_shape([top + bottom, top + bottom]), (2, 1))

    def test_no_multistaff_part_means_no_accompaniment(self):
        one = grid.system_rows([row("S1", G), row("S2", F)])
        self.assertEqual(grid.grid_shape([one]), (2, 1))


class NormaliseTests(unittest.TestCase):
    def setUp(self):
        # system 1: piano alone, two treble staves (the printed intro).
        # systems 2-3: vocal on top, piano below -- right hand treble, LEFT
        # hand bass. homr folds the voice into the piano and orphans the bass
        # into a "Voice" part; the grid must undo both.
        self.intro = grid.system_rows([grand("P1", G, G, bars=4, bot="G",
                                             octave=(4, 4))])
        self.verse = grid.system_rows([grand("P1", G, G, bars=3),
                                       row("P2", F, bars=3, octave=2)])
        self.page = grid.normalise("t", [self.intro, self.verse, self.verse],
                                   divisions=12)

    def test_parts_are_a_solo_and_the_accompaniment(self):
        self.assertEqual([p.name for p in self.page.parts],
                         [grid.SOLO_NAME, grid.ACCOMPANIMENT_NAME])

    def test_accompaniment_is_bottom_aligned_grand_staff(self):
        piano = self.page.parts[-1]
        self.assertEqual(sorted(piano.clef_by_staff), [1, 2])

    def test_solo_staff_is_silent_during_the_intro(self):
        voice = self.page.parts[0]
        # systems 2-3 have 3 bars each, so the page is 4 + 3 + 3 = 10 bars.
        self.assertEqual(len(voice.measures), 10)
        for m in voice.measures[:4]:
            self.assertTrue(all(n.is_rest for n in m.notes),
                            "intro bar %d should be silent on the solo staff"
                            % m.number)

    def test_system_bars_do_not_overwrite_each_other(self):
        voice = self.page.parts[0]
        self.assertEqual([m.number for m in voice.measures],
                         list(range(1, 11)))
        # the singer sings in bars 5..10 and nowhere else
        sung = {m.number for m in voice.measures if pitched(m)}
        self.assertEqual(sung, set(range(5, 11)))

    def test_intro_clef_is_kept_and_verse_becomes_a_change(self):
        piano = self.page.parts[-1]
        # opening clef is the intro's own: both staves treble.
        self.assertEqual(piano.clef_by_staff, {1: G, 2: G})
        # the bass staff appears as a CHANGE at the verse (bar 5), not by
        # forcing bass onto the genuinely-treble intro.
        self.assertEqual(piano.clef_changes, [(5, 2, F)])

    def test_bass_notes_are_not_transposed(self):
        piano = self.page.parts[-1]
        bass = [n for n in pitched(piano) if n.staff == 2]
        # The grid re-seats a staff and never edits the note, so the lower staff
        # holds exactly what was read on it: the intro's treble-register C4
        # (bars 1-4) and the verse's bass-register C2 (bars 5-10), not one
        # octave chosen for the whole part.
        self.assertEqual({n.pitch.octave for n in bass}, {2, 4})

    def test_no_note_is_lost(self):
        source = sum(len(pitched(r)) for sys_rows in
                     (self.intro, self.verse, self.verse) for r in sys_rows)
        self.assertEqual(len(pitched(self.page.parts[0]))
                         + len(pitched(self.page.parts[1])), source)

    def test_clef_override_forces_a_row(self):
        page = grid.normalise("t", [self.intro, self.verse], divisions=12,
                              clef_overrides={0: F})
        piano = page.parts[-1]
        self.assertEqual(piano.clef_by_staff[2], F)
        self.assertEqual(piano.clef_changes, [])


class ConflictTests(unittest.TestCase):
    def test_disagreeing_row_is_reported(self):
        intro = grid.system_rows([grand("P1", G, G, bars=4)])
        verse = grid.system_rows([grand("P1", G, G, bars=3), row("P2", F, bars=3)])
        conflicts = grid.clef_conflicts([intro, verse], total=3, accomp=2)
        self.assertEqual([c["from_bottom"] for c in conflicts], [0])
        self.assertIn("F4", conflicts[0]["clefs"])
        self.assertIn("G2", conflicts[0]["clefs"])

    def test_agreeing_rows_are_not_reported(self):
        verse = grid.system_rows([grand("P1", G, F, bars=3)])
        self.assertEqual(grid.clef_conflicts([verse, verse], 2, 2), [])


def _note(dur, onset=0, voice=1, pitch=("C", 0, 4), rest=False):
    return Note(staff=1, voice=voice, onset=onset, duration=dur,
                pitch=None if rest else Pitch(*pitch), measure=1,
                is_rest=rest)


def _filled(where, clef, bars=1, dur=48):
    """A staff row of full bars: one note of ``dur`` ticks per bar."""
    part = Part(id=where, name="x")
    part.clef_by_staff = {1: clef}
    for number in range(1, bars + 1):
        part.measures.append(Measure(number=number, notes=[_note(dur)]))
    return part


def _tacet(where, clef, bars=1, dur=48):
    """A staff row that is printed but silent: rests only."""
    part = Part(id=where, name="x")
    part.clef_by_staff = {1: clef}
    for number in range(1, bars + 1):
        part.measures.append(Measure(number=number, notes=[_note(dur, rest=True)]))
    return part


class BeatTests(unittest.TestCase):
    """Counting beats that add up on each standard staff line.

    The grid counts STAVES; these count MUSIC. At GLOBAL_DIVISIONS = 12 a
    quarter is 12 ticks, so a full 4/4 bar is 48 ticks = 4 beats.
    """

    def test_a_bar_of_one_note_counts_its_beats(self):
        self.assertEqual(grid._bar_fill(_filled("r", G).measures[0]), 48)

    def test_a_chord_counts_once_not_once_per_note(self):
        # Two notes at one onset are a chord: one span, not two. Summing them
        # would read a four-beat bar as eight and flag every chord.
        m = Measure(number=1, notes=[_note(48, pitch=("C", 0, 4)),
                                     _note(48, pitch=("E", 0, 4))])
        self.assertEqual(grid._bar_fill(m), 48)

    def test_the_longest_voice_wins(self):
        # Two voices each filling the bar are still one bar of music.
        m = Measure(number=1, notes=[_note(48, voice=1), _note(48, voice=2)])
        self.assertEqual(grid._bar_fill(m), 48)

    def test_a_rest_is_not_a_beat(self):
        self.assertEqual(grid._bar_fill(_tacet("r", G).measures[0]), 0)

    def test_beats_are_totalled_per_grid_row_from_the_bottom(self):
        intro = grid.system_rows([grand("P1", G, G, bars=4, bot="G",
                                        octave=(4, 4))])
        verse = grid.system_rows([grand("P1", G, G, bars=3),
                                  row("P2", F, bars=3, octave=2)])
        totals = grid.beat_totals([intro, verse, verse], divisions=1)
        # Each bar above holds 2 ticks of note. The intro is 4 bars on 2 rows
        # (8 each) and each verse 3 bars on 3 rows (6 each). Rows 0 and 1 are
        # in all three systems = 8 + 6 + 6; row 2 (the voice) only in the two
        # verses = 6 + 6. The intro's two rows feed rows 0 AND 1, which is why
        # the voice's total is not a simple fraction of theirs.
        self.assertEqual(totals, [20.0, 20.0, 12.0])

    def test_content_rows_sees_a_printed_but_silent_row(self):
        loud = grid.system_rows([_filled("S1", G, bars=2),
                                 _filled("S2", F, bars=2)])
        quiet = grid.system_rows([_filled("S1", G, bars=2),
                                  _tacet("S2", F, bars=2)])
        self.assertEqual(grid.content_rows([loud], divisions=1), 2)
        self.assertEqual(grid.content_rows([quiet], divisions=1), 1)

    def test_staff_beat_table_keys_each_bar(self):
        table = grid.staff_beat_table(
            [grid.system_rows([_filled("S1", G, bars=3)])], divisions=12)
        self.assertEqual(table[0][0], {1: 4.0, 2: 4.0, 3: 4.0})

    def test_short_bar_is_flagged_but_a_tacet_is_not(self):
        short = grid.system_rows([_filled("S1", G, bars=1, dur=36)])  # 3 beats
        tacet = grid.system_rows([_tacet("S1", G, bars=1)])
        self.assertEqual(len(grid.beat_mismatches([short], 12, 4, 4)), 1)
        self.assertEqual(grid.beat_mismatches([tacet], 12, 4, 4), [])

    def test_beats_are_formatted_whole_or_two_places(self):
        self.assertEqual(grid._fmt_beats(4.0), "4")
        self.assertEqual(grid._fmt_beats(4.5), "4.50")

    def test_report_names_both_shape_and_content(self):
        loud = grid.system_rows([_filled("S1", G, bars=2),
                                 _filled("S2", F, bars=2)])
        text = grid.report([loud], divisions=12)
        self.assertIn("staff grid: 2 row(s)", text)
        self.assertIn("beats per grid row", text)
        # two 4/4 bars a row = 8 beats, on each of the two rows.
        self.assertIn("beats [8, 8]", text)


if __name__ == "__main__":
    unittest.main()
