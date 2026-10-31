"""Every staff a part's notes land on must be declared (``<staves>``).

The defect these exist to prevent
---------------------------------
``_attributes`` writes ``<staves>`` only when the part's clef map holds more
than one staff, and it numbers the ``<clef>`` elements from that map. So a part
whose *notes* carry ``<staff>2</staff>`` while the *map* holds staff 1 alone was
published as a one-staff part with notes on a staff that was never declared.
MuseScore imports that happily -- it silently invents the staff -- which is why
it survived a whole band run; ``scripts/validate_musicxml.py`` rejects it, and
one page of the I-Just-Fall run (p0029, bars 34-35) produced exactly that shape.

The fix is ``emit._declare_every_used_staff()``: the declaration is made to
match the notes, inheriting the part's primary clef for any staff the map lacks.
No note is moved, invented, or dropped.

Why assert on the XML text: the graph was never wrong in that run -- the file on
disk was. A test of the graph cannot see this bug, so these read the emitted
document, and one test disables the helper to prove the assertions have teeth.

Run: .venv/bin/python -m unittest tests.test_declared_staffs -v
"""

import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pysrc"))

from scorehalo import emit
from scorehalo.emit import GLOBAL_DIVISIONS, emit_page
from scorehalo.graph import Measure, Note, Page, Part, Pitch

G = ("G", 2)
F = ("F", 4)


def page_with(staffs, clef_by_staff, measures=1, notes_per_measure=2):
    """A one-part page whose notes land on ``staffs``, clefs declared as given.

    Built directly from the graph rather than through ``assemble_page`` because
    this is the *shape a role bug leaves behind*: the brace logic called the
    region single-staff, so the map has one clef, while the notes it stamped
    carry a second staff number.
    """
    part = Part(id="P", name="Piano", clef_by_staff=dict(clef_by_staff))
    for number in range(1, measures + 1):
        notes = [Note(staff=s, voice=1, onset=i, duration=1,
                      pitch=Pitch(step="e", alter=0, octave=4), measure=number)
                 for i, s in enumerate(staffs)]
        part.measures.append(Measure(number=number, divisions=GLOBAL_DIVISIONS,
                                     notes=notes))
    return Page(id="p", parts=[part])


def part_xml(xml, index=1):
    return xml.split('<part id="P%d">' % index)[1].split("</part>")[0]


class UndeclaredStaffTests(unittest.TestCase):
    """The p0029 shape: notes on staff 2, clef map holding staff 1 only."""

    def setUp(self):
        self.page = page_with([1, 2], {1: G})
        self.xml = emit_page(self.page)
        self.part = part_xml(self.xml)

    def test_the_staff_count_matches_the_notes(self):
        self.assertIn("<staves>2</staves>", self.part)

    def test_both_staves_declare_a_clef(self):
        self.assertIn('<clef number="1"><sign>G</sign><line>2</line></clef>',
                      self.part)
        self.assertIn('<clef number="2"><sign>G</sign><line>2</line></clef>',
                      self.part)

    def test_the_missing_staff_inherits_the_primary_clef(self):
        """No clef was read for staff 2, so the part's own clef is the honest guess."""
        signs = re.findall(r'<clef number="\d"><sign>([A-Z])</sign>', self.part)
        self.assertEqual(signs, ["G", "G"])

    def test_no_note_is_lost(self):
        self.assertEqual(self.part.count("<note>"), 2)
        self.assertEqual(self.part.count("<staff>2</staff>"), 1)

    def test_staves_precedes_the_clefs(self):
        """Schema order: <staves> sits between <time> and <clef>."""
        self.assertLess(self.part.index("<staves>"), self.part.index("<clef"))

    def test_the_declaration_survives_a_second_bar(self):
        """The map is repaired on the part, so bar 2 must not fall back."""
        page = page_with([1, 2], {1: G}, measures=2)
        bars = re.findall(r'<measure number="(\d+)">(.*?)</measure>',
                          part_xml(emit_page(page)), re.S)
        for number, body in bars:
            if "<staves>" in body:
                self.assertIn("<staves>2</staves>", body,
                              "bar %s declares a different staff count" % number)


class GappedStaffTests(unittest.TestCase):
    """Staff numbers must stay contiguous: a gap cannot be left as a hole.

    ``<staves>`` is a *count*; the ``<staff>`` numbers are positions 1..count.
    Notes on staves 1 and 3 with a two-entry clef map would declare "two
    staves" and then name staff 3 -- a document no reader can lay out. The
    missing position is declared rather than the notes renumbered, because the
    staff number is page geometry and the note's pixel provenance depends on it.
    """

    def setUp(self):
        self.xml = emit_page(page_with([1, 3], {1: G}))
        self.part = part_xml(self.xml)

    def test_the_count_covers_the_highest_staff(self):
        self.assertIn("<staves>3</staves>", self.part)

    def test_every_position_has_a_clef(self):
        numbers = re.findall(r'<clef number="(\d)">', self.part)
        self.assertEqual(numbers, ["1", "2", "3"])

    def test_no_staff_is_named_beyond_the_count(self):
        declared = int(re.search(r"<staves>(\d+)</staves>", self.part).group(1))
        used = {int(n) for n in re.findall(r"<staff>(\d+)</staff>", self.part)}
        self.assertLessEqual(max(used), declared)

    def test_the_gap_staff_carries_no_notes(self):
        self.assertEqual(self.part.count("<staff>2</staff>"), 0)


class UntouchedPartTests(unittest.TestCase):
    """The helper must not change a part that was already coherent."""

    def test_one_staff_part_keeps_the_scalar_form(self):
        part = part_xml(emit_page(page_with([1], {1: G})))
        self.assertNotIn("<staves>", part)
        self.assertNotIn("<clef number=", part)

    def test_a_declared_two_staff_part_keeps_its_own_clefs(self):
        part = part_xml(emit_page(page_with([1, 2], {1: G, 2: F})))
        self.assertIn('<clef number="2"><sign>F</sign><line>4</line></clef>',
                      part)

    def test_a_part_with_no_clef_map_still_gets_one(self):
        """homr supplies no clef at all; the fallback must be treble, not a crash."""
        part = part_xml(emit_page(page_with([1, 2], {})))
        self.assertIn("<staves>2</staves>", part)
        self.assertEqual(len(re.findall(r"<clef number=", part)), 2)


class MutationControlTests(unittest.TestCase):
    """Disable the helper and the assertions above must fail.

    Without this, a refactor that drops the call site leaves the suite green --
    the exact situation that let the p0029 page publish a note on an undeclared
    staff through a full 247-staff run.
    """

    def test_without_the_helper_the_staff_is_undeclared(self):
        original = emit._declare_every_used_staff
        emit._declare_every_used_staff = lambda page: None
        try:
            part = part_xml(emit_page(page_with([1, 2], {1: G})))
        finally:
            emit._declare_every_used_staff = original
        self.assertNotIn("<staves>", part)
        self.assertIn("<staff>2</staff>", part,
                      "the note still lands on staff 2 -- only the declaration is gone")


if __name__ == "__main__":
    unittest.main()
