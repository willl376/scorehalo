"""Label-free structural audit of converted pages.

Accuracy against the source needs a human label, but a lot of what goes wrong
downstream is detectable without one: broken measure arithmetic, colliding
voices, clefs that contradict the notes on the staff, and statistical
fingerprints of known misreadings. This module reports those per page and
ranks the suspicious spots so proofreading effort goes where it pays.

Checks:
  measure_arithmetic   per measure, does the notated time add up to a bar
  voice_collision      two notes at the same position in one voice
  clef_content         declared clef vs. where the notes actually sit
  outlier_pitch        notes implausibly far outside the staff
  dotted_eighth        adjacent dotted-eighth pairs (beamed-eighth misread)
  duplicate_adjacent   repeated identical notes back to back
  missing_context      no clef/key/time declared up front
"""

import glob
import os
from fractions import Fraction

from scorehalo.score import PITCHED, parse_document

STEP_ORDER = {"C": 0, "D": 1, "E": 2, "F": 3, "G": 4, "A": 5, "B": 6}

STAFF_RANGE = {
    "G": (("E", 4), ("F", 5)),
    "F": (("G", 2), ("A", 3)),
    "C": (("E", 4), ("F", 5)),
}

LEDGER_TOLERANCE = 4


def _diatonic(pitch):
    step, _alter, octave = pitch
    if step is None or octave is None:
        return None
    try:
        return int(octave) * 7 + STEP_ORDER[step.upper()]
    except (KeyError, ValueError):
        return None


def _clef_bounds(sign):
    span = STAFF_RANGE.get((sign or "G").upper())
    if not span:
        return None
    (low_step, low_oct), (high_step, high_oct) = span
    low = low_oct * 7 + STEP_ORDER[low_step]
    high = high_oct * 7 + STEP_ORDER[high_step]
    return low, high


def _fmt(pitch):
    if pitch is None:
        return "rest"
    step, alter, octave = pitch
    acc = "#" if alter == 1 else ("b" if alter == -1 else "")
    return f"{step}{acc}{octave}"


