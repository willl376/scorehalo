"""Assemble per-staff MusicXML fragments into one score.

ScoreHalo recognises one staff at a time, so a page becomes N single-staff
MusicXML files that no notation program will open together. This module glues
them into one ``score-partwise`` document with one part per staff, which every
editor (MuseScore included) reads and plays.

Deliberately dumb: parts are laid end to end and MuseScore decides the rest.
It does not try to reconcile differing measure counts between systems -- that
is a human judgement call, and hiding it inside a guess would be worse than
leaving it visible.
"""

import copy
import xml.etree.ElementTree as ET

DOCTYPE = (
    '<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 4.0 Partwise//EN"'
    ' "http://www.musicxml.org/dtds/partwise.dtd">'
)


def _clean(part):
    """Drop measures that carry no music, and collapse repeated <attributes>.

    homr sometimes emits a leading measure holding only one or two
    ``<attributes>`` elements and no notes at all. On its own such a part
    imports; combined with any other part MuseScore rejects the whole file
    (silent rc=40). The measure encodes no music, so it is safe to discard --
    it is a recognition artifact, not a rest bar.
    """
    for measure in list(part.findall("measure")):
        if measure.find("note") is None:
            part.remove(measure)
            continue
        seen = False
        for el in list(measure):
            if el.tag == "attributes":
                if seen:
                    measure.remove(el)
                seen = True
    return part


def assemble(fragments, names=None, title=None):
    """Return a MusicXML document string from per-staff MusicXML file paths.

    ``fragments`` is an ordered sequence of paths; the order is reading order
    and is preserved in the output part-list. ``names`` optionally labels each
    part (defaults to "Staff N").
    """
    roots = [ET.parse(p).getroot() for p in fragments]
    out = ET.Element("score-partwise", {"version": "4.0"})

    if title:
        work = ET.SubElement(out, "work")
        ET.SubElement(work, "work-title").text = title

    part_list = ET.SubElement(out, "part-list")
    for i, root in enumerate(roots, start=1):
        entry = ET.SubElement(part_list, "score-part", {"id": f"P{i}"})
        name = (names[i - 1] if names and i - 1 < len(names) else f"Staff {i}")
        ET.SubElement(entry, "part-name").text = name

    for i, root in enumerate(roots, start=1):
        part = copy.deepcopy(root.find("part"))
        part.set("id", f"P{i}")
        _clean(part)
        for n, measure in enumerate(part.findall("measure"), start=1):
            measure.set("number", str(n))
        out.append(part)

    body = ET.tostring(out, encoding="unicode")
    return DOCTYPE + "\n" + body


def write(fragments, dest, names=None, title=None):
    """Assemble and write to ``dest``. Returns the number of parts written."""
    count = len(fragments)
    xml = assemble(fragments, names=names, title=title)
    with open(dest, "w", encoding="utf-8") as fh:
        fh.write(xml)
    return count
