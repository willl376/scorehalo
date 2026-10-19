"""Recognise the recurring shape of a page, and say plainly when it breaks.

The problem this solves
-----------------------
Every serious bug in this project has had the same shape: something assumed a
fixed expectation and the page disagreed, and the disagreement surfaced as a
downstream invariant failure rather than as itself.

    "piano parts have two staves"    -> but this one has three
    "the meter is 4/4"               -> but this one declares 3/4
    "the bass clef sits below a G"   -> but this one does not

A hard-coded expectation can only ever answer *wrong*. It cannot answer **"I
don't recognise this"**, which is the answer that would have been useful in
every one of those cases. This module takes the opposite approach: learn the
page's own recurring patterns from the systems where they hold, then say
which systems match a known pattern and, for those that do not, name the
features that broke.

That distinction matters musically. A page whose second system repeats the
first is evidence that the page is *regular*, and regularity is what makes an
oddity meaningful -- the first system cannot corroborate itself. So the two
leading systems are the reference, exactly as they are in the question that
prompted this: if you can sense the first and second set of staves, you can
tell when the pattern shifts into something unrecognised.

What a pattern is
-----------------
A :class:`SystemSignature` is a small tuple of *structural* facts about one
system -- staff count, meter, clef shape, pitch span, bar lengths. Not the
music. Two verses of the same song differ in every note and agree in every
signature; a system where homr dropped a staff differs in staff count and
matches nothing.

Nothing here reads pixels, edits a score, or decides what the page *means*. It
compares. :func:`find_deviations` is the whole public surface, plus
:func:`summarise` for a human-readable report.

Honest limitations, because they bound what this can ever claim
----------------------------------------------------------------
* **It reports, it does not repair.** A deviation is a question, not a fix. Use
  :mod:`scorehalo.breakpoints` to look at the offending system.
* **It can only find breaks in a pattern that recurs.** A page where every
  system differs is reported as *all novel*, which is truthful and not useful.
  ``max_unmatched`` distinguishes "three systems broke" from "nothing here
  repeats".
* **A novelty is not a bug.** A modulation, a meter change, a double-width
  system and a recognition failure all look identical from here. Naming which
  feature broke is the most this can honestly do.
* **Signatures are compared exactly.** ``(G2, F4)`` and ``(G2, F3)`` are
  different patterns; that is deliberate, because a bass clef that moved a line
  is a real difference and silently tolerating it is how the F4 case got
  missed.

Examples
--------
>>> from scorehalo import pattern
>>> # Two systems with the same shape, one that gained a staff.
>>> page = pattern.example_page()
>>> report = pattern.find_deviations(page)
>>> [d.system for d in report.deviations]
[3]
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .graph import Page, Part

#: How many leading systems to treat as the reference, when no explicit
#: reference set is given. Two is the smallest number from which "a pattern
#: recurs" is a claim rather than an assumption: one system is its own pattern.
REFERENCE_SYSTEMS = 2


@dataclass(frozen=True)
class SystemSignature:
    """The structural shape of one system, independent of its music.

    Every field is deliberately coarse. A finer signature -- pitch classes,
    rhythm histograms -- would make almost every system novel, and a detector
    that says "unrecognised" about the whole page is not a detector.
    """

    staff_count: int
    #: Meter as (beats, beat_type) if the page declares one, else None.
    meter: Optional[Tuple[int, int]]
    #: Clefs in staff order. Grand staff is (G2, F4); a vocal line is (G2,).
    clefs: Tuple[Tuple[str, int], ...]
    #: Distinct sounding pitch count per staff, in staff order.
    #: Distinguishes a moving bass line from a held one.
    pitch_counts: Tuple[int, ...]
    #: Bar lengths in staff order, in whole notes.
    bar_lengths: Tuple[Tuple[int, ...], ...]

    @property
    def pattern_key(self) -> Tuple:
        """The coarse identity used to decide whether two systems *match*.

        Deliberately excludes ``pitch_counts`` and ``bar_lengths``. An earlier
        version included them and was wrong in the way this whole module exists
        to avoid: two verses of one song are the same pattern with different
        notes, so including the notes made every real page report "nothing
        repeats" and turned the detector into noise.

        What is left is the part a part *keeps* between systems -- how many
        staves, what meter, what clef in each position. That is what makes it a
        pattern rather than a fingerprint, and it is what lets system 3 predict
        the bar length system 2 got wrong.
        """
        return (self.staff_count, self.meter, self.clefs)

    @property
    def staff_pattern_keys(self) -> Tuple:
        """One key per staff position: ``(clef,)``.

        Column-wise comparison is the useful axis. Clefs repeat *down a
        position* across systems -- the third staff is F4 in system 2 and F4 in
        system 3 -- and it is that agreement which predicts what the second
        staff of system 1 should be. Comparing whole systems can only say
        "different"; comparing columns says "position 2 was G2 here and G2
        there, but the bass position is F4 everywhere it appears".
        """
        return tuple((clef,) for clef in self.clefs)

    def describe(self) -> str:
        meter = "%d/%d" % self.meter if self.meter else "no meter"
        clefs = " ".join("%s%d" % clef for clef in self.clefs) or "no clef"
        return "%d staff(s), %s, %s" % (self.staff_count, meter, clefs)

    def differences(self, other: "SystemSignature") -> List[str]:
        """Which fields differ, as readable phrases.

        Returns phrases rather than field names because the reader of this
        report is trying to decide what to go and look at in the image, not
        which attribute changed.
        """
        found: List[str] = []
        if self.staff_count != other.staff_count:
            found.append("staff count %d (expected %d)"
                         % (other.staff_count, self.staff_count))
        if self.meter != other.meter:
            shown = ("%d/%d" % other.meter) if other.meter else "none"
            found.append("meter %s (expected %s)" % (shown, self.describe().split(", ")[1]))
        if self.clefs != other.clefs:
            shown = " ".join("%s%d" % clef for clef in other.clefs) or "none"
            found.append("clefs %s (expected %s)"
                         % (shown, " ".join("%s%d" % c for c in self.clefs) or "none"))
        if self.pitch_counts != other.pitch_counts:
            found.append("pitch counts %s (expected %s)"
                         % (list(other.pitch_counts), list(self.pitch_counts)))
        if self.bar_lengths != other.bar_lengths:
            found.append("bar lengths %s (expected %s)"
                         % (list(other.bar_lengths), list(self.bar_lengths)))
        return found


@dataclass
class Deviation:
    """One system that matches no known pattern."""

    system: int
    signature: SystemSignature
    #: Nearest known pattern's signature, for the "expected" half of each phrase.
    nearest: Optional[SystemSignature] = None
    #: Feature phrases from ``nearest`` to this signature.
    broke: List[str] = field(default_factory=list)

    def __str__(self) -> str:
        if self.broke:
            return "system %d: %s; broke %s" % (
                self.system, self.signature.describe(), "; ".join(self.broke))
        return "system %d: %s; matches no pattern on this page" % (
            self.system, self.signature.describe())


@dataclass
class DeviationReport:
    """What the page repeats, and where it stops repeating."""

    #: System number -> signature, for every system examined.
    observed: Dict[int, SystemSignature] = field(default_factory=dict)
    #: pattern key -> systems that share it, in first-seen order.
    patterns: Dict[Tuple, List[int]] = field(default_factory=dict)
    #: Systems used as the reference.
    reference: List[int] = field(default_factory=list)
    deviations: List[Deviation] = field(default_factory=list)
    #: Per-staff-position agreement across the page. See :func:`column_analysis`.
    columns: List["ColumnObservation"] = field(default_factory=list)
    #: True when too few systems share a pattern for the comparison to mean much.
    weak: bool = False

    def matched(self) -> List[int]:
        """Systems that fit a pattern, in order."""
        bad = {deviation.system for deviation in self.deviations}
        return [system for system in sorted(self.observed) if system not in bad]

    def repeats(self) -> List[Tuple[Tuple, List[int]]]:
        """Patterns shared by more than one system, with their members.

        Keys are :attr:`SystemSignature.pattern_key` tuples, not signatures --
        one key can stand for several systems with different notes in them.
        """
        return [(key, systems) for key, systems in self.patterns.items()
                if len(systems) > 1]

    def summarise(self) -> str:
        """Multi-line report for a terminal."""
        lines = ["examined %d system(s); %d pattern(s), %d repeating; "
                 "%d deviation(s)" % (len(self.observed), len(self.patterns),
                                      len(self.repeats()), len(self.deviations))]
        if self.reference:
            lines.append("reference: system(s) %s"
                         % ", ".join(str(system) for system in self.reference))
        for key, systems in self.repeats():
            lines.append("  pattern %-42s system(s) %s"
                         % (describe_key(key),
                            ", ".join(str(system) for system in systems)))
        if self.weak and not self.repeats():
            lines.append("  NOTE nothing repeats on this page; every system is "
                         "novel, so 'deviation' here means 'no pattern to match'.")
        if self.deviations:
            lines.append("deviations:")
            for deviation in self.deviations:
                lines.append("  " + str(deviation))
        established = [c for c in self.columns if c.established]
        if established:
            lines.append("staff positions:")
            for column in self.columns:
                marker = "  " if column.consistent else " *"
                lines.append(marker + str(column))
            broken = [c for c in self.columns if c.established and not c.consistent]
            if broken:
                lines.append("  (* = position disagrees with the pattern elsewhere "
                             "on this page)")
        return "\n".join(lines)


def describe_key(key: Tuple) -> str:
    """Render a :attr:`SystemSignature.pattern_key` for a human."""
    staff_count, meter, clefs = key
    meter_text = "%d/%d" % meter if meter else "no meter"
    clef_text = " ".join("%s%d" % clef for clef in clefs) or "no clef"
    return "%d staff(s), %s, %s" % (staff_count, meter_text, clef_text)


def _staff_order_key(part: Part) -> Tuple[int, int]:
    """Sort key placing a part in staff order within its system.

    ``Part`` has no ``system_staff_index``: it has ``staff_index``, and in every
    caller-built page that is the index *within* the system. Reading a field
    that does not exist -- my first attempt -- silently yielded 0 for every
    staff, so every system collapsed into one unordered pile and the signature
    compared clefs in arbitrary order. ``staff_index`` is the real key; the
    second element is the name, purely to make the sort total and stable.
    """
    return (int(getattr(part, "staff_index", 0) or 0), part.name or "")


def _part_clef(part: Part) -> Optional[Tuple[str, int]]:
    """The clef for a part's first staff, or None if the page states none.

    ``Part`` carries clefs per staff in ``clef_by_staff``, not in a scalar
    ``clef``. A signature over a grand staff therefore has to read *both*
    staves of it, which is why :func:`signature_of` works from measures and not
    from the part's own clef field.
    """
    for measure in part.measures:
        if measure.clef:
            return measure.clef
    by_staff = getattr(part, "clef_by_staff", None) or {}
    if by_staff:
        first = min(by_staff)
        return by_staff[first]
    return None


def signature_of(parts: Sequence[Part]) -> SystemSignature:
    """Build the structural signature of one system, from its parts in order.

    ``parts`` must be in staff order, and in the same order for every system:
    comparing a signature taken from staff order against one taken from
    whatever order a dictionary yielded would report differences that are not
    there. :func:`find_deviations` sorts by ``part.system_staff_index`` for
    exactly this reason.
    """
    ordered = sorted(parts, key=_staff_order_key)

    # One entry per *staff*, not per part. A part may span two staves (a piano
    # grand staff), and a signature that only saw the part would call a piano
    # and a voice the same shape whenever they hold the same number of bars --
    # which is exactly the confusion that put a singer's staff inside the piano
    # earlier. So clefs are collected per staff from each measure.
    clefs: List[Tuple[str, int]] = []
    pitch_counts: List[int] = []
    bar_lengths: List[Tuple[int, ...]] = []
    meter: Optional[Tuple[int, int]] = None
    staff_count = 0

    for part in ordered:
        by_staff = getattr(part, "clef_by_staff", None) or {}
        keys = sorted(by_staff) if by_staff else [1]
        notes_by_staff: Dict[int, List[Note]] = defaultdict(list)
        for measure in part.measures:
            for note in measure.notes:
                notes_by_staff[getattr(note, "staff", 1)].append(note)
            if meter is None:
                meter = (measure.beats, measure.beat_type)

        for staff in keys:
            staff_count += 1
            clef = by_staff.get(staff)
            if clef is None:
                clef = next((m.clef for m in part.measures if m.clef), None)
            clefs.append(clef if clef else ("?", 0))
            pitch_counts.append(len(notes_by_staff.get(staff, [])))
            staff_notes = notes_by_staff.get(staff, [])
            lengths = []
            for measure in part.measures:
                if any(getattr(n, "staff", 1) == staff for n in measure.notes):
                    lengths.append(measure.length)
            bar_lengths.append(tuple(lengths))

    if not staff_count:
        # Parts with no notes and no clefs: still a staff, still countable.
        staff_count = len(ordered)
        for part in ordered:
            clefs.append(_part_clef(part) or ("?", 0))
            pitch_counts.append(0)
            bar_lengths.append(tuple(m.length for m in part.measures))

    return SystemSignature(
        staff_count=staff_count,
        meter=meter,
        clefs=tuple(clefs),
        pitch_counts=tuple(pitch_counts),
        bar_lengths=tuple(bar_lengths),
    )


def _staff_order(page: Page) -> Dict[int, List[Part]]:
    """Group a page's parts by system, each group in staff order.

    Page.systems() exists and does not sort; sorting here is what makes the
    comparison meaningful, and doing it in one place means no caller has to
    remember.
    """
    grouped: Dict[int, List[Part]] = defaultdict(list)
    for part in page.parts:
        grouped[part.system].append(part)
    return {
        system: sorted(parts, key=_staff_order_key)
        for system, parts in grouped.items()
    }


def find_deviations(
    page: Page,
    reference: Optional[Iterable[int]] = None,
    min_pattern_members: int = 2,
) -> DeviationReport:
    """Compare every system against the patterns the page repeats.

    ``reference`` names the systems to hold up as the known patterns. Default:
    the first :data:`REFERENCE_SYSTEMS` systems, because a page's opening is
    where a repeated form is established and where a later break is therefore
    evidence of something.

    A system matching *any* pattern seen anywhere on the page -- not only those
    named as reference -- is not a deviation. Verse structure means system four
    legitimately resembles system two, and calling that a break would be the
    detector failing at the one job it exists for.

    Returns a :class:`DeviationReport`; it never raises on odd input, because a
    page that does not fit a pattern is the normal case here, not an error.
    """
    grouped = _staff_order(page)
    observed: Dict[int, SystemSignature] = {}
    for system in sorted(grouped):
        parts = grouped[system]
        if parts:
            observed[system] = signature_of(parts)

    if not observed:
        return DeviationReport()

    if reference is None:
        chosen = sorted(observed)[:REFERENCE_SYSTEMS]
    else:
        chosen = sorted(set(reference) & set(observed))
        if not chosen:
            chosen = sorted(observed)[:1]

    patterns: Dict[Tuple, List[int]] = {}
    for system in sorted(observed):
        patterns.setdefault(observed[system].pattern_key, []).append(system)

    report = DeviationReport(
        observed=observed,
        patterns=patterns,
        reference=chosen,
    )
    report.weak = len(report.repeats()) < 1

    # Recurring patterns first. These are *evidence*, and evidence outranks the
    # opening of the page: an earlier version exempted the reference systems,
    # which silently hid the most useful finding on p0020 -- systems 2 and 3
    # agree on G2 G2 F4, and system 1 agreed with neither, but system 1 is in
    # the reference so it could never be reported. A reference is a hypothesis,
    # not an amnesty.
    recurring: List[Tuple] = [
        key for key, members in patterns.items() if len(members) >= min_pattern_members
    ]
    known: List[Tuple] = list(recurring)
    representatives: Dict[Tuple, SystemSignature] = {}
    for system in sorted(observed):
        representatives.setdefault(observed[system].pattern_key, observed[system])
    if not recurring:
        # Nothing repeats, so there is no evidence to outrank anything. The
        # opening systems are then the only patterns available and are taken as
        # such -- with ``weak`` set above, so the reader knows the comparison
        # has nothing behind it.
        for system in chosen:
            key = observed[system].pattern_key
            if key not in known:
                known.append(key)
    else:
        report.weak = False

    for system in sorted(observed):
        signature = observed[system]
        if signature.pattern_key in known:
            continue
        nearest = _nearest(signature, known, representatives)
        report.deviations.append(Deviation(
            system=system,
            signature=signature,
            nearest=nearest,
            broke=nearest.differences(signature) if nearest else [],
        ))

    report.columns = column_analysis(observed)
    return report


def column_analysis(observed: Dict[int, SystemSignature]) -> List["ColumnObservation"]:
    """Agree on what each staff position should be, and flag the positions that
    disagree with the majority.

    This is the part that turns novelty into a *prediction*. Whole-system
    comparison can only say "system 2 is unlike system 3". Column comparison
    says "staff position 3 is F4 in system 2 and F4 in system 3, so it is F4,
    and system 1's position 2 being G2 breaks that".

    Majority, not reference-only: a position established in two systems is
    evidence regardless of which systems opened the page, and a position seen
    once has no majority and so predicts nothing.
    """
    by_position: Dict[int, List[Tuple[int, Optional[Tuple[str, int]]]]] = defaultdict(list)
    by_position_bars: Dict[int, List[Tuple[int, Tuple[int, ...]]]] = defaultdict(list)

    for system, signature in sorted(observed.items()):
        for position, clef in enumerate(signature.clefs):
            by_position[position].append((system, clef))
        for position, lengths in enumerate(signature.bar_lengths):
            if lengths:
                by_position_bars[position].append((system, lengths))

    out: List[ColumnObservation] = []
    for position in sorted(by_position):
        readings = by_position[position]
        tally: Counter = Counter(clef for _, clef in readings)
        agreed, votes = tally.most_common(1)[0]
        outliers = [(system, clef) for system, clef in readings if clef != agreed]
        bars: Dict[int, Counter] = {
            system: Counter(lengths) for system, lengths in by_position_bars.get(position, [])
        }
        bar_tally: Counter = Counter()
        for counts in bars.values():
            bar_tally.update(counts)
        agreed_bars = bar_tally.most_common(1)[0][0] if bar_tally else None
        out.append(ColumnObservation(
            position=position,
            readings={system: clef for system, clef in readings},
            agreed=agreed,
            votes=votes,
            total=len(readings),
            outliers=outliers,
            agreed_bar_length=agreed_bars,
            bar_outliers=[system for system, counts in bars.items()
                          if agreed_bars is not None and agreed_bars not in counts],
        ))
    return out


@dataclass(frozen=True)
class ColumnObservation:
    """What one staff position says across the whole page."""

    #: 0-based index within the system.
    position: int
    #: system -> clef declared at this position.
    readings: Dict[int, Optional[Tuple[str, int]]]
    #: The clef the majority agree on, or None if all readings differ.
    agreed: Optional[Tuple[str, int]]
    votes: int
    total: int
    #: (system, clef) for systems disagreeing with ``agreed``.
    outliers: List[Tuple[int, Optional[Tuple[str, int]]]]
    #: The bar length the majority of systems show at this position.
    agreed_bar_length: Optional[int] = None
    #: Systems whose bars at this position differ from the agreed length.
    bar_outliers: List[int] = field(default_factory=list)

    @property
    def consistent(self) -> bool:
        return not self.outliers and self.agreed is not None

    @property
    def established(self) -> bool:
        """True when enough systems agreed for the majority to be evidence."""
        return self.agreed is not None and self.votes > self.total / 2

    def __str__(self) -> str:
        shown = ("%s%d" % self.agreed) if self.agreed else "no agreement"
        line = ("staff position %d: %s in %d of %d system(s)"
                % (self.position, shown, self.votes, self.total))
        if self.outliers:
            line += "; deviates: " + ", ".join(
                "system %d has %s" % (system, "%s%d" % clef if clef else "none")
                for system, clef in self.outliers)
        if self.bar_outliers:
            line += "; bar length %s elsewhere, deviates: system(s) %s" % (
                self.agreed_bar_length,
                ", ".join(str(system) for system in self.bar_outliers))
        return line


def _nearest(signature: SystemSignature, known: Sequence[Tuple],
             representatives: Dict[Tuple, SystemSignature]):
    """The known pattern differing in the fewest fields. Ties break by order.

    Exact field equality, not a similarity score with weights somebody chose.
    "Differing in the fewest of five structural facts" is a claim I can defend;
    a weighted distance is not, and it would hide the answer inside a number.
    """
    def distance(candidate_key: Tuple) -> Tuple[int, int]:
        """How far a known pattern_key is from this signature, in two parts.

        First part: differing structural fields, plus a penalty for a different
        staff count. Second part: differing fields alone. The split exists
        because ``_nearest`` takes a tie-breaker from ``min``, and a missing
        staff is a different *kind* of problem from a different meter -- so the
        same-count candidate should win a tie on field count alone.

        Exact equality, not a weighted score: "differing in k of three
        structural facts" is a claim I can defend in a bug report, and a
        weighted distance would bury the answer inside a number.
        """
        staff_count, meter, clefs = signature.staff_count, signature.meter, signature.clefs
        k_staff, k_meter, k_clefs = candidate_key
        differing = 0
        if k_staff != staff_count:
            differing += 1
        if k_meter != meter:
            differing += 1
        if k_clefs != clefs:
            differing += 1
        bonus = 0 if k_staff == staff_count else 1
        return (differing + bonus, differing)

    if not known:
        return None
    key = min(known, key=distance)
    # Return a real member of the page, not a stub built from the key. An
    # earlier version reconstructed a signature with empty pitch_counts and
    # bar_lengths, so every "expected" phrase for those two fields read
    # "expected []" -- noise that buried the two phrases that mattered.
    return representatives.get(key)


def summarise(page: Page, reference: Optional[Iterable[int]] = None) -> str:
    """Convenience wrapper: the report as a string."""
    return find_deviations(page, reference).summarise()


def example_page() -> Page:
    """A tiny page: three matching systems and one that gained a staff.

    Used by the tests and by anyone wanting to see the output shape without
    a corpus. Systems 1-3 are one pattern; system 4 has three staves where the
    others have two, which is the kind of break a real recognition failure
    produces.
    """
    from .graph import Measure, Note, Page, Part, Pitch

    def notes_for(measure_number: int, staff: int, octave: int, count: int) -> List[Note]:
        return [
            Note(staff=staff, voice=1, onset=index * 4, duration=4,
                 pitch=Pitch("C", 0, octave), measure=measure_number)
            for index in range(count)
        ]

    def piano(system: int) -> Part:
        """One piano part spanning two staves, as homr emits it."""
        treble = Measure(number=1, clef=("G", 2),
                         notes=notes_for(1, 1, 4, 2))
        bass = Measure(number=1, clef=("F", 4),
                       notes=notes_for(1, 2, 2, 2))
        return Part(
            id="p%d" % system,
            name="Piano",
            system=system,
            staff_index=0,
            measures=[treble, bass],
            clef_by_staff={1: ("G", 2), 2: ("F", 4)},
        )

    def voice(system: int) -> Part:
        return Part(
            id="v%d" % system,
            name="Voice",
            system=system,
            staff_index=1,
            measures=[Measure(number=1, clef=("G", 2),
                              notes=notes_for(1, 1, 5, 1))],
            clef_by_staff={1: ("G", 2)},
        )

    parts: List[Part] = []
    for system in (1, 2, 3):
        parts.append(piano(system))
        parts.append(voice(system))
    # System 4: the piano has gained a third staff, and the voice is missing.
    broken = piano(4)
    broken.id = "p4"
    broken.staff_index = 0
    broken.measures = [
        Measure(number=1, clef=("G", 2), notes=notes_for(1, 1, 4, 2)),
        Measure(number=1, clef=("F", 4), notes=notes_for(1, 2, 2, 2)),
        Measure(number=1, clef=("C", 3), notes=notes_for(1, 3, 3, 1)),
    ]
    broken.clef_by_staff = {1: ("G", 2), 2: ("F", 4), 3: ("C", 3)}
    parts.append(broken)
    return Page(id="example", parts=parts)