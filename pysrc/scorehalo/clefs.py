"""Read-only clef/staff audit. XML declarations are not source-image evidence.
Tracks changes in score time (not voice serialization order). No repair occurs.
Uncertain timing produces unknown assignments rather than guessed clefs.
"""
from fractions import Fraction
import xml.etree.ElementTree as ET

def _local(tag):
    return tag.rsplit('}', 1)[-1]

def _children(e, name):
    return [c for c in e if _local(c.tag) == name]

def _text(e, name, default=None):
    found = _children(e, name)
    return (found[0].text or '').strip() if found else default

def _positive(value):
    try:
        number = int(value)
        return number if number > 0 else None
    except (ValueError, TypeError):
        return None

def audit_clefs(xml):
    declarations, notes, findings = [], [], []
    def issue(code, message, pid=None, measure=None, staff=None, severity='warning'):
        findings.append(dict(code=code, message=message, part=pid, measure=measure,
                             staff=staff, severity=severity))
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        issue('clef_xml_parse', str(exc), severity='error')
        return dict(declarations=[], note_assignments=[], findings=findings,
                    source_review='not_performed')
    if _local(root.tag) != 'score-partwise':
        issue('clef_root_unassessed', 'Only score-partwise is supported', severity='info')
    for part in _children(root, 'part'):
        pid, divisions, staves = part.get('id'), None, 1
        state = {}
        reported_missing = set()
        reported_conflict = set()
        for ordinal, measure in enumerate(_children(part, 'measure'), 1):
            number = measure.get('number', str(ordinal))
            cursor, previous_onset = Fraction(0), None
            changes, pending = [], []
            for seq, el in enumerate(measure):
                kind = _local(el.tag)
                if kind == 'attributes':
                    d = _text(el, 'divisions')
                    if d is not None:
                        try:
                            divisions = Fraction(d)
                            if divisions <= 0: divisions = None
                        except (ValueError, ZeroDivisionError): divisions = None
                    count = _text(el, 'staves')
                    if count is not None:
                        staves = _positive(count)
                        if staves is None: issue('clef_staves_invalid', 'Invalid staff count', pid, number)
                    for c in _children(el, 'clef'):
                        staff = _positive(c.get('number', '1'))
                        sign, line = _text(c, 'sign'), _text(c, 'line')
                        octave = _text(c, 'clef-octave-change', '0')
                        valid = True
                        if staff is None or (staves is not None and staff > staves):
                            valid = False
                            issue('clef_staff_invalid', 'Clef references an undeclared/invalid staff', pid, number, staff)
                        if sign not in {'G','F','C','percussion','TAB','jianpu','none'}:
                            valid = False
                            issue('clef_sign_invalid', 'Missing or unsupported clef sign', pid, number, staff)
                        if line is not None and _positive(line) is None:
                            valid = False
                            issue('clef_line_invalid', 'Clef line must be positive', pid, number, staff)
                        try: int(octave)
                        except (ValueError, TypeError):
                            valid = False
                            issue('clef_octave_invalid', 'Clef octave change must be an integer', pid, number, staff)
                        record = dict(part=pid, measure=number, measure_ordinal=ordinal,
                            element_index=seq, onset=str(cursor) if cursor is not None else None,
                            staff=staff, sign=sign, line=line, octave_change=octave,
                            additional=c.get('additional', 'no'), print_object=c.get('print-object', 'yes'),
                            after_barline=c.get('after-barline', 'no'), valid=valid,
                            provenance='xml_declaration_source_unverified')
                        declarations.append(record)
                        if record['additional'] != 'yes': changes.append((cursor, seq, record))
                        if sign in {'G','F','C'} and line is None:
                            issue('clef_line_unspecified', 'Line omitted; audit does not infer a line', pid, number, staff, 'info')
                    continue
                if kind not in {'note','backup','forward'}: continue
                grace = kind == 'note' and bool(_children(el, 'grace'))
                chord = kind == 'note' and bool(_children(el, 'chord'))
                onset = previous_onset if chord else cursor
                if kind == 'note':
                    staff = _positive(_text(el, 'staff', '1'))
                    if staff is None or (staves is not None and staff > staves):
                        issue('clef_note_staff_invalid', 'Note references undeclared/invalid staff', pid, number, staff)
                    pending.append(dict(part=pid, measure=number, measure_ordinal=ordinal,
                        element_index=seq, onset=str(onset) if onset is not None else None,
                        staff=staff, voice=_text(el,'voice','1'), grace=grace,
                        pitched=bool(_children(el,'pitch'))))
                    if not chord: previous_onset = onset
                    if grace or chord: continue
                try:
                    if divisions is None: raise ValueError('unknown divisions')
                    duration = Fraction(_text(el,'duration')) / divisions
                    if duration < 0: raise ValueError('negative duration')
                    if cursor is not None:
                        cursor += -duration if kind == 'backup' else duration
                        if cursor < 0: cursor = None
                except (ValueError, TypeError, ZeroDivisionError): cursor = None
                if kind in {'backup','forward'}: previous_onset = None
            known = sorted(((t, seq, r) for t, seq, r in changes if t is not None), key=lambda item: (item[0], item[1]))
            unknown_staffs = {r['staff'] for t, seq, r in changes if t is None}
            for n in pending:
                staff, active = n['staff'], state.get(n['staff'])
                if n['onset'] is not None and staff not in unknown_staffs:
                    t = Fraction(n['onset'])
                    eligible = [(at, r) for at, seq, r in known if r['staff'] == staff and at <= t]
                    if eligible:
                        latest = max(at for at, r in eligible)
                        same = [r for at, r in eligible if at == latest]
                        identities = {(r['sign'],r['line'],r['octave_change']) for r in same}
                        active = same[-1] if len(identities) == 1 else None
                        if len(identities) > 1:
                            # One finding per conflicting time, not one per note.
                            key = (staff, ordinal, str(latest))
                            if key not in reported_conflict:
                                issue('clef_conflict', 'Different primary clefs at the same score time', pid, number, staff)
                                reported_conflict.add(key)
                else: active = None
                n['clef'] = dict(active) if active and active['valid'] else None
                if n['clef'] is not None:
                    n['clef_origin'] = 'declared_this_measure' if active['measure_ordinal'] == ordinal else 'inherited'
                else:
                    n['clef_origin'] = 'unresolved'
                    key = (staff, ordinal)
                    if n['pitched'] and key not in reported_missing:
                        issue('clef_unresolved', 'No unambiguous active clef; inspect source and timing', pid, number, staff)
                        reported_missing.add(key)
                notes.append(n)
            for staff in {r['staff'] for t, seq, r in changes}:
                candidates = [(t, seq, r) for t, seq, r in known if r['staff'] == staff]
                if staff in unknown_staffs or not candidates:
                    state.pop(staff, None)
                else:
                    latest = max(t for t, seq, r in candidates)
                    same = [r for t, seq, r in candidates if t == latest]
                    if len({(r['sign'],r['line'],r['octave_change']) for r in same}) == 1 and same[-1]['valid']:
                        state[staff] = same[-1]
                    else: state.pop(staff, None)
    return dict(declarations=declarations, note_assignments=notes, findings=findings,
                source_review='not_performed')

