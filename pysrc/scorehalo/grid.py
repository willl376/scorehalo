"""Fit every system of a piece onto ONE staff grid, bottom-aligned.

Why this module exists
----------------------
A printed song changes how many staves it uses from system to system. "We've
Only Just Begun" (Carpenters Gold Songbook, p.20) opens with the piano alone on
**two** staves and then adds the singer on top for **three** on every system
after that -- the book prints 2 + 3 + 3, which is one piece of music laid out on
a 3-row grid, not three pieces of two, three and three rows.

Recognising each system on its own and then trusting its internal grouping gets
this wrong, and it gets it wrong *quietly*. homr pairs the staves of one system
by CLEF, so a voice-on-top page -- clefs ``(G, G, F)`` -- is split into a
two-staff "Piano" (the two G staves: the voice and the right hand folded
together) and a one-staff "Voice" (the F staff: the actual left hand). The bass
line ends up in the wrong part with a part name that says it is a singer, and
the piano's own lower staff never gets the bass clef that homr did read.

The fix is the one a notation program uses when you set the page up by hand:
scan the whole piece first, find the LARGEST number of staves any system uses,
and lay every system out on that many rows -- aligned to the BOTTOM, because a
song grows upwards (the accompaniment stays put and the voice is added above).
The piano is then always the bottom rows, the singer always the top row, and
the bass clef homr detected stays attached to the staff it belongs to.

The staff COUNT says how tall the grid is; it does not say how much music sits
on each line. So alongside the shape this module counts the BEATS that add up
on every staff line -- the sum of the note values per bar, the longest voice
winning so a chord or a second voice is not double-counted. Two staff lines of
the same printed height can carry very different amounts: the singer is tacet
through the piano intro and enters on the last systems, and the count shows
that as a smaller total rather than as a missing row. The count also names the
bars that do not fill the meter, which is the honest way to tell a real pickup
or an under-read bar from a fully-read one.

This module decides *nothing* about the notes. It only re-seats whole staves
onto rows and counts what they carry, and it reports where the pieces disagree
rather than inventing a clef or a bar to hide it.
"""

from dataclasses import replace
from typing import Dict, List, Optional, Sequence, Tuple

from .emit import GLOBAL_DIVISIONS
from .graph import Measure, Note, Page, Part

Clef = Tuple[str, int]

#: Conventional name for the braced group at the bottom of the grid. Pixels do
#: not say "Piano" -- they say "a brace over k staves". Rename per corpus.
ACCOMPANIMENT_NAME = "Piano"

#: Name for a lone row above the accompaniment.
SOLO_NAME = "Voice"

#: Clef assumed for a row no system ever printed, and the fallback when a row's
#: clef disagrees across systems and the caller does not override it.
DEFAULT_CLEF: Clef = ("G", 2)


def split_part_by_staff(part: Part) -> List[Part]:
    """One single-staff Part per staff of ``part``, in staff order.

    homr writes a grand staff as ONE ``<part>`` with ``<staves>2</staves>`` and
    ``<staff>1|2</staff>`` on the notes. The grid works in single staves, so the
    part is split at the staff boundary; the clef for each staff comes from
    ``clef_by_staff`` when the caller has it and defaults to treble otherwise.

    A note with no ``<staff>`` counts as staff 1 -- that is what MusicXML means,
    and it is also what homr's single-staff fragments carry.
    """
    staves = {n.staff for n in part.notes}
    staves |= set(part.clef_by_staff) or {1}
    out: List[Part] = []
    for staff in sorted(staves):
        measures: List[Measure] = []
        for meas in part.measures:
            # The staff is renumbered to 1 because the result is a single-staff
            # part: leaving `staff=2` on the notes would make a part whose only
            # staff is 1 carry notes on a staff it does not declare.
            notes = [replace(n, staff=1) for n in meas.notes if n.staff == staff]
            measures.append(replace(meas, notes=notes))
        clef = part.clef_by_staff.get(staff, DEFAULT_CLEF)
        out.append(Part(
            id="%s.s%d" % (part.id, staff),
            name=part.name,
            system=part.system,
            staff_index=staff,
            measures=measures,
            clef_by_staff={1: clef},
        ))
    return out


