"""MusicXML <-> the page-anchored graph.

Parsing walks a measure once, turning the cursor into absolute onsets, so all
cursor arithmetic is discarded on the way in. Emitting never re-derives a
backup from a foreign one: it sorts by onset and computes the exact gap. That
is why the assembly cannot reproduce the failures we were chasing (overshooting
backups, empty measures, parts whose divisions and time signatures disagree) --
they are not representable in this model.

Divisions are normalised to one value for the whole page, so fragments that
disagreed (2, 4, 6) become commensurate by construction.

Three rules below are not style choices. Each was found by reducing a file
MuseScore refused to import (rc=40, a silent headless import dialog) to the
smallest case that still failed, and each is verified by a probe that fails
without the rule and passes with it.

How much each rule is actually REQUIRED was measured against both engravers,
because they do not agree -- and the honest answer is not the reassuring one:

1. Every <note> needs a <type>. MEASURED: a lone note of 8 ticks (2/3 of a
   quarter) with no <type> is REJECTED by MuseScore and accepted by
   musicxml2ly. So this is a MuseScore-compatibility rule, not a MusicXML
   one. It earns its place anyway because tuplets are the only source of such
   durations: <duration> states the measured length, <type> states how it is
   written, and <time-modification> states why those differ.
2. Every <part> must span the same number of measures. MEASURED: MuseScore
   REJECTS unequal widths (rc=40); musicxml2ly ACCEPTS them and silently
   emits a 2-bar staff against a 1-bar staff, which is a wrong score with no
   complaint. So this is a real musical requirement and not merely a quirk --
   and musicxml2ly's permissiveness is a trap, not a counterexample. Parts
   sound simultaneously, so a part that stops early misaligns everything
   after it.
3. A rest and a pitched note at the same (voice, onset) is a contradiction from
   merging two fragments that disagreed. Neither engraver objects to the
   literal output; a <chord> tone attached to a rest is simply meaningless,
   and it was corrupting real content. The note wins: a rest is absence of
   information, a note is content.
"""

import xml.etree.ElementTree as ET
from dataclasses import replace
from math import gcd
from typing import Dict, List, Optional, Tuple

from .graph import Measure, Note, Part, Page, Pitch

#: LCM of the divisions homr ACTUALLY emits, measured over the book: the set is
#: {1, 2, 3, 4, 6, 8, 12} (fragments: 14x1, 57x2, 1x3, 35x4, 6x6, 6x8, 2x12),
#: whose LCM is 24. This used to be 12, from a comment that named only {2, 4, 6}
#: -- and 12 is not a multiple of 8, so every note of a ``divisions=8`` fragment
#: was scaled by 12/8 = 1.5 and rounded. There are 37 such notes, all 32nds, and
#: rounding 1.5 up to 2 makes each one a THIRD too long: not a sub-tick nicety
#: but a wrong rhythm, in a bar that our own cursor checks then accept because
#: <duration> is self-consistent.
GLOBAL_DIVISIONS = 24

_TYPE = {"whole": 4.0, "half": 2.0, "quarter": 1.0, "eighth": 0.5,
         "16th": 0.25, "32nd": 0.125, "64th": 0.0625, "128th": 0.03125}


def _plain_values(divisions):
    """Written value -> (type name, dot count) for every exact note value.

    Derived from ``_TYPE`` rather than typed out, because a hand-written table
    silently stops matching its own constant the moment the constant moves: the
    previous one was correct at 12 and wrong at 24 (it still called 3 ticks a
    "16th", when at 24 ticks to the quarter a 3-tick note is a 32nd). Dots are
    walked from fewest to most, so an undotted value is never spelled with a
    needless dot.
    """
    out = {}
    for dots in range(3):
        for name, quarters in _TYPE.items():
            ticks = quarters * divisions * (2 ** (dots + 1) - 1) / (2 ** dots)
            if ticks == int(ticks):
                out.setdefault(int(ticks), (name, dots))
    return out


