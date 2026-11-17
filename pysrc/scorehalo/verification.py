"""Read-only timing diagnostics. No source-image accuracy claim or auto-repair.
Unsupported meter forms are reported as not assessable. Underfull measures are
not errors: pickups, cadenzas and split measures require source review.
"""
import xml.etree.ElementTree as ET
from fractions import Fraction
from .clefs import audit_clefs

def local(tag):
    return tag.rsplit("}", 1)[-1]

def children(el, name):
    return [c for c in el if local(c.tag) == name]

def child(el, name):
    return next(iter(children(el, name)), None)

def text(el, name, default=None):
    c = child(el, name)
    return (c.text or "").strip() if c is not None else default

def verify_musicxml(xml):
    findings, events = [], []
    def flag(code, message, part=None, measure=None, severity="warning"):
        findings.append(dict(code=code, message=message, part=part,
                             measure=measure, severity=severity))
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        flag("xml_parse", str(exc), severity="error")
        return dict(source_review="not_performed", findings=findings, events=events)
    for part in children(root, "part"):
        pid = part.get("id")
        divisions, meter, staves = None, None, None
        for ordinal, measure in enumerate(children(part, "measure"), 1):
            number = measure.get("number", str(ordinal))
            cursor, extent, previous_onset = Fraction(0), Fraction(0), None
            has_timed = False
            for element in measure:
                kind = local(element.tag)
                if kind == "attributes":
                    value = text(element, "divisions")
                    if value is not None:
                        try:
                            divisions = Fraction(value)
                            if divisions <= 0: raise ValueError("nonpositive divisions")
                        except (ValueError, ZeroDivisionError):
                            divisions = None
                            flag("divisions", "Invalid divisions", pid, number)
                    value = text(element, "staves")
                    if value is not None:
                        try: staves = int(value)
                        except ValueError: flag("staves", "Invalid staves", pid, number)
                    times = children(element, "time")
                    if times:
                        meter = None
                        try:
                            if len(times) != 1 or times[0].get("number"):
                                raise ValueError("staff-specific meter")
                            beats, units = children(times[0], "beats"), children(times[0], "beat-type")
                            if not beats or len(beats) != len(units): raise ValueError("unsupported meter")
                            meter = sum((sum(Fraction(b) for b in (a.text or "").split("+")) * 4 /
                                         Fraction(u.text) for a, u in zip(beats, units)), Fraction(0))
                            if meter <= 0: raise ValueError("nonpositive meter")
                        except (ValueError, TypeError, ZeroDivisionError):
                            meter = None
                            flag("meter_unassessed", "Meter form not assessed", pid, number, "info")
                    continue
                if kind not in {"note", "backup", "forward"}: continue
                if kind == "note" and child(element, "grace") is not None: continue
                value = text(element, "duration")
                try:
                    if divisions is None: raise ValueError("missing divisions")
                    duration = Fraction(value) / divisions
                    if duration < 0: raise ValueError("negative duration")
                except (ValueError, TypeError, ZeroDivisionError):
                    flag("duration_unassessed", "Missing/invalid duration or divisions", pid, number)
                    continue
                if kind == "backup":
                    cursor -= duration
                    previous_onset = None
                    if cursor < 0: flag("negative_cursor", "Backup precedes measure start", pid, number)
                elif kind == "forward":
                    cursor += duration
                    previous_onset = None
                    extent = max(extent, cursor)
                else:
                    has_timed = True
                    chord = child(element, "chord") is not None
                    onset = previous_onset if chord and previous_onset is not None else cursor
                    if chord and previous_onset is None:
                        flag("orphan_chord", "Chord note without preceding onset", pid, number)
                    if not chord:
                        previous_onset = onset
                        cursor += duration
                    extent = max(extent, onset + duration)
                    staff = text(element, "staff", "1")
                    try:
                        if int(staff) < 1 or (staves is not None and int(staff) > staves):
                            flag("staff_reference", "Staff outside declared range", pid, number)
                    except ValueError: flag("staff_reference", "Invalid staff number", pid, number)
                    pitch = child(element, "pitch")
                    events.append(dict(part=pid, measure=number, measure_ordinal=ordinal,
                        onset=str(onset), duration=str(duration), staff=staff,
                        voice=text(element, "voice", "1"), rest=child(element, "rest") is not None,
                        pitch=None if pitch is None else dict(step=text(pitch,"step"),
                            alter=text(pitch,"alter","0"), octave=text(pitch,"octave"))))
            if meter is not None and extent > meter:
                flag("overfull_measure", f"Extent {extent} exceeds meter length {meter}; check source (may be intentional)", pid, number,
                     "info" if measure.get("implicit") == "yes" or measure.get("non-controlling") == "yes" else "warning")
            if not has_timed:
                flag("no_timed_notes", "No assessable non-grace notes/rests", pid, number, "info")
    clef_report = audit_clefs(xml)
    findings.extend(clef_report["findings"])
    return dict(source_review="not_performed", findings=findings, events=events, clefs=clef_report)


def summary(report: dict) -> dict:
    """Compact copy for manifests: findings and clef declarations, no per-note detail.

    The full report (with ``events`` and ``clefs.note_assignments``) belongs in
    ``verification.json``; embedding it in every manifest entry made a one-page
    manifest ~160 kB and a whole-book manifest tens of MB.
    """
    clefs = report.get("clefs") or {}
    return {
        "source_review": report.get("source_review", "not_performed"),
        "findings": report.get("findings", []),
        "clefs": {
            "declarations": clefs.get("declarations", []),
            "findings": clefs.get("findings", []),
            "declaration_count": len(clefs.get("declarations", [])),
            "note_assignment_count": len(clefs.get("note_assignments", [])),
        },
    }

