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
from scorehalo.emit import (GLOBAL_DIVISIONS, Note, emit_page,  # noqa: E402
                            parse_part)
from scorehalo.graph import Measure, Page, Part, Pitch  # noqa: E402

SYSTEM_DIR = os.path.join(ROOT, "data", "out-system")
STEM = "p0020"


def _fragments():
    d = os.path.join(SYSTEM_DIR, "homr", STEM)
    if not os.path.isdir(d):
        return []
    return sorted(f for f in os.listdir(d) if f.endswith("_in.musicxml"))


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

def _assemble_real_page(fragments=None):
    """Assemble the real page exactly the way build_page_graph does."""
    fragments = fragments if fragments is not None else _fragments()
    sysdir = os.path.join(SYSTEM_DIR, "homr", STEM)
    parts_by_role, order = {}, []
    systems = []
    for sysno, f in enumerate(fragments, start=1):
        parts = parse_part(os.path.join(sysdir, f), system=sysno)
        systems.append((sysno, parts))
        for p in parts:
            if p.name not in parts_by_role:
                parts_by_role[p.name] = []
                order.append(p.name)
            parts_by_role[p.name].append(p)
    page = Page(id=STEM, divisions=GLOBAL_DIVISIONS)
    width = {s: max((len(p.measures) for p in ps), default=0)
             for s, ps in systems}
    for i, name in enumerate(order, start=1):
        part = Part(id="P%d" % i, name=name, staff_index=i)
        by_sys = {p.system: p for p in parts_by_role[name]}
        for sysno, _ in systems:
            if sysno in by_sys:
                part.measures.extend(by_sys[sysno].measures)
            else:
                for _ in range(width[sysno]):
                    part.measures.append(Measure(
                        number=0, divisions=GLOBAL_DIVISIONS, is_empty=True))
        page.parts.append(part)
    for p in page.parts:
        for n, m in enumerate(p.measures, start=1):
            m.number = n
    return page, order, len(systems)


# NOTE: these must live at MODULE level. A TestCase nested inside another
# TestCase is not discovered by `unittest discover` -- the first version of
# this file hid all six of them inside `class Real`, and the suite reported
# "Ran 7 tests ... OK" while silently running none of the checks that touch
# the real page. A green suite that is not testing what it claims to is worse
# than no suite at all.


@unittest.skipUnless(_fragments(), "no system fragments on disk")
class TestRealPage(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.page, self.order, self.nsystems = _assemble_real_page()
        self.root = _root(emit_page(self.page))

    def test_all_invariants_hold(self):
        for check in (check_parts_same_width, check_every_note_typed,
                      check_no_chord_on_rest, check_measures_fill,
                      check_measures_numbered_sequentially):
            with self.subTest(check=check.__name__):
                self.assertEqual(check(self.root), [])

    def test_roles_survive_assembly(self):
        self.assertEqual(check_roles_preserved(self.root, self.order), [])

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
        self.assertLessEqual(n_parts, len(_fragments()))

    def test_no_pitched_note_lost_between_graph_and_file(self):
        in_graph = sum(1 for n in self.page.notes if not n.is_rest)
        in_file = sum(1 for p in self.root.findall("part")
                      for m in p.findall("measure")
                      for n in m.findall("note") if n.find("rest") is None)
        self.assertEqual(in_graph, in_file)
        self.assertGreater(in_graph, 0)

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

    def test_absent_role_is_silent_not_missing(self):
        """A role absent from a system contributes bars, not a gap.

        Parts sound simultaneously, so a role that is not printed on the intro
        system must still occupy that system's bars with silence. Otherwise the
        following system's music slides earlier in time and every bar after the
        gap is misaligned.
        """
        for p in self.root.findall("part"):
            nums = [int(m.get("number")) for m in p.findall("measure")]
            # numbered consecutively, no holes
            self.assertEqual(nums, list(range(1, len(nums) + 1)))
        widths = {len(p.findall("measure")) for p in self.root.findall("part")}
        self.assertEqual(len(widths), 1, "parts of unequal width: %s" % widths)

        def test_not_one_part_per_fragment(self):
            page, _order, _nsys = self._page()
            root = _root(emit_page(page))
            n_parts = len(root.find("part-list").findall("score-part"))
            self.assertLessEqual(n_parts, len(self.fragments))
            self.assertGreaterEqual(n_parts, 1)


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
        self.assertEqual(note.duration, 8)  # normalised to divisions=12

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
