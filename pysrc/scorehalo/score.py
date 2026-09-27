"""Note-level comparison of two MusicXML scores.

Accuracy questions need labels, so this module does the scoring half: it
flattens both scores into (part, staff, voice) note streams, aligns the two
streams with an LCS diff over (pitch, duration) pairs, and reports where the
prediction matches the reference, substitutes, or drifts in length.

Durations are normalized to quarter-note Fractions so two files with different
<divisions> still compare. Rests participate in the alignment (so the cursor
stays musical) but are excluded from pitch statistics.
"""

import xml.etree.ElementTree as ET
from fractions import Fraction

PITCHED = 0
REST = 1


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _quarter(duration, divisions):
    if duration is None:
        return Fraction(0)
    return Fraction(int(duration), int(divisions or 1))


def _stream_key(part_index, staff, voice):
    return (part_index, staff or "1", voice or "1")


def parse_document(path):
    """Return a structural view of a MusicXML score.

    {"parts": int,
     "staves": {part_index: int},
     "time": (beats, beat_type) or None,
     "clefs": {(part_index, staff): sign},
     "clef_decls": [(part_index, staff, sign), ...],
     "streams": {(part, staff, voice): [(measure, onset, local, kind, pitch, dur, chord), ...]}}

    `onset` is cumulative from the start of the part; `local` is relative to
    the measure, which is what collision and measure-arithmetic checks need.
    `chord` marks a note that shares its onset with the previous one, so it
    must not be counted twice when totalling a measure.
    """
    root = ET.parse(path).getroot()
    doc = {
        "parts": 0,
        "staves": {},
        "time": None,
        "clefs": {},
        "clef_decls": [],
        "streams": {},
    }
    parts = [p for p in root.iter() if _local(p.tag) == "part"]
    doc["parts"] = len(parts)
    for part_index, part in enumerate(parts):
        divisions = 1
        base = Fraction(0)
        staves = 1
        for measure in [m for m in part if _local(m.tag) == "measure"]:
            for attr in measure.iter():
                tag = _local(attr.tag)
                if tag == "divisions":
                    divisions = int(attr.text or 1)
                elif tag == "staves":
                    staves = int(attr.text or 1)
                elif tag == "clef":
                    number = attr.get("number")
                    sign = None
                    for c in attr:
                        ctag = _local(c.tag)
                        if ctag == "number":
                            number = c.text
                        elif ctag == "sign":
                            sign = c.text
                    if sign:
                        doc["clef_decls"].append((part_index, number or "1", sign))
                elif tag == "time" and doc["time"] is None:
                    beats = beat_type = None
                    for c in attr:
                        ctag = _local(c.tag)
                        if ctag == "beats":
                            beats = c.text
                        elif ctag == "beat-type":
                            beat_type = c.text
                    if beats and beat_type:
                        doc["time"] = (int(beats), int(beat_type))
            measure_no = measure.get("number") or str(len(doc["streams"]) + 1)
            cursor = Fraction(0)
            span = Fraction(0)
            last_onset = Fraction(0)
            for child in measure:
                tag = _local(child.tag)
                if tag == "backup":
                    for n in child:
                        if _local(n.tag) == "duration":
                            cursor -= _quarter(n.text, divisions)
                elif tag == "forward":
                    for n in child:
                        if _local(n.tag) == "duration":
                            cursor += _quarter(n.text, divisions)
                            span = max(span, cursor)
                elif tag == "note":
                    dur = _quarter(None, divisions)
                    is_chord = False
                    is_rest = False
                    staff = voice = None
                    pitch = None
                    for n in child:
                        ntag = _local(n.tag)
                        if ntag == "duration":
                            dur = _quarter(n.text, divisions)
                        elif ntag == "chord":
                            is_chord = True
                        elif ntag == "staff":
                            staff = n.text
                        elif ntag == "voice":
                            voice = n.text
                        elif ntag == "rest":
                            is_rest = True
                        elif ntag == "pitch":
                            step = alter = octave = None
                            for p in n:
                                ptag = _local(p.tag)
                                if ptag == "step":
                                    step = p.text
                                elif ptag == "alter":
                                    alter = int(float(p.text or 0))
                                elif ptag == "octave":
                                    octave = p.text
                            if alter is None:
                                alter = 0
                            pitch = (step, alter, octave)
                    onset = last_onset if is_chord else cursor
                    if not is_chord:
                        cursor += dur
                        span = max(span, cursor)
                        last_onset = onset
                    key = _stream_key(part_index, staff, voice)
                    kind = REST if is_rest or pitch is None else PITCHED
                    doc["streams"].setdefault(key, []).append(
                        (measure_no, base + onset, onset, kind, pitch, dur, is_chord)
                    )
            base += span
        doc["staves"][part_index] = staves
    for part_index, staff, sign in doc["clef_decls"]:
        slot = doc["clefs"]
        if slot.get((part_index, staff)) in (None, sign) or len(doc["clef_decls"]) == 1:
            slot[(part_index, staff)] = sign
    return doc


