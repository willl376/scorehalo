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
from typing import List, Optional, Tuple

from .graph import Measure, Note, Part, Page, Pitch

#: LCM of the divisions homr actually emits (2, 4, 6) -- every fragment's
#: durations scale to a whole number of ticks, so nothing is ever rounded.
GLOBAL_DIVISIONS = 12

_TYPE = {"whole": 4.0, "half": 2.0, "quarter": 1.0, "eighth": 0.5,
         "16th": 0.25, "32nd": 0.125, "64th": 0.0625, "128th": 0.03125}

#: Duration in GLOBAL_DIVISIONS ticks -> (type name, dot count). A note whose
#: <type> disagrees with its <duration> is rejected by MuseScore, so the type is
#: derived from the number we actually write rather than assumed to be a quarter.
#: Generated from _TYPE at GLOBAL_DIVISIONS=12 (1 quarter = 12 ticks), so every
#: entry is exact: a dotted quarter is 18, a double-dotted half is 42.
_TYPE_FOR = {
    3: ("16th", 0),
    6: ("eighth", 0), 9: ("eighth", 1),
    12: ("quarter", 0), 18: ("quarter", 1), 21: ("quarter", 2),
    24: ("half", 0), 36: ("half", 1), 42: ("half", 2),
    48: ("whole", 0), 72: ("whole", 1), 84: ("whole", 2),
}

#: Tuplet subdivision at GLOBAL_DIVISIONS=12, in the order a reader tries them.
#: 12/3 = 4 (a 3:2 tuplet eighth, written "eighth"), 12/2 = 6 (a 2:3 tuplet
#: quarter, written "quarter"). One of these always lands on a representable
#: value for the durations homr emits, so the <type> can always be named.
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
    emits a few, e.g. 42) is named by the value at or above it that is closest,
    so the written note is never SHORTER than the space it occupies. A
    deliberately conservative choice: ``<duration>`` -- the number that governs
    playback, and the one we measured -- always states the truth, and the
    nominal length may overstate it.
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
    # Nearest value at or above the true length, so the written note is never
    # shorter than the ticks it fills.
    above = [t for t in _TYPE_FOR if t >= duration]
    ticks = min(above, key=lambda t: (t - duration, t)) if above else max(_TYPE_FOR)
    name, dots = _TYPE_FOR[ticks]
    return name, dots, 0, 0


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
            attrs = m.find("attributes")
            if attrs is not None:
                if attrs.findtext("divisions"):
                    divisions = int(attrs.findtext("divisions"))
                if attrs.find("time") is not None:
                    state["beats"] = int(attrs.findtext("time/beats") or 4)
                    state["beat_type"] = int(attrs.findtext("time/beat-type") or 4)
                    meas.beats, meas.beat_type = state["beats"], state["beat_type"]
                if attrs.find("key") is not None:
                    state["fifths"] = int(attrs.findtext("key/fifths") or 0)
                    meas.key_fifths = state["fifths"]
                if attrs.find("clef") is not None:
                    state["clef"] = (attrs.findtext("clef/sign") or "G",
                                     int(attrs.findtext("clef/line") or 2))
                    meas.clef = state["clef"]
                    # Remember which staff that clef belongs to, so a grand
                    # staff does not end up treble on both staves.
                    clef_staff = int(attrs.findtext("staff") or 1)
                    part.clef_by_staff[clef_staff] = state["clef"]
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
                        # A note carries its OWN <staff>; fall back to the part's
                        # index only when absent. Stamping staff_index here
                        # collapsed a piano's lower staff onto staff 1 and
                        # silently emitted a 2-staff part as one staff.
                        staff=int(el.findtext("staff") or staff_index),
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


