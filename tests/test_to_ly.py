"""Regression tests for the native LilyPond emitter.

The ordering test exists because of a real bug: the emitter wrote
``c'^\\markup{..}4`` -- markup between pitch and duration. LilyPond reads that
as an untimed note wearing a markup followed by a stray ``4``, so the note
silently became a whole note and every bar ran long. The only symptom was a
barcheck warning pointing at a bar, never at the note, and a duration
re-reader would agree with the wrong answer, because the digits are right.
So the structural rule is asserted directly, and LilyPond is asked to agree.
"""

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

from scorehalo.graph import Measure, Note, Page, Part, Pitch
from scorehalo.emit import parse_part


def _REAL_PAGE():
    """The assembled p0020 page, when the fragments are on disk."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "data", "out-system", "pages", "p0020.musicxml")
from scorehalo.to_ly import (
    _render_event,
    _group_events,
    duration_token,
    pitch_token,
    split_length,
    to_ly,
)

WHOLE = 4  # quarters in a whole note


def _note(**kw) -> Note:
    base = dict(staff=1, voice=1, onset=0, duration=4, pitch=Pitch("C", 0, 4),
                measure=1)
    base.update(kw)
    return Note(**base)


class TestDurationGrammar(unittest.TestCase):
    def test_duration_number_is_a_reciprocal_of_a_whole_note(self):
        # The single most important fact in this file: LilyPond's `k` means 1/k
        # of a whole note, not k quarters. Getting it backwards quadruples every
        # duration, which still parses and still "looks" like music.
        self.assertEqual(duration_token(Fraction(WHOLE)), "1")
        self.assertEqual(duration_token(Fraction(2)), "2")
        self.assertEqual(duration_token(Fraction(1)), "4")
        self.assertEqual(duration_token(Fraction(1, 2)), "8")
        self.assertEqual(duration_token(Fraction(1, 4)), "16")

    def test_dots_multiply_by_two_minus_one_over_two_each(self):
        self.assertEqual(duration_token(Fraction(6)), "1.")
        self.assertEqual(duration_token(Fraction(3)), "2.")
        self.assertEqual(duration_token(Fraction(3, 2)), "4.")

    def test_rests_split_into_plain_durations_never_a_scale_factor(self):
        # A whole bar is 4 quarters, which is not a note type; `1..*4/1` both
        # reads terribly and makes barcheck see a bar twice as long.
        self.assertEqual(split_length(Fraction(WHOLE)), [Fraction(WHOLE)])
        self.assertEqual(
            [duration_token(p) for p in split_length(Fraction(5))], ["1", "4"])
        self.assertEqual(
            [duration_token(p) for p in split_length(Fraction(6))], ["1."])

    def test_pitch_token_uses_octave_marks_not_relative_mode(self):
        self.assertEqual(pitch_token(_note(pitch=Pitch("C", 0, 4))), "c'")
        self.assertEqual(pitch_token(_note(pitch=Pitch("C", 0, 3))), "c")
        self.assertEqual(pitch_token(_note(pitch=Pitch("E", -1, 4))), "ees'")


class TestNoteShape(unittest.TestCase):
    def test_duration_binds_to_the_pitch_before_any_post_event(self):
        event = _group_events([_note()], 4)[0]
        out = _render_event(event, 4, provenance=True)
        # pitch, then duration, then post-events -- in that order.
        self.assertRegex(out, r"^c'\d")
        self.assertNotRegex(out, r"\^\s*\\markup[^{}]*\d")
        # The duration must be adjacent to the pitch, not merely present. A
        # post-event may follow, but none has to: this used to demand one with
        # `(?:\^|~|_|<)`, which only ever passed because provenance attached a
        # fake "^(x=-1 y=-1...)" to every single note.
        self.assertRegex(out, r"^c'4(\^|~|_|<)?$")
        # With a real pixel, the provenance markup comes back.
        withpixel = _render_event(
            _group_events([_note(src_x=120, src_y=340)], 4)[0], 4, provenance=True)
        self.assertIn("x=120 y=340", withpixel)

    def test_a_scale_factor_also_binds_directly_to_the_pitch(self):
        event = _group_events([_note(duration=5)], 4)[0]
        out = _render_event(event, 4, provenance=False)
        self.assertRegex(out, r"^c'\d+(\.\.)?(\*\d+/\d+)?")

    def test_chord_closes_before_the_duration(self):
        notes = [_note(pitch=Pitch("C", 0, 4)), _note(pitch=Pitch("E", 0, 4))]
        event = _group_events(notes, 4)[0]
        self.assertEqual(_render_event(event, 4, provenance=False), "<c' e'>4")


def _one_bar_page() -> Page:
    """One staff, one bar, four quarter notes: unambiguously 4/4."""
    notes = [_note(onset=o, duration=4, measure=1) for o in (0, 4, 8, 12)]
    part = Part(id="p1", name="Music", system=1)
    part.measures = [Measure(number=1, divisions=4, notes=notes)]
    return Page(id="test", parts=[part])


class TestClampToBar(unittest.TestCase):
    """Fitting one voice's notes into one bar.

    This function had no tests at all, which is how a bug survived that
    trimmed 18 notes on p0020 -- every one of them a chord tone, none of them
    overlapping anything. A chord is several notes at ONE onset; the clamp
    judged them with a single running cursor and read the second tone as an
    overlap of the first.
    """

    def setUp(self):
        from scorehalo.to_ly import clamp_to_bar
        self.clamp = clamp_to_bar
        self.measure = Measure(number=1, divisions=12, beats=4, beat_type=4)

    def test_a_chord_is_not_an_overlap(self):
        """Two notes at one onset are a chord, and both must survive."""
        notes = [_note(pitch=Pitch("E", 0, 5), onset=0, duration=48),
                 _note(pitch=Pitch("G", 0, 5), onset=0, duration=48)]
        out, damaged = self.clamp(notes, self.measure)
        self.assertEqual(damaged, 0)
        self.assertEqual(len(out), 2)

    def test_a_three_note_chord_survives(self):
        notes = [_note(pitch=Pitch("G", 0, 3), onset=0, duration=24),
                 _note(pitch=Pitch("C", 0, 4), onset=0, duration=24),
                 _note(pitch=Pitch("E", 0, 4), onset=0, duration=24)]
        out, damaged = self.clamp(notes, self.measure)
        self.assertEqual(damaged, 0)
        self.assertEqual(len(out), 3)

    def test_a_real_overlap_is_still_trimmed(self):
        """A note starting after a previous onset but before it ended."""
        notes = [_note(onset=0, duration=24),
                 _note(onset=12, duration=12)]
        out, damaged = self.clamp(notes, self.measure)
        self.assertEqual(damaged, 1)
        self.assertEqual(len(out), 1)

    def test_a_note_past_the_barline_is_dropped(self):
        notes = [_note(onset=0, duration=24), _note(onset=48, duration=12)]
        out, damaged = self.clamp(notes, self.measure)
        self.assertEqual(damaged, 1)
        self.assertEqual(len(out), 1)

    def test_a_note_overrunning_the_barline_is_truncated(self):
        notes = [_note(onset=0, duration=60)]
        out, damaged = self.clamp(notes, self.measure)
        self.assertEqual(damaged, 1)
        self.assertEqual(out[0].duration, 48)

    def test_a_gap_is_left_alone(self):
        """Silence is not damage; _voice_body writes the rest."""
        notes = [_note(onset=0, duration=12), _note(onset=36, duration=12)]
        out, damaged = self.clamp(notes, self.measure)
        self.assertEqual(damaged, 0)
        self.assertEqual(len(out), 2)

    def test_chord_then_melody_line_up(self):
        """A chord followed by a melody note: the chord is one event."""
        notes = [_note(pitch=Pitch("E", 0, 5), onset=0, duration=24),
                 _note(pitch=Pitch("G", 0, 5), onset=0, duration=24),
                 _note(onset=24, duration=24)]
        out, damaged = self.clamp(notes, self.measure)
        self.assertEqual(damaged, 0)
        self.assertEqual(len(out), 3)


class TestMidiInstrument(unittest.TestCase):
    """A General MIDI export should sound like the parts it names.

    Without ``midiInstrument`` a staff plays LilyPond's default, so an export
    whose parts are "Piano" and "Voice" is two generic instruments and the
    audition says nothing about whether the notes are right.
    """

    def test_names_are_lilypond_names_with_spaces(self):
        """General MIDI's underscore spellings are all REJECTED by LilyPond.

        The first version of this map used ``acoustic_grand_piano`` and
        ``choir_aahs`` and produced "warning: no such MIDI instrument" x4 on an
        otherwise clean page. LilyPond's own names live in scm/lily/midi.scm and
        contain spaces. Guessing an external contract instead of reading it is
        the same error as guessing a clef.
        """
        from scorehalo.to_ly import MIDI_INSTRUMENTS, _staff_properties
        for value in MIDI_INSTRUMENTS.values():
            self.assertNotIn("_", value, "LilyPond names use spaces: %r" % value)
        self.assertIn('midiInstrument = "acoustic grand"',
                      _staff_properties("Piano"))
        self.assertIn('midiInstrument = "choir aahs"',
                      _staff_properties("Voice"))

    def test_role_lookup_is_case_and_space_insensitive(self):
        from scorehalo.to_ly import _staff_properties
        for role in ("Piano", "piano", "  PIANO "):
            self.assertIn("acoustic grand", _staff_properties(role))

    def test_an_unknown_role_gets_no_guessed_instrument(self):
        """Silence is better than a wrong sound.

        A role we do not recognise is left without ``midiInstrument`` -- same
        discipline as not guessing a clef.
        """
        from scorehalo.to_ly import _staff_properties
        props = _staff_properties("Staff 7 (system 2/3)")
        self.assertIn('instrumentName = "Staff 7 (system 2/3)"', props)
        self.assertNotIn("midiInstrument", props)

    def test_instrument_name_is_always_present(self):
        from scorehalo.to_ly import _staff_properties
        self.assertTrue(_staff_properties("Piano").startswith("instrumentName"))

    def test_lilypond_accepts_every_name_we_emit(self):
        """The referee decides, by compiling and refusing to warn.

        Each name gets its OWN output directory. Reusing one basename made
        LilyPond return rc=1 on every run after the first -- the output file
        already existed -- which reads as "violin is rejected" and is not.
        """
        from scorehalo.to_ly import MIDI_INSTRUMENTS
        for name in sorted(set(MIDI_INSTRUMENTS.values())):
            with tempfile.TemporaryDirectory() as work:
                ly = os.path.join(work, "probe.ly")
                with open(ly, "w") as fh:
                    fh.write('\\version "2.24.3"\n\\score {\n  <<\n'
                             '    \\new Staff \\with { midiInstrument = "%s" }'
                             ' { c4 }\n  >>\n  \\midi { }\n}\n' % name)
                out = subprocess.run(
                    ["lilypond", "-dno-point-and-click",
                     "-o", os.path.join(work, "probe"), ly],
                    capture_output=True, text=True, timeout=120)
                self.assertNotIn("no such MIDI instrument", out.stderr,
                                 "LilyPond rejects midiInstrument %r" % name)
                self.assertEqual(out.returncode, 0, name)
                self.assertTrue(os.path.exists(os.path.join(work, "probe.midi")),
                                "no MIDI written for %r" % name)


class TestSlursOnChords(unittest.TestCase):
    """A slur may start on any tone of a chord, and the bracket must survive.

    Two independent bugs met here, and the second only became reachable once
    chords stopped being trimmed:

    1. ``_resolve_slurs`` returned notes in SORTED order while its docstring
       promised the input order, and the caller zips the result back against the
       per-measure buckets. The sort key carried no pitch tiebreaker, so with a
       chord putting two notes on the same (staff, voice, measure, onset) the
       order could permute and a slur mark landed on the wrong note.
    2. ``_render_event`` read the mark from ``ev.notes[0]`` only. A chord's
       slur belongs to the event, so a slur starting on a lower tone was
       dropped while its partner ``)`` survived.

    Together: p0020 rendered one voice with 0 opens and 2 closes, which LilyPond
    reports as "cannot end slur" -- a syntax error, not a warning.
    """

    def setUp(self):
        from scorehalo.to_ly import _group_events, _render_event, _resolve_slurs
        self.resolve = _resolve_slurs
        self.render = _render_event
        self.events = _group_events

    def _pair(self):
        """A balanced slur whose start and stop both sit on a LOWER chord tone.

        _resolve_slurs drops an orphan start by design ("an orphan start never
        reaches a stop, so it emits nothing"), so every test here needs a real
        pair or it asserts nothing.
        """
        return [
            _note(pitch=Pitch("E", 0, 4), onset=0, duration=12, slur="start"),
            _note(pitch=Pitch("G", 0, 4), onset=0, duration=12),
            _note(pitch=Pitch("C", 0, 5), onset=12, duration=12, slur="stop"),
            _note(pitch=Pitch("E", 0, 5), onset=12, duration=12),
        ]

    def test_input_order_is_preserved_despite_the_sort(self):
        """Two notes at one onset tie in the sort key; input order must survive."""
        out = self.resolve(self._pair())
        self.assertEqual([(n.onset, n.pitch.step) for n in out],
                         [(0, "E"), (0, "G"), (12, "C"), (12, "E")])

    def test_input_order_is_preserved_when_it_differs_from_sorted_order(self):
        """The discriminating case.

        A stable sort keeps the input order whenever the input is ALREADY
        sorted, so a test that only feeds sorted notes passes even with the
        sort-based return in place -- that mutation survived the first version
        of this file. The real caller zips the result back against per-measure
        buckets, and a voice can hand over notes whose (staff, voice, measure,
        onset) order differs from the order they arrived in, so the input
        sequence here is deliberately NOT sorted.
        """
        notes = [
            _note(staff=2, pitch=Pitch("E", 0, 3), onset=12, duration=4),
            _note(staff=1, pitch=Pitch("C", 0, 4), onset=0, duration=4),
            _note(staff=2, pitch=Pitch("G", 0, 3), onset=0, duration=4),
        ]
        out = self.resolve(notes)
        self.assertEqual([(n.staff, n.onset, n.pitch.step) for n in out],
                         [(2, 12, "E"), (1, 0, "C"), (2, 0, "G")])

    def test_a_slur_start_on_a_lower_chord_tone_reaches_the_output(self):
        """The mark is on tone 1 of its event, and reading only ev.notes[0]
        would work by luck -- so the LOWER tone is what must be checked."""
        notes = [
            _note(pitch=Pitch("E", 0, 4), onset=0, duration=12),
            _note(pitch=Pitch("G", 0, 4), onset=0, duration=12, slur="start"),
            _note(pitch=Pitch("C", 0, 5), onset=12, duration=12, slur="stop"),
            _note(pitch=Pitch("E", 0, 5), onset=12, duration=12),
        ]
        out = [self.render(e, 4, provenance=False)
               for e in self.events(self.resolve(notes), 4)]
        self.assertIn("(", out[0])

    def test_a_slur_stop_on_a_chord_tone_reaches_the_output(self):
        notes = [
            _note(pitch=Pitch("E", 0, 4), onset=0, duration=12, slur="start"),
            _note(pitch=Pitch("G", 0, 4), onset=0, duration=12),
            _note(pitch=Pitch("C", 0, 5), onset=12, duration=12),
            _note(pitch=Pitch("E", 0, 5), onset=12, duration=12, slur="stop"),
        ]
        out = [self.render(e, 4, provenance=False)
               for e in self.events(self.resolve(notes), 4)]
        self.assertIn(")", out[1])

    def test_brackets_come_after_the_duration(self):
        """The rule: pitch, then duration, then post-events -- always.

        12 ticks at divisions 4 is three quarters, written as a dotted half
        ``2.``, not ``4``. Asserting the wrong token here would have passed a
        broken emitter and failed a correct one.
        """
        out = [self.render(e, 4, provenance=False)
               for e in self.events(self.resolve(self._pair()), 4)]
        self.assertRegex(out[0], r"^<e' g'>2\.\(")

    def test_a_chord_with_a_slur_yields_exactly_one_bracket(self):
        out = [self.render(e, 4, provenance=False)
               for e in self.events(self.resolve(self._pair()), 4)]
        self.assertEqual(out[0].count("("), 1)
        self.assertEqual(out[0].count(")"), 0)
        self.assertEqual(out[1].count(")"), 1)

    @unittest.skipUnless(shutil.which("lilypond"), "lilypond not installed")
    def test_every_rendered_voice_has_balanced_brackets(self):
        """The invariant that actually matters, checked on the whole page.

        Unit tests can only prove a bracket survives one hop. This proves the
        emitted score has none of them stranded, which is the condition LilyPond
        calls "cannot end slur" -- a syntax error, not a warning.
        """
        path = _REAL_PAGE()
        if not os.path.exists(path):
            self.skipTest("no assembled page on disk")
        from scorehalo.to_ly import to_ly
        parts = parse_part(path, staff_index=1, system=1)
        page = Page(id="real", parts=parts)
        for line in to_ly(page).splitlines():
            if "\\new Voice" not in line:
                continue
            self.assertEqual(line.count("("), line.count(")"),
                             "unbalanced slur brackets: %s" % line)

    @unittest.skipUnless(shutil.which("lilypond"), "lilypond not installed")
    def test_lilypond_reports_no_unbalanced_slur(self):
        """Ask the referee, rather than counting brackets ourselves."""
        import subprocess
        import tempfile
        path = _REAL_PAGE()
        if not os.path.exists(path):
            self.skipTest("no assembled page on disk")
        from scorehalo.to_ly import to_ly
        parts = parse_part(path, staff_index=1, system=1)
        with tempfile.TemporaryDirectory() as work:
            ly = os.path.join(work, "page.ly")
            with open(ly, "w") as fh:
                fh.write(to_ly(Page(id="real", parts=parts)))
            proc = subprocess.run(
                ["lilypond", "-dno-point-and-click", "-o",
                 os.path.join(work, "page"), ly],
                capture_output=True, text=True, timeout=300)
        self.assertEqual(proc.returncode, 0)
        self.assertNotIn("cannot end slur", proc.stderr,
                         "a stranded slur bracket is a syntax error")


class TestEmittedFile(unittest.TestCase):
    def setUp(self):
        self.ly = to_ly(_one_bar_page(), provenance=True)

    def test_point_and_click_is_actually_switched_on(self):
        """Frescobaldi jumps PDF -> .ly only if the .ly turns it on.

        musicxml2ly emits \\pointAndClickOff, and this module exists to replace
        it. The `if point_and_click:` branch used to write a *comment* about
        scan provenance, so the promise in the docstring -- "Emit
        \\pointAndClickOn so the PDF carries links back into the .ly" -- was
        never kept and the PDF carried no links at all. No test noticed, which
        is how a dead feature survives.
        """
        self.assertIn("\\pointAndClickOn", self.ly)

    def test_point_and_click_can_be_switched_off(self):
        page = _one_bar_page()
        self.assertNotIn("\\pointAndClickOn", to_ly(page, point_and_click=False))

    def test_no_fake_provenance_markup(self):
        """Every note used to carry "^(x=-1 y=-1 ...)".

        src_x is never assigned anywhere, and homr's MusicXML has no position
        data to assign it from, so that markup was pure noise on every note of
        every bar -- and asserted a pixel that does not exist.
        """
        self.assertNotIn("x=-1", self.ly)
        self.assertNotIn("\\typewriter", self.ly)

    def test_provenance_is_emitted_when_a_pixel_exists(self):
        """Not dead code -- dormant, waiting on a pixel it has never been given."""
        from dataclasses import replace
        page = _one_bar_page()
        part = page.parts[0]
        part.measures[0].notes = [replace(part.measures[0].notes[0],
                                         src_x=120, src_y=340)]
        self.assertIn("x=120 y=340", to_ly(page, provenance=True))

    def test_braces_balance_across_the_whole_file(self):
        """Every ``{`` has a ``}``.

        Counted by scanning character by character with a depth counter rather
        than with a regex over ``<<``/``>>``: an earlier version of this walked
        angle brackets too and treated ``>>`` as an OPENER, so depth only ever
        rose and the check reported unbalanced braces on a file that was fine.
        """
        text = to_ly(_one_bar_page())
        depth = 0
        lowest = 0
        for char in text:
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                lowest = min(lowest, depth)
        self.assertEqual(depth, 0, "unbalanced braces")
        self.assertEqual(lowest, 0, "a closing brace with nothing open")

    def test_score_block_is_present_and_holds_one_outer_expression(self):
        """LilyPond's hard rule: one outermost music expression, inside braces.

        It also explains the silent-success trap: LilyPond inserts a score block
        behind the scenes if you omit one, so a file that only DEFINES music and
        never asks for it to be displayed exits rc=0 with a clean log and no
        score at all. Asserting the shape of our score block is the cheapest
        guard against a whole class of "it said Success" failures.
        """
        text = to_ly(_one_bar_page())
        self.assertEqual(text.count("\\score {"), 1,
                         "exactly one score block")
        start = text.index("\\score {")
        # Everything between the score block's own braces, counted by depth.
        depth = 0
        body = []
        for char in text[start + len("\\score "):]:
            if char == "{":
                depth += 1
                if depth == 1:
                    continue          # this is the score block's own brace
            elif char == "}":
                depth -= 1
                if depth == 0:
                    break             # closed the score block
            body.append(char)
        body = "".join(body).strip()
        # The body is the music expression FOLLOWED BY \layout { }. Asserting it
        # ends with ">>" was wrong -- \score { << music >> \layout { } } is the
        # normal shape -- so the check is on the leading expression instead.
        self.assertTrue(body.startswith("<<"), body[:40])
        self.assertTrue(body.endswith("}"), body[-40:])
        # Exactly one expression at the top level of the score block.
        depth = 0
        top = 0
        for char in body:
            if char == "<":
                if depth == 0:
                    top += 1
                depth += 1
            elif char == ">":
                depth -= 1
        self.assertEqual(depth, 0, "unbalanced << >> in the score block")
        self.assertEqual(top, 1, "score block must hold ONE outer expression")

    def test_language_is_declared_not_inherited(self):
        # Note names are language-dependent. LilyPond's default is nederlands,
        # which is why cis/ces happen to work, but "happen to" breaks the day a
        # template or a user sets a language -- and 2.24.3's english table is a
        # different set entirely (cs/cf/css).
        self.assertIn('\\language "nederlands"', self.ly)

    def test_every_pitched_note_carries_a_duration(self):
        # LilyPond silently supplies a default duration for an untimed note, so
        # a missing one is not a parse error -- it is a note that quietly
        # becomes a whole note. Structural commands carry letters too
        # (`\clef` has an `e`), so they have to go before scanning.
        structural = re.compile(
            r"\\clef\s+\w+|\\key\s+\w+\s+\\(?:major|minor)"
            r"|\\time\s+\d+/\d+|\\new\s+Voice|\\voiceOne|\\language\s+\w+")
        for line in self.ly.splitlines():
            if "\\new Voice" not in line:
                continue
            body = structural.sub(" ", re.sub(r"\\markup\s*\{[^}]*\}", " ", line))
            for token in re.findall(r"(?<![\w\\])[a-g](?:is|es)*[',]*", body):
                self.assertRegex(
                    body, re.escape(token) + r"\d", "note without a duration")

    def test_score_block_and_simultaneity(self):
        self.assertIn("\\score", self.ly)
        self.assertIn("<<", self.ly)
        self.assertIn("}", self.ly)


@unittest.skipUnless(shutil.which("lilypond"), "lilypond not installed")
class TestLilyPondAgrees(unittest.TestCase):
    """The referee. Two barchecks, two identical quarters, plus provenance."""

    def test_lilypond_reports_no_barcheck(self):
        notes = [_note(onset=o, duration=4, measure=1) for o in (0, 4, 8, 12)]
        part = Part(id="p1", name="Music", system=1)
        part.measures = [Measure(number=1, divisions=4, notes=notes)]
        ly = to_ly(Page(id="test", parts=[part]), provenance=True)
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "t.ly"
            src.write_text(ly)
            out = subprocess.run(
                ["lilypond", "-o", str(Path(tmp) / "t.pdf"), str(src)],
                capture_output=True, text=True, timeout=300)
            log = out.stdout + out.stderr
        self.assertEqual(
            [ln for ln in log.splitlines() if "barcheck" in ln], [],
            "LilyPond disagreed with the emitted bar lengths:\n" + log)


if __name__ == "__main__":
    unittest.main()


class TestSpanners(unittest.TestCase):
    """Slurs and ties are different marks and need different tokens.

    Both defects here were found by asking what the corpus actually contains
    rather than what the code intends: the corpus has 8733 slur starts and not
    one tie, so the tie-stop bug sat latent and every slur was being discarded
    in plain sight. ``musicxml2ly`` on the same page emits 32 slur marks where
    this emitter used to emit none.
    """

    def _page(self, notes):
        meas = Measure(number=1, beats=4, beat_type=4, divisions=1)
        meas.notes = notes
        part = Part(id="p1", name="Music", system=1)
        part.measures = [meas]
        return Page(id="t", parts=[part])

    def test_tie_stop_emits_no_token_at_all(self):
        # LilyPond has no tie-stop marker: `~` on the first note binds the
        # pair by itself. A trailing `_` is a hard syntax error that fails the
        # whole compile, not a warning.
        notes = [_note(tie="start", onset=0), _note(tie="stop", onset=1)]
        text = to_ly(self._page(notes), version="2.24.3", provenance=False)
        self.assertNotIn("_", text.split("layout")[0])
        self.assertIn("~", text)

    def test_tie_stop_never_emits_an_underscore(self):
        notes = [_note(tie="stop", onset=0)]
        text = to_ly(self._page(notes), version="2.24.3", provenance=False)
        self.assertNotIn("_ ", text)

    def test_balanced_slur_reaches_the_lilypond(self):
        notes = [_note(slur="start", onset=0, duration=1),
                 _note(onset=1, duration=1), _note(onset=2, duration=1),
                 _note(slur="stop", onset=3, duration=1)]
        text = to_ly(self._page(notes), version="2.24.3", provenance=False)
        self.assertEqual(text.count("("), 1)
        self.assertEqual(text.count(")"), 1)

    def test_orphan_start_is_dropped_not_left_open(self):
        # A '(' with no partner slur runs on to the end of the system.
        text = to_ly(self._page([_note(slur="start", onset=0)]),
                     version="2.24.3", provenance=False)
        self.assertEqual(text.count("("), 0)

    def test_orphan_stop_is_dropped_not_left_closed(self):
        # A ')' with no '(' is a syntax error.
        text = to_ly(self._page([_note(slur="stop", onset=0)]),
                     version="2.24.3", provenance=False)
        self.assertEqual(text.count(")"), 0)

    def test_overlapping_slurs_do_not_become_nested_brackets(self):
        # LilyPond refuses a second concurrent slur: "already have slur",
        # then "cannot end slur" for the rest of the voice.
        notes = [_note(slur="start", onset=0, duration=1),
                 _note(slur="start", onset=1, duration=1),
                 _note(slur="stop", onset=2, duration=1),
                 _note(slur="stop", onset=3, duration=1)]
        text = to_ly(self._page(notes), version="2.24.3", provenance=False)
        self.assertNotIn("((", text)
        self.assertEqual(text.count("("), text.count(")"))

    def test_slur_may_cross_a_barline(self):
        a = Measure(number=1, beats=4, beat_type=4, divisions=1)
        a.notes = [_note(slur="start", onset=0, duration=1),
                  _note(onset=1, duration=1), _note(onset=2, duration=1),
                  _note(onset=3, duration=1)]
        b = Measure(number=2, beats=4, beat_type=4, divisions=1)
        b.notes = [_note(measure=2, onset=0, duration=1),
                  _note(measure=2, onset=1, duration=1),
                  _note(measure=2, onset=2, duration=1),
                  _note(measure=2, slur="stop", onset=3, duration=1)]
        part = Part(id="p1", name="Music", system=1)
        part.measures = [a, b]
        text = to_ly(Page(id="t", parts=[part]), version="2.24.3",
                     provenance=False)
        self.assertEqual(text.count("("), 1)
        self.assertEqual(text.count(")"), 1)

    def test_slur_survives_a_clamped_bar(self):
        # Clamping rebuilds notes, so a start can be dropped after pairing.
        # Pairing must therefore happen on the survivors, or the surviving
        # ')' is left with nothing to close against.
        notes = [_note(slur="start", onset=0, duration=9),
                 _note(slur="stop", onset=1, duration=1)]
        text = to_ly(self._page(notes), version="2.24.3", provenance=False)
        self.assertEqual(text.count("("), text.count(")"))


class TestSpannersInLilyPond(unittest.TestCase):
    """Ask LilyPond, which is the authority, rather than trusting the token."""

    def _compile(self, notes) -> str:
        page = TestSpanners()._page(notes)
        text = to_ly(page, version="2.24.3", provenance=False)
        if shutil.which("lilypond") is None:
            self.skipTest("lilypond not installed")
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "t.ly"
            src.write_text(text)
            proc = subprocess.run(
                ["lilypond", "-o", str(Path(td) / "t"), str(src)],
                capture_output=True, text=True, timeout=180)
        return "\n".join(r for r in (proc.stdout, proc.stderr) if r)

    def test_lilypond_never_hears_about_a_slur_we_emitted(self):
        notes = [_note(slur="start", onset=0, duration=1),
                 _note(onset=1, duration=1), _note(onset=2, duration=1),
                 _note(slur="stop", onset=3, duration=1)]
        out = self._compile(notes)
        for complaint in ("already have slur", "cannot end slur",
                          "unterminated slur", "missing slur"):
            self.assertNotIn(complaint, out)


class TestGrandStaffStructure(unittest.TestCase):
    """One printed staff per Staff context -- measured, not assumed.

    On p0020 the compiled ``.ly`` rendered 2 staves where the page has 3
    (Piano 2 + Voice 1). A role's staves were joined with ``\\\\`` inside
    ONE ``\\new Staff``, and simultaneous streams inside a single Staff
    share one staff line: the grand staff collapsed into a single staff
    and ``\\clef treble`` fought ``\\clef bass`` at bar 1. The unit here
    is the staff line LilyPond actually draws, so these tests pin the
    emitted structure, and the page-level render control counts staves on
    the rasterized PDF.
    """

    def _page(self, notes, name="Piano", clefs=None):
        meas = Measure(number=1, beats=4, beat_type=4, divisions=1)
        meas.notes = notes
        part = Part(id="p1", name=name, system=1)
        if clefs:
            part.clef_by_staff = clefs
        part.measures = [meas]
        return Page(id="t", parts=[part])

    def test_two_printed_staves_get_two_staff_contexts(self):
        notes = [_note(staff=1, onset=0, duration=4),
                 _note(staff=2, onset=0, duration=4)]
        text = to_ly(self._page(notes), version="2.24.3", provenance=False)
        self.assertIn("\\new PianoStaff", text,
                      "a 2-staff role is one grand staff:\n" + text)
        self.assertEqual(
            text.count("\\new Staff"), 2,
            "\\new PianoStaff does not match \\new Staff -- one Staff "
            "context per printed staff:\n" + text)

    def test_single_staff_role_is_not_wrapped_in_a_pianostaff(self):
        text = to_ly(self._page([_note()]), version="2.24.3",
                     provenance=False)
        self.assertNotIn("PianoStaff", text)
        self.assertEqual(text.count("\\new Staff"), 1)

    def test_every_staff_of_a_grand_staff_carries_the_midi_instrument(self):
        """MIDI is per-Staff. Whether a group's property reaches its child
        staves is not something to bet an audition on, so each child gets
        an explicit copy: group + 2 children = 3."""
        notes = [_note(staff=1), _note(staff=2)]
        text = to_ly(self._page(notes), version="2.24.3", provenance=False)
        self.assertEqual(
            text.count('midiInstrument = "acoustic grand"'), 3, text)

    def test_each_staff_keeps_its_own_clef(self):
        """The collapsed grand staff put both clefs on one staff line."""
        notes = [_note(staff=1, onset=0, duration=4),
                 _note(staff=2, onset=0, duration=4)]
        page = self._page(notes, clefs={1: ("G", 2), 2: ("F", 4)})
        text = to_ly(page, version="2.24.3", provenance=False)
        self.assertIn("\\clef treble", text)
        self.assertIn("\\clef bass", text)