def audit_file(path):
    doc = parse_document(path)
    flags = []
    streams = doc["streams"]
    pitched_total = 0

    per_measure = {}
    for key, notes in streams.items():
        for measure_no, _onset, local, kind, pitch, dur, chord in notes:
            per_measure.setdefault((key, measure_no), []).append((local, kind, pitch, dur, chord))

    measure_totals = {}
    for (key, measure_no), items in sorted(per_measure.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        part, staff, voice = key
        total = sum(d for _l, _k, _p, d, chord in items if not chord)
        measure_totals.setdefault(total, 0)
        measure_totals[total] += 1
        pitched = sum(1 for _l, k, _p, _d, _c in items if k == PITCHED)
        pitched_total += pitched
        seen = {}
        for local, kind, pitch, dur, chord in items:
            if kind != PITCHED or chord:
                continue
            if local in seen:
                flags.append(
                    {
                        "check": "voice_collision",
                        "severity": 3,
                        "where": f"part {part} staff {staff} voice {voice} measure {measure_no}",
                        "detail": f"two notes at beat {local}: {_fmt(seen[local])} and {_fmt(pitch)}",
                    }
                )
            seen[local] = pitch

    if doc["time"]:
        expected = Fraction(doc["time"][0] * 4, doc["time"][1])
        modal = max(measure_totals.items(), key=lambda kv: kv[1])[0] if measure_totals else None
        if modal and abs(modal - expected) > Fraction(1, 16):
            flags.append(
                {
                    "check": "wrong_meter",
                    "severity": 3,
                    "where": "page (all parts)",
                    "detail": f"declares {doc['time'][0]}/{doc['time'][1]} "
                    f"({expected} quarters) but content is {modal} quarters per measure",
                }
            )
        for (key, measure_no), items in per_measure.items():
            part, staff, voice = key
            total = sum(d for _l, _k, _p, d, chord in items if not chord)
            if total and abs(total - expected) > Fraction(1, 16):
                flags.append(
                    {
                        "check": "broken_measure",
                        "severity": 3 if total > expected else 2,
                        "where": f"part {part} staff {staff} voice {voice} measure {measure_no}",
                        "detail": f"notated {total} quarters vs expected {expected} in "
                        f"{doc['time'][0]}/{doc['time'][1]}"
                        + (" (OVERFULL)" if total > expected else " (underfull)"),
                    }
                )
    else:
        modal = max(measure_totals.items(), key=lambda kv: kv[1])[0] if measure_totals else None
        flags.append(
            {
                "check": "missing_meter",
                "severity": 2,
                "where": "page (all parts)",
                "detail": f"no <time> declared; content is {modal} quarters per measure",
            }
        )
        for (key, measure_no), items in per_measure.items():
            part, staff, voice = key
            total = sum(d for _l, _k, _p, d, chord in items if not chord)
            if modal and total and abs(total - modal) > Fraction(1, 16):
                flags.append(
                    {
                        "check": "broken_measure",
                        "severity": 3 if total > modal else 2,
                        "where": f"part {part} staff {staff} voice {voice} measure {measure_no}",
                        "detail": f"notated {total} quarters vs page-modal {modal}"
                        + (" (OVERFULL)" if total > modal else " (underfull)"),
                    }
                )

    for key, notes in streams.items():
        part, staff, voice = key
        sign = doc["clefs"].get((part, staff))
        bounds = _clef_bounds(sign)
        pitched = [n for n in notes if n[3] == PITCHED and _diatonic(n[4]) is not None]
        if not pitched:
            continue
        if bounds:
            low, high = bounds
            outliers = [n for n in pitched if not (low - LEDGER_TOLERANCE <= _diatonic(n[4]) <= high + LEDGER_TOLERANCE)]
            if outliers and len(outliers) > max(2, len(pitched) * 0.05):
                sample = ", ".join(_fmt(n[4]) for n in outliers[:4])
                flags.append(
                    {
                        "check": "outlier_pitch",
                        "severity": 2,
                        "where": f"part {part} staff {staff} voice {voice}",
                        "detail": f"{len(outliers)}/{len(pitched)} notes outside a {sign or '?'} staff: {sample}",
                    }
                )
            indices = sorted(_diatonic(n[4]) for n in pitched)
            median = indices[len(indices) // 2]
            if median < low - 2 or median > high + 2:
                flags.append(
                    {
                        "check": "clef_content",
                        "severity": 2,
                        "where": f"part {part} staff {staff} voice {voice}",
                        "detail": (
                            f"clef is {sign or 'unset'} but the notes sit at median staff position "
                            f"{median - low:+d} relative to the staff"
                        ),
                    }
                )
        seq = [n for n in notes if n[3] == PITCHED]
        for a, b in zip(seq, seq[1:]):
            if a[5] == Fraction(3, 4) and b[5] == Fraction(3, 4):
                flags.append(
                    {
                        "check": "dotted_eighth",
                        "severity": 1,
                        "where": f"part {part} staff {staff} voice {voice} measure {a[0]}",
                        "detail": f"adjacent dotted eighths {_fmt(a[4])},{_fmt(b[4])} (possible beamed-eighth misread)",
                    }
                )
            if a[4] == b[4] and a[5] == b[5]:
                flags.append(
                    {
                        "check": "duplicate_adjacent",
                        "severity": 1,
                        "where": f"part {part} staff {staff} voice {voice} measure {a[0]}",
                        "detail": f"repeated identical note {_fmt(a[4])} dur {a[5]}",
                    }
                )

    if not any(k[0] == 0 and v for k, v in doc["clefs"].items()):
        flags.append(
            {
                "check": "missing_context",
                "severity": 1,
                "where": "score",
                "detail": "no clef declared; MuseScore will default every staff to treble",
            }
        )

    return {
        "file": os.path.basename(path),
        "parts": doc["parts"],
        "staves": doc["staves"],
        "time": doc["time"],
        "clefs": {f"part {p} staff {s}": sign for (p, s), sign in sorted(doc["clefs"].items())},
        "pitched_notes": pitched_total,
        "measures": len({m for (_k, m) in per_measure}),
        "streams": len(streams),
        "flags": flags,
    }


def audit_dir(out_dir, top=25):
    files = sorted(glob.glob(os.path.join(out_dir, "*.musicxml")))
    files = [f for f in files if os.path.basename(f) != "score.musicxml"]
    reports = [audit_file(f) for f in files]
    tally = {}
    for r in reports:
        for f in r["flags"]:
            tally.setdefault(f["check"], {"count": 0, "pages": set()})
            tally[f["check"]]["count"] += 1
            tally[f["check"]]["pages"].add(r["file"])
    ranked = sorted(
        (
            {
                "check": check,
                "count": data["count"],
                "pages": sorted(data["pages"]),
            }
            for check, data in tally.items()
        ),
        key=lambda d: -d["count"],
    )
    suspects = []
    for r in reports:
        for f in r["flags"]:
            suspects.append({"file": r["file"], **f})
    suspects.sort(key=lambda s: -s["severity"])
    return {"out_dir": out_dir, "pages": reports, "tally": ranked, "suspects": suspects[:top]}


def format_audit(result):
    lines = [f"audit: {result['out_dir']}", ""]
    lines.append("| page | parts | staves | time | clefs | notes | measures | flags |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for r in result["pages"]:
        flag_count = len(r["flags"])
        clefs = ", ".join(f"{k.split('part ')[1]}:{v}" for k, v in r["clefs"].items()) or "none"
        lines.append(
            f"| {r['file']} | {r['parts']} | {sum(r['staves'].values())} | {r['time']} | "
            f"{clefs} | {r['pitched_notes']} | {r['measures']} | {flag_count} |"
        )
    lines += ["", "flag census:"]
    for row in result["tally"]:
        lines.append(f"  {row['check']:22s} {row['count']:5d} flags on {len(row['pages'])} page(s)")
    lines += ["", f"top {len(result['suspects'])} suspects:"]
    for s in result["suspects"]:
        lines.append(f"  [{s['file']}] {s['check']}: {s['where']} — {s['detail']}")
    return "\n".join(lines)
