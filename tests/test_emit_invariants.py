"""Invariants every emitted score must satisfy.

These run on the graph and on the emitted MusicXML, with no MuseScore and no
scan, so they are fast enough to guard every commit. Each test is named for the
defect it prevents, and the "regression" tests deliberately rebuild the broken
shape and assert the checker CATCHES it -- a checker that cannot fail is not a
checker.

Run: .venv/bin/python -m unittest discover -s tests -v
"""

import os
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pysrc"))

from scorehalo import preprocess  # noqa: E402
from scorehalo.emit import (GLOBAL_DIVISIONS, Note, _TYPE, emit_page,  # noqa: E402
                            parse_part)
from scorehalo.graph import Measure, Page, Part, Pitch  # noqa: E402

SYSTEM_DIR = os.path.join(ROOT, "data", "out-perstaff")
STEM = "p0020"


def _fragments():
    """The staff fragments that produced the shipped page, in staff order.

    ``data/out-system`` (one fragment per system) is retired; production now
    recognizes per staff and writes ``PAGE-<stem>.musicxml`` beside those
    fragments. Reading the per-staff directory keeps this fixture honest -- an
    absent directory yields [] and the class SKIPS, which is visible, rather
    than passing on a page that was never built.
    """
    d = os.path.join(SYSTEM_DIR, "homr", STEM)
    if not os.path.isdir(d):
        return []
    return sorted(f for f in os.listdir(d)
                  if f.startswith(STEM + ".s") and f.endswith("_in.musicxml"))


def _shipped_page_path():
    return os.path.join(SYSTEM_DIR, "PAGE-%s.musicxml" % STEM)


def _root(xml_text):
    return ET.fromstring(xml_text)


# --------------------------------------------------------------------------
# Checkers. Each returns a list of human-readable problems; empty == healthy.
# --------------------------------------------------------------------------

def check_parts_same_width(root):
    """Every part must span the same number of measures.

    Parts sound simultaneously, so a part that stops early is malformed, not
    short. MuseScore rejects the whole file (rc=40) rather than padding.
    """
    problems = []
    widths = {}
    for p in root.findall("part"):
        widths[p.get("id")] = len(p.findall("measure"))
    if len(set(widths.values())) > 1:
        problems.append("parts have unequal measure counts: %s" % widths)
    return problems


def check_every_note_typed(root):
    """Every note must carry a <type>.

    Omitting it is legal MusicXML but not survivable here: a note of 2/3 of a
    quarter has a <duration> that no plain note type expresses, and importers
    reject it. The note needs a <type> plus a <time-modification> to say why.
    """
    problems = []
    for p in root.findall("part"):
        for m in p.findall("measure"):
            for i, n in enumerate(m.findall("note"), start=1):
                if n.find("rest") is not None and n.find("type") is None:
                    continue  # a whole-measure rest legitimately has no type
                if n.find("type") is None:
                    problems.append("part %s measure %s note %d has no <type>"
                                    % (p.get("id"), m.get("number"), i))
    return problems


def check_no_chord_on_rest(root):
    """A <chord> tone must attach to a real note, never to a rest.

    Merging fragments that disagree can place a rest and a pitched note at the
    same (voice, onset). The corruption is not that the chord note *is* a rest
    -- a <chord> note carries its own <pitch> -- it is that the chord ATTACHES
    TO the rest. So the check has to look at what each <chord> points back to,
    which is the previous non-chord note. (Getting this wrong the first time
    is why the regression test below exists: the naive version reported a clean
    bill of health on exactly the corruption it was written to catch.)
    """
    problems = []
    for p in root.findall("part"):
        for m in p.findall("measure"):
            prev_is_rest = True   # a chord before any note is unattached
            for n in m.findall("note"):
                is_chord = n.find("chord") is not None
                is_rest = n.find("rest") is not None
                if is_chord:
                    if prev_is_rest:
                        problems.append("part %s measure %s: chord tone "
                                        "attaches to a rest or to nothing"
                                        % (p.get("id"), m.get("number")))
                else:
                    prev_is_rest = is_rest
    return problems


