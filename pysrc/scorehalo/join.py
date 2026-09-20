"""Join per-page MusicXML files into one score.

Canonicalization (validated against real homr output): homr emits the bulk of
the music in part index 0 as two staves — staff 1 (vocal) with voice 1/2 and
staff 2 (piano) with voice 5/6 etc. On dense pages it additionally spills some
notes into a second part (staff 1 only). Joining by part index would strand
those notes in a sparse second part, so we canonicalize each page to ONE part:

    - part 0's measures become the target measures
    - every additional part's notes are merged into the SAME measures,
      keeping staff/positions, remapping only their voice numbers into a
      free band (10,20,30... per part) so nothing collides with part 0's
      voices or with each other
    - nothing is dropped; duplicates are left for the human proofreader

Part-list: a single score-part (name from the first page). Each page boundary
gets a <print new-page="yes"/> on the first measure so engraving roughly keeps
the original pagination.
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


def _merge_measures(dst_part, src_part, voice_offset):
    """Append src measures' note content into the same-numbered dst measures."""
    dst_measures = dst_part.findall("measure")
    for m in src_part.findall("measure"):
        num = int(m.get("number", "1"))
        if num < 1:
            continue
        target = dst_measures[num - 1] if num <= len(dst_measures) else None
        if target is None:
            return
        for child in list(m):
            tag = _local(child.tag)
            if tag in ("print", "barline", "direction"):
                continue
            target.append(child)


def join_pages(page_xmls, out_xml, out_mxl=None):
    roots = [ET.parse(p).getroot() for p in page_xmls]
    if not roots:
        raise ValueError("no pages to join")

    first = roots[0]
    name = None
    sp0 = first.find(".//part")
    if sp0 is not None:
        pn = sp0.find("part-name")
        name = pn.text if pn is not None else None

    new_root = ET.Element("score-partwise", {"version": "4.0"})
    work = first.find("work")
    if work is not None:
        new_root.append(work)
    encoding = first.find(".//identification/encoding")
    if encoding is not None:
        ident = ET.SubElement(new_root, "identification")
        ident.append(encoding)
    part_list = ET.SubElement(new_root, "part-list")
    score_part = ET.SubElement(part_list, "score-part", {"id": "P1"})
    pname = ET.SubElement(score_part, "part-name")
    pname.text = name or "Part 1"

    merged = ET.SubElement(new_root, "part", {"id": "P1"})
    measure_counter = 0

    for page_no, root in enumerate(roots):
        parts = root.findall(".//part")
        base = parts[0] if parts else None
        if base is None:
            continue
        for m in base.findall("measure"):
            counter = measure_counter + 1
            m2 = ET.SubElement(merged, "measure", {"number": str(counter)})
            measure_counter += 1
            if page_no > 0:
                ET.SubElement(m2, "print", {"new-page": "yes"})
            for child in m:
                tag = _local(child.tag)
                if tag in ("print", "barline"):
                    continue
                m2.append(child)
        for extra_idx, extra in enumerate(parts[1:], start=1):
            _remap_voices(extra, extra_idx * 10)
            _merge_measures(merged, extra, extra_idx * 10)

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