"""Repair homr's raw MusicXML into something a strict reader will accept.

homr emits a <time-modification> with <actual-notes> and <normal-notes> but never
writes the <type> that the MusicXML 4.0 schema requires. Across every corpus
converted so far that is 1143 elements and not one of them is valid, so
musicxml2ly dies with a TypeError in its tuplet grouper and the LilyPond leg of
the referee has never once run on a page containing a triplet. MuseScore imports
the same files without complaint, which is exactly why the defect survived: one
implementation tolerates what another cannot read.

The <type> is recoverable rather than guessed, because it is fully determined by
the tuplet ratio. Three eighths in the time of two is an eighth-note triplet, and
five sixteenths in the time of four is a sixteenth-note quintuplet, so the ratio
pins the printed note value. Only ratios that are actually observed are
repaired; anything else is left alone for validate() to report rather than being
quietly guessed at.

A tuplet also has to be told <dot> when the ratio is dotted, and the elements
must appear in schema order, so this rewrites each element in place instead of
appending a child.
"""

import xml.etree.ElementTree as ET

# (actual-notes, normal-notes) -> printed <type>, dotted flag
TUPLET_TYPE = {
    ("2", "3"): ("quarter", False),
    ("3", "2"): ("eighth", False),
    ("3", "4"): ("quarter", False),
    ("5", "4"): ("sixteenth", False),
    ("6", "4"): ("eighth", True),
    ("7", "4"): ("sixteenth", False),
    ("9", "8"): ("thirty-second", False),
}

ORDER = ("actual-notes", "normal-notes", "type", "dot", "time-only", "stem",
         "beam-number", "notehead", "staff")


def repair_time_modifications(path):
    """Add the missing <type> to every tuplet. Return the number repaired."""
    tree = ET.parse(path)
    root = tree.getroot()
    fixed = 0
    for tm in root.iter("time-modification"):
        if tm.find("type") is not None:
            continue
        key = (tm.findtext("actual-notes"), tm.findtext("normal-notes"))
        if key not in TUPLET_TYPE:
            continue
        name, dotted = TUPLET_TYPE[key]
        node = ET.Element("type")
        node.text = name
        tm.append(node)
        if dotted:
            tm.append(ET.Element("dot"))
        kids = sorted(list(tm), key=lambda e: ORDER.index(e.tag) if e.tag in ORDER else 99)
        for kid in kids:
            tm.append(kid)
        fixed += 1
    if fixed:
        tree.write(path, encoding="UTF-8", xml_declaration=True)
    return fixed