def system_rows(parts: Sequence[Part]) -> List[Part]:
    """The printed staves of one system, single-staff, in reading order.

    A system's rows are its parts read top to bottom, and the staves inside each
    part in staff order. homr emits parts in the order it met them, which for a
    system it recognised as a whole is top to bottom, so concatenating is what
    the page shows. The assumption is stated here rather than buried because it
    is the one thing this module cannot check from the fragments alone.
    """
    rows: List[Part] = []
    for part in parts:
        rows.extend(split_part_by_staff(part))
    return rows


# ---------------------------------------------------------------------------
# Beat accounting: how many beats each printed staff line actually carries.
#
# The grid above answers "how many staves?"; this answers "how much music on
# each of them?" A staff count is a SHAPE, a beat count is CONTENT, and the two
# disagree in exactly the cases that matter: a row only some systems print (the
# singer's, absent through the piano intro) and a row that is printed but
# silent. Counting beats per stave line makes that difference visible instead
# of inferring it from the height of the system.
# ---------------------------------------------------------------------------


def _bar_fill(meas: Measure) -> int:
    """Ticks of sounding music in one bar, the LONGEST voice winning.

    A chord is several notes at one onset: it fills one span, not one per note,
    so the span is the longest of them. Two voices each filling the bar are
    still one bar of music, so the bar's fill is the longest voice rather than
    the sum -- summing them would read a two-voice bar as twice its meter.
    """
    by_voice: Dict[int, Dict[int, int]] = {}
    for n in meas.notes:
        if n.is_rest:
            continue
        span = by_voice.setdefault(n.voice, {})
        if n.duration > span.get(n.onset, 0):
            span[n.onset] = n.duration
    if not by_voice:
        return 0
    return max(sum(span.values()) for span in by_voice.values())


def _row_fill(row: Part) -> int:
    """Ticks of sounding music on one staff row, all its bars."""
    return sum(_bar_fill(m) for m in row.measures)


def staff_beat_table(rows_by_system: Sequence[Sequence[Part]],
                     divisions: int = GLOBAL_DIVISIONS) -> List[List[Dict[int, float]]]:
    """Beats per bar on each printed staff line, per system.

    The shape mirrors ``rows_by_system``: ``table[system][row]`` is
    ``{bar: beats}`` in reading order (top row first). This reads the ORIGINAL
    fragments, so the whole-measure rests the grid pads a tacet staff with are
    not in it -- a bar absent from the table was absent from the page, and that
    is a different fact from a bar of zero beats.
    """
    return [[{m.number: _bar_fill(m) / divisions for m in row.measures}
             for row in rows] for rows in rows_by_system]


def beat_totals(rows_by_system: Sequence[Sequence[Part]],
                divisions: int = GLOBAL_DIVISIONS) -> List[float]:
    """Total beats each GRID row carries, indexed from the bottom.

    Bottom-aligned like everything else here: ``[0]`` is the lowest printed
    staff. A system that does not print a row adds nothing to it, so a solo row
    that enters only after an intro sums to fewer beats than a full row -- the
    honest reading, and the reason this is REPORTED rather than used to pick
    the grid on the reader's behalf.
    """
    total, _ = grid_shape(rows_by_system)
    totals = [0.0] * total
    for rows in rows_by_system:
        for i, row in enumerate(rows):
            from_bottom = len(rows) - 1 - i
            totals[from_bottom] += _row_fill(row) / divisions
    return totals


def content_rows(rows_by_system: Sequence[Sequence[Part]],
                 divisions: int = GLOBAL_DIVISIONS) -> int:
    """The largest number of rows that carry ANY beats in a single system.

    ``grid_shape`` counts rows a system PRINTS; this counts rows it SINGS on.
    A staff can be printed and be silent -- a tacet line, or a recogniser that
    seated nothing -- and the two counts differ exactly then. Reported so the
    grid can be trusted knowingly rather than assumed.
    """
    return max((sum(1 for r in rows if _row_fill(r) > 0)
                for rows in rows_by_system), default=0)