#: Duration in GLOBAL_DIVISIONS ticks -> (type name, dot count). A note whose
#: <type> disagrees with its <duration> is rejected by MuseScore, so the type is
#: derived from the number we actually write rather than assumed to be a quarter.
_TYPE_FOR = _plain_values(GLOBAL_DIVISIONS)

#: Note values a tuplet may be written on, in the order a reader tries them.
#: Each is scaled by GLOBAL_DIVISIONS: at 24 ticks to the quarter a quarter is
#: 24 (a 3:2 tuplet eighth, written "eighth") and a half is 48. The caller's
#: ratio, not this list, fixes the length; the list only decides which value the
#: tuplet is notated on, and the first that lands within half a tick wins.
_TUPLET_TYPES = ("eighth", "quarter", "half", "16th", "whole")


def _type_for(duration: int, tuplet: Optional[Tuple[int, int]] = None
              ) -> Tuple[str, int, int, int]:
    """The (type, dots, actual, normal) to write for a duration in ticks.

    Every note MUST get a ``<type>``. Verified against MuseScore 4: a single
    note of duration 8 (= 2/3 of a quarter) with no ``<type>`` is rejected
    outright (rc=40); the identical note with ``<type>quarter</type>`` imports
    fine. Omitting the element is legal MusicXML but is not survivable here --
    an earlier version of this function returned None for such durations and
    wrote nothing, which is what broke the file.

    ``<type>`` names the WRITTEN (nominal) value; ``<time-modification>``
    carries the ratio that compresses it to the ticks we actually occupy. So a
    3:2 tuplet eighth (4 ticks) is written as type=eighth with actual=3,
    normal=2, and the reader scales eighth/3*2 = 2/3 quarter = 4 ticks.

    A duration that is neither a plain value nor a tuplet subdivision (homr
    emits a few, e.g. 45 or 15) has no single written value, so it is named by
    the CLOSEST value and the exact ratio is carried in ``<time-modification>``.
    Writing the note at the value at-or-above it -- an earlier, deliberately
    "conservative" choice -- is not survivable: MuseScore's measure check sums
    the NOMINAL values, so a 15-tick note written as a dotted quarter (18) made
    a complete bar read as 51/48 and the importer reported "Measure 1, staff 1
    incomplete. Expected: 4/4; Found: 17/16" (reduced to the smallest case).
    The cursor still landed on the barline because ``<duration>`` is clamped,
    which is why our own cursor-based checkers never saw it.

    The ratio is the one with the smallest numerator and denominator, so a note
    is never written under a needlessly exotic tuplet: 45 ticks becomes a dotted
    half under 4:5, not a half under 15:8.
    """
    if tuplet:
        actual, normal = tuplet
        # Nominal length that, compressed by the tuplet, gives these ticks.
        for name in _TUPLET_TYPES:
            nominal = _TYPE[name] * GLOBAL_DIVISIONS
            if abs(nominal * normal / actual - duration) < 0.5:
                return name, 0, actual, normal
    hit = _TYPE_FOR.get(duration)
    if hit is not None:
        return hit[0], hit[1], 0, 0
    # No written value fits: keep the closest one and state the exact ratio.
    # normal/actual scales the nominal to the ticks, so the reader computes
    # ticks * normal / actual == duration and the bar stays exactly its meter.
    best = None
    for ticks, (name, dots) in sorted(_TYPE_FOR.items()):
        g = gcd(duration, ticks)
        normal, actual = duration // g, ticks // g
        key = (max(normal, actual), abs(ticks - duration), ticks)
        if best is None or key < best[0]:
            best = (key, name, dots, actual, normal)
    _, name, dots, actual, normal = best
    return name, dots, actual, normal


def _pitch_of(el) -> Optional[Pitch]:
    p = el.find("pitch")
    if p is None:
        return None
    step = p.findtext("step") or "C"
    alter = int(float(p.findtext("alter") or 0))
    octave = int(p.findtext("octave") or 4)
    return Pitch(step, alter, octave)


