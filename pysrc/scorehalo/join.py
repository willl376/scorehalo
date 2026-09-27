"""Join per-page MusicXML files into one score.

homr writes each detected staff region as its own part:
  - the main region is part P1: a two-staff grand staff (treble on staff 1,
    bass/piano LH on staff 2) carrying the bulk of the music;
  - when homr splits a page, a second bass region becomes P2: single-staff,
    same measure count, running PARALLEL to P1; P3+ appear on rare dense
    pages the same way.

Parallel regions are the SAME music time, so naive per-part joining breaks
(MusicXML forces all parts to share one measure grid; rest-filling absent
parts draws whole empty staves, reflowing dense pages). Instead we
canonicalize every page to ONE part with TWO staves:

    - P1's measures become the target measures; their staff-1/staff-2 split
      is preserved as homr tagged it
    - every additional part's notes are merged into the SAME measures forced
      onto staff 2 (the bass staff), with voice numbers remapped into a free
      band (10,20,30...) so nothing collides with P1's voices or the other
      regions
    - nothing is dropped

Page size: MusicXML page-layout is in tenths-of-a-millimetre and MuseScore
rescales arbitrary values, so we emit homr's EMPTY <defaults/> and rely on
MuseScore's own layout. Page boundaries come from <print new-page="yes"/>
placed as the FIRST child of the first measure of each page.
"""

import zipfile
import xml.etree.ElementTree as ET


def _local(tag):
    return tag.split("}")[-1]


