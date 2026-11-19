"""MusicXML output validation: must be parseable + carry notes.

Also flags the historical bug classes (unbound xlink/xsi prefixes,
missing part-list) so we never hand MuseScore a poisoned file.

The schema checks exist because the engravers disagreed with this file. MuseScore
imported it without complaint while musicxml2ly died with a TypeError deep in
its tuplet grouper, and LilyPond reported unbalanced slurs -- yet a parseable,
non-empty file passed here as "valid". A converter crash is a verdict about our
output, so the specific things that made the converters fail are now checked
directly and cheaply, and a defect is caught before it costs a slow round trip.
"""

import collections
import xml.etree.ElementTree as ET

BUGGY_PREFIXES = ("xsi:", "xlink:")


def _schema_errors(root):
    """Required-children rules for the elements we actually emit."""
    errs = []

    if root.find("part-list") is None:
        errs.append("no <part-list> element")

    for part in root.findall("part"):
        if not part.findall("measure"):
            errs.append(f"part {part.get('id')!r} has no measures")
        for measure in part.findall("measure"):
            num = measure.get("number")
            for note in measure.findall("note"):
                if not (note.find("pitch") is not None
                        or note.find("rest") is not None
                        or note.find("unpitched") is not None):
                    errs.append(
                        f"m{num}: <note> has neither <pitch>, <rest> nor <unpitched>")
                tm = note.find("time-modification")
                if tm is not None and note.find("type") is None:
                    errs.append(
                        f"m{num}: tuplet note without a <type> "
                        "(the note type is a sibling of <time-modification>, "
                        "not a child; without it the printed value is ambiguous)")

    starts = stops = 0
    for slur in root.findall(".//slur"):
        kind = slur.get("type")
        if kind == "start":
            starts += 1
        elif kind == "stop":
            stops += 1
    if starts != stops:
        errs.append(f"unbalanced slurs: {starts} start vs {stops} stop")

    # A part id is the only link between <part-list> and the music. If the two
    # disagree, a consumer may silently drop a staff (or invent a part), so this
    # is a structural error, not a cosmetic warning. Duplicates are reported too.
    part_list = root.find("part-list")
    declared = ([sp.get("id") for sp in part_list.findall("score-part")]
                if part_list is not None else [])
    used = [p.get("id") for p in root.findall("part")]
    for ids, label in ((declared, "score-part"), (used, "part")):
        counts = collections.Counter(i for i in ids if i is not None)
        for pid, n in sorted(counts.items()):
            if n > 1:
                errs.append(f"duplicate {label} id {pid!r} (x{n})")
    if part_list is not None:
        for pid in used:
            if pid is not None and pid not in declared:
                errs.append(f"part id {pid!r} has no <score-part> declaration")
        for pid in declared:
            if pid is not None and pid not in used:
                errs.append(f"<score-part id={pid!r}> has no <part>")

    tally = collections.Counter(errs)
    return [f"{msg} (x{n})" if n > 1 else msg for msg, n in tally.items()]


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

    report["errors"].extend(_schema_errors(root))

    report["ok"] = not report["errors"]
    return report["ok"], report


def validate_clefs(text: str) -> list[dict]:
    """Read-only semantic clef/staff diagnostics, separate from structural acceptance.

    Reports clef declaration conflicts, unresolved assignments, invalid staff/line/sign,
    and unbalanced timing -- without repairing anything. Complements
    :func:`validate` which checks well-formedness and structure.
    """
    from .clefs import audit_clefs
    return audit_clefs(text)["findings"]