def _duration_ticks(el, divisions: int) -> int:
    """Ticks for a note/forward/backup, normalised to GLOBAL_DIVISIONS."""
    d = el.findtext("duration")
    if d is not None:
        return int(round(int(d) * GLOBAL_DIVISIONS / divisions))
    t = el.findtext("type")
    if t and t in _TYPE:
        return int(round(_TYPE[t] * GLOBAL_DIVISIONS))
    return 0


def parse_part(path, staff_index=1, system=1) -> List[Part]:
    """Read every <part> of a MusicXML file into graph Parts.

    The part NAME comes from ``<part-list>``, not from the position. homr
    labels each fragment's role ("Piano", "Voice"), and that label is the only
    thing that says which fragments are the same instrument across systems.
    Naming every part "Staff 1" -- as this used to -- silently destroys the
    identity, so a page-wide assembly cannot tell a continuing piano part from
    a fresh one and ends up emitting fragments of one instrument as if they
    were several instruments.
    """
    root = ET.parse(path).getroot()
    plist = root.find("part-list")
    names = {}
    if plist is not None:
        for sp in plist.findall("score-part"):
            label = sp.findtext("part-name") or sp.findtext("part-abbreviation")
            if label:
                names[sp.get("id")] = label.strip()
    out: List[Part] = []
    for pos, src in enumerate(root.findall("part"), start=1):
        name = names.get(src.get("id")) or "Staff %d" % staff_index
        part = Part(id="P%d" % pos, name=name,
                    system=system, staff_index=staff_index)
        divisions = GLOBAL_DIVISIONS
        # MusicXML attributes are declared once and INHERITED by later
        # measures. Carrying them forward is not a nicety: without it a part
        # silently reverts to the default meter halfway through.
        state = {"beats": 4, "beat_type": 4, "fifths": 0, "clef": ("G", 2)}
        for m in src.findall("measure"):
            number = int(m.get("number") or len(part.measures) + 1)
            meas = Measure(number=number, divisions=GLOBAL_DIVISIONS,
                           beats=state["beats"], beat_type=state["beat_type"],
                           key_fifths=state["fifths"], clef=state["clef"])
            # A measure may carry SEVERAL <attributes> blocks. homr writes
            # <divisions> in one and <key>/<time>/<clef> in another -- both
            # legal MusicXML, since each change of state gets its own block.
            # `m.find("attributes")` returns only the FIRST, so reading one
            # block silently discarded the clef, key and meter whenever homr
            # split them: the part came out with no clef at all and the default
            # treble, which looks exactly like a detection failure and is not.
            for attrs in m.findall("attributes"):
                if attrs.findtext("divisions"):
                    divisions = int(attrs.findtext("divisions"))
                if attrs.find("time") is not None:
                    state["beats"] = int(attrs.findtext("time/beats") or 4)
                    state["beat_type"] = int(
                        attrs.findtext("time/beat-type") or 4)
                    meas.beats, meas.beat_type = state["beats"], state["beat_type"]
                if attrs.find("key") is not None:
                    state["fifths"] = int(attrs.findtext("key/fifths") or 0)
                    meas.key_fifths = state["fifths"]
                # ``<staff>`` names which staff a clef belongs to and lives
                # INSIDE the ``<clef>``, not on ``<attributes>``. Reading it from
                # the wrong parent returns None for every clef, so a grand
                # staff's bass clef was recorded against staff 1 and both staves
                # came out treble -- the exact symptom this prevents. Both
                # spellings are accepted: some writers put ``<staff>`` on the
                # clef, some use ``<clef number="N">``.
                for clef_el in attrs.findall("clef"):
                    sign = clef_el.findtext("sign") or "G"
                    line = int(clef_el.findtext("line") or 2)
                    state["clef"] = (sign, line)
                    meas.clef = (sign, line)
                    clef_staff = (clef_el.findtext("staff")
                                  or clef_el.get("number") or 1)
                    part.clef_by_staff[int(clef_staff)] = (sign, line)
            cursor = 0
            last_onset = 0
            for el in m:
                if el.tag == "backup":
                    cursor -= _duration_ticks(el, divisions)
                elif el.tag == "forward":
                    cursor += _duration_ticks(el, divisions)
                elif el.tag == "note":
                    dur = _duration_ticks(el, divisions)
                    pitch = _pitch_of(el)
                    is_chord = el.find("chord") is not None
                    is_rest = el.find("rest") is not None
                    if is_chord:
                        # A chord tone does NOT advance the cursor: it shares
                        # the onset of the note it is attached to. Because the
                        # graph encodes simultaneity as equal onsets, a chord
                        # is simply more notes at one onset.
                        onset = last_onset
                    else:
                        onset = max(0, cursor)
                        last_onset = onset
                    tm = el.find("time-modification")
                    tuplet = None
                    if tm is not None and tm.findtext("actual-notes"):
                        tuplet = (int(tm.findtext("actual-notes") or 1),
                                  int(tm.findtext("normal-notes") or 1))
                    slur_el = el.find("notations/slur")
                    meas.notes.append(Note(
                        # A note carries its OWN <staff>; fall back to 1 when
                        # absent -- staff 1 OF THE FRAGMENT, which is what
                        # grouping._restamp's contract assumes ("every
                        # fragment's notes all carry staff=1"). Falling back
                        # to the caller's page-wide staff_index instead leaked
                        # that index into single-staff parts, whose restamp is
                        # a no-op by design: a p0024 singer tacet bar came out
                        # stamped <staff>3</staff> and strict importers
                        # rejected it. Multi-staff parts are renumbered by
                        # _restamp regardless, so this changes nothing there.
                        staff=int(el.findtext("staff") or 1),
                        voice=int(el.findtext("voice") or 1),
                        onset=onset, duration=dur, pitch=pitch,
                        measure=number, is_rest=is_rest or pitch is None,
                        tie=el.findtext("tie/type"),
                        slur=slur_el.get("type") if slur_el is not None else None,
                        tuplet=tuplet,
                        system=system))
                    if not is_chord:
                        cursor += dur
            meas.notes.sort(key=lambda n: (n.onset, n.voice))
            meas.is_empty = not meas.notes
            part.measures.append(meas)
        out.append(part)
    return out


