"""Regression tests for the native LilyPond emitter.

The ordering test exists because of a real bug: the emitter wrote
``c'^\\markup{..}4`` -- markup between pitch and duration. LilyPond reads that
as an untimed note wearing a markup followed by a stray ``4``, so the note
silently became a whole note and every bar ran long. The only symptom was a
barcheck warning pointing at a bar, never at the note, and a duration
re-reader would agree with the wrong answer, because the digits are right.
So the structural rule is asserted directly, and LilyPond is asked to agree.
"""

import re
import shutil
import subprocess
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

from scorehalo.graph import Measure, Note, Page, Part, Pitch
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
        # The duration must be adjacent to the pitch, not merely present.
        self.assertRegex(out, r"^c'4(?:\^|~|_|<)")

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


class TestEmittedFile(unittest.TestCase):
    def setUp(self):
        self.ly = to_ly(_one_bar_page(), provenance=True)

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