def _voice_number(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _remap_voices(part, offset):
    remap = {}

    def newv(v):
        n = _voice_number(v)
        if n not in remap:
            remap[n] = n + offset
        return remap[n]

    for voice in part.iter("voice"):
        if voice.text is None:
            continue
        voice.text = str(newv(_voice_number(voice.text)))
    return remap


def _force_staff(notes, staff_no):
    """Tag every note with <staff>n</staff>, replacing any existing tag."""
    for el in notes:
        if _local(el.tag) != "note":
            continue
        st = el.find("staff")
        if st is None:
            st = ET.SubElement(el, "staff")
        st.text = str(staff_no)


def _strip_direction(notes):
    """Drop <backup>/<forward> (and stray <direction>) from copied notes.

    They are relative to the source region's own stream and would corrupt the
    destination measure's cursor in MuseScore's importer.
    """
    for el in notes:
        if _local(el.tag) != "note":
            continue
        for sub in list(el):
            t = _local(sub.tag)
            if t in ("backup", "forward", "direction"):
                el.remove(sub)


def _split_parall(part):
    """Yield non-print children; the print/barline/direction stay behind."""

    def clean():
        for child in part:
            t = _local(child.tag)
            yield t, child

    return list(clean())


def _merge_regions(page_measures, extras):
    """Merge extra parallel regions into the same-numbered measures.

    Every extra region is a bass/overflow staff: its notes go to staff 2, its
    attributes contribute a bass clef, voices already remapped by caller.
    Regions run in lockstep with the base part, so measures are matched by
    ordinal position within the page, not by measure number.
    """
    for extra in extras:
        for i, m in enumerate(extra.findall("measure")):
            target = page_measures[i] if i < len(page_measures) else None
            if target is None:
                continue
            notes = [c for c in m if _local(c.tag) == "note"]
            if not notes:
                continue
            _force_staff(notes, 2)
            _strip_direction(notes)
            # group by voice; homr wrote each voice contiguously with backups
            # relative to its own stream, so re-emit clean groups separated by
            # a single full-measure backup to reset the cursor.
            by_voice = {}
            for n in notes:
                v_note = n.find("voice")
                vid = v_note.text if v_note is not None and v_note.text else "1"
                by_voice.setdefault(vid, []).append(n)

            def group_fill(group):
                # sum durations of leading (non-chord) events only
                fill = 0
                for n in group:
                    if n.find("chord") is not None:
                        continue
                    d = n.find("duration")
                    fill += int(d.text or 0) if d is not None else 0
                return fill

            # region notes must begin at tick 0 of the measure; the source
            # baseline content may leave the cursor mid-measure, so reset it
            # by the fill of the last existing voice group.
            end_cursor = 0
            target_groups = {}
            for n in target.findall("note"):
                if n.find("chord") is not None:
                    continue
                v_n = n.find("voice")
                vid = v_n.text if v_n is not None and v_n.text else "1"
                target_groups.setdefault(vid, 0)
                d = n.find("duration")
                target_groups[vid] += int(d.text or 0) if d is not None else 0
            if target_groups:
                end_cursor = max(target_groups.values())

            prev_fill = 0
            for vid in sorted(by_voice, key=lambda x: int(x) if x.isdigit() else 0):
                group = by_voice[vid]
                if not prev_fill:
                    if end_cursor:
                        b = ET.SubElement(target, "backup")
                        ET.SubElement(b, "duration").text = str(end_cursor)
                else:
                    b = ET.SubElement(target, "backup")
                    ET.SubElement(b, "duration").text = str(prev_fill)
                prev_fill = group_fill(group)
                for n in group:
                    target.append(n)
            amp = target.find("attributes")
            if amp is None:
                amp = ET.Element("attributes")
                target.insert(0, amp)
            # sprinkle a staves=2 (grand staff) declaration if absent
            if amp.find("staves") is None:
                st = ET.SubElement(amp, "staves")
                st.text = "2"
            # ensure staff 2 carries a bass clef from the region's own attrs
            has_bass = any(
                _local(c.tag) == "clef"
                and c.find("sign") is not None
                and c.find("sign").text == "F"
                for c in amp
            )
            if not has_bass:
                src_clefs = m.findall("attributes/clef")
                use = next(
                    (c for c in src_clefs if c.find("sign") is not None and c.find("sign").text == "F"),
                    None,
                )
                cloned = ET.fromstring(ET.tostring(use)) if use is not None else None
                if cloned is None and not any(_local(c.tag) == "clef" for c in amp):
                    cloned = ET.Element("clef")
                    ET.SubElement(cloned, "sign").text = "F"
                    ET.SubElement(cloned, "line").text = "4"
                if cloned is not None:
                    cloned.set("number", "2")
                    # keep child order: put clef where homr usually has it
                    amp.append(cloned)
            # renumber duplicates so MuseScore accepts a staves=2 part
            for idx, clef_el in enumerate(
                [c for c in amp if _local(c.tag) == "clef"]
            ):
                clef_el.set("number", str(idx + 1))


def join_pages(page_xmls, out_xml, out_mxl=None, page_w=None, page_h=None):
    roots = [ET.parse(p).getroot() for p in page_xmls]
    if not roots:
        raise ValueError("no pages to join")

    first = roots[0]
    work = first.find("work")
    enc = first.find(".//identification/encoding")
    name = None
    sp0 = first.find(".//part")
    if sp0 is not None:
        pn = sp0.find("part-name")
        name = pn.text if pn is not None else None

    new_root = ET.Element("score-partwise", {"version": "4.0"})
    if work is not None:
        new_root.append(work)
    if enc is not None:
        ident = ET.SubElement(new_root, "identification")
        ident.append(enc)
    ET.SubElement(new_root, "defaults")
    part_list = ET.SubElement(new_root, "part-list")
    score_part = ET.SubElement(part_list, "score-part", {"id": "P1"})
    pname = ET.SubElement(score_part, "part-name")
    pname.text = name or "Part 1"

    merged = ET.SubElement(new_root, "part", {"id": "P1"})
    counter = 0

    for page_no, root in enumerate(roots):
        parts = root.findall(".//part")
        base = parts[0] if parts else None
        if base is None:
            continue
        extras = parts[1:]
        for extra in extras:
            _remap_voices(extra, 10)
        page_start = True
        page_measures = []
        for m in base.findall("measure"):
            counter += 1
            m2 = ET.SubElement(merged, "measure", {"number": str(counter)})
            if page_start and page_no > 0:
                ET.SubElement(m2, "print", {"new-page": "yes"})
            page_start = False
            page_measures.append(m2)
            for child in m:
                t = _local(child.tag)
                if t == "print":
                    # homr's new-system breaks fit an isolated page; in a
                    # joined continuum they over-fragment MuseScore's
                    # layout, so drop them. Keep only our page breaks.
                    if child.get("new-system") == "yes":
                        continue
                    m2.append(child)
                    continue
                m2.append(child)
        # merge extra parallel regions into the same measures
        if extras:
            _merge_regions(page_measures, extras)

    ET.indent(new_root, space="  ")
    ET.ElementTree(new_root).write(out_xml, encoding="UTF-8", xml_declaration=True)

    if out_mxl:
        _write_mxl(out_xml, out_mxl)
    return out_xml


def _write_mxl(xml_path, mxl_path):
    """Minimal valid .mxl (MusicXML compressed): container + content types."""
    name = xml_path.rpartition("/")[2]
    with zipfile.ZipFile(mxl_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(xml_path, name)
        z.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" '
            'version="1.0">\n  <rootfiles>\n'
            f'    <rootfile full-path="{name}" media-type="application/vnd.recordare.musicxml+xml"/>\n'
            "  </rootfiles>\n</container>\n",
        )
        z.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
            '  <Default Extension="xml" ContentType="application/vnd.recordare.musicxml+xml"/>\n'
            "  <Override PartName=\"/\"/>\n</Types>\n",
        )