def _accidental(alter: int) -> Optional[str]:
    return {-2: "flat-flat", -1: "flat", 0: None,
            1: "sharp", 2: "double-sharp"}.get(alter)


def _bar_duration(meas: Measure) -> int:
    return meas.beats * GLOBAL_DIVISIONS * 4 // meas.beat_type


def _attributes(meas: Measure, pad: str,
                part: Optional[Part] = None,
                clef_by_staff: Optional[Dict[int, Tuple[str, int]]] = None,
                ) -> List[str]:
    """Emit a bar's attributes, declaring every staff the part actually has.

    ``Measure.clef`` is a single scalar, which cannot express a two-staff
    instrument -- the whole reason ``Part.clef_by_staff`` exists. When a part
    carries several staves, this emits ``<staves>`` and one ``<clef>`` per
    staff; a one-staff part emits neither, exactly as before.

    ``clef_by_staff`` overrides the part's own map for this bar. It is how a
    mid-part clef change is written: the caller passes the clefs in force at
    this bar, which differ from ``part.clef_by_staff`` exactly when a change
    lands here.
    """
    out = ["%s<attributes>" % pad,
           "%s  <divisions>%d</divisions>" % (pad, GLOBAL_DIVISIONS),
           "%s  <key><fifths>%d</fifths></key>" % (pad, meas.key_fifths),
           "%s  <time><beats>%d</beats><beat-type>%d</beat-type></time>"
           % (pad, meas.beats, meas.beat_type)]
    by_staff = dict(clef_by_staff if clef_by_staff is not None
                    else (getattr(part, "clef_by_staff", None) or {}))
    explicit = clef_by_staff is not None
    if len(by_staff) > 1:
        out.append("%s  <staves>%d</staves>" % (pad, len(by_staff)))
        for number in sorted(by_staff):
            sign, line = by_staff[number]
            out.append('%s  <clef number="%d"><sign>%s</sign><line>%d</line>'
                       "</clef>" % (pad, number, sign, line))
    elif explicit and by_staff:
        # The caller named the clef in force (the part's own, or the one a
        # mid-part change put here). It outranks `meas.clef`, because a padded
        # opening bar carries the page TEMPLATE's scalar clef and using that is
        # how a singer's bass staff was published as treble: her first bar is
        # tacet, inherited the template's G2, and the part's real F4 was never
        # written anywhere.
        sign, line = by_staff[min(by_staff)]
        out.append("%s  <clef><sign>%s</sign><line>%d</line></clef>"
                   % (pad, sign, line))
    elif meas.clef:
        out.append("%s  <clef><sign>%s</sign><line>%d</line></clef>"
                   % (pad, meas.clef[0], meas.clef[1]))
    elif by_staff:
        sign, line = by_staff[min(by_staff)]
        out.append("%s  <clef><sign>%s</sign><line>%d</line></clef>"
                   % (pad, sign, line))
    out.append("%s</attributes>" % pad)
    return out


