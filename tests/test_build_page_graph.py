"""Tests for the page assembler's clef handling (scripts/build_page_graph.py).

``_concat_roles`` concatenates one role's fragments across every system of the
page. It used to copy only the *measures*, so the clefs homr had read on each
fragment were dropped on the floor and ``Part.clef_by_staff`` arrived empty.
``emit._attributes`` then found a part with no clef map at all and fell back to
a single ``Measure.clef`` scalar -- which is why a two-staff piano was published
declaring **no** ``<staves>`` element and one treble clef, engraving its bass
staff as treble.

The rules under test, in order of how quietly they can go wrong:

* a fragment's clefs survive concatenation onto the assembled part;
* a staff's clef is a vote across systems, so one misread system loses to the
  two that read it right (p0020's own shape: 2 of 3 systems say bass);
* a disagreement is *reported*, never resolved in silence;
* the emitted MusicXML declares ``<staves>2</staves>`` and one clef per staff.

The last one is the assertion that matters most: it is what a before/after
render actually measured, and nothing else in the suite would catch its loss --
these functions live in ``scripts/``, which no other test imports.

Run: .venv/bin/python -m unittest discover -s tests -v
"""

import io
import os
import sys
import unittest
from contextlib import redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pysrc"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from build_page_graph import _concat_roles           # noqa: E402
from scorehalo.emit import GLOBAL_DIVISIONS, emit_page  # noqa: E402
from scorehalo.graph import Measure, Page, Part      # noqa: E402

G, F = ("G", 2), ("F", 4)


def _fragment(name, system, clefs, bars=1):
    """One system's worth of one role, as homr recognised it."""
    part = Part(id="%s-%d" % (name, system), name=name, system=system,
                clef_by_staff=dict(clefs))
    for i in range(bars):
        part.measures.append(Measure(number=i + 1, divisions=GLOBAL_DIVISIONS))
    return part


def _page_with(clefs_by_system, bars=1):
    """Assemble ``{system: [(role, clefs), ...]}`` the way main() does."""
    systems, roles, order = [], {}, []
    for sysno in sorted(clefs_by_system):
        parts = []
        for name, clefs in clefs_by_system[sysno]:
            p = _fragment(name, sysno, clefs, bars)
            parts.append(p)
            if name not in roles:
                roles[name] = []
                order.append(name)
            roles[name].append(p)
        systems.append((sysno, parts))
    page = Page(id="p", divisions=GLOBAL_DIVISIONS)
    page.parts = _concat_roles(order, roles, systems, page)
    return page


class TestClefsSurviveConcatenation(unittest.TestCase):
    """The clefs homr read must reach the assembled part."""

    def test_single_system_clefs_are_carried(self):
        page = _page_with({1: [("Piano", {1: G, 2: F})]})
        self.assertEqual(page.parts[0].clef_by_staff, {1: G, 2: F})

    def test_a_two_staff_role_keeps_both_clefs(self):
        page = _page_with({1: [("Piano", {1: G, 2: F})],
                           2: [("Piano", {1: G, 2: F})],
                           3: [("Piano", {1: G, 2: F})]})
        self.assertEqual(page.parts[0].clef_by_staff, {1: G, 2: F})

    def test_every_role_gets_its_own_clefs(self):
        page = _page_with({1: [("Piano", {1: G, 2: F}), ("Voice", {1: G})]})
        by_name = {p.name: p for p in page.parts}
        self.assertEqual(by_name["Piano"].clef_by_staff, {1: G, 2: F})
        self.assertEqual(by_name["Voice"].clef_by_staff, {1: G})


class TestClefVote(unittest.TestCase):
    """One misread system must not overrule the systems that read it right.

    p0020's shape: system 1 reads the piano's bass staff as treble, systems 2
    and 3 read it as bass. First-wins would ship treble; the vote ships bass.
    """

    def test_majority_of_systems_wins(self):
        page = _page_with({1: [("Piano", {1: G, 2: G})],
                           2: [("Piano", {1: G, 2: F})],
                           3: [("Piano", {1: G, 2: F})]})
        self.assertEqual(page.parts[0].clef_by_staff, {1: G, 2: F},
                         "2 of 3 systems say bass; the vote must take bass")

    def test_tie_keeps_the_first_reading(self):
        page = _page_with({1: [("Piano", {1: G})],
                           2: [("Piano", {1: F})]})
        self.assertEqual(page.parts[0].clef_by_staff, {1: G})

    def test_a_role_absent_from_a_system_casts_no_vote(self):
        # Voice sits out system 1, so only systems 2 and 3 vote on its clef.
        page = _page_with({1: [("Piano", {1: G, 2: F})],
                           2: [("Piano", {1: G, 2: F}), ("Voice", {1: G})],
                           3: [("Piano", {1: G, 2: F}), ("Voice", {1: G})]})
        voice = [p for p in page.parts if p.name == "Voice"][0]
        self.assertEqual(voice.clef_by_staff, {1: G})


class TestDisagreementIsReported(unittest.TestCase):
    """A conflict resolved by a vote must still be said out loud."""

    def test_conflicting_systems_print_a_note(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            _page_with({1: [("Piano", {1: G})],
                        2: [("Piano", {1: G})],
                        3: [("Piano", {1: F})]})
        out = buf.getvalue()
        self.assertIn("systems disagree on the clef", out)
        self.assertIn("G2", out)
        self.assertIn("F4", out)

    def test_agreeing_systems_print_nothing(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            _page_with({1: [("Piano", {1: G})],
                        2: [("Piano", {1: G})]})
        self.assertNotIn("disagree", buf.getvalue())


class TestEmittedMusicXML(unittest.TestCase):
    """The point of carrying clefs: what actually reaches the engraver."""

    def _xml(self):
        page = _page_with({1: [("Piano", {1: G, 2: F})],
                           2: [("Piano", {1: G, 2: F})],
                           3: [("Piano", {1: G, 2: F})]})
        return emit_page(page)

    def test_two_staff_part_declares_staves_and_one_clef_each(self):
        xml = self._xml()
        self.assertIn("<staves>2</staves>", xml,
                      "a two-staff piano must declare <staves>; without it the "
                      "engraver is told the bass staff does not exist")
        self.assertIn('<clef number="1"><sign>G</sign><line>2</line></clef>', xml)
        self.assertIn('<clef number="2"><sign>F</sign><line>4</line></clef>', xml)

    def test_bass_staff_is_not_published_as_treble(self):
        xml = self._xml()
        first_bar = xml.split("<note", 1)[0]
        self.assertIn('<clef number="2">', first_bar)
        self.assertNotRegex(
            first_bar, r'number="2"><sign>G',
            "the bass staff was engraved as treble -- the note reads nearly "
            "two octaves from where it sounds")


if __name__ == "__main__":
    unittest.main()
