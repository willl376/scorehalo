"""Put the several transcriptions of one printed system back into one file.

Why this exists
---------------
A printed system can carry more than one part: "We've Only Just Begun" prints a
singer above a braced piano. The pipeline used to hand homr a crop of the whole
system, and on a dense page homr finds the three staves and then MERGES them
into a single one-staff part -- in its own words:

    Found 3 staffs
    Found 2 connected staffs (after merging grand staffs, multiple voices):  [1, 1]

Every note survives that merge and the singer's line quietly becomes piano
music. Measured on p0035 system 1: the merged read gives one part of 99 notes,
while the two braced groups read separately give 19 notes of "Voice" and 80 of
"Piano" -- the same 99, which is how we know the notes were folded rather than
lost.

homr reads a lone staff and a braced pair reliably, so the system is cropped at
its braces, the printed boundary between instruments, and each group is
transcribed on its own (see ``scripts/per_system_all.py``). This module is the
other half of that: it merges the fragments back into the single file the rest
of the pipeline reads -- one file per system, parts in reading order, the top
group first, so the grid sees the singer where the page prints them.

The merge is textual on purpose. Notes, bar numbers, attributes and part names
are the recogniser's, untouched; only the part ids are renumbered, because every
fragment calls its part ``P1`` and MusicXML requires ids to be unique within one
score.
"""

import copy
import os
import shutil
import xml.etree.ElementTree as ET

#: Header elements copied from the first fragment. ``<defaults/>`` is homr's
#: empty page-setup, which tells MuseScore to lay the page out itself.
HEADER_TAGS = ("work", "identification", "defaults")


def merge_fragments(paths, out_path):
    """Write one score holding every part of ``paths``, in the order given.

    ``paths`` are the fragments of ONE printed system, top group first, so the
    merged part order is the page's reading order.

    A single fragment is copied rather than rebuilt: there is nothing to merge
    and the recogniser's own file is the better artifact to keep. If it is also
    ``out_path`` (the common case -- one group, already written where the
    pipeline looks for it) this is a no-op.

    Returns the number of parts in the merged score.
    """
    paths = [p for p in paths if p]
    if not paths:
        raise ValueError("merge_fragments: nothing to merge")
    if len(paths) == 1:
        if os.path.abspath(paths[0]) != os.path.abspath(out_path):
            shutil.copyfile(paths[0], out_path)
        return len(ET.parse(out_path).getroot().findall("part"))

    roots = [ET.parse(path).getroot() for path in paths]
    score = ET.Element("score-partwise",
                       {"version": roots[0].get("version", "4.0")})
    for tag in HEADER_TAGS:
        el = roots[0].find(tag)
        if el is not None:
            score.append(copy.deepcopy(el))

    part_list = ET.SubElement(score, "part-list")
    number = 0
    for root in roots:
        declared = {sp.get("id"): sp
                    for sp in root.findall("./part-list/score-part")}
        for body in root.findall("part"):
            number += 1
            new_id = "P%d" % number
            old_id = body.get("id") or new_id
            decl = declared.get(old_id)
            decl = copy.deepcopy(decl) if decl is not None else ET.Element("score-part")
            decl.set("id", new_id)
            _repoint(decl, old_id, new_id)
            part_list.append(decl)
            body = copy.deepcopy(body)
            _repoint(body, old_id, new_id)
            score.append(body)

    tree = ET.ElementTree(score)
    ET.indent(tree, space="  ")
    tree.write(out_path, encoding="UTF-8", xml_declaration=True)
    return number


def _repoint(element, old_id, new_id):
    """Rename every id reference to ``old_id`` inside ``element``.

    MusicXML ties a part to its instrument declarations by id
    (``<score-instrument id="P1-I1">``), so a renumbered part has to take its
    instruments with it: rewriting ``P1`` and leaving ``P1-I1`` behind would
    name an instrument that no longer exists.
    """
    if old_id == new_id:
        return
    for el in element.iter():
        for key, value in list(el.attrib.items()):
            if value == old_id:
                el.set(key, new_id)
            elif value.startswith(old_id + "-"):
                el.set(key, new_id + value[len(old_id):])