def beat_mismatches(rows_by_system: Sequence[Sequence[Part]],
                    divisions: int = GLOBAL_DIVISIONS, beats: int = 4,
                    beat_type: int = 4) -> List[Dict[str, object]]:
    """Bars whose sounding beats do not fill the page's meter.

    "The beats add up" on a staff line means its bars fill the meter; a bar
    that does not is either a genuine pickup/short bar or a seating mistake, and
    naming it is how the two are told apart. A bar with NO beats is not reported
    here -- silence is the singer's tacet, already visible as a missing bar, and
    calling it a mismatch would drown the real ones.
    """
    expected = beats * 4.0 / beat_type
    out: List[Dict[str, object]] = []
    for sys_no, rows in enumerate(rows_by_system, start=1):
        for i, row in enumerate(rows):
            for m in row.measures:
                fill = _bar_fill(m) / divisions
                if fill and abs(fill - expected) > 1e-9:
                    out.append({"system": sys_no, "row_from_top": i,
                                "bar": m.number, "beats": fill,
                                "expected": expected})
    return out


def _fmt_beats(b: float) -> str:
    """Beats as a whole number when it is one, else two decimals."""
    return "%d" % round(b) if abs(b - round(b)) < 1e-9 else "%.2f" % b


def grid_shape(rows_by_system: Sequence[Sequence[Part]]) -> Tuple[int, int]:
    """``(total_rows, accompaniment_rows)`` for the whole piece.

    ``total_rows`` is the largest number of staves any system printed -- the
    page's tallest layout, which every system must be fit onto.

    ``accompaniment_rows`` is the largest braced group seen. homr states it by
    writing the group as one multi-staff part, so it is the largest
    ``len(clef_by_staff)`` a single part advertised. With no multi-staff part
    anywhere it is 1: every staff is its own instrument and there is nothing to
    brace. It is never allowed past ``total_rows``.

    A page that stacks two parts is a solo above an accompaniment, and the
    singer must survive on every system -- silent where the book prints
    nothing. Nothing else guarantees that: where the voice staff stands alone
    (see :func:`attach_orphan_staves`) homr reports one-row and two-row
    systems of *different* parts, the tallest layout comes out two rows, and
    the voice row is then not in the grid at all -- the vocal part silently
    vanishes from the piece. A page that prints a single part everywhere is
    left alone rather than given a singer it never had.
    """
    total = max((len(rows) for rows in rows_by_system), default=0)
    accomp = 0
    parts_seen = set()
    for rows in rows_by_system:
        # Rows arrive already split, so recover the group size from how the
        # caller packed them: the widest group is the widest run of rows that
        # share a source part. We only need the width, and split_part_by_staff
        # preserved it in the id ("<part>.s<staff>").
        counts: Dict[str, int] = {}
        for row in rows:
            base = row.id.rsplit(".s", 1)[0]
            counts[base] = counts.get(base, 0) + 1
            parts_seen.add(base)
        if counts:
            accomp = max(accomp, max(counts.values()))
    if accomp <= 0:
        accomp = 1
    total = max(total, 1)
    if len(parts_seen) > 1 and total == accomp:
        total = accomp + 1
    return total, min(accomp, total)