def _attributes(meas: Measure, pad: str) -> List[str]:
    out = ["%s<attributes>" % pad,
           "%s  <divisions>%d</divisions>" % (pad, GLOBAL_DIVISIONS),
           "%s  <key><fifths>%d</fifths></key>" % (pad, meas.key_fifths),
           "%s  <time><beats>%d</beats><beat-type>%d</beat-type></time>"
           % (pad, meas.beats, meas.beat_type)]
    if meas.clef:
        out.append("%s  <clef><sign>%s</sign><line>%d</line></clef>"
                   % (pad, meas.clef[0], meas.clef[1]))
    out.append("%s</attributes>" % pad)
    return out


def _emit_measure(meas: Measure, indent: str, with_attributes: bool = False) -> List[str]:
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
        lines.extend(_attributes(meas, pad))
    groups: dict = {}
    for n in meas.notes:
        groups.setdefault((n.voice, n.onset), []).append(n)

    cursor = 0
    dropped = 0
    total = _bar_duration(meas)
    for voice in sorted({k[0] for k in groups}):
        if cursor > 0:
            lines.append("%s<backup>" % pad)
            lines.append("%s  <duration>%d</duration>" % (pad, cursor))
            lines.append("%s</backup>" % pad)
            cursor = 0
        for v, onset in sorted(k for k in groups if k[0] == voice):
            if onset >= total:
                # homr sometimes writes more note-duration into a bar than the
                # meter allows. The graph keeps every note; MusicXML cannot
                # express the overflow, so the projection stops at the barline
                # and reports the loss rather than emitting a file that no
                # importer will open.
                dropped += len(groups[(v, onset)])
                continue
            notes = groups[(v, onset)]
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
                lines.extend(_emit_note(n, pad, duration=dur,
                                        chord_tone=(i > 0)))
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


def _emit_note(n: Note, pad: str, duration: Optional[int] = None,
               chord_tone: bool = False) -> List[str]:
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
    if n.tie:
        out.append('%s  <tie type="%s"/>' % (pad, n.tie))
    if n.is_rest:
        out.append("%s  <rest/>" % pad)
    else:
        out.append("%s  <pitch>" % pad)
        out.append("%s    <step>%s</step>" % (pad, n.pitch.step))
        if n.pitch.alter:
            out.append("%s    <alter>%d</alter>" % (pad, n.pitch.alter))
        out.append("%s    <octave>%d</octave>" % (pad, n.pitch.octave))
        out.append("%s  </pitch>" % pad)
    out.append("%s  <duration>%d</duration>" % (pad, dur))
    name, dots, actual, normal = _type_for(dur, n.tuplet)
    out.append("%s  <type>%s</type>" % (pad, name))
    for _ in range(dots):
        out.append("%s  <dot/>" % pad)
    if actual:
        out.append("%s  <time-modification>" % pad)
        out.append("%s    <actual-notes>%d</actual-notes>" % (pad, actual))
        out.append("%s    <normal-notes>%d</normal-notes>" % (pad, normal))
        out.append("%s  </time-modification>" % pad)
    out.append("%s  <voice>%d</voice>" % (pad, n.voice))
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
        for n in range(len(part.measures) + 1, width + 1):
            part.measures.append(Measure(
                number=n, divisions=GLOBAL_DIVISIONS, beats=beats,
                beat_type=beat_type, key_fifths=fifths, clef=clef,
                is_empty=True))
    for i, part in enumerate(page.parts, start=1):
        out.append('  <part id="P%d">' % i)
        for j, meas in enumerate(part.measures):
            if meas.is_empty:
                out.append('    <measure number="%d">' % meas.number)
                out.extend(_attributes(meas, "      "))
                total = _bar_duration(meas)
                out.append("      <note><rest measure=\"yes\"/>"
                           "<duration>%d</duration>"
                           "<voice>1</voice></note>" % total)
                out.append("    </measure>")
                continue
            body, lost = _emit_measure(meas, "    ", with_attributes=(j == 0))
            out.extend(body)
            clamped += lost
        out.append("  </part>")
    out.append("</score-partwise>")
    xml = "\n".join(out) + "\n"
    emit_page.last_clamped = clamped
    return xml
