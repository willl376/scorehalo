"""Join per-page MusicXML files into one score (part-order preserved).

KISS join strategy:
  - parts are matched by position (index) across pages
  - measures are appended sequentially per part, renumbered globally
  - each page boundary becomes a <print new-page="yes"/> so engraving
    roughly preserves the original pagination
  - only the first page contributes <work>/part-list metadata
"""

import os
import zipfile
import xml.etree.ElementTree as ET


def _partwise(page_roots):
    """Yield roots that are score-partwise (score-timewise is handled by
    wrapping). Returns (roots, was_timewise)."""
    t = page_roots[0].tag.split("}")[-1]
    return page_roots, (t == "score-timewise")


def join_pages(page_xmls, out_xml, out_mxl=None):
    roots = [ET.parse(p).getroot() for p in page_xmls]
    if not roots:
        raise ValueError("no pages to join")

    first_enc = roots[0].find(".//identification/encoding")
    work = roots[0].find("work")

    # unified part names by index: first page that has that part wins
    part_names = []
    for r in roots:
        for idx, sp in enumerate(r.findall(".//part")):
            while len(part_names) <= idx:
                part_names.append(None)
            if part_names[idx] is None:
                pname = sp.find("part-name")
                part_names[idx] = pname.text if pname is not None else f"P{idx + 1}"

    # build new root
    new_root = ET.Element("score-partwise", {"version": "4.0"})
    if work is not None:
        new_root.append(work)
    ident = ET.SubElement(new_root, "identification")
    if first_enc is not None:
        ident.append(first_enc)
    part_list = ET.SubElement(new_root, "part-list")
    for idx, name in enumerate(part_names):
        sp = ET.SubElement(part_list, "score-part", {"id": f"P{idx + 1}"})
        pn = ET.SubElement(sp, "part-name")
        pn.text = name

    counters = [0] * len(part_names)
    for page_no, root in enumerate(roots):
        for idx, part in enumerate(root.findall(".//part")):
            new_part = new_root.find(f"./part[@id='P{idx + 1}']")
            if new_part is None:
                new_part = ET.SubElement(new_root, "part", {"id": f"P{idx + 1}"})
            for m in part.findall("measure"):
                m2 = ET.SubElement(new_part, "measure", {"number": str(counters[idx] + 1)})
                counters[idx] += 1
                if page_no > 0 and idx == 0:
                    pr = ET.SubElement(m2, "print", {"new-page": "yes"})
                for child in m:
                    if child.tag.split("}")[-1] == "print" and page_no > 0 and idx == 0:
                        continue  # drop stale per-page print from source
                    m2.append(child)

    ET.indent(new_root, space="  ")
    tree = ET.ElementTree(new_root)
    tree.write(out_xml, encoding="UTF-8", xml_declaration=True)

    if out_mxl:
        _write_mxl(out_xml, out_mxl)
    return out_xml


def _write_mxl(xml_path, mxl_path):
    """Minimal valid .mxl (MusicXML compressed): container + content types."""
    name = os.path.basename(xml_path)
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
            f'  <Default Extension="xml" ContentType="application/vnd.recordare.musicxml+xml"/>\n'
            "  <Override PartName=\"/\"/>\n</Types>\n",
        )