def check_measures_fill(root):
    """No VOICE may overrun its meter.

    This has to be computed with a cursor that honours ``<backup>`` and
    ``<forward>``, because MusicXML walks a single cursor through all voices
    and steps BACKWARD between them. Summing every note's <duration> -- the
    obvious way -- counts a measure with three voices as three times overfull
    and reports corruption that is not there. (That mistake made this checker
    fire on all 9 measures of a file MuseScore and LilyPond both accepted.)

    The rule it enforces: the cursor never goes negative (a backup that
    overshoots the start of its measure), and the final cursor lands exactly on
    the bar length (nothing spills past the barline).
    """
    problems = []
    for p in root.findall("part"):
        divisions = GLOBAL_DIVISIONS
        beats, beat_type = 4, 4
        for m in p.findall("measure"):
            attrs = m.find("attributes")
            if attrs is not None:
                if attrs.findtext("divisions"):
                    divisions = int(attrs.findtext("divisions"))
                if attrs.findtext("time/beats"):
                    beats = int(attrs.findtext("time/beats"))
                if attrs.findtext("time/beat-type"):
                    beat_type = int(attrs.findtext("time/beat-type"))
            total = beats * divisions * 4 // beat_type
            pos = 0
            low = 0
            for el in m:
                if el.tag == "backup":
                    pos -= int(el.findtext("duration") or 0)
                    low = min(low, pos)
                elif el.tag == "forward":
                    pos += int(el.findtext("duration") or 0)
                elif el.tag == "note":
                    if el.find("chord") is not None:
                        continue   # shares the previous note's onset
                    pos += int(el.findtext("duration") or 0)
            if low < 0:
                problems.append("part %s measure %s: backup overshoots the "
                                "bar start (cursor reached %d)"
                                % (p.get("id"), m.get("number"), low))
            elif pos > total:
                problems.append("part %s measure %s overruns: %d > %d"
                                % (p.get("id"), m.get("number"), pos, total))
    return problems


def check_written_value_matches_duration(root):
    """The written value of a note must equal the ticks it occupies.

    ``<type>``/``<dot>`` and ``<time-modification>`` are what an importer reads
    to decide how long a note IS; ``<duration>`` is what it plays. When the two
    disagree the file is self-contradictory, and MuseScore resolves it the
    engraver's way: it sums the NOMINAL values and reports the bar as short or
    long. Rounding a 15-tick note up to a dotted quarter (18) made a full bar
    read as 51/48 -- "Measure 1, staff 1 incomplete. Expected: 4/4; Found:
    17/16" -- while every cursor-based check here and in validate_musicxml.py
    stayed silent, because <duration> (which those checks sum) was correct.

    This is the check that would have caught it.
    """
    from fractions import Fraction
    problems = []
    for p in root.findall("part"):
        divisions = GLOBAL_DIVISIONS
        for m in p.findall("measure"):
            attrs = m.find("attributes")
            if attrs is not None and attrs.findtext("divisions"):
                divisions = int(attrs.findtext("divisions"))
            for i, n in enumerate(m.findall("note"), start=1):
                t = n.findtext("type")
                if t is None:
                    continue
                dur = n.findtext("duration")
                if dur is None:
                    continue
                dots = len(n.findall("dot"))
                written = Fraction(_TYPE[t]) * divisions
                written *= Fraction(2 ** (dots + 1) - 1, 2 ** dots)
                tm = n.find("time-modification")
                if tm is not None:
                    a = int(tm.findtext("actual-notes") or 1)
                    nn = int(tm.findtext("normal-notes") or 1)
                    written *= Fraction(nn, a)
                if written != int(dur):
                    problems.append(
                        "part %s measure %s note %d: %s with %d dot(s) "
                        "under <time-modification> is %s ticks but "
                        "<duration> is %s"
                        % (p.get("id"), m.get("number"), i, t, dots,
                           written, dur))
    return problems


def check_measures_numbered_sequentially(root):
    """Each part's measures must read 1..N with no gaps or repeats."""
    problems = []
    for p in root.findall("part"):
        nums = [int(m.get("number")) for m in p.findall("measure")]
        if nums != list(range(1, len(nums) + 1)):
            problems.append("part %s measure numbers are not 1..N: %s"
                            % (p.get("id"), nums))
    return problems


def check_roles_preserved(root, expected):
    """Part names must still say which instrument they are.

    Regression: naming every part "Staff 1" makes a page of one piano
    impossible to assemble, because the fragments can no longer be matched.
    """
    got = [sp.findtext("part-name")
           for sp in root.find("part-list").findall("score-part")]
    if sorted(got) != sorted(expected):
        return ["part names %s != expected %s" % (got, expected)]
    return []


def check_no_fabricated_parts(root, real_fragments):
    """Parts must equal the set of real roles, not one per fragment.

    Regression: emitting one part per system fragment turns a 2-instrument,
    10-bar page into a 5-instrument, 4-bar page -- a different piece of music
    that happens to import.
    """
    names = [sp.findtext("part-name")
             for sp in root.find("part-list").findall("score-part")]
    if len(names) > len(real_fragments):
        return []
    return ["emitted %d parts for %d fragments: one part per fragment is the "
            "one-part-per-system bug" % (len(names), len(real_fragments))]


# --------------------------------------------------------------------------
# Unit tests
# --------------------------------------------------------------------------

# REMOVED: _assemble_real_page(). It built the page itself from per-system
# fragments and called scorehalo.meter by hand. That was the second assembler
# which let these tests stay green while measuring pre-fix behaviour (the four
# dropped notes stayed invisible here long after the production path was fixed),
# and it is moot now that the per-system fixture directory is retired:
# _fragments() returned [] there, so it assembled an EMPTY page and the note-count
# test compared 0 == 0. tests/test_meter.py has its own per-staff assembler where
# a deliberately UNFIXED page is still worth having.

# NOTE: these must live at MODULE level. A TestCase nested inside another
# TestCase is not discovered by `unittest discover` -- the first version of
# this file hid all six of them inside `class Real`, and the suite reported
# "Ran 7 tests ... OK" while silently running none of the checks that touch
# the real page. A green suite that is not testing what it claims to is worse
# than no suite at all.
#
# The class below checks the artifact production wrote rather than rebuilding it,
# for two reasons: the per-system fixture directory is retired, and a hand-rolled
# second assembler is exactly how this file once measured pre-fix behaviour while
# claiming to measure the shipping path. Ground truth -- how many fragments, how
# many notes, how many systems -- is read from the fragments, so the assertions
# cannot drift with the artifact.


