"""The merge that keeps a singer's line out of the piano.

The bug this exists to prevent: homr reads a whole three-staff system, finds the
three staves, and then MERGES them into one one-staff part -- in its own words,
"Found 2 connected staffs (after merging grand staffs, multiple voices): [1, 1]"
-- so on p0035 system 1 the singer's 19 notes arrive as piano music inside a
99-note part. Nothing is lost, which is exactly why it went unnoticed: the page
still carries every note, in the wrong part.

The repair is to read each braced group on its own and merge the transcriptions
back (see :mod:`scorehalo.fragments`). What these tests hold still is what the
rest of the pipeline leans on: parts in the order the page prints them, every
note and bar arriving, ids that stay unique (a renumbered part must take its
instruments with it), and a merged file the validator accepts.

Run: .venv/bin/python -m unittest discover -s tests -p 'test_fragments.py'
"""

import os
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pysrc"))

from scorehalo import fragments  # noqa: E402
from scorehalo import validate  # noqa: E402
from scorehalo.emit import parse_part  # noqa: E402

FRAGMENT = """<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<score-partwise version="4.0">
  <work>
    <work-title />
  </work>
  <identification>
    <encoding>
      <software>homr</software>
    </encoding>
  </identification>
  <defaults />
  <part-list>
    <score-part id="P1">
      <part-name>{name}</part-name>
      <score-instrument id="P1-I1">
        <instrument-name>{name}</instrument-name>
        <instrument-sound>{sound}</instrument-sound>
      </score-instrument>
      <midi-instrument id="P1-I1">
        <midi-channel>1</midi-channel>
        <midi-program>{program}</midi-program>
      </midi-instrument>
    </score-part>
  </part-list>
  <part id="P1">
{body}
  </part>
</score-partwise>
"""


def _clefs(staves):
    """One clef per staff, tagged with the staff it belongs to."""
    out = []
    for staff in range(1, staves + 1):
        sign, line = ("G", 2) if staff == 1 else ("F", 4)
        out.append('<clef number="%d"><sign>%s</sign><line>%d</line></clef>'
                   % (staff, sign, line))
    return "".join(out)


def _measure(number, staves):
    """One bar: the attributes on the first, then a note per staff."""
    parts = []
    if number == 1:
        parts.append("<attributes><divisions>4</divisions>"
                     "<time><beats>4</beats><beat-type>4</beat-type></time>"
                     + ("<staves>%d</staves>" % staves if staves > 1 else "")
                     + _clefs(staves) + "</attributes>")
    for staff in range(1, staves + 1):
        if staff > 1:
            parts.append("<backup><duration>4</duration></backup>")
        parts.append("<note><pitch><step>E</step><octave>4</octave></pitch>"
                     "<duration>4</duration><voice>1</voice><type>quarter</type>"
                     '<staff>%d</staff></note>' % staff)
    return '<measure number="%d">%s</measure>' % (number, "".join(parts))


def fragment(name, staves=1, measures=3, sound="piano", program=1):
    """A homr fragment: one part, ``staves`` staves, one note per staff per bar."""
    body = "\n".join("    " + _measure(n, staves)
                     for n in range(1, measures + 1))
    return FRAGMENT.format(name=name, body=body, sound=sound, program=program)


class MergeFragmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, text):
        path = os.path.join(self.dir, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def read_bytes(self, path):
        with open(path, "rb") as fh:
            return fh.read()

    def out_path(self, name="merged.xml"):
        return os.path.join(self.dir, name)

    # -- the shape the grid depends on ------------------------------------

    def test_parts_come_back_in_the_order_the_page_prints_them(self):
        voice = self.write("a.xml", fragment("Voice", sound="voice", program=54))
        piano = self.write("b.xml", fragment("Piano", staves=2))
        out = self.out_path()

        self.assertEqual(fragments.merge_fragments([voice, piano], out), 2)

        parts = parse_part(out, staff_index=1, system=1)
        self.assertEqual([p.name for p in parts], ["Voice", "Piano"])
        self.assertEqual(parts[0].clef_by_staff, {1: ("G", 2)})
        # Both clefs, each on the staff it belongs to: a merge that lost the
        # bass clef would hand the grid a piano of two treble staves.
        self.assertEqual(parts[1].clef_by_staff, {1: ("G", 2), 2: ("F", 4)})

    def test_the_second_fragment_is_renumbered_with_its_instruments(self):
        voice = self.write("a.xml", fragment("Voice"))
        piano = self.write("b.xml", fragment("Piano", staves=2))
        out = self.out_path()

        fragments.merge_fragments([voice, piano], out)

        root = ET.parse(out).getroot()
        self.assertEqual([sp.get("id") for sp in
                          root.findall("./part-list/score-part")], ["P1", "P2"])
        self.assertEqual([p.get("id") for p in root.findall("part")],
                         ["P1", "P2"])
        # Each part's instrument declarations follow the part's own id.
        for pid in ("P1", "P2"):
            decl = [sp for sp in root.findall("./part-list/score-part")
                    if sp.get("id") == pid][0]
            self.assertEqual(sorted(el.get("id") for el in decl.iter()
                                    if el.get("id")),
                             [pid, pid + "-I1", pid + "-I1"])

    def test_every_note_and_measure_arrives(self):
        voice = self.write("a.xml", fragment("Voice", measures=3))
        piano = self.write("b.xml", fragment("Piano", staves=2, measures=3))
        out = self.out_path()

        fragments.merge_fragments([voice, piano], out)

        counts = [(len(p.findall("measure")), len(p.findall(".//note")))
                  for p in ET.parse(out).getroot().findall("part")]
        self.assertEqual(counts, [(3, 3), (3, 6)])

    def test_the_merged_file_is_valid_musicxml(self):
        voice = self.write("a.xml", fragment("Voice"))
        piano = self.write("b.xml", fragment("Piano", staves=2))
        out = self.out_path()

        fragments.merge_fragments([voice, piano], out)

        ok, report = validate.validate(out)
        self.assertTrue(ok, report)
        self.assertEqual(report["parts"], 2)
        self.assertEqual(report["notes"], 9)

    def test_bars_keep_their_own_numbering(self):
        # Systems are read independently, so a group's bars are numbered from 1;
        # the merge must not renumber them into one another.
        voice = self.write("a.xml", fragment("Voice", measures=2))
        piano = self.write("b.xml", fragment("Piano", staves=2, measures=4))
        out = self.out_path()

        fragments.merge_fragments([voice, piano], out)

        numbered = [[m.get("number") for m in p.findall("measure")]
                    for p in ET.parse(out).getroot().findall("part")]
        self.assertEqual(numbered, [["1", "2"], ["1", "2", "3", "4"]])

    # -- the cases that must not surprise anyone --------------------------

    def test_a_single_fragment_is_copied_verbatim(self):
        # There is nothing to merge, and the recogniser's own file is the better
        # artifact to keep.
        voice = self.write("a.xml", fragment("Voice"))

        fragments.merge_fragments([voice], self.out_path("merged.xml"))

        self.assertEqual(self.read_bytes(self.out_path()),
                         self.read_bytes(voice))

    def test_merging_a_fragment_onto_itself_changes_nothing(self):
        # The common case in the pipeline: one brace group, already written
        # where the rest of the pipeline looks for it.
        voice = self.write("a.xml", fragment("Voice"))
        before = self.read_bytes(voice)

        self.assertEqual(fragments.merge_fragments([voice], voice), 1)

        self.assertEqual(self.read_bytes(voice), before)

    def test_nothing_to_merge_is_an_error(self):
        with self.assertRaises(ValueError):
            fragments.merge_fragments([], self.out_path())
        with self.assertRaises(ValueError):
            fragments.merge_fragments([None, ""], self.out_path())

    def test_a_fragment_with_no_part_list_still_merges(self):
        # Not something homr writes, but a fragment missing its <part-list>
        # must not take the whole page down with it.
        bare = self.write("bare.xml", "<score-partwise version=\"4.0\">"
                                     "<part id=\"P1\">" + _measure(1, 2)
                                     + "</part></score-partwise>")
        voice = self.write("a.xml", fragment("Voice"))
        out = self.out_path()

        self.assertEqual(fragments.merge_fragments([bare, voice], out), 2)

        parts = ET.parse(out).getroot().findall("part")
        self.assertEqual([p.get("id") for p in parts], ["P1", "P2"])
        self.assertTrue(validate.validate(out)[0])


if __name__ == "__main__":
    unittest.main()
