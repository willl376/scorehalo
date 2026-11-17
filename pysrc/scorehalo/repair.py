"""Repair homr's raw MusicXML into something a strict reader will accept.

A tuplet note's printed value is carried by a ``<type>`` that is a **sibling**
of ``<time-modification>`` on the ``<note>`` element.  The MusicXML 4.0 content
model for ``<time-modification>`` itself is only ``actual-notes``,
``normal-notes`` and optional ``normal-type``/``normal-dot`` -- there is no
``<type>`` or ``<dot>`` child.  An earlier version of this repair appended
those two elements *inside* ``<time-modification>``, producing a document that
MuseScore tolerated but a schema-aware reader was entitled to reject.

This rewrite does the schema-correct thing, in two steps:

1. strip any stray ``<type>``/``<dot>`` children out of every
   ``<time-modification>`` (so re-running heals files the old repair touched);
2. if the note has no ``<type>`` of its own, infer the printed value from the
   tuplet ratio and insert ``<type>``/``<dot>`` as note-level siblings in the
   right content-model position.

The inference is safe rather than guessed: only ratios actually observed in
our corpora are repaired, and anything else is left for validate() to report.
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

# Children that do NOT belong inside <time-modification>.  Kept as a set so a
# re-run removes the non-schema shape the old repair wrote.
_STRIP_FROM_TM = {"type", "dot"}

# Children of <note> in MusicXML content-model order.  Used only to place an
# inferred <type>/<dot> among siblings that predate them (voice, accidental,
# etc.); indices need not be exhaustive, just monotonic through type/dot.
_NOTE_ORDER = (
    "chord", "pitch", "unpitched", "rest", "duration", "tie", "instrument",
    "footnote", "level", "voice", "type", "dot", "accidental",
    "time-modification", "stem", "notehead", "notehead-text", "staff",
    "beam", "notations", "lyric",
)
_TYPE_RANK = _NOTE_ORDER.index("type")


def _insertion_index(note):
    """Index in ``note`` where an inferred ``<type>`` belongs."""
    for i, child in enumerate(note):
        rank = (_NOTE_ORDER.index(child.tag)
                if child.tag in _NOTE_ORDER else len(_NOTE_ORDER))
        if rank >= _TYPE_RANK:
            return i
    return len(note)


def repair_time_modifications(path):
    """Put every tuplet's ``<type>`` in the schema-correct place.

    Returns the number of notes changed (a note counts once whether it was
    healed, given a type, or both).
    """
    tree = ET.parse(path)
    root = tree.getroot()
    fixed = 0
    for note in root.iter("note"):
        tm = next((c for c in note if c.tag == "time-modification"), None)
        if tm is None:
            continue

        changed = False

        # 1. Heal the old non-schema shape: <type>/<dot> inside <tm>.
        for child in list(tm):
            if child.tag in _STRIP_FROM_TM:
                tm.remove(child)
                changed = True

        # 2. Make sure the note has its own <type> sibling.
        if note.find("type") is None:
            key = (tm.findtext("actual-notes"), tm.findtext("normal-notes"))
            if key in TUPLET_TYPE:
                name, dotted = TUPLET_TYPE[key]
                idx = _insertion_index(note)
                node = ET.Element("type")
                node.text = name
                note.insert(idx, node)
                changed = True
                if dotted and note.find("dot") is None:
                    note.insert(idx + 1, ET.Element("dot"))

        if changed:
            fixed += 1

    if fixed:
        tree.write(path, encoding="UTF-8", xml_declaration=True)
    return fixed