def clef_conflicts(rows_by_system: Sequence[Sequence[Part]],
                   total: int, accomp: int) -> List[Dict[str, object]]:
    """Where the systems disagree about the clef of the same grid row.

    Row indices are counted from the BOTTOM (row 0 = the lowest printed staff),
    because the grid is bottom-aligned and that is the numbering the alignment
    makes stable. Rows are reported as ``from_bottom``; the top row of a 3-row
    piece is ``from_bottom = 2``.

    A row that is only sometimes present (the singer's row during a piano intro)
    contributes only the clefs actually printed there, so its absence is not a
    conflict. This is a report, not a repair: an honest difference between two
    systems is a recognition question for a human, and silently overwriting one
    of them would be the same class of error as guessing.
    """
    seen: Dict[int, Dict[Clef, List[int]]] = {}
    for sys_no, rows in enumerate(rows_by_system, start=1):
        for i, row in enumerate(rows):
            # rows are printed top to bottom, so the bottom row is index
            # len-1; counting from the bottom makes the key stable when a
            # system uses fewer rows than the grid.
            from_bottom = len(rows) - 1 - i
            clef = row.clef_by_staff.get(1, DEFAULT_CLEF)
            seen.setdefault(from_bottom, {}).setdefault(clef, []).append(sys_no)
    out: List[Dict[str, object]] = []
    for key in sorted(seen):
        clefs = seen[key]
        if len(clefs) > 1:
            out.append({
                "from_bottom": key,
                "clefs": {("%s%d" % c): s for c, s in sorted(clefs.items())},
            })
    return out


def normalise(name: str,
              rows_by_system: Sequence[Sequence[Part]],
              divisions: int,
              beats: int = 4,
              beat_type: int = 4,
              key_fifths: int = 0,
              dpi: int = 300,
              clef_overrides: Optional[Dict[int, Clef]] = None) -> Page:
    """Lay every system out on one bottom-aligned grid and return the Page.

    ``rows_by_system`` is one entry per system (in time order), each holding that
    system's single-staff rows in reading order -- exactly what
    :func:`system_rows` returns.

    The bottom ``accomp`` grid rows become ONE part (a grand staff); the rows
    above become one part each. A system with fewer rows than the grid keeps its
    staves at the bottom and gets silent rows above them, so the accompaniment
    is continuous and the singer drops in only where the book prints them.

    ``clef_overrides`` maps a ``from_bottom`` row index to the clef to force --
    the single knob for a human who knows the page (e.g. "row 0 of this piece is
    a bass staff even though the intro prints treble").
    """
    total, accomp = grid_shape(rows_by_system)
    overrides = dict(clef_overrides or {})
    solo_rows = total - accomp

    # Each system numbers its bars from 1, so a page is a *sequence* of systems:
    # system 2's bar 1 is the page's bar (system 1's bar count + 1). Without the
    # offset every system would overwrite the same bars and only the last
    # system's music would survive.
    counts = [max((m.number for row in rows for m in row.measures), default=0)
              for rows in rows_by_system]
    offsets: List[int] = []
    running = 0
    for count in counts:
        offsets.append(running + 1)
        running += count
    numbers = list(range(1, running + 1))

    page = Page(id=name, divisions=divisions, beats=beats,
                beat_type=beat_type, key_fifths=key_fifths, dpi=dpi)

    def row_voices(row: Part) -> Dict[int, int]:
        """This row's own voice numbers, ranked 1..k by first appearance.

        A printed staff carries one voice per musical line, whatever ids the
        recogniser happened to put on them, and those ids are meaningless
        ACROSS staves -- homr numbers voices 1..4 over a whole part, so the
        same line is voice 2 on one staff and voice 5 on another. Re-seating
        several such staves onto one grid staff then merges the labels and
        inflates the staff past the four voices notation can hold; MuseScore
        quietly drops the surplus notes (it lost exactly 2 notes in bar 6 of
        p0020, whose staff 3 was labelled voice 6). Ranking each row's ids
        before they meet restores the invariant that matters -- <= the voices
        the row actually has -- and leaves one row's labels alone.
        """
        out: Dict[int, int] = {}
        for meas in row.measures:
            for n in meas.notes:
                out.setdefault(n.voice, len(out) + 1)
        return out

    def row_notes(row: Part, offset: int, staff: int) -> Dict[int, List[Note]]:
        """This row's notes keyed by PAGE bar number, restamped to staff."""
        voices = row_voices(row)
        out: Dict[int, List[Note]] = {}
        for meas in row.measures:
            out.setdefault(offset + meas.number - 1, []).extend(
                replace(n, staff=staff, voice=voices[n.voice])
                for n in meas.notes)
        return out

    def collect(from_bottom: int) -> Tuple[Dict[int, List[Note]],
                                           List[Tuple[int, Clef]]]:
        """This grid row's notes, plus (bar, clef) for each system that has it.

        The clef list is a *schedule*, in time order, so the caller can keep the
        first one as the row's opening clef and turn every later difference into
        a clef change. A row whose clef never changes yields a one-entry list.
        """
        merged: Dict[int, List[Note]] = {}
        clefs: List[Tuple[int, Clef]] = []
        for sys_i, rows in enumerate(rows_by_system):
            if len(rows) < from_bottom + 1:
                continue  # this system did not print this row
            row = rows[len(rows) - 1 - from_bottom]
            clefs.append((offsets[sys_i],
                          row.clef_by_staff.get(1, DEFAULT_CLEF)))
            for num, notes in row_notes(row, offsets[sys_i], 1).items():
                merged.setdefault(num, []).extend(notes)
        return merged, clefs

    # -- solo rows, top of the grid first -------------------------------------
    solo_parts: List[Part] = []
    for grid_row in range(solo_rows):
        from_bottom = accomp + (solo_rows - 1 - grid_row)  # top row first
        part = Part(id="S%d" % (grid_row + 1), name=SOLO_NAME)
        by_number, schedule = collect(from_bottom)
        if from_bottom in overrides:
            part.clef_by_staff = {1: overrides[from_bottom]}
        else:
            initial, changes = _clef_schedule(schedule)
            part.clef_by_staff = {1: initial}
            part.clef_changes = [(bar, 1, clef) for bar, clef in changes]
        part.measures = _bars(numbers, by_number, page, staff=1)
        solo_parts.append(part)

    # -- accompaniment rows, bottom of the grid -------------------------------
    accomp_part = Part(id="A1", name=ACCOMPANIMENT_NAME)
    for grid_row in range(accomp):
        from_bottom = grid_row  # bottom row first
        local = accomp - grid_row  # staff 1 is the TOP of the grand staff
        by_number, schedule = collect(from_bottom)
        by_number = {num: [replace(n, staff=local) for n in notes]
                     for num, notes in by_number.items()}
        if from_bottom in overrides:
            accomp_part.clef_by_staff[local] = overrides[from_bottom]
        else:
            initial, changes = _clef_schedule(schedule)
            accomp_part.clef_by_staff[local] = initial
            for bar, clef in changes:
                accomp_part.clef_changes.append((bar, local, clef))
        accomp_part.measures = _merge_bars(accomp_part.measures,
                                           _bars(numbers, by_number, page,
                                                 staff=local))
    page.parts = solo_parts + [accomp_part]
    return page


