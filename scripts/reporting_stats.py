"""MusicXML structural counts, not accuracy or schema validation."""

from pathlib import Path
import xml.etree.ElementTree as ET


def xml_stats(path):
    if path is None or str(path) == "":
        return {"status": "missing", "why": "no output path supplied"}
    try:
        raw = Path(path).read_bytes()
    except FileNotFoundError:
        return {"status": "missing", "why": "output file does not exist"}
    except OSError as exc:
        return {"status": "unreadable", "why": str(exc)}

    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        return {"status": "invalid", "why": str(exc)}

    tag = root.tag
    namespace = tag[1:].split("}", 1)[0] if tag.startswith("{") else ""

    def q(name):
        return "{%s}%s" % (namespace, name) if namespace else name

    if tag != q("score-partwise"):
        return {
            "status": "unsupported",
            "why": "expected score-partwise MusicXML",
            "root": tag,
        }

    parts = root.findall(q("part"))
    if not parts:
        return {"status": "empty", "why": "no musical parts"}

    measures_by_part = [p.findall(q("measure")) for p in parts]
    if any(not measures for measures in measures_by_part):
        return {
            "status": "empty",
            "why": "one or more musical parts have no measures",
        }

    measures = [m for group in measures_by_part for m in group]
    notes = [n for m in measures for n in m.findall(q("note"))]
    pitched = sum(n.find(q("pitch")) is not None for n in notes)
    unpitched = sum(n.find(q("unpitched")) is not None for n in notes)
    rests = sum(n.find(q("rest")) is not None for n in notes)

    return {
        "status": "ok",
        "parts": len(parts),
        "part_measures": len(measures),
        "note_elements": len(notes),
        "pitched_notes": pitched,
        "unpitched_notes": unpitched,
        "rests": rests,
        "has_pitched_notes": pitched > 0,
    }
