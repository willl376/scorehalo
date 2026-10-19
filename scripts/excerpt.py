"""Build a playable excerpt of the joined score around a given measure range.

The naive version of this -- delete the measures you do not want -- produces a file
that musicxml2ly silently mangles into `s1*8` skips: the excerpt keeps `<staves>3`
while carrying notes for staff 1 only, and with the first measure gone the
`<attributes>` block (divisions, key, time, clefs, staves) goes with it. The result
renders to a near-silent MIDI and fails with no error, which is the worst kind of
bug.

So this clones the real `<attributes>` from the score's first measure into the first
retained measure, and renumbers so the range reads 1..N.
"""

import copy
import sys
import xml.etree.ElementTree as ET


def tag(e):
    return e.tag.split("}")[-1]


def build(src, dst, lo, hi):
    tree = ET.parse(src)
    root = tree.getroot()

    # The score's opening attributes are the template: divisions, key, time,
    # staves and a clef per staff. Copy it before deleting anything.
    first_attrs = None
    for part in root.iter("part"):
        for meas in part.iter("measure"):
            found = next((k for k in meas if tag(k) == "attributes"), None)
            if found is not None:
                first_attrs = copy.deepcopy(found)
            break
        break

    removed = 0
    for part in root.iter("part"):
        measures = list(part.iter("measure"))
        keep = []
        for meas in measures:
            try:
                num = int(meas.get("number"))
            except (TypeError, ValueError):
                keep.append(meas)
                continue
            if lo <= num <= hi:
                keep.append(meas)
            else:
                part.remove(meas)
                removed += 1
        if keep and first_attrs is not None:
            keep[0].insert(0, first_attrs)
        # Renumber so a score handed to a human starts at 1.
        for offset, meas in enumerate(keep, 1):
            meas.set("number", str(offset))

    ET.register_namespace("", "http://www.musicxml.org/xsd/partwise")
    tree.write(dst, encoding="UTF-8", xml_declaration=True)
    return removed


if __name__ == "__main__":
    src, dst, lo, hi = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
    gone = build(src, dst, lo, hi)

    root = ET.parse(dst).getroot()
    staves = {s.text for s in root.iter("staves")}
    used = sorted({s.text for s in root.iter("staff")})
    notes = sum(1 for _ in root.iter("note"))
    print(f"removed {gone} measures -> {dst}")
    print(f"  <staves> declared: {sorted(staves)}   note <staff> used: {used}")
    print(f"  notes: {notes}")
    for part in root.iter("part"):
        for meas in part.iter("measure"):
            n = sum(1 for k in meas if tag(k) == "note")
            staff = sorted({k.text for k in meas.iter("staff")})
            flag = "   <-- EMPTY" if not n else ""
            print(f"    m{meas.get('number'):>3}: {n:>3} notes  staff {staff}{flag}")