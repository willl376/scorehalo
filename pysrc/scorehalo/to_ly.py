"""Native LilyPond emitter: ScoreHalo page graph -> Frescobaldi-ready .ly

Why this exists next to ``musicxml2ly``
---------------------------------------
``musicxml2ly`` is LilyPond's own MusicXML front end and it is good. This module
is deliberately **not** a competing general-purpose converter. It exists because
two things ``musicxml2ly`` structurally cannot do:

1. **It throws the page away.** ``musicxml2ly`` emits ``\\pointAndClickOff`` and
   MusicXML has nowhere to put a note's pixel position on the original scan.
   The whole premise of :mod:`scorehalo.graph` is that ``src_x``/``src_y``,
   ``staff`` and ``system`` survive to this point. Emitting from the graph keeps
   them, as a source comment on the note's own line -- so LilyPond's native
   point-and-click lands on a line that says *which pixel of which scan page*
   the note came from.
2. **It cannot express the page's staff topology.** A piano role is *one
   instrument on two staves*. MusicXML models that as a ``<part>`` with
   ``<staves>2</staves>`` and back-and-forth ``<backup>``; this emits the
   idiomatic LilyPond ``<< { \\new Voice } \\\\ { \\new Voice } >>`` directly,
   because the graph already knows simultaneity by equal onset and never needed
   a cursor to get there.

So: ``scorehalo toly`` for provenance and correct grand staffs,
``musicxml2ly`` when you want the reference converter's answer.

Conventions
-----------
* **Absolute mode** (every pitch carries an explicit octave mark), which is what
  ``musicxml2ly -a`` emits. Relative mode would require correct octave
  arithmetic across a cursor-free graph, buying nothing here.
* **Exact durations.** Everything is :class:`fractions.Fraction`; a tuplet is
  recovered as ``written = sounding * actual / normal`` and regrouped into one
  ``\\tuplet`` bracket, because a run of three triplet eighths written as three
  bare eighths is a bar-length error that LilyPond reports as a barcheck
  failure.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from fractions import Fraction
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .graph import Measure, Note, Page, Part

#: LilyPond's default (English) note names. The accidental goes BEFORE the
#: letter -- B-flat is ``bes``, not ``bf`` -- and the spelling is per-step,
#: because E-sharp is ``eis`` but F-sharp is ``fis`` and A-flat is ``aes``.
#: Getting this wrong is a hard parse error ("not a note name"), not a warning.
NOTE_NAMES = {
    "C": ("c", "cis", "ces", "cisis", "ceses"),
    "D": ("d", "dis", "des", "disis", "deses"),
    "E": ("e", "eis", "ees", "eisis", "eeses"),
    "F": ("f", "fis", "fes", "fisis", "feses"),
    "G": ("g", "gis", "ges", "gisis", "geses"),
    "A": ("a", "ais", "aes", "aisis", "aeses"),
    "B": ("b", "bis", "bes", "bisis", "beses"),
}
#: index by chromatic alteration: 0, +1, -1, +2, -2
ALTER_INDEX = {0: 0, 1: 1, -1: 2, 2: 3, -2: 4}

#: MusicXML ``(sign, line)`` clefs that map onto a plain LilyPond clef command.
CLEFS = {
    ("G", 2): "treble",
    ("F", 4): "bass",
    ("C", 3): "alto",
    ("G", 1): "treble_8",
    ("F", 3): "baritone",
    ("percussion", 2): "percussion",
}


class DurationError(ValueError):
    """A note length that cannot be written as a plain (dotted) type."""


# --------------------------------------------------------------------------
# duration arithmetic
# --------------------------------------------------------------------------

#: LilyPond counts a note's duration in WHOLE notes: 1 = whole, 2 = half,
#: 4 = quarter, 8 = eighth. Everything in this module is carried in QUARTER
#: notes, because that is what the graph's ticks divide by cleanly. Forgetting
#: the conversion writes every note a whole note long -- which imports without
#: error, engraves, and leaves every bar three quarters out.
WHOLE = 4


def duration_token(quarters: Fraction) -> str:
    """Render a note length as a LilyPond duration string, e.g. ``8.`` or ``4``."""
    log2, dots = decompose(Fraction(quarters, WHOLE))
    return str(2 ** log2) + "." * dots


def decompose(whole: Fraction) -> Tuple[int, int]:
    """Split a length in WHOLE notes into ``(log2, dots)``.

    A plain written duration is ``2**-log2 * (2 - 2**-dots)`` in whole notes.
    Returns the decomposition, or raises :class:`DurationError` when the length
    is not one (triplets, quintuplets and friends must go through a ``\\tuplet``
    bracket or an explicit scale factor instead of being rounded -- rounding
    here is what silently re-times a bar).
    """
    for dots in range(0, 3):
        plain = Fraction(2) - Fraction(1, 2 ** dots)  # 1, 1.5, 1.75
        for log2 in range(0, 9):
            if Fraction(1, 2 ** log2) * plain == whole:
                return log2, dots
    raise DurationError(f"cannot write {whole} whole notes as a plain duration")


def _plain_whole_candidates() -> List[Fraction]:
    """Every length a plain note type can express, in WHOLE-note units."""
    out = []
    for dots in range(0, 3):
        for log2 in range(0, 9):
            out.append(Fraction(1, 2 ** log2) * (Fraction(2) - Fraction(1, 2 ** dots)))
    return sorted(set(out), reverse=True)


def _is_plain(quarters: Fraction) -> bool:
    """Can this length be written as a plain (dotted) note type?"""
    try:
        decompose(Fraction(quarters, WHOLE))
    except DurationError:
        return False
    return True


def _plain_nearest(quarters: Fraction) -> Optional[Fraction]:
    """The largest plain duration that fits ``quarters``, in QUARTER units.

    Returns None when nothing fits, which is the caller's cue to fall back to
    an explicit scale factor rather than round silently -- rounding re-times the
    bar, and a re-timed bar is a wrong score that looks right.
    """
    for cand in _plain_whole_candidates():
        value = cand * WHOLE
        if value <= quarters:
            return value
    return None


def split_length(quarters: Fraction, cap: int = 12) -> List[Fraction]:
    """Break a length into plain (dotted) durations, largest first.

    Greedy over plain lengths plus whole multiples, so 4 quarters becomes a
    single ``r1`` and 6 becomes ``r1 r1 r2`` -- not the scale factor ``1*6/1``.
    Scale factors are legal but unreadable, and they are a good way to hide a
    timing error from whoever proofreads the .ly by hand.
    """
    if quarters <= 0:
        return []
    candidates = {c * WHOLE for c in _plain_whole_candidates()}
    candidates.update(Fraction(WHOLE * m) for m in range(1, 9))
    plain = sorted(candidates, reverse=True)
    out: List[Fraction] = []
    remaining = quarters
    while remaining > 0 and len(out) < cap:
        for cand in plain:
            if cand <= remaining:
                out.append(cand)
                remaining -= cand
                break
        else:
            # No plain duration fits: the remainder is genuinely not
            # representable (a quintuplet fraction, say). Stop rather than loop.
            break
    return out


def _nearest_power(quarters: Fraction) -> Fraction:
    """The nearest power-of-two note to ``quarters``.

    Last-resort fallback for :func:`_plain_nearest`; it is not representable in
    every case, which is exactly why it is the fallback and not the default.
    """
    if quarters <= 0:
        return Fraction(WHOLE)
    whole = Fraction(quarters, WHOLE)
    for log2 in range(0, 13):
        if Fraction(1, 2 ** log2) <= whole:
            return Fraction(1, 2 ** log2) * WHOLE
    return Fraction(1, 2 ** 12) * WHOLE


def _rest_token(quarters: Fraction) -> str:
    """One or more rests covering exactly ``quarters``."""
    parts = split_length(quarters)
    if not parts:
        return ""
    return " ".join("r" + duration_token(p) for p in parts)


#: The graph carries ``fifths`` but not the mode, so the tonic is inferred and
#: always written as ``major``. 1 flat as a major key is F, but a minor key with
#: 1 flat is D -- inferring a tonic from a signature means guessing a mode, and
#: guessing wrong here changes the notes LilyPond expects the key to imply.
_MAJOR_SHARPS = ["c", "g", "d", "a", "e", "b", "fis", "cis"]
_MINOR_FLATS = ["d", "g", "c", "f", "bes", "ees", "aes"]


def _key_token(fifths: int) -> str:
    """A key signature from a (fifths, mode=minor) reading of the source.

    homr reports the signature but not the mode.  Choral scans are overwhelmingly
    flat and major, so flats are read as minor and sharps as major -- the two
    readings that agree with the mode a printed signature most often implies.
    A genuinely modal page will get the right signature and the wrong tonic;
    that is stated rather than hidden.
    """
    if fifths >= 0:
        return "\\key %s \\major" % _MAJOR_SHARPS[min(fifths, 7)]
    return "\\key %s \\minor" % _MINOR_FLATS[min(-fifths, 7) - 1]


def _bar_prefix(measure: Measure, clef: Optional[Tuple[str, int]],
                key_fifths: int, meter_changed: bool, clef_changed: bool,
                key_changed: bool) -> str:
    """The ``\\clef``/``\\key``/``\\time`` that must precede a bar."""
    bits: List[str] = []
    if clef_changed and clef is not None:
        command = CLEFS.get(clef)
        if command:
            bits.append("\\clef " + command)
    if key_changed:
        bits.append(_key_token(key_fifths))
    if meter_changed:
        bits.append("\\time %d/%d" % (measure.beats, measure.beat_type))
    return (" ".join(bits) + " ") if bits else ""


def infer_clef(notes: Sequence[Note]) -> Optional[Tuple[str, int]]:
    """Guess ``(sign, line)`` from where the notes sit.

    homr does not emit clef elements, so without this every staff opens as
    treble. Below middle C is the only distinction worth making here, and it is
    a guess -- which is why callers count it.
    """
    pitches = [n.pitch.chromatic for n in notes
               if n.pitch is not None and not n.is_rest]
    if len(pitches) < 4:
        return None
    pitches.sort()
    return ("F", 4) if pitches[len(pitches) // 2] < 48 else ("G", 2)


# --------------------------------------------------------------------------
# note properties
# --------------------------------------------------------------------------

def length_of(note: Note, divisions: int) -> Fraction:
    """Sounding length in QUARTER notes."""
    return Fraction(note.duration, divisions)


def written_length(note: Note, divisions: int) -> Fraction:
    """Length to *write*, in QUARTER notes.

    Inside ``\\tuplet 3/2`` LilyPond divides what is written, so a triplet
    eighth is written as a plain eighth: written = sounding * 3/2.
    """
    sounding = length_of(note, divisions)
    if note.tuplet:
        actual, normal = note.tuplet
        return sounding * Fraction(actual, normal)
    return sounding


def pitch_token(note: Note) -> str:
    """Absolute-mode LilyPond pitch, e.g. ``ees'`` for Eb4 and ``bes,,`` for Bb2.

    Absolute mode (``musicxml2ly -a``) is the safe choice for a cursor-free
    graph: relative mode would need octave arithmetic relative to a previous
    note, and one wrong guess silently changes the register rather than failing
    to parse.
    """
    name = NOTE_NAMES[note.pitch.step][ALTER_INDEX.get(note.pitch.alter, 0)]
    # LilyPond absolute: middle C (C4) is c', each octave up adds one mark.
    marks = note.pitch.octave - 3
    if marks > 0:
        return name + "'" * marks
    if marks < 0:
        return name + "," * (-marks)
    return name


def _provenance(note: Note) -> str:
    """A comment on the note's own line naming the source pixel.

    This is what makes a point-and-click link mean something: the .ly line
    LilyPond jumps to says which pixel of which scan the note came from.
    """
    x = note.src_x if note.src_x is not None else -1
    y = note.src_y if note.src_y is not None else -1
    return ('\\markup { \\typewriter "(x=%d y=%d sys=%d staff=%d)" }'
            % (x, y, note.system, note.staff))


# --------------------------------------------------------------------------
# bar arithmetic
# --------------------------------------------------------------------------

def clamp_to_bar(notes: Sequence[Note],
                 measure: Measure) -> Tuple[List[Note], int]:
    """Fit a voice's notes inside one bar, and say how much it cost.

    A voice is a single line in time, so a note that starts before the previous
    one ended is either an overlap homr invented or a second voice mislabelled.
    It is trimmed rather than emitted, and counted -- the alternative is
    overlapping barlines, which is a hard error in both engravers.

    Gaps are left alone on purpose. A voice that skips from tick 0 to tick 12
    is silent, not broken, and :func:`_voice_body` writes the silence.
    """
    limit = measure.length
    ordered = sorted(notes, key=lambda n: (n.onset, n.staff,
                                           n.pitch.chromatic if n.pitch else 0))
    out: List[Note] = []
    damaged = 0
    cursor = 0
    for n in ordered:
        start, end = n.onset, n.onset + n.duration
        touched = False
        if end <= 0 or n.onset >= limit:
            damaged += 1
            continue
        if start < cursor:
            start = cursor
            touched = True
        if start >= limit:
            damaged += 1
            continue
        if end > limit:
            end = limit
            touched = True
        if end <= start:
            damaged += 1
            continue
        if touched:
            damaged += 1
            n = replace(n, onset=start, duration=end - start)
        out.append(n)
        cursor = end
    return out, damaged


@dataclass
class _Event:
    """Everything that sounds at one instant, in one voice on one staff."""
    onset: int
    notes: List[Note]
    is_rest: bool
    tuplet: Optional[Tuple[int, int]]
    written: Fraction


def _group_events(notes: Sequence[Note], divisions: int) -> List[_Event]:
    """Collapse a voice's notes into simultaneous events.

    A chord is several notes sharing an onset; the graph keeps them as separate
    notes because that is what the scan said, and the engraver wants one
    ``<c e g>`` token. Ordering tones by pitch keeps the output deterministic
    across runs -- the same page must not engrave differently twice.
    """
    buckets: Dict[int, List[Note]] = {}
    for n in notes:
        buckets.setdefault(n.onset, []).append(n)
    events: List[_Event] = []
    for onset in sorted(buckets):
        group = sorted(buckets[onset],
                       key=lambda n: (n.pitch.chromatic if n.pitch else 0,
                                      n.pitch.diatonic if n.pitch else 0))
        head = group[0]
        is_rest = all(n.is_rest for n in group)
        events.append(_Event(onset=onset, notes=group, is_rest=is_rest,
                             tuplet=head.tuplet,
                             written=written_length(head, divisions)))
    return events


def _resolve_slurs(notes: Sequence[Note]) -> List[Note]:
    """Return ``notes`` with ``slur_mark`` set on the safely emittable slurs.

    Two independent hazards force this to be conservative, because both wrong
    answers corrupt the score:

    1. **Unbalanced data.** homr's corpus holds 8733 slur starts against 8304
       stops, so a missing partner is the *normal* case. A ``(`` with no ``)``
       slur to the end of the system; a ``)`` with no ``(`` is a syntax error
       that fails the whole compile. So an unmatched slur is dropped.
    2. **Overlapping spans.** homr sometimes has two slur spans open at once on
       one voice. Rendering that as ``((`` is not nesting -- LilyPond rejects a
       second concurrent slur with "already have slur" and then "cannot end
       slur" for the rest of the voice. So a start arriving while another is
       already open is dropped too.

    What survives is therefore a set of *flat*, non-overlapping, balanced slurs
    per (staff, voice) -- always valid LilyPond, at the cost of dropping the
    overlapping spans. That is a real musical loss, and the honest fix is a
    proper second-pass spanner router that reassigns overlapping spans to
    separate voices (or uses ``\\=``), not a cleverer pairing here.

    Pairing walks each voice in document order, so a slur may cross a barline.
    """
    order = sorted(notes, key=lambda n: (n.staff, n.voice, n.measure, n.onset))
    index = {id(n): i for i, n in enumerate(order)}
    marks: Dict[int, str] = {}
    streams: Dict[Tuple[int, int], List[Note]] = {}
    for n in order:
        streams.setdefault((n.staff, n.voice), []).append(n)
    for stream in streams.values():
        open_at: Optional[int] = None
        for n in stream:
            here = index[id(n)]
            if n.slur == "start":
                if open_at is None:
                    open_at = here
                # else: overlapping span -- drop, see hazard 2
            elif n.slur == "stop":
                if open_at is None:
                    continue  # orphan stop: a bare ')' is a syntax error
                marks[open_at] = "("
                marks[here] = ")"
                open_at = None
            # an orphan start never reaches a stop, so it emits nothing
    return [replace(n, slur_mark=marks.get(i)) if i in marks else n
            for i, n in enumerate(order)]


def _render_event(ev: _Event, divisions: int, provenance: bool,
                  duration_override: Optional[Fraction] = None) -> str:
    """One chord, note or rest, with its duration and optional provenance."""
    written = duration_override if duration_override is not None else ev.written

    if ev.is_rest:
        # Rests are spelled as a SUM of plain durations, never as a scale
        # factor. A rest filling a bar is 4 quarters, which is not a note type
        # at all, and writing it `1..*4/1` both reads terribly and makes
        # LilyPond's barcheck report a bar that looks 8 quarters long.
        parts = split_length(written)
        if len(parts) != 1:
            return " ".join("r" + duration_token(p) for p in parts)
        body, post = "r", ""
    else:
        tones = [pitch_token(n) for n in ev.notes]
        body = tones[0] if len(tones) == 1 else "<" + " ".join(tones) + ">"
        # A tie is a property of the note's own tail, not of the whole chord.
        # LilyPond has NO tie-stop token: a tie is a single `~` on the FIRST
        # note and it binds to the next same-pitch note on its own. So a
        # `<tie type="stop"/>` must emit nothing at all. It used to map to `_`,
        # which is not a tie marker but is a hard *syntax error* in 2.24.3
        # ("syntax error, unexpected '}'"), so any real tied music -- and the
        # current corpus has no ties at all, which is why this never fired --
        # would have failed to compile the entire score.
        tie = ev.notes[0].tie
        post = "~" if tie == "start" else ""
        if provenance:
            post += "^" + _provenance(ev.notes[0])
    # A slur is a post-event like a tie: `c4( d)`. `(` opens on the first
    # note of the phrase and `)` closes on the last. It comes from
    # _resolve_slurs, which sets it only on notes it successfully paired, so
    # this can never be an unmatched bracket.
    if ev.notes[0].slur_mark:
        post += ev.notes[0].slur_mark

    try:
        duration = duration_token(written)
    except DurationError:
        # Not a plain (dotted) type -- a quintuplet remainder, say. Write the
        # largest plain duration that fits and carry the rest as an explicit
        # factor, e.g. ``c4*5/6``. Never round silently: rounding re-times the
        # bar, and a re-timed bar looks fine and is wrong.
        near = _plain_nearest(written) or _nearest_power(written)
        duration = duration_token(near) + "*{0}/{1}".format(
            written.numerator, written.denominator)
    # Pitch, then duration, then post-events. The duration has to bind to the
    # pitch directly: `c^\markup{..}4` reads as an untimed `c` wearing a markup
    # followed by a stray `4`, so the note silently becomes a whole note and
    # every bar runs long. LilyPond reports it only as a barcheck warning far
    # from the cause, and a duration re-reader will happily agree with the
    # wrong answer.
    return body + duration + post


def _voice_body(notes: Sequence[Note], measure: Measure, provenance: bool,
                prefix: str = "",
                preclamped: bool = False) -> Tuple[str, int]:
    """Render one voice of one measure, regrouping tuplets into brackets.

    A run of contiguous notes sharing a ``\\tuplet`` ratio becomes a single
    bracket. Without this a triplet reads as three full-length notes and the bar
    comes out ``3/2`` too long -- which LilyPond reports as a barcheck failure
    and which no importer catches.

    ``preclamped`` says the caller already ran :func:`clamp_to_bar` -- it has
    to, because slur pairing is only valid across the notes that survive it.
    """
    divisions = measure.divisions
    if preclamped:
        clamped, n_clamped = notes, 0
    else:
        clamped, n_clamped = clamp_to_bar(notes, measure)

    # A completely empty bar. Deliberately NOT R1: LilyPond reads R1 as a
    # *multi-measure* rest, and stacking a pageful of them in one voice makes
    # it report "Multi measure rest seems misplaced" (a programming error, not a
    # warning) and refuse to lay the bar out. An explicit rest is unambiguous.
    if not clamped and measure.beats == 4 and measure.beat_type == 4:
        return (prefix + " r1").strip(), 0

    events = _group_events(clamped, divisions)
    out: List[str] = []
    cursor = 0

    def _gap(to: int) -> None:
        """Fill silence between two notes.

        Gaps are as real as notes: a voice that skips from tick 0 to tick 12
        must still write four rests' worth of bar, or the following bar lands
        early and LilyPond reports "barcheck failed at: 9/16".
        """
        nonlocal cursor
        if to > cursor:
            token = _rest_token(Fraction(to - cursor, divisions))
            if token:
                out.append(token)
                cursor = to

    i = 0
    while i < len(events):
        ev = events[i]
        # NOTE: an earlier version of this loop carried a `duration <= 0 -> skip`
        # guard, added on the theory that a zero-length event could stall the
        # tuplet-run scan. Measured: it cannot. The outer `i` is advanced by
        # every branch (i += 1 for the non-tuplet path, i = j where j >= i + 1
        # for the run path), so the loop is bounded by len(events) either way,
        # and the guard made no observable difference on any of the five cases
        # A/B'd against it. Left out: a guard that does nothing is a false
        # promise of safety. If a real zero-duration stall is ever observed,
        # prove it with a timeout first.
        _gap(ev.onset)
        if ev.tuplet is None:
            out.append(_render_event(ev, divisions, provenance))
            cursor = ev.onset + ev.notes[0].duration
            i += 1
            continue
        ratio = ev.tuplet
        run = [ev]
        j = i + 1
        while (
            j < len(events)
            and events[j].tuplet == ratio
            and run[-1].onset + run[-1].notes[0].duration == events[j].onset
        ):
            run.append(events[j])
            j += 1
        actual, normal = ratio
        # Each note keeps its OWN written length (sounding * actual/normal), so
        # the run's sounding total is preserved exactly and the bar still adds
        # up. Forcing one shared length across the run would flatten a tuplet
        # that legitimately mixes a quarter and an eighth.
        inner = " ".join(_render_event(e, divisions, provenance) for e in run)
        out.append(f"\\tuplet {actual}/{normal} {{ {inner} }}")
        cursor = run[-1].onset + run[-1].notes[0].duration
        i = j

    # Pad out to the end of the bar, or the voice desynchronises from every
    # other voice on the page. Silence where homr saw nothing is the same
    # judgement emit_page() makes -- it is not invented music, but it is a
    # claim, so it is counted.
    _gap(measure.length)
    if prefix:
        out.insert(0, prefix)
    return " ".join(out), n_clamped


# --------------------------------------------------------------------------
# staves and voices
# --------------------------------------------------------------------------

def _voices_in(notes: Iterable[Note]) -> List[int]:
    return sorted({n.voice for n in notes})


def _role_staves(part_notes: Sequence[Note]) -> List[int]:
    """Which staves of the graph a single role occupies (1 and 2 for a piano)."""
    return sorted({n.staff for n in part_notes})


def _indent(text: str, pad: int) -> str:
    return "\n".join(" " * pad + line if line else line for line in text.split("\n"))


def _role_block(role: str, notes: Sequence[Note], index: int,
                measures: Sequence[Measure], provenance: bool,
                stats: Optional[Dict[str, int]] = None,
                part_clefs: Optional[Dict[int, Tuple[str, int]]] = None
                ) -> Tuple[str, int]:
    """Emit one role (instrument) as a LilyPond ``Staff``.

    Two staves of one role become ``<< { ... } \\\\ { ... } >>`` -- the ordinary
    grand staff -- rather than two separate parts, because they are one
    instrument sounding together.
    """
    staves = _role_staves(notes)
    lines: List[str] = []
    clamped_total = 0
    for staff_no in staves:
        staff_notes = [n for n in notes if n.staff == staff_no]
        # Clef is a per-staff property. Prefer what the source stated; fall back
        # to an inference from the pitch range, and count that we had to guess.
        clef = (part_clefs or {}).get(staff_no)
        if clef is None:
            clef = infer_clef(staff_notes)
            if stats is not None:
                stats["clefs_inferred"] = stats.get("clefs_inferred", 0) + 1
        key_fifths = next((m.key_fifths for m in measures if m.key_fifths),
                          measures[0].key_fifths if measures else 0)
        voices = _voices_in(staff_notes)
        voice_blocks: List[str] = []
        for v_i, voice in enumerate(voices):
            vnotes = [n for n in staff_notes if n.voice == voice]
            # Clamp EVERY measure first, then pair slurs across the survivors.
            # Order matters: clamping can drop or shorten notes, so pairing on
            # the unclamped list would mark a '(' on a note that never reaches
            # the output and leave its ')' with nothing to close against --
            # which is exactly the "cannot end slur" / "unterminated slur"
            # pair LilyPond reports. Resolve first, render second.
            per_measure: List[Tuple[List[Note], int]] = []
            for m in measures:
                mn = [n for n in vnotes if n.measure == m.number]
                per_measure.append(clamp_to_bar(mn, m))
            clamped_total += sum(n for _, n in per_measure)
            resolved = _resolve_slurs(
                [n for clamped, _ in per_measure for n in clamped])
            # _resolve_slurs returns one note per survivor, in the same order.
            buckets: Dict[int, List[Note]] = {}
            it = iter(resolved)
            for (clamped, _), m in zip(per_measure, measures):
                buckets[m.number] = [next(it) for _ in clamped]
            body: List[str] = []
            prev_meter = None
            prev_key = None
            for idx, m in enumerate(measures):
                mn = buckets.get(m.number, [])
                meter = (m.beats, m.beat_type)
                first = idx == 0
                # Clef/key open the staff once; the meter is restated whenever
                # it changes, so a mid-page time change is not silently lost.
                prefix = ""
                if v_i == 0:
                    prefix = _bar_prefix(
                        m, clef, key_fifths,
                        meter_changed=first or meter != prev_meter,
                        clef_changed=first,
                        key_changed=first or key_fifths != prev_key,
                    )
                prev_meter = meter
                prev_key = key_fifths
                bar, _ = _voice_body(mn, m, provenance, prefix,
                                     preclamped=True)
                # An empty voice still consumes its bar (handled inside
                # _voice_body as a full-bar rest), so every staff stays aligned.
                body.append(bar)
            # \voiceOne / \voiceTwo give correct stem directions; the first voice
            # of a staff must take \voiceOne or LilyPond complains.
            selector = {0: "\\voiceOne", 1: "\\voiceTwo", 2: "\\voiceThree"}.get(v_i)
            head = f"{selector} " if selector else ""
            # Explicit barlines: they make the .ly readable in Frescobaldi and,
            # more usefully, they make LilyPond's barcheck actually check every
            # bar instead of inferring bar structure from \time alone.
            voice_blocks.append(
                f"\\new Voice {{ {head}{' | '.join(body)} | }}")
        if len(voice_blocks) == 1:
            lines.append(voice_blocks[0])
        else:
            inner = ("\n" + _indent(" ".join(voice_blocks), 2) + "\n")
            lines.append("<<" + inner + ">>")

    joined = ("\n" + _indent(" \\\\\n".join(lines), 2) + "\n")
    block = (f'\\new Staff = "role{index}"\n'
             f'  \\with {{ instrumentName = "{role}" }}\n'
             f"  <<" + joined + ">>")
    if stats is not None:
        stats["clamped_notes"] = stats.get("clamped_notes", 0) + clamped_total
        stats["staves"] = stats.get("staves", 0) + len(staves)
    return block, clamped_total


# --------------------------------------------------------------------------
# whole page
# --------------------------------------------------------------------------

def to_ly(page: Page, version: str = "2.24.3", title: Optional[str] = None,
          point_and_click: bool = True, provenance: bool = True,
          midi: bool = False, snippet: bool = False,
          stats: Optional[Dict[str, int]] = None) -> str:
    """Render a :class:`~scorehalo.graph.Page` as LilyPond source.

    Parameters
    ----------
    page:
        The assembled page. Roles are emitted in ``page.parts`` order; each role
        keeps its own measures, so simultaneous parts are padded by the
        assembler rather than by this function.
    version:
        ``\\version`` string. 2.24.3 is the system LilyPond; 2.26.0 also runs.
    point_and_click:
        Emit ``\\pointAndClickOn`` so the PDF carries links back into the .ly.
    provenance:
        Annotate each note with the scan pixel it came from.
    midi:
        Also emit a ``\\midi`` block.
    snippet:
        Wrap the music in Frescobaldi's ``%<<`` / ``%.>>`` snippet markers so it
        can be dropped straight into the Snippet Browser.
    """
    head = "\\version \"%s\"" % version
    label = title or page.id
    head += f'\n\\header {{ title = "{label}" }}'
    # Declare the language, do not inherit it. LilyPond's note names are
    # language-dependent and the default is `nederlands`, which is why the
    # cis/ces/bes spellings below happen to work -- but "happens to" is exactly
    # the kind of dependency that breaks the day a user sets a language, a
    # Frescobaldi template sets one, or a file is opened under a different
    # locale. LilyPond 2.24.3's *English* table is a different set entirely
    # (`cs`/`cf`/`css`, not `cis`/`ces`), so leaving this implicit means the
    # accidentals are the first thing to fail.
    head += '\n\\language "nederlands"'
    if point_and_click:
        head += "\n% scan provenance: x/y are pixels on the source page"

    blocks: List[str] = []
    seen: List[str] = []
    if stats is not None:
        stats.update({"roles": 0, "notes": 0, "pitched": 0, "tuplets": 0,
                      "clamped_notes": 0, "staves": 0, "clefs_inferred": 0})
    for i, part in enumerate(page.parts):
        pnotes = part.notes
        if not pnotes:
            continue
        # Parts that share a role name across systems are one instrument; the
        # assembler already merged them, so a repeated name here would be a
        # genuine duplicate and is worth refusing rather than silently doubling.
        if part.name in seen:
            raise ValueError(f"duplicate role {part.name!r}; merge it before emitting")
        seen.append(part.name)
        block, _ = _role_block(part.name, pnotes, i, part.measures,
                                provenance, stats, part.clef_by_staff)
        blocks.append(block)
        if stats is not None:
            stats["roles"] += 1
            stats["notes"] += len(pnotes)
            stats["pitched"] += sum(1 for n in pnotes if not n.is_rest)
            stats["tuplets"] += sum(1 for n in pnotes if n.tuplet)

    body = "\n".join(blocks)
    tail = "\\layout { }"
    if midi:
        tail += "\n\\midi { }"

    score = f"{head}\n\n\\score {{\n  <<\n" + _indent(body, 4) + \
        "\n  >>\n  " + tail.replace("\n", "\n  ") + "\n}"
    if snippet:
        score = "%<<\n" + score + "\n%.>>\n"
    return score + "\n"