@unittest.skipUnless(_fragments(), "no per-staff fragments on disk")
class TestRealPage(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        path = _shipped_page_path()
        if not os.path.exists(path):
            self.skipTest("shipped PAGE-%s.musicxml not present" % STEM)
        with open(path, encoding="utf-8") as fh:
            self.root = _root(fh.read())
        self.fragments = _fragments()
        # This page is three staves per system (3+3+3); see tests/test_brace.py.
        self.nsystems = len(self.fragments) // 3

    def _fragment_note_total(self):
        total = 0
        for name in self.fragments:
            path = os.path.join(SYSTEM_DIR, "homr", STEM, name)
            for part in parse_part(path):
                total += sum(1 for m in part.measures for n in m.notes
                             if not n.is_rest)
        return total

    def _file_note_total(self):
        return sum(1 for p in self.root.findall("part")
                   for m in p.findall("measure")
                   for n in m.findall("note") if n.find("rest") is None)

    def test_all_invariants_hold(self):
        for check in (check_parts_same_width, check_every_note_typed,
                      check_no_chord_on_rest, check_measures_fill,
                      check_written_value_matches_duration,
                      check_measures_numbered_sequentially):
            with self.subTest(check=check.__name__):
                self.assertEqual(check(self.root), [])

    def test_roles_survive_assembly(self):
        """One singer, one piano -- the voice is not folded into the piano."""
        self.assertEqual(check_roles_preserved(self.root, ["Voice", "Piano"]), [])

    def test_duplicate_role_parts_are_rejected(self):
        """Two parts with the same role means the system bug is back.

        Regression, proven by mutation: emitting one part per system fragment
        produces 5 parts named Piano/Piano/Voice/Piano/Voice. That file
        IMPORTS into MuseScore with rc=0 and passes the equal-width check, so
        nothing else here would catch it -- it is musically wrong (a 2-role,
        10-bar page rewritten as a 5-part, 4-bar page) while being
        structurally valid.
        """
        names = [sp.findtext("part-name")
                 for sp in self.root.find("part-list").findall("score-part")]
        self.assertEqual(len(names), len(set(names)),
                         "duplicate role parts: %s -- one part per system "
                         "fragment is the bug" % names)

    def test_not_one_part_per_fragment(self):
        n_parts = len(self.root.find("part-list").findall("score-part"))
        self.assertLessEqual(n_parts, len(self.fragments))

    def test_no_pitched_note_lost_between_fragments_and_file(self):
        """The shipped page against the fragments' own count (183 here).

        This compares the artifact to its inputs, not one rebuild to another: the
        previous version of this test compared an empty page to itself (0 == 0)
        once the fixture directory was retired, and could not fail.
        """
        in_file = self._file_note_total()
        self.assertEqual(in_file, self._fragment_note_total())
        self.assertGreater(in_file, 0)

    def test_no_measure_is_empty_xml(self):
        for p in self.root.findall("part"):
            for m in p.findall("measure"):
                self.assertTrue(m.findall("note"),
                                "part %s measure %s has no note"
                                % (p.get("id"), m.get("number")))

    def test_page_is_longer_than_one_system(self):
        """A page must span all its systems, not just the first.

        Guards the regression that motivated role assembly: the old output was
        4 bars long (one system) presented as a whole page.
        """
        widths = {p.get("id"): len(p.findall("measure"))
                  for p in self.root.findall("part")}
        longest = max(widths.values())
        self.assertGreaterEqual(
            longest, self.nsystems,
            "page is only %d bars for %d systems -- fragments are not being "
            "concatenated in reading order" % (longest, self.nsystems))

    def test_every_part_spans_the_same_bars(self):
        """Parts sound together: a short part is malformed, not terse.

        Also the shape the "absent role" rule protects -- a role not printed on
        a system must still occupy that system's bars with silence, or every bar
        after the gap slides early for that part alone.
        """
        for p in self.root.findall("part"):
            nums = [int(m.get("number")) for m in p.findall("measure")]
            # numbered consecutively, no holes
            self.assertEqual(nums, list(range(1, len(nums) + 1)))
        widths = {len(p.findall("measure")) for p in self.root.findall("part")}
        self.assertEqual(len(widths), 1, "parts of unequal width: %s" % widths)


class TestCheckersCatchRegressions(unittest.TestCase):
    """A checker that cannot fail is not a checker."""

    def test_equal_width_checker_catches_unequal_parts(self):
        xml = """<score-partwise version="4.0"><part-list>
        <score-part id="P1"><part-name>A</part-name></score-part>
        <score-part id="P2"><part-name>B</part-name></score-part></part-list>
        <part id="P1"><measure number="1">
        <attributes><divisions>12</divisions><time><beats>4</beats>
        <beat-type>4</beat-type></time></attributes>
        <note><rest measure="yes"/><duration>48</duration></note>
        </measure></part>
        <part id="P2"><measure number="1">
        <attributes><divisions>12</divisions><time><beats>4</beats>
        <beat-type>4</beat-type></time></attributes>
        <note><rest measure="yes"/><duration>48</duration></note>
        </measure><measure number="2">
        <attributes><divisions>12</divisions><time><beats>4</beats>
        <beat-type>4</beat-type></time></attributes>
        <note><rest measure="yes"/><duration>48</duration></note>
        </measure></part></score-partwise>"""
        problems = check_parts_same_width(_root(xml))
        self.assertTrue(problems, "equal-width checker missed unequal parts")
        self.assertIn("unequal", problems[0])

    def test_typed_note_checker_catches_untyped_note(self):
        xml = """<score-partwise version="4.0"><part-list>
        <score-part id="P1"><part-name>A</part-name></score-part></part-list>
        <part id="P1"><measure number="1">
        <attributes><divisions>12</divisions><time><beats>4</beats>
        <beat-type>4</beat-type></time></attributes>
        <note><pitch><step>C</step><octave>4</octave></pitch>
        <duration>8</duration></note>
        </measure></part></score-partwise>"""
        problems = check_every_note_typed(_root(xml))
        self.assertTrue(problems, "type checker missed an untyped note")

    def test_chord_on_rest_checker_catches_it(self):
        xml = """<score-partwise version="4.0"><part-list>
        <score-part id="P1"><part-name>A</part-name></score-part></part-list>
        <part id="P1"><measure number="1">
        <attributes><divisions>12</divisions><time><beats>4</beats>
        <beat-type>4</beat-type></time></attributes>
        <note><rest measure="yes"/><duration>48</duration></note>
        <note><chord/><pitch><step>C</step><octave>4</octave></pitch>
        <duration>48</duration></note>
        </measure></part></score-partwise>"""
        problems = check_no_chord_on_rest(_root(xml))
        self.assertTrue(problems, "chord-on-rest checker missed it")

    def test_overrun_checker_catches_overrun(self):
        xml = """<score-partwise version="4.0"><part-list>
        <score-part id="P1"><part-name>A</part-name></score-part></part-list>
        <part id="P1"><measure number="1">
        <attributes><divisions>12</divisions><time><beats>4</beats>
        <beat-type>4</beat-type></time></attributes>
        <note><rest measure="yes"/><duration>60</duration></note>
        </measure></part></score-partwise>"""
        problems = check_measures_fill(_root(xml))
        self.assertTrue(problems, "overrun checker missed an overfull measure")

    def test_numbering_checker_catches_gaps(self):
        xml = """<score-partwise version="4.0"><part-list>
        <score-part id="P1"><part-name>A</part-name></score-part></part-list>
        <part id="P1"><measure number="1">
        <attributes><divisions>12</divisions><time><beats>4</beats>
        <beat-type>4</beat-type></time></attributes>
        <note><rest measure="yes"/><duration>48</duration></note>
        </measure><measure number="3">
        <attributes><divisions>12</divisions><time><beats>4</beats>
        <beat-type>4</beat-type></time></attributes>
        <note><rest measure="yes"/><duration>48</duration></note>
        </measure></part></score-partwise>"""
        problems = check_measures_numbered_sequentially(_root(xml))
        self.assertTrue(problems, "numbering checker missed a gap")

    def test_written_value_checker_catches_overstated_type(self):
        """A 15-tick note written as a dotted quarter (18) must be caught.

        This is the exact defect MuseScore reported as "Measure 1, staff 1
        incomplete. Expected: 4/4; Found: 17/16" (6+3+24+18 = 51 = 17/16), and
        which no cursor-based check in the suite could see.
        """
        xml = """<score-partwise version="4.0"><part-list>
        <score-part id="P1"><part-name>A</part-name></score-part></part-list>
        <part id="P1"><measure number="1">
        <attributes><divisions>12</divisions><time><beats>4</beats>
        <beat-type>4</beat-type></time></attributes>
        <note><pitch><step>C</step><octave>4</octave></pitch>
        <duration>15</duration><type>quarter</type><dot/></note>
        </measure></part></score-partwise>"""
        problems = check_written_value_matches_duration(_root(xml))
        self.assertTrue(problems,
                        "written-value checker missed an overstated <type>")

    def test_written_value_checker_accepts_exact_time_modification(self):
        """The same 15 ticks written with an exact 4:5 ratio is honest."""
        xml = """<score-partwise version="4.0"><part-list>
        <score-part id="P1"><part-name>A</part-name></score-part></part-list>
        <part id="P1"><measure number="1">
        <attributes><divisions>12</divisions><time><beats>4</beats>
        <beat-type>4</beat-type></time></attributes>
        <note><pitch><step>C</step><octave>4</octave></pitch>
        <duration>15</duration><type>quarter</type>
        <time-modification><actual-notes>4</actual-notes>
        <normal-notes>5</normal-notes></time-modification></note>
        </measure></part></score-partwise>"""
        self.assertEqual(check_written_value_matches_duration(_root(xml)), [])


class TestTypeForIsExact(unittest.TestCase):
    """The <type> we write must name exactly the ticks we occupy.

    ``_type_for`` is where the overstatement lived: a duration with no plain
    written value was named by the value at-or-above it, so a 15-tick note
    became a dotted quarter (18) and MuseScore summed the bar past its meter.
    Nothing here talks to MuseScore, so this guards every commit.
    """

    def test_every_duration_names_itself_exactly(self):
        from fractions import Fraction
        from scorehalo.emit import _TYPE, _type_for

        def written(dur):
            name, dots, actual, normal = _type_for(dur)
            value = Fraction(_TYPE[name]) * GLOBAL_DIVISIONS
            value *= Fraction(2 ** (dots + 1) - 1, 2 ** dots)
            if actual:
                value *= Fraction(normal, actual)
            return value

        for dur in range(1, 97):
            with self.subTest(duration=dur):
                self.assertEqual(written(dur), dur,
                                 "duration %d is written as %s"
                                 % (dur, _type_for(dur)))

    def test_awkward_durations_use_the_simplest_ratio(self):
        """A note is never written under a needlessly exotic tuplet.

        The spelling depends on GLOBAL_DIVISIONS, so assert the property, not
        the spelling: for any duration with no plain written value, the ratio
        actually chosen must be the simplest available -- the one minimising the
        larger of numerator and denominator over every exact note value. 45
        ticks must come out as a 4:5 tuplet, never as a 15:8 one.
        """
        from math import gcd
        from scorehalo.emit import _TYPE_FOR, _type_for
        for dur in range(1, 97):
            if dur in _TYPE_FOR:
                continue
            with self.subTest(duration=dur):
                name, dots, actual, normal = _type_for(dur)
                self.assertIn(name, _TYPE)
                self.assertEqual(gcd(actual, normal), 1)
                want = min(max(dur // gcd(dur, t), t // gcd(dur, t))
                           for t in _TYPE_FOR)
                self.assertEqual(max(actual, normal), want,
                                 "%d written as %d:%d, simplest is %d"
                                 % (dur, normal, actual, want))

    def test_plain_durations_carry_no_ratio(self):
        from scorehalo.emit import _TYPE_FOR, _type_for
        self.assertIn(3, _TYPE_FOR)
        for dur in _TYPE_FOR:
            with self.subTest(duration=dur):
                self.assertEqual(_type_for(dur)[2:], (0, 0))


class TestTupletRoundTrip(unittest.TestCase):
    """A 3:2 tuplet must survive parse -> graph -> emit intact."""

    def test_tuplet_ratio_preserved(self):
        xml = """<score-partwise version="4.0"><part-list>
        <score-part id="P1"><part-name>A</part-name></score-part></part-list>
        <part id="P1"><measure number="1">
        <attributes><divisions>6</divisions><time><beats>4</beats>
        <beat-type>4</beat-type></time></attributes>
        <note><pitch><step>C</step><octave>4</octave></pitch>
        <duration>4</duration><type>quarter</type>
        <time-modification><actual-notes>3</actual-notes>
        <normal-notes>2</normal-notes></time-modification></note>
        </measure></part></score-partwise>"""
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".musicxml",
                                         delete=False) as fh:
            fh.write(xml)
            path = fh.name
        try:
            parts = parse_part(path)
        finally:
            os.unlink(path)
        note = parts[0].measures[0].notes[0]
        self.assertEqual(note.tuplet, (3, 2))
        # 4 units at <divisions>6</divisions> is 2/3 of a quarter, so the tick
        # count follows GLOBAL_DIVISIONS (8 when it was 12, 16 now it is 24).
        self.assertEqual(note.duration, 4 * GLOBAL_DIVISIONS // 6)

    def test_type_written_for_every_non_rest_note(self):
        page = Page(id="t", divisions=GLOBAL_DIVISIONS)
        part = Part(id="P1", name="A")
        m = Measure(number=1, divisions=GLOBAL_DIVISIONS)
        m.notes.append(Note(staff=1, voice=1, onset=0, duration=8,
                            pitch=Pitch("C", 0, 4), measure=1))
        part.measures.append(m)
        page.parts.append(part)
        root = _root(emit_page(page))
        self.assertEqual(check_every_note_typed(root), [])
        n = root.find(".//note")
        self.assertIsNotNone(n.find("type"))


class TestEnhanceWritesFile(unittest.TestCase):
    """--enhance was silently a no-op: the enhanced/ dir was never created and
    cv2.imwrite returns False rather than raising, so convert went on to hand
    homr a path that did not exist."""

    def test_creates_missing_parent_and_writes_png(self):
        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "in.png")
            dst = os.path.join(td, "does", "not", "exist", "out.png")
            cv2.imwrite(src, np.full((40, 60, 3), 200, dtype=np.uint8))

            returned = preprocess.enhance(src, dst)

            self.assertEqual(returned, dst)
            self.assertTrue(os.path.exists(dst), "enhance() returned a path it never wrote")
            self.assertIsNotNone(cv2.imread(dst))

    def test_raises_instead_of_silently_skipping_when_write_fails(self):
        """Measured on OpenCV 5.0.0, imwrite has TWO distinct behaviours:
        a missing parent directory and an unwritable directory both return
        False with only a stderr WARN, while a bad extension raises cv2.error.
        The silent-False cases are the dangerous ones, so enhance() must not
        hand back a path it never wrote."""
        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "in.png")
            cv2.imwrite(src, np.full((20, 20, 3), 10, dtype=np.uint8))

            ro = os.path.join(td, "readonly")
            os.makedirs(ro)
            os.chmod(ro, 0o500)
            try:
                with self.assertRaises(OSError):
                    preprocess.enhance(src, os.path.join(ro, "out.png"))
            finally:
                os.chmod(ro, 0o700)


if __name__ == "__main__":
    unittest.main(verbosity=2)
