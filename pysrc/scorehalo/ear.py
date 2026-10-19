"""Ear checks: musical priors that need no ground truth.

The note-by-note differ in score.py answers "does this match the reference?".
These answer a different question that needs no reference at all: *does this sound
like music a person would recognise?*

That is the gap. homr can drop four bars, mis-spell a tuplet, or fold two pianos
into one part and the resulting file is still schema-valid, still imports into
MuseScore without complaint, and still has the right note count. A differ with
ground truth catches it. An ear catches it without any ground truth existing.

Each check is a prior that a musician would state without hesitating:

  vocal_leap     the top voice of a song does not leap more than an octave
                 between consecutive notes. Songs are sung, and singers sing in
                 steps. Leaps this big in a melody mean a misread, not a composer.
  out_of_range   nothing is written outside the piano's 88 keys (A0..C8).
  silence_run    three consecutive measures where NO part sounds is not music.
                 A held fermata is one bar; three empty bars is a dropped system.

These are priors, not laws. Every one of them can be wrong on real music, which is
why they are reported as suspects with counts and never auto-corrected. The
danger is not a false positive; it is a false NEGATIVE going unnoticed because
nothing was listening.
"""


PITCH_CLASS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
VOCAL_MAX_LEAP = 12  # an octave; semitones, consecutive notes in the top voice
PIANO_LO, PIANO_HI = 21, 108  # A0..C8


def midi(pitch):
    """'F#4' -> 66. 'C' -> None. Accepts only well-formed step/alter/octave."""
    if not pitch or len(pitch) < 2:
        return None
    step = pitch[0]
    if step not in PITCH_CLASS:
        return None
    idx = 1
    alter = 0
    while idx < len(pitch) and pitch[idx] in "#b":
        alter += 1 if pitch[idx] == "#" else -1
        idx += 1
    octave = pitch[idx:]
    if not octave.isdigit() or (len(octave) > 1 and octave[0] == "0"):
        return None
    return (int(octave) + 1) * 12 + PITCH_CLASS[step] + alter


def _measure_key(measure_no):
    """Sort measures NUMERICALLY.

    MusicXML measure numbers are strings, and lexicographic order interleaves
    them: '38','39','4','40'. A hole spanning measures 38-40 then has measure '4'
    sitting between '39' and '40'; if measure 4 has notes, the run resets and a
    real three-bar hole is reported as none at all. That bug hid every hole in the
    joined Carpenters score. Non-numeric numbers sort last, stably.
    """
    text = str(measure_no)
    return (0, int(text), "") if text.isdigit() else (1, 0, text)


def check_streams(streams, measure_index=None):
    """streams: {(part, staff, voice): [(measure, onset, kind, pitch, dur, chord)]}

    measure_index: optional {part: [every measure number in the raw file]}.

    This argument is NOT cosmetic. A measure containing no notes produces no stream
    entries, so a measure list derived from the streams is missing exactly the holes
    we are looking for. silence_run is therefore blind by construction unless the
    caller supplies the real measure list from the parsed tree. When it is omitted
    the check still runs, but only over measures that have some content -- it will
    find a hole flanked by music and miss one that is empty end to end.

    Returns (suspects, counts). Streams must be ordered by onset; score.py's
    parse_document already guarantees that.
    """
    suspects = []
    counts = {"vocal_leap": 0, "out_of_range": 0, "silence_run": 0}

    per_key = {}
    for key, notes in streams.items():
        # score.py streams are 7-tuples: (measure, onset, local, kind, pitch,
        # dur, chord). 'onset' is a running position across the page and is NOT
        # comparable between measures; 'local' is the within-measure position and
        # is the only correct thing to test two notes for simultaneity (§26).
        for measure_no, _onset, local, kind, pitch, _dur, chord in notes:
            per_key.setdefault(key, []).append((measure_no, local, kind, pitch, chord))

    # vocal_leap applies to the melody, and the melody is the staff whose notes
    # actually sit highest -- NOT the highest-numbered staff. In a piano part
    # staff 2 is the bass, and a bass line leaps all day. Ranking by staff number
    # would test the wrong line and report every left-hand octave as a fault.
    # Median pitch is used rather than mean so one stray note cannot crown a bass
    # line as the melody.
    melody_staff = {}
    for part in {key[0] for key in per_key}:
        by_staff = {}
        for key, items in per_key.items():
            if key[0] != part:
                continue
            for _m, _o, kind, pitch, _c in items:
                if kind == "pitched" and pitch:
                    note = midi(pitch)
                    if note is not None:
                        by_staff.setdefault(key[1], []).append(note)
        if by_staff:
            melody_staff[part] = max(
                by_staff, key=lambda s: (sorted(by_staff[s])[len(by_staff[s]) // 2], -s)
            )

    for key, items in sorted(per_key.items(), key=lambda kv: str(kv[0])):
        part, staff, voice = key
        sounded = [(m, o, p) for m, o, k, p, _c in items if k == "pitched" and p]

        is_top = key[1] == melody_staff.get(part)
        if is_top:
            prev = None
            for measure_no, onset, pitch in sounded:
                m = midi(pitch)
                if m is None:
                    continue
                if prev is not None:
                    leap = abs(m - prev)
                    if leap > VOCAL_MAX_LEAP:
                        counts["vocal_leap"] += 1
                        suspects.append(
                            {
                                "ear": "vocal_leap",
                                "where": f"part {part} staff {staff} voice {voice} "
                                f"measure {measure_no}",
                                "detail": f"{prev}-{m} is {leap} semitones in the top "
                                f"voice (limit {VOCAL_MAX_LEAP}) - {pitch}",
                            }
                        )
                prev = m

        for measure_no, _onset, pitch in sounded:
            m = midi(pitch)
            if m is not None and not (PIANO_LO <= m <= PIANO_HI):
                counts["out_of_range"] += 1
                suspects.append(
                    {
                        "ear": "out_of_range",
                        "where": f"part {part} staff {staff} measure {measure_no}",
                        "detail": f"{pitch} = MIDI {m}, outside piano A0..C8 "
                        f"({PIANO_LO}..{PIANO_HI})",
                    }
                )

    all_measures = {}
    if measure_index is not None:
        for part, measures in measure_index.items():
            all_measures.setdefault(part, set()).update(measures)
    else:
        for key, items in per_key.items():
            for measure_no, _o, _k, _p, _c in items:
                all_measures.setdefault(key[0], set()).add(measure_no)

    # --- silence_run -----------------------------------------------------------
    # "Silent" must mean no notes AT ALL -- not "no PITCHED notes". A measure of
    # rests is composed silence and entirely legitimate; conflating it with data
    # loss fired 28 times on known-good fixtures before the control caught it.
    # Only a measure containing no note element of any kind has lost its music.
    entry_measures = set()
    for key, items in per_key.items():
        for measure_no, _o, _k, _p, _c in items:
            entry_measures.add((key[0], measure_no))

    for part, measures in sorted(all_measures.items()):
        run = 0
        for measure_no in sorted(measures, key=_measure_key):
            if (part, measure_no) in entry_measures:
                run = 0
                continue
            run += 1
            if run >= 3:
                counts["silence_run"] += 1
                suspects.append(
                    {
                        "ear": "silence_run",
                        "where": f"part {part} measure {measure_no}",
                        "detail": f"{run} consecutive measures with no note of any kind "
                        "- dropped music, not a rest",
                    }
                )
    return suspects, counts