def _emit_measure(meas: Measure, indent: str, with_attributes: bool = False,
                  part: Optional[Part] = None,
                  voices: Optional[Dict[Tuple[int, int], int]] = None,
                  clef_by_staff: Optional[Dict[int, Tuple[str, int]]] = None,
                  ) -> List[str]:
    """Emit one bar, computing every cursor move from onsets.

    Nothing here is copied from the source: backups are the exact distance
    between one onset and the last, so they cannot overshoot. ``<attributes>``
    is mandatory in the first bar of a part -- without a ``<divisions>``
    declaration every duration in the part is uninterpretable and importers
    reject the file outright.
    """
    lines = ['%s<measure number="%d">' % (indent, meas.number)]
    pad = indent + "  "
    if with_attributes:
        lines.extend(_attributes(meas, pad, part, clef_by_staff))
    meas = _apply_voices(meas, voices)
    # A chord is two notes on the SAME staff at the SAME onset. Staff is part of
    # the grouping key: without it, a two-staff instrument's hands -- both
    # voice 1, both starting at tick 0 -- are emitted as one <chord/>, turning
    # two hands into a single chord an octave apart and losing the distinction
    # MusicXML's <chord/> exists to express.
    groups: dict = {}
    for n in meas.notes:
        groups.setdefault((n.voice, n.onset, n.staff), []).append(n)

    cursor = 0
    dropped = 0
    total = _bar_duration(meas)
    for voice in sorted({k[0] for k in groups}):
        if cursor > 0:
            lines.append("%s<backup>" % pad)
            lines.append("%s  <duration>%d</duration>" % (pad, cursor))
            lines.append("%s</backup>" % pad)
            cursor = 0
        for v, onset, _staff in sorted(k for k in groups if k[0] == voice):
            if onset >= total:
                # homr sometimes writes more note-duration into a bar than the
                # meter allows. The graph keeps every note; MusicXML cannot
                # express the overflow, so the projection stops at the barline
                # and reports the loss rather than emitting a file that no
                # importer will open.
                dropped += len(groups[(v, onset, _staff)])
                continue
            notes = groups[(v, onset, _staff)]
            # A rest and a pitched note at the same (voice, onset) is a
            # contradiction, and it arrives by merging two fragments that
            # disagreed about that instant. Emitting the note as a chord tone on
            # the rest produces a file MuseScore refuses to import (rc=40), so
            # the notes win: a rest is absence of information, a note is
            # content. The rest is counted as dropped rather than swallowed.
            pitched = [n for n in notes if not n.is_rest]
            if pitched and len(pitched) != len(notes):
                dropped += len(notes) - len(pitched)
                notes = pitched
            if onset > cursor:
                lines.append("%s<forward>" % pad)
                lines.append("%s  <duration>%d</duration>" % (pad, onset - cursor))
                lines.append("%s</forward>" % pad)
            elif onset < cursor:
                lines.append("%s<backup>" % pad)
                lines.append("%s  <duration>%d</duration>" % (pad, cursor - onset))
                lines.append("%s</backup>" % pad)
            cursor = onset
            # A note that starts inside the bar but ends past the barline is
            # truncated to the space that remains. Clamping the cursor alone is
            # not enough: the note's own <duration> is what an importer sums,
            # so the overflow would survive and the file would be rejected.
            room = total - onset
            for i, n in enumerate(notes):
                dur = n.duration if n.duration <= room else room
                if dur <= 0:
                    dropped += 1
                    continue
                # A full-bar rest whose duration names no standard note value
                # (a lengthened bar pads to e.g. 120 ticks) would be written
                # as an invented tuplet -- whole x 4:5 -- and MuseScore rejects
                # <time-modification> on a rest outright (rc=40). A full-bar
                # rest has a first-class form anyway: measure="yes", which is
                # how the empty-bar path writes it (at any duration).
                full_bar_rest = (
                    n.is_rest and onset == 0 and dur == total
                    and bool(_type_for(dur, n.tuplet)[2]))
                lines.extend(_emit_note(n, pad, duration=dur,
                                        chord_tone=(i > 0),
                                        measure_rest=full_bar_rest))
                if dur < n.duration:
                    dropped += 1
            cursor = onset + min(notes[0].duration, room)
            if cursor > total:
                cursor = total
        if cursor < total:
            lines.append("%s<forward>" % pad)
            lines.append("%s  <duration>%d</duration>" % (pad, total - cursor))
            lines.append("%s</forward>" % pad)
            cursor = total
    lines.append("%s</measure>" % indent)
    return lines, dropped


