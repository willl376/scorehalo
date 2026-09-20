"""MusicXML output validation: must be parseable + carry notes.

Also flags the historical bug classes (unbound xlink/xsi prefixes,
missing part-list) so we never hand MuseScore a poisoned file.
"""

import xml.etree.ElementTree as ET

BUGGY_PREFIXES = ("xsi:", "xlink:")


def validate(xml_path):
    """Return (ok, report-dict)."""
    report = {"path": xml_path, "ok": False, "errors": []}
    try:
        with open(xml_path, "rb") as fh:
            raw = fh.read().decode("utf-8", errors="replace")
    except OSError as e:
        report["errors"].append(f"read error: {e}")
        return False, report

    for pref in BUGGY_PREFIXES:
        if f"<{pref}" in raw:
            report["errors"].append(f"unbound prefix usage {pref!r}")

    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        report["errors"].append(f"not well-formed XML: {e}")
        return False, report

    tag = root.tag.split('}')[-1]
    report["root"] = tag
    if tag not in ("score-partwise", "score-timewise"):
        report["errors"].append(f"unexpected root {tag}")
        return False, report

    parts = root.findall(".//part")
    notes = root.findall(".//note")
    measures = root.findall(".//measure")
    report["parts"] = len(parts)
    report["notes"] = len(notes)
    report["measures"] = len(measures)
    if not parts:
        report["errors"].append("empty part-list (the '#201 missing part-list' class)")
    if notes == 0:
        report["errors"].append("zero notes: likely empty/garbage transcription")

    report["ok"] = not report["errors"]
    return report["ok"], report