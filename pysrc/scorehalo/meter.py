"""Decide a page's meter page-wide, and report where a fragment disagreed.

The problem this exists to solve, measured on p0020:

    system 1  declares 3/4   notes in those bars run to 4 quarters
    system 2  declares 4/4
    system 3  declares 4/4   (sys3 declares nothing at all)

``build_page_graph.py`` already computed the page-wide answer -- the modal
meter, 4/4 -- and printed a warning that the systems disagree. But it applied
that answer only to the *padded* measures. Real measures were carried across
with whatever their own fragment declared, so system 1 kept 3/4, its bars were
36 ticks long, and every note at or past the 36th tick was dropped by the
emitter. Four pitched notes were silently discarded to keep a bar the same
length as a label the rest of the page contradicts.

The decision was made and then thrown away. This module makes the decision
stick.

Why not simply stamp every measure with the modal meter: because music really
does change meter mid-piece, and blanketing the page would destroy a genuine
change just as thoroughly as it fixes a misread one. So the rule is narrower:

    Override a measure only when its own content contradicts its own meter.

A measure whose notes overflow its declared bar, and which fit inside the
page-wide meter instead, is a misread meter -- that is the one case where the
declaration is provably wrong rather than merely different. A genuine meter
change has content that fits the meter it declares, and is left alone.

The note this rests on is the one Wilbur insisted on twice: the notes are the
music, the meter is a description of the music. When the two disagree and the
description is the thing contradicted, the description loses.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Dict, List, Tuple

from .graph import Measure, Note, Page


@dataclass(frozen=True)
class MeterOverride:
    """One measure whose declared meter was replaced, and why."""

    part: str
    measure: int
    staff: int
    was: Tuple[int, int]
    now: Tuple[int, int]
    #: Ticks the measure's own content occupies.
    needed: int
    #: Ticks the measure's *declared* bar could hold -- the boundary the notes
    #: were being lost past. Not the page length; the first wording of this
    #: message printed the page length here and called it the declared bar,
    #: which reported a 3/4 bar as holding 48 ticks.
    declared_held: int
    #: Ticks the replacement bar holds.
    now_holds: int


def measure_extent(measure: Measure) -> int:
    """How many ticks of bar this measure's own content occupies.

    Simultaneity is equality of onsets, so the extent is the furthest end of
    any single (staff, voice) line -- not the sum of every note. Summing a
    two-staff piano's notes gives roughly twice the bar, which would make every
    grand staff look overfull.
    """
    furthest: Dict[Tuple[int, int], int] = {}
    for note in measure.notes:
        line = (note.staff, note.voice)
        end = note.onset + note.duration
        if end > furthest.get(line, 0):
            furthest[line] = end
    return max(furthest.values(), default=0)


def declared_meters(page: Page) -> Counter:
    """How many measures declare each meter, page-wide."""
    counts: Counter = Counter()
    for part in page.parts:
        for measure in part.measures:
            counts[(measure.beats, measure.beat_type)] += 1
    return counts


def reconcile_meter(page: Page) -> List[MeterOverride]:
    """Fix measures that contradict their own meter, and report every one.

    Returns the overrides it made, in page order, so a caller can print them.
    The page's own ``beats``/``beat_type`` are treated as the page-wide truth;
    :func:`resolve_page_meter` is what decides them.
    """
    target = (page.beats, page.beat_type)
    target_length = page.divisions * page.beats * 4 // page.beat_type
    overrides: List[MeterOverride] = []
    for part in page.parts:
        for measure in part.measures:
            was = (measure.beats, measure.beat_type)
            if was == target or measure.is_empty:
                continue
            needed = measure_extent(measure)
            if not needed or needed <= measure.length:
                continue          # fits what it declares: a real meter change
            if target_length and needed > target_length:
                continue          # does not fit the page meter either
            # Read the declared length BEFORE overwriting the meter: Measure.length
            # is derived from beats/beat_type, so assigning first makes
            # declared_held report the replacement length and the message claims
            # a 3/4 bar held 48 ticks.
            declared_held = measure.length
            measure.beats, measure.beat_type = target
            overrides.append(MeterOverride(
                part=part.name, measure=measure.number,
                staff=max((n.staff for n in measure.notes), default=1),
                was=was, now=target, needed=needed,
                declared_held=declared_held, now_holds=target_length))
    return overrides


def resolve_page_meter(page: Page) -> Tuple[int, int]:
    """Choose the page-wide meter and record it on the page.

    The modal declared meter, computed over measures rather than fragments, so
    a long system outvotes a short one. Ties break toward 4/4, which is the
    reading a bar of four beats already implies.
    """
    counts = declared_meters(page)
    if not counts:
        return page.beats, page.beat_type
    best = max(counts.items(), key=lambda kv: (kv[1], kv[0] == (4, 4)))[0]
    page.beats, page.beat_type = best
    return best


def format_overrides(overrides: List[MeterOverride]) -> str:
    """One line per override, stating the loss it prevents."""
    if not overrides:
        return ""
    lines = ["page meter applied to %d measure(s) whose own content "
             "overran their declared meter:" % len(overrides)]
    for o in overrides:
        lines.append("  %s measure %d staff %d: %d/%d -> %d/%d "
                     "(content needs %d ticks, the declared bar held %d, "
                     "so %d tick(s) of music past it would have been dropped)"
                     % (o.part, o.measure, o.staff, o.was[0], o.was[1],
                        o.now[0], o.now[1], o.needed, o.declared_held,
                        o.needed - o.declared_held))
    return "\n".join(lines)


@dataclass(frozen=True)
class BarExtension:
    """One bar lengthened page-wide because its content overran the meter."""

    measure: int
    was: Tuple[int, int]
    now: Tuple[int, int]
    #: Ticks the widest part's content occupies at this bar.
    needed: int
    #: Ticks the bar held before the extension.
    held: int


def extend_overfull_bars(page: Page,
                         max_factor: float = 2.0
                         ) -> Tuple[List[BarExtension], List[BarExtension]]:
    """Lengthen, page-wide, bars whose content overruns their meter.

    Runs after :func:`reconcile_meter`: that one handles content that fits the
    page meter under a wrong declaration; this one handles content that fits
    no declared bar at all -- homr read more music into the bar than the
    printed meter holds (a missed barline, an over-long duration).

    The emitter's fallback for such bars drops every note whose onset lies
    past the barline and truncates the note crossing it -- on the dense pages
    of the Carpenters book that was 4-13% of a page's notes. Dropping kills
    real strikes; squeezing the content (scaling onsets to fit) moves every
    note and pulls the hands off each other's read positions. Both fabricate.
    The barline is the thing contradicted here, so the barline gives: the bar
    gets the smallest meter that CONTAINS the content, in the page's own
    beat-type, and every part at that bar gets it too -- the parts stay
    bar-aligned and no note moves or dies. Silent bars take the new meter
    as well, so a tacet part's timeline keeps pace with the part that
    overflowed.

    Bars needing more than ``max_factor`` times the page meter are left
    alone: content of several bar-lengths is a misread beyond repair, and
    dressing it in a nine-beat time signature would be fiction wearing a
    label. They come back as unrepairable so the caller can report them and
    the emitter's drop still stands, visibly.

    Returns ``(extended, unrepairable)`` in bar order.
    """
    target = (page.beats, page.beat_type)
    target_length = page.divisions * page.beats * 4 // page.beat_type
    cap = int(target_length * max_factor)
    widest: Dict[int, int] = {}
    for part in page.parts:
        for measure in part.measures:
            extent = measure_extent(measure)
            if extent > widest.get(measure.number, 0):
                widest[measure.number] = extent

    extended: List[BarExtension] = []
    unrepairable: List[BarExtension] = []
    for number in sorted(widest):
        needed = widest[number]
        if needed <= target_length:
            continue
        if needed > cap:
            unrepairable.append(BarExtension(number, target, target,
                                             needed, target_length))
            continue
        # Smallest beat count, in the page's beat-type, whose bar holds the
        # content: ceil(needed * beat_type / (divisions * 4)).
        beats = -(-needed * page.beat_type // (page.divisions * 4))
        now = (beats, page.beat_type)
        for part in page.parts:
            for measure in part.measures:
                if measure.number == number:
                    measure.beats, measure.beat_type = now
        extended.append(BarExtension(number, target, now,
                                     needed, target_length))
    return extended, unrepairable


def format_bar_extensions(extended: List[BarExtension],
                          unrepairable: List[BarExtension]) -> str:
    """One line per bar: what was lengthened, and what could not be."""
    lines: List[str] = []
    if extended:
        lines.append("bar(s) lengthened page-wide so overrunning content "
                     "survives (nothing moves; the barline gives instead):")
        for e in extended:
            lines.append("  measure %d: %d/%d -> %d/%d "
                         "(content needs %d ticks, the bar held %d)"
                         % (e.measure, e.was[0], e.was[1],
                            e.now[0], e.now[1], e.needed, e.held))
    if unrepairable:
        lines.append("bar(s) whose content overruns even the lengthened "
                     "limit -- left alone; the emitter still drops what "
                     "lies past their barline:")
        for e in unrepairable:
            lines.append("  measure %d: content needs %d ticks, the page "
                         "bar holds %d" % (e.measure, e.needed, e.held))
    return "\n".join(lines)