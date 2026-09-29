"""Page-anchored music graph: the intermediate ScoreHalo assembles into.

Why this exists
---------------
MusicXML expresses simultaneity by walking a cursor *backwards*
(``<backup>``). That makes it an engraving format, not a music format. Merging
independent per-staff recognitions therefore means reconciling cursor
arithmetic across fragments that never shared a pen -- and every one of those
reconciliations is a bug waiting to happen: backups that overshoot, empty
measures, per-part ``divisions`` that disagree, time signatures that contradict
each other.

This module sidesteps all of it. A note here is *data about simultaneity*:

    Note(staff=3, voice=1, onset=16, duration=8, pitch=Pitch("E", 4, 4))

Notes are sorted by onset. Two notes sound together **because their onsets are
equal** -- there is no cursor to misplace, so a backup cannot overshoot and an
empty measure cannot corrupt anything. ``divisions`` becomes a global constant
of the graph rather than a per-fragment guess.

What makes this ours rather than a notation engine's model: every note keeps
``staff`` and ``system`` as *page geometry*, and ``src_x``/``src_y`` as its
pixel position on the original scan. MuseScore and LilyPond are score-centric
-- they assume the page is gone. We still have the page, which is what allows
confidence-weighted review, cross-staff beaming, and note-to-pixel provenance.
"""

from dataclasses import dataclass, field, replace
from typing import Dict, Iterable, List, Optional, Tuple

# Pitches as (step, alter, octave). Alter is in semitones: 1 sharp, -1 flat.
Step = str
Pitch = Tuple[Step, int, int]

#: Accidentals MusicXML spells differently from the chromatic alter.
ALTER_TO_ACCIDENTAL = {
    -2: "flat-flat", -1: "flat", 0: "natural", 1: "sharp", 2: "double-sharp",
}

SEMITONE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


@dataclass(frozen=True)
class Pitch:
    step: Step
    alter: int
    octave: int

    @property
    def diatonic(self) -> int:
        """Staff position index, C0 == 0, ascending diatonically."""
        base = {"C": 0, "D": 1, "E": 2, "F": 3, "G": 4, "A": 5, "B": 6}
        return self.octave * 7 + base[self.step]

    @property
    def chromatic(self) -> int:
        """MIDI-style semitone index, C-1 == 0."""
        return (self.octave + 1) * 12 + SEMITONE[self.step] + self.alter

    def transposed(self, semitones: int) -> "Pitch":
        """Return this pitch shifted by ``semitones``.

        Done on the chromatic value and re-spelled through the key-neutral
        natural scale, so transposing a C-sharp major chord by a whole tone
        yields D-sharp major rather than three individually-rounded notes.
        """
        target = self.chromatic + semitones
        octave, within = divmod(target, 12)
        order = [("C", 0), ("C", 1), ("D", 0), ("D", 1), ("E", 0), ("F", 0),
                 ("F", 1), ("G", 0), ("G", 1), ("A", 0), ("A", 1), ("B", 0)]
        step, alter = order[within]
        return Pitch(step, alter, octave - 1)


@dataclass(frozen=True)
class Note:
    """One sounding note, anchored to a staff on a page.

    ``onset`` and ``duration`` are in graph ticks, measured from the start of
    the measure. Simultaneity is equality of onsets; there is no cursor.
    """
    staff: int
    voice: int
    onset: int
    duration: int
    pitch: Pitch
    measure: int
    is_rest: bool = False
    tie: Optional[str] = None
    #: ``"start"``/``"stop"`` from ``<notations><slur>``. A slur is a smooth
    #: (legato) phrase mark between notes of *any* pitch -- unlike ``tie``,
    #: which joins the same pitch. LilyPond has distinct event classes for
    #: these, so they must not share one field or one output token.
    #: Limitation: a note carrying two different slurs (simultaneous start and
    #: stop) keeps only the first, which is what homr's page fragments emit.
    slur: Optional[str] = None
    #: The LilyPond bracket this note was assigned by slur pairing: ``"("``,
    #: ``")"`` or ``None``. This is a *resolved rendering* field, not source
    #: data: :func:`scorehalo.to_ly._resolve_slurs` sets it only on notes whose
    #: start and stop were successfully paired. It lives on the note (rather
    #: than in a side table keyed by ``id()``) because bar clamping rebuilds
    #: notes with ``dataclasses.replace``, which mints new identities but
    #: preserves every other field.
    slur_mark: Optional[str] = None
    #: ``(actual, normal)`` from ``<time-modification>``, e.g. (3, 2) for a
    #: triplet eighth. Tuplets are the reason a duration can be a value no plain
    #: note type expresses: 3-in-the-time-of-2 at divisions=6 is 4 ticks, which
    #: is neither an eighth nor a sixteenth. Dropping this and keeping only the
    #: tick count produces a file importers reject.
    tuplet: Optional[Tuple[int, int]] = None
    system: int = 1
    src_x: Optional[int] = None
    src_y: Optional[int] = None
    confidence: float = 1.0

    def transposed(self, semitones: int) -> "Note":
        if self.is_rest:
            return self
        return replace(self, pitch=self.pitch.transposed(semitones))