def parse_streams(path):
    """Return {(part, staff, voice): [(onset, kind, pitch, dur), ...]}, chords normalized."""
    doc = parse_document(path)
    streams = {}
    for key, notes in doc["streams"].items():
        groups = {}
        for note in notes:
            groups.setdefault(note[1], []).append(note)
        flat = []
        for onset in sorted(groups):
            group = groups[onset]
            pitched = sorted(
                (n for n in group if n[3] == PITCHED),
                key=lambda n: (n[4][0] or "", n[4][1] or 0, n[4][2] or ""),
            )
            rests = [n for n in group if n[3] == REST]
            flat.extend((n[1], n[3], n[4], n[5]) for n in (pitched + rests))
        streams[key] = flat
    return streams


MAX_DP_CELLS = 1_500_000


def _greedy_ops(a, b):
    """Linear-time alignment fallback for very large streams."""
    ops = []
    i = j = 0
    n, m = len(a), len(b)
    while i < n and j < m:
        if a[i] == b[j]:
            ops.append(("m", i, j))
            i += 1
            j += 1
        elif a[i] in b[j : j + 8]:
            ops.append(("d", i, j))
            i += 1
        else:
            ops.append(("i", i, j))
            j += 1
    while i < n:
        ops.append(("d", i, j))
        i += 1
    while j < m:
        ops.append(("i", i, j))
        j += 1
    return ops


def _lcs_ops(a, b):
    """Align two key lists; return list of (op, a_idx, b_idx) with op in m/s/i/d."""
    n, m = len(a), len(b)
    if n * m > MAX_DP_CELLS:
        return _greedy_ops(a, b)
    table = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        row, nxt = table[i], table[i + 1]
        ai = a[i]
        for j in range(m - 1, -1, -1):
            if ai == b[j]:
                row[j] = nxt[j + 1] + 1
            else:
                row[j] = nxt[j] if nxt[j] >= row[j + 1] else row[j + 1]
    ops = []
    i = j = 0
    while i < n and j < m:
        if a[i] == b[j]:
            ops.append(("m", i, j))
            i += 1
            j += 1
        elif table[i + 1][j] >= table[i][j + 1]:
            ops.append(("d", i, j))
            i += 1
        else:
            ops.append(("i", i, j))
            j += 1
    while i < n:
        ops.append(("d", i, j))
        i += 1
    while j < m:
        ops.append(("i", i, j))
        j += 1
    return ops