def voice_map(part: Optional[Part]) -> Dict[Tuple[int, int], int]:
    """Assign one voice number per ``(staff, voice)`` pair, stable page-wide.

    MusicXML scopes ``<voice>`` to a *part*, not to a staff, so two staves of one
    instrument that both number their single voice "1" collide. The staves are
    then read as one voice hopping between staves: ``musicxml2ly`` emits a
    ``\\change Staff`` for every switch and LilyPond answers "cannot find context
    to change to: Staff = 1".

    The map is built from *every* note in the part, not per measure. Numbering
    each bar independently looks equivalent and is not: a bar where only the
    treble plays gives treble voice 1, the next bar where both play gives treble
    1 and bass 2, and the third gives treble 1 and bass 2 again -- so the
    numbers shift mid-part and the importer sees the voice migrate between
    staves. A page-wide map cannot drift, because a pair keeps its number in
    every bar it appears in.
    """
    by_staff = dict(getattr(part, "clef_by_staff", None) or {})
    pairs = sorted({(n.staff, n.voice)
                    for m in getattr(part, "measures", None) or []
                    for n in m.notes})
    if len(by_staff) <= 1 or not pairs:
        return {}
    return {pair: i + 1 for i, pair in enumerate(pairs)}


def _apply_voices(meas: Measure, voices: Dict[Tuple[int, int], int]) -> Measure:
    """Restamp one measure's notes onto the part-wide voice numbering."""
    if not voices:
        return meas
    return replace(meas, notes=[replace(n, voice=voices[(n.staff, n.voice)])
                                for n in meas.notes])