def _clef_schedule(schedule: Sequence[Tuple[int, Clef]]) -> Tuple[Clef,
                                                                 List[Tuple[int, Clef]]]:
    """Split a row's ``(bar, clef)`` readings into an opening clef and changes.

    The first system that prints the row sets the clef it opens with; every
    later system whose clef differs is a change at that system's first bar. This
    is what keeps a piano honest: the printed page can genuinely open two-treble
    and switch the left hand to a bass staff when the voice enters, and that is
    one part with one clef change, not a conflict to be voted into one clef.
    """
    if not schedule:
        return DEFAULT_CLEF, []
    initial = schedule[0][1]
    current = initial
    changes: List[Tuple[int, Clef]] = []
    for bar, clef in schedule[1:]:
        if clef != current:
            changes.append((bar, clef))
            current = clef
    return initial, changes


def _bars(numbers: Sequence[int], by_number: Dict[int, List[Note]],
          page: Page, staff: int = 1) -> List[Measure]:
    """The part's bars 1..N, with a whole-measure rest where it has nothing.

    A missing bar is not padding: the singer genuinely does not sing while the
    piano plays the intro. A whole-measure rest is the notation of that fact,
    and it is stamped with the staff it belongs to so a grand staff gets one
    rest per hand rather than two rests stacked on the treble.
    """
    out: List[Measure] = []
    for number in numbers:
        notes = by_number.get(number)
        if notes:
            out.append(Measure(
                number=number, divisions=page.divisions, beats=page.beats,
                beat_type=page.beat_type, key_fifths=page.key_fifths,
                notes=notes))
        else:
            out.append(Measure(
                number=number, divisions=page.divisions, beats=page.beats,
                beat_type=page.beat_type, key_fifths=page.key_fifths,
                is_empty=True,
                notes=[Note(staff=staff, voice=1, onset=0,
                            duration=page.divisions * page.beats,
                            pitch=None, measure=number, is_rest=True)]))
    return out