@dataclass
class Measure:
    """A bar of one staff: ticks per quarter note, and its notes."""
    number: int
    divisions: int = 4
    beats: int = 4
    beat_type: int = 4
    notes: List[Note] = field(default_factory=list)
    clef: Optional[Tuple[str, int]] = None
    key_fifths: int = 0
    is_empty: bool = False

    @property
    def length(self) -> int:
        return self.beats * self.divisions * 4 // self.beat_type


@dataclass
class Part:
    """One staff of a page, with the measures that staff contains."""
    id: str
    name: str
    system: int = 1
    staff_index: int = 1
    measures: List[Measure] = field(default_factory=list)
    #: Clef per staff, when the source states one. A two-staff role (a piano
    #: grand staff) has a *different* clef on each staff, so a single scalar per
    #: measure cannot express it -- homr supplies no clef at all, and callers
    #: that assumed treble silently engraved a piano's bass line an octave and
    #: a third too high.
    clef_by_staff: Dict[int, Tuple[str, int]] = field(default_factory=dict)

    @property
    def notes(self) -> List[Note]:
        return [n for m in self.measures for n in m.notes]


@dataclass
class Page:
    """A whole recognised page: the unit ScoreHalo assembles and reviews."""
    id: str
    parts: List[Part] = field(default_factory=list)
    divisions: int = 4
    beats: int = 4
    beat_type: int = 4
    key_fifths: int = 0
    dpi: int = 300
    source_pdf: Optional[str] = None
    source_page: Optional[int] = None

    @property
    def notes(self) -> List[Note]:
        return [n for p in self.parts for n in p.notes]

    def systems(self) -> Dict[int, List[Part]]:
        out: Dict[int, List[Part]] = {}
        for p in self.parts:
            out.setdefault(p.system, []).append(p)
        return out

    # -- graph transforms ------------------------------------------------
    def transposed(self, semitones: int) -> "Page":
        clone = replace(
            self,
            parts=[replace(p, measures=[replace(
                m, notes=[n.transposed(semitones) for n in m.notes])
                for m in p.measures]) for p in self.parts],
        )
        return clone

    def voice_count(self) -> int:
        return len({n.voice for n in self.notes}) or 1

    def staff_count(self) -> int:
        return len(self.parts)

    def duration_ticks(self) -> int:
        return sum(self.length for self in self.parts[:1])

    def low_confidence(self, threshold: float = 0.8) -> List[Note]:
        return [n for n in self.notes if n.confidence < threshold]


def chordify(page: Page) -> List[Tuple[int, int, Tuple[Pitch, ...]]]:
    """Collapse the page to simultaneous pitch sets: (staff, onset, pitches).

    The classic first step of harmonic analysis, and free once simultaneity is
    a property of the data rather than of a cursor.
    """
    buckets: Dict[Tuple[int, int], List[Pitch]] = {}
    for n in page.notes:
        if n.is_rest:
            continue
        buckets.setdefault((n.staff, n.onset), []).append(n.pitch)
    return [(s, o, tuple(sorted(p, key=lambda p: p.diatonic)))
            for (s, o), p in sorted(buckets.items())]