def _emit_note(n: Note, pad: str, duration: Optional[int] = None,
               chord_tone: bool = False,
               measure_rest: bool = False) -> List[str]:
    """Emit one note head.

    ``duration`` overrides the graph's own value. The override is not
    cosmetic: a note that starts inside a bar but ends past the barline has to
    be truncated in the <duration> we actually write, because that number is
    what an importer sums. Clamping only the cursor leaves the overflow in the
    file and the reader rejects it.
    """
    dur = n.duration if duration is None else duration
    out = ["%s<note>" % pad]
    if chord_tone:
        out.append("%s  <chord/>" % pad)
    if n.is_rest:
        if measure_rest:
            # First-class form of a rest that fills the bar: no note value
            # for an importer to second-guess (an invented tuplet on a rest
            # is what MuseScore rejects with rc=40).
            out.append('%s  <rest measure="yes"/>' % pad)
        else:
            out.append("%s  <rest/>" % pad)
    else:
        out.append("%s  <pitch>" % pad)
        out.append("%s    <step>%s</step>" % (pad, n.pitch.step))
        if n.pitch.alter:
            out.append("%s    <alter>%d</alter>" % (pad, n.pitch.alter))
        out.append("%s    <octave>%d</octave>" % (pad, n.pitch.octave))
        out.append("%s  </pitch>" % pad)
    out.append("%s  <duration>%d</duration>" % (pad, dur))
    # Everything from here follows the MusicXML content model for <note>
    # exactly: duration, tie*, voice?, type?, dot*, time-modification?, staff?,
    # notations*. The order is not a style choice -- a validating importer
    # rejects the whole document if it is wrong, which is how emit was managing
    # to write files MuseScore called "not a valid MusicXML file" while every
    # number in them was correct. <tie> belongs after <duration> (it used to
    # precede <pitch>) and <voice> before <type> (it used to follow it).
    if n.tie:
        out.append('%s  <tie type="%s"/>' % (pad, n.tie))
    out.append("%s  <voice>%d</voice>" % (pad, n.voice))
    if not measure_rest:
        name, dots, actual, normal = _type_for(dur, n.tuplet)
        out.append("%s  <type>%s</type>" % (pad, name))
        for _ in range(dots):
            out.append("%s  <dot/>" % pad)
        if actual:
            out.append("%s  <time-modification>" % pad)
            out.append("%s    <actual-notes>%d</actual-notes>" % (pad, actual))
            out.append("%s    <normal-notes>%d</normal-notes>" % (pad, normal))
            out.append("%s  </time-modification>" % pad)
    out.append("%s  <staff>%d</staff>" % (pad, n.staff))
    if n.slur:
        # <notations> is the last thing before </note> in the MusicXML content
        # model; emitting it earlier makes strict importers reject the file.
        out.append("%s  <notations>" % pad)
        out.append('%s    <slur type="%s" number="1"/>' % (pad, n.slur))
        out.append("%s  </notations>" % pad)
    out.append("%s</note>" % pad)
    return out


DOCTYPE = ('<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 4.0 '
           'Partwise//EN" "http://www.musicxml.org/dtds/partwise.dtd">')