def _merge_bars(dst: List[Measure], src: List[Measure]) -> List[Measure]:
    """Fold one staff's bars into the grand staff's, bar by bar.

    Both lists are already 1..N, so this is a positional union: a bar that the
    grand staff already has gains this staff's notes, and a bar it does not have
    yet is taken whole. Order is preserved because the caller writes staff 1
    first and relies on MusicXML's partwise cursor doing the right thing.
    """
    if not dst:
        return list(src)
    by_number = {m.number: m for m in dst}
    for meas in src:
        if meas.number in by_number:
            by_number[meas.number].notes.extend(meas.notes)
        else:
            dst.append(meas)
    return dst


def report(rows_by_system: Sequence[Sequence[Part]],
           divisions: int = GLOBAL_DIVISIONS, beats: int = 4, beat_type: int = 4) -> str:
    """A short, honest description of the grid that would be built.

    Both counts are given: the tallest STAFF count (the shape the grid uses) and
    the beats that add up on each staff line (the content). They are different
    questions and this answers both rather than letting one stand for the other.
    """
    total, accomp = grid_shape(rows_by_system)
    content = content_rows(rows_by_system, divisions)
    lines = ["staff grid: %d row(s), %d accompaniment row(s), %d solo row(s)"
             % (total, accomp, total - accomp)]
    if content < total:
        lines.append("  %d of %d row(s) carry beats; %d row(s) are printed but "
                     "never sound (tacet rows)." % (content, total,
                                                    total - content))
    totals = beat_totals(rows_by_system, divisions)
    lines.append("  beats per grid row (row 0 = lowest staff): "
                 + ", ".join("row%d=%s" % (i, _fmt_beats(b))
                             for i, b in enumerate(totals)))
    for sys_no, rows in enumerate(rows_by_system, start=1):
        clefs = ", ".join(
            "%s%s" % (r.clef_by_staff.get(1, DEFAULT_CLEF)[0],
                      r.clef_by_staff.get(1, DEFAULT_CLEF)[1])
            for r in rows)
        row_beats = ", ".join(_fmt_beats(_row_fill(r) / divisions) for r in rows)
        pad = total - len(rows)
        lines.append("  system %d: %d row(s) [%s] beats [%s]%s"
                     % (sys_no, len(rows), clefs, row_beats,
                        "  (+%d silent row(s) on top)" % pad if pad else ""))
    silent = [i + 1 for i, rows in enumerate(rows_by_system)
              if rows and all(_row_fill(r) == 0 for r in rows)]
    if silent:
        lines.append("  system(s) %s printed but carry NO beats -- the "
                     "recogniser seated nothing there."
                     % ", ".join(str(s) for s in silent))
    mism = beat_mismatches(rows_by_system, divisions, beats, beat_type)
    if mism:
        lines.append("  %d bar(s) do not fill the %d/%d meter (e.g. system %s "
                     "row %s bar %s: %s of %s beats)"
                     % (len(mism), beats, beat_type, mism[0]["system"],
                        mism[0]["row_from_top"], mism[0]["bar"],
                        _fmt_beats(mism[0]["beats"]),
                        _fmt_beats(mism[0]["expected"])))
    conflicts = clef_conflicts(rows_by_system, total, accomp)
    for c in conflicts:
        lines.append("  CLEF CONFLICT on row %d from the bottom: %s"
                     % (c["from_bottom"], c["clefs"]))
    return "\n".join(lines)
