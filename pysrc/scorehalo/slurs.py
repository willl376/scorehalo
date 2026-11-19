"""Conservative repair of unbalanced MusicXML slurs.

A slur is identified by ``(part, staff, voice, number)``. Inside one such
stream, starts and stops form properly nested pairs (LIFO). This module pairs
them, drops orphans, and records every drop in a JSON report. It never invents
a start or stop: a dangling mark is a defect in the source notation, and adding
one would be an unverifiable musical claim.

Source slur numbers are deliberately preserved. In the 2026-11-19 band A/B,
renumbering otherwise identical pairs made MuseScore 3.6.2 segfault on
``close-to-you`` and ``for-all-we-know``; preserving the original ``number``
attribute paired the same slurs with rc=0 on all six songs. Do not reintroduce
renumbering here without a fresh A/B.

This is an opt-in post-processing step. It changes only ``<slur>`` elements and
empty ``<notations>`` wrappers; note pitches, durations, voices, staves and
backup/forward structure are left byte-identical and verified by a fingerprint.
"""

from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _children(el: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in el if _local(child.tag) == name]


def _text(el: ET.Element, name: str, default: str) -> str:
    for child in el:
        if _local(child.tag) == name:
            return (child.text or "").strip() or default
    return default


def _note_fingerprint(root: ET.Element) -> str:
    """Hash every note with ``<slur>`` removed -- proves music is untouched."""
    clone = ET.fromstring(ET.tostring(root, encoding="unicode"))
    for note in clone.iter():
        if _local(note.tag) != "note":
            continue
        for notations in list(note):
            if _local(notations.tag) != "notations":
                continue
            for slur in list(notations):
                if _local(slur.tag) == "slur":
                    notations.remove(slur)
            # The repair also removes the now-empty wrapper; ignoring it here
            # keeps the fingerprint about notes, not formatting. Non-empty
            # notations (dynamics, articulations, ...) are still covered.
            if len(notations) == 0 and not (notations.text or "").strip():
                note.remove(notations)
    return hashlib.sha256(ET.tostring(clone, encoding="unicode").encode()).hexdigest()


def _register_root_namespace(root: ET.Element) -> None:
    if root.tag.startswith("{"):
        uri = root.tag[1:].split("}", 1)[0]
        ET.register_namespace("", uri)


def _doctype(text: str) -> str | None:
    for line in text.splitlines():
        if line.startswith("<!DOCTYPE"):
            return line
    return None


def _pair_slurs(root: ET.Element) -> dict[str, Any]:
    parents = {child: parent for parent in root.iter() for child in parent}
    streams: dict[tuple[str, str, str, str], list[ET.Element]] = {}
    info: dict[ET.Element, dict[str, Any]] = {}
    all_slurs: list[ET.Element] = []

    for part in root.iter():
        if _local(part.tag) != "part":
            continue
        part_id = part.get("id") or ""
        for measure_no, measure in enumerate(_children(part, "measure"), 1):
            for note_no, note in enumerate(_children(measure, "note"), 1):
                notations = None
                for child in note:
                    if _local(child.tag) == "notations":
                        notations = child
                        break
                if notations is None:
                    continue
                staff = _text(note, "staff", "1")
                voice = _text(note, "voice", "1")
                for slur in _children(notations, "slur"):
                    kind = slur.get("type")
                    if kind not in ("start", "stop"):
                        continue
                    number = slur.get("number") or "1"
                    key = (part_id, staff, voice, number)
                    streams.setdefault(key, []).append(slur)
                    info[slur] = {
                        "part": part_id,
                        "staff": staff,
                        "voice": voice,
                        "number": number,
                        "measure": measure.get("number") or str(measure_no),
                        "note": note_no,
                        "type": kind,
                    }
                    all_slurs.append(slur)

    pairs: list[dict[str, Any]] = []
    orphans: list[dict[str, Any]] = []
    matched: set[ET.Element] = set()

    for key, slurs in streams.items():
        stack: list[ET.Element] = []
        for slur in slurs:
            detail = info[slur]
            if detail["type"] == "start":
                stack.append(slur)
                continue
            if not stack:
                orphans.append({**detail, "reason": "stop_without_start"})
                continue
            start = stack.pop()
            matched.add(start)
            matched.add(slur)
            pairs.append({
                "part": key[0],
                "staff": key[1],
                "voice": key[2],
                "number": key[3],
                "start": {k: info[start][k] for k in ("part", "staff", "voice", "number", "measure", "note")},
                "stop": {k: detail[k] for k in ("part", "staff", "voice", "number", "measure", "note")},
            })
        for slur in stack:
            orphans.append({**info[slur], "reason": "start_without_stop"})

    for slur in all_slurs:
        if slur in matched:
            continue
        parent = parents.get(slur)
        if parent is not None:
            parent.remove(slur)

    for parent in list(parents):
        if (_local(parent.tag) == "notations" and len(parent) == 0
                and not (parent.text or "").strip()):
            grand = parents.get(parent)
            if grand is not None:
                grand.remove(parent)

    starts_before = sum(1 for s in all_slurs if info[s]["type"] == "start")
    stops_before = sum(1 for s in all_slurs if info[s]["type"] == "stop")
    return {
        "starts_before": starts_before,
        "stops_before": stops_before,
        "starts_after": len(pairs),
        "stops_after": len(pairs),
        "pairs": len(pairs),
        "orphans": len(orphans),
        "pair_detail": pairs,
        "orphan_detail": orphans,
    }


def repair_file(path: str | Path, out: str | Path,
                report: str | Path | None = None) -> dict[str, Any]:
    """Pair slurs in ``path``, write repaired XML to ``out``, return a report."""
    path = Path(path)
    out = Path(out)
    text = path.read_text(errors="replace")
    root = ET.fromstring(text)
    before = _note_fingerprint(root)
    result = _pair_slurs(root)

    _register_root_namespace(root)
    doctype = _doctype(text)
    body = ET.tostring(root, encoding="unicode")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text((doctype + "\n" if doctype else "") + body + "\n")

    after = _note_fingerprint(ET.parse(out).getroot())
    result.update({
        "input": str(path),
        "output": str(out),
        "note_hash_before": before,
        "note_hash_after": after,
        "notes_unchanged": before == after,
        "policy": "pair_per_part_staff_voice_number; preserve_numbers; drop_orphans",
    })
    if report is not None:
        report = Path(report)
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(result, indent=2) + "\n")
    return result


def summary(result: dict[str, Any]) -> dict[str, Any]:
    """Compact report for logs/manifests (full pair detail stays in the file)."""
    return {k: v for k, v in result.items()
            if k not in ("pair_detail", "orphan_detail")}