def emit_page(page: Page) -> str:
    """Render the graph as MusicXML with one consistent musical context."""
    out = ['%s\n<score-partwise version="4.0">' % DOCTYPE]
    work = ET.Element("work")
    ET.SubElement(work, "work-title").text = page.id
    out.append("  <work><work-title>%s</work-title></work>" % page.id)
    out.append("  <identification><encoding>"
               "<software>ScoreHalo graph</software></encoding>"
               "</identification>")
    out.append("  <part-list>")
    for i, part in enumerate(page.parts, start=1):
        out.append('    <score-part id="P%d">' % i)
        out.append("      <part-name>%s</part-name>" % part.name)
        # The part's NAME is data homr read off the page, so repeating it as an
        # instrument label is transcription, not invention. No <midi-instrument>
        # follows: choosing a program number would be guessing the sound, which
        # is the same class of error as guessing a clef -- see to_ly.
        out.append('      <score-instrument id="P%d-I1">' % i)
        out.append("        <instrument-name>%s</instrument-name>" % part.name)
        out.append("      </score-instrument>")
        out.append("    </score-part>")
    out.append("  </part-list>")
    clamped = 0
    # Every <part> of a <score-partwise> document must span the SAME number of
    # measures: parts sound simultaneously, so a part that stops early
    # misaligns everything after it. homr's fragments do not respect that -- one
    # system can be 4 bars while its neighbour is 2. MuseScore rejects the file
    # outright (rc=40); musicxml2ly accepts it and silently engraves a short
    # staff against a long one, which is worse because it looks like success.
    # Pad the short parts with silent measures, inheriting the meter/key/clef
    # in force at the end of the part so the pad cannot shift what follows.
    width = max((len(p.measures) for p in page.parts), default=0)
    for part in page.parts:
        if len(part.measures) >= width:
            continue
        if part.measures:
            last = part.measures[-1]
            beats, beat_type = last.beats, last.beat_type
            fifths, clef = last.key_fifths, last.clef
        else:
            beats, beat_type, fifths, clef = 4, 4, 0, ("G", 2)
        # Pad TRAILING bars only, by number: a part that deliberately starts
        # late (the singer tacet through the piano intro, so her staff is not
        # printed at all) must not be re-padded from bar 1 -- positional
        # padding here would append bars whose numbers duplicate what the
        # part already has.
        start = max((m.number for m in part.measures), default=0) + 1
        for n in range(start, width + 1):
            part.measures.append(Measure(
                number=n, divisions=GLOBAL_DIVISIONS, beats=beats,
                beat_type=beat_type, key_fifths=fifths, clef=clef,
                is_empty=True))
    for i, part in enumerate(page.parts, start=1):
        out.append('  <part id="P%d">' % i)
        voices = voice_map(part)
        # A part's clef is not fixed: fold its mid-part changes (if any) so that
        # a bar where one lands declares the clef now in force. The initial map
        # is `clef_by_staff`; each change replaces one staff's entry from its bar
        # onward. Without the fold the emitter had one clef per staff for the
        # whole part, so a piano written two-treble for the intro and bass for
        # the verse could only be published all-treble (losing the bass staff).
        changes: Dict[int, Dict[int, Tuple[str, int]]] = {}
        for number, staff, clef in getattr(part, "clef_changes", None) or []:
            changes.setdefault(number, {})[staff] = clef
        in_force = dict(getattr(part, "clef_by_staff", None) or {})
        in_meter: Optional[Tuple[int, int]] = None
        for j, meas in enumerate(part.measures):
            if meas.number in changes:
                in_force.update(changes[meas.number])
            change_here = meas.number in changes
            # A bar whose meter was lengthened to hold overrunning content
            # (scorehalo.meter.extend_overfull_bars) must restate <time> --
            # including in SILENT bars, so a tacet part's timeline stays
            # aligned with the part that overflowed.
            meter_now = (meas.beats, meas.beat_type)
            meter_changed = in_meter is not None and meter_now != in_meter
            in_meter = meter_now
            if meas.is_empty:
                out.append('    <measure number="%d">' % meas.number)
                if j == 0 or change_here or meter_changed:
                    # Only the first bar declares: <divisions> is mandatory
                    # there (every later duration is read against it) and is
                    # inherited after that. Restating <time>/<key>/<clef> in
                    # EVERY padded bar is what put a second 4/4 at the right
                    # end of the top system -- renderers print the signatures
                    # they are shown, and the engraver caught it. Meter
                    # changes are restated deliberately (see above).
                    out.extend(_attributes(meas, "      ", part, in_force))
                total = _bar_duration(meas)
                if len(in_force) > 1:
                    # A padded measure in a two-staff part needs a rest on EVERY
                    # staff. A single-staff rest leaves the other staff with no
                    # bar content at all, which importers read as a short bar.
                    #
                    # Each staff's rest covers the SAME span of time, so the
                    # cursor has to be rewound between them. Without the
                    # <backup> both rests land in voice 1 as one continuous
                    # 96-tick run and a strict reader reports the bar as twice
                    # its declared meter -- which is exactly what the validator
                    # said the first time a two-staff part reached this branch.
                    for k, number in enumerate(sorted(in_force)):
                        if k:
                            out.append("      <backup>")
                            out.append("        <duration>%d</duration>" % total)
                            out.append("      </backup>")
                        out.append('      <note><rest measure="yes"/>'
                                   "<duration>%d</duration>"
                                   "<voice>%d</voice><staff>%d</staff></note>"
                                   % (total, voices.get((number, 1), 1),
                                      number))
                else:
                    out.append("      <note><rest measure=\"yes\"/>"
                               "<duration>%d</duration>"
                               "<voice>1</voice></note>" % total)
                out.append("    </measure>")
                continue
            body, lost = _emit_measure(meas, "   ", part=part,
                                       voices=voices,
                                       with_attributes=(j == 0 or change_here
                                                        or meter_changed),
                                       clef_by_staff=in_force)
            out.extend(body)
            clamped += lost
        out.append("  </part>")
    out.append("</score-partwise>")
    xml = "\n".join(out) + "\n"
    emit_page.last_clamped = clamped
    return xml