def declaration_inventory(xml):
    """Exact per-measure clef XML payloads; ignores only measure numbering.
    For preservation tests, not musical-equivalence comparison.
    """
    root = ET.fromstring(xml)
    result = {}
    def tree(e):
        return (_local(e.tag), tuple(sorted(e.attrib.items())), (e.text or '').strip(),
                tuple(tree(c) for c in e))
    for p in _children(root,'part'):
        result[p.get('id')] = [tuple((seq, tuple(tree(c) for c in _children(el,'clef')))
            for seq, el in enumerate(c for c in m if _local(c.tag) != 'print')
            if _local(el.tag) == 'attributes' and _children(el,'clef'))
            for m in _children(p,'measure')]
    return result

def compare_clef_maps(a, b):
    """Compare declared score positions; differing segmentation is not aligned."""
    def signature(xml):
        return [(r['part'], r['measure_ordinal'], r['onset'], r['staff'], r['sign'],
                 r['line'], r['octave_change'], r['additional'])
                for r in audit_clefs(xml)['declarations']]
    sa, sb = signature(a), signature(b)
    return dict(declarations_identical=sa == sb, a_count=len(sa), b_count=len(sb),
                alignment='part IDs and measure ordinals only; no automatic correspondence',
                source_accuracy='not_assessed')


def main(argv=None):
    """Print a JSON audit of an uncompressed MusicXML file."""
    import argparse
    import json
    from pathlib import Path
    parser = argparse.ArgumentParser(description='Read-only clef/staff audit; no image verification')
    parser.add_argument('input', help='Uncompressed .musicxml or .xml file (not .mxl)')
    args = parser.parse_args(argv)
    report = audit_clefs(Path(args.input).read_text(encoding='utf-8'))
    print(json.dumps(report, indent=2))
    return 1 if any(f['severity'] == 'error' for f in report['findings']) else 0

if __name__ == '__main__':
    raise SystemExit(main())