def compare_streams(truth, pred):
    """Compare two stream dicts; return per-stream and total dicts.

    Alignment runs on pitch first, so a note that is present with the wrong
    duration is reported as a duration error instead of vanishing into a
    miss/spurious pair. Unmatched notes are then paired in order to surface
    pitch substitutions.
    """
    per_stream = {}
    totals = {
        "truth_notes": 0,
        "pred_notes": 0,
        "exact": 0,
        "pitch_wrong": 0,
        "duration_wrong": 0,
        "both_wrong": 0,
        "missed": 0,
        "spurious": 0,
        "pitch_class_only": 0,
    }
    for key in sorted(set(truth) | set(pred)):
        t = truth.get(key, [])
        p = pred.get(key, [])
        stats = dict.fromkeys(totals, 0)
        tk = [_pitch_key(n[2]) for n in t]
        pk = [_pitch_key(n[2]) for n in p]
        loose_truth, loose_pred = [], []
        for op, i, j in _lcs_ops(tk, pk):
            if op == "m":
                _count_match(stats, t[i], p[j])
            elif op == "d":
                if t[i][1] == PITCHED:
                    loose_truth.append(t[i])
            else:
                if p[j][1] == PITCHED:
                    loose_pred.append(p[j])
        for a, b in zip(loose_truth, loose_pred):
            _count_loose(stats, a, b)
        stats["missed"] += max(0, len(loose_truth) - len(loose_pred))
        stats["spurious"] += max(0, len(loose_pred) - len(loose_truth))
        stats["truth_notes"] = sum(1 for n in t if n[1] == PITCHED)
        stats["pred_notes"] = sum(1 for n in p if n[1] == PITCHED)
        per_stream[key] = stats
        for field in totals:
            totals[field] += stats[field]
    return per_stream, totals


def _pitch_key(pitch):
    if pitch is None:
        return (REST, 0)
    return (PITCHED, pitch[0], pitch[1], pitch[2])


def _count_loose(stats, truth_note, pred_note):
    if truth_note[3] == pred_note[3]:
        stats["pitch_wrong"] += 1
        step_t, step_p = truth_note[2][0], pred_note[2][0]
        if step_t and step_p and step_t.upper() == step_p.upper():
            stats["pitch_class_only"] += 1
    else:
        stats["both_wrong"] += 1


def _count_match(stats, truth_note, pred_note):
    if truth_note[1] == REST and pred_note[1] == REST:
        return
    if truth_note[1] == REST or pred_note[1] == REST:
        stats["duration_wrong"] += 1
        return
    if truth_note[3] == pred_note[3]:
        stats["exact"] += 1
    else:
        stats["duration_wrong"] += 1


def _pct(num, den):
    return 0.0 if not den else 100.0 * num / den


def format_report(truth_path, pred_path, per_stream, totals):
    lines = [
        f"truth: {truth_path}",
        f"pred : {pred_path}",
        "",
        f"streams compared        : {len(per_stream)}",
        f"truth notes (pitched)   : {totals['truth_notes']}",
        f"pred notes  (pitched)   : {totals['pred_notes']}",
        "",
        f"exact pitch+duration    : {totals['exact']:5d}  ({_pct(totals['exact'], totals['pred_notes']):.1f}% of pred)",
        f"  right pitch, wrong dur: {totals['duration_wrong']:5d}",
        f"  right dur, wrong pitch: {totals['pitch_wrong']:5d}  ({totals['pitch_class_only']} of these same letter, wrong octave/alter)",
        f"  both wrong            : {totals['both_wrong']:5d}",
        f"missed (in truth only)  : {totals['missed']:5d}",
        f"spurious (pred only)    : {totals['spurious']:5d}",
        "",
    ]
    matched = totals["exact"]
    precision = _pct(matched, totals["pred_notes"])
    recall = _pct(matched, totals["truth_notes"])
    f1 = 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)
    lines += [
        f"note precision (exact)  : {precision:.1f}%",
        f"note recall    (exact)  : {recall:.1f}%",
        f"F1                      : {f1:.1f}%",
        "",
        "per stream:",
    ]
    for key, s in per_stream.items():
        if not s["pred_notes"] and not s["truth_notes"]:
            continue
        part, staff, voice = key
        lines.append(
            f"  part {part} staff {staff} voice {voice}: truth {s['truth_notes']:4d} "
            f"pred {s['pred_notes']:4d} exact {s['exact']:4d} "
            f"pitch {s['pitch_wrong']:3d} dur {s['duration_wrong']:3d} "
            f"both {s['both_wrong']:3d} missed {s['missed']:3d} spurious {s['spurious']:3d}"
        )
    return "\n".join(lines)


def score_files(truth_path, pred_path):
    per_stream, totals = compare_streams(parse_streams(truth_path), parse_streams(pred_path))
    return per_stream, totals
