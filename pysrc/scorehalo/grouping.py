"""Group per-staff recognitions into the parts (instruments) of a page.

homr recognises a page one staff at a time, and hands each fragment back as an
independent single-staff part carrying a role name that is not usable ("Voice"
for every staff, or "Staff N (system X/Y)"). A printed page is not a pile of
staves: it is a set of *systems* laid out in time order, each holding one or
more *parts* sounding together, and a part with two staves is one instrument.

Two facts drive the grouping:

* **A brace states which staves are one instrument.** It is the only evidence on
  the page that does not require guessing: a brace over two staves means one
  instrument in two registers, and a staff no brace touches is a separate
  instrument -- the singer. Measured on p0020 (see :mod:`scorehalo.brace`),
  this yields a piano-only first system and voice-over-piano thereafter, which
  is what the page actually shows.
* A part that appears in more than one system is the *same* instrument
  continuing, so its measures concatenate and renumber across systems. This is
  what makes the emitted score a single flowing performance rather than one
  instrument per system fragment.

``brace_partitions`` decides the pairing, ``assemble_page`` decides the
arithmetic. The geometry detectors that will eventually supply ``system``,
``clef`` and ``brace_ids`` are deliberately not imported here: this module takes
them as inputs so the arithmetic can be tested against known-good values before
any vision work exists.

The clef-based pairing that used to be the only rule survives as a **fallback**
(:func:`role_staves` with no ``brace_ids``), and it warns when used. In a
three-staff system it must choose between pairing staves 2-3 and pairing 1-2,
and the clefs give it no evidence either way -- so it assumes the singer is the
topmost staff. For p0020 that is right. For a singer *between* two braced groups
it is wrong, and wrong quietly: clefs ``(G, G, F)`` are what a voice-on-top
layout prints and what a voice-in-the-middle layout prints, so the rule folds the
vocal line into the piano without complaining.
"""
import warnings
from dataclasses import replace
from typing import Dict, List, Optional, Sequence, Tuple

from .graph import Measure, Note, Page, Part, Pitch

Clef = Tuple[str, int]

#: Conventional name for a braced group. Pixels identify the *shape* (a brace
#: over two or more staves), not the instrument: this could equally be a harp, an
#: organ or a marimba. Rename per corpus; do not read it as a fact.
TWO_STAFF_ROLE = "Piano"

#: Name for an unbraced staff sharing a system with a braced group -- the
#: singing or solo line.
SOLO_ROLE = "Voice"

ClefTuple = Optional[Clef]
GROUPS = (
    (("G", 2), ("F", 4)),
    (("G", 2), ("F", 3)),
)
#: One system's parts: the staff indices, and whether a brace covers them.
Partition = List[Tuple[List[int], bool]]


def brace_partitions(
    clefs: Sequence[ClefTuple],
    brace_ids: Optional[Sequence[Optional[object]]] = None,
) -> Partition:
    """Split one system's staves into parts, preferring brace geometry.

    ``clefs`` are the system's staves in reading order. ``brace_ids`` is a
    parallel sequence: ``None`` for a staff no brace touches, any other value
    for a staff under a brace, with equal values meaning the same brace. Ids are
    only ever compared *within* one system -- matching the same instrument across
    systems is not something a brace can tell you, and pretending otherwise is
    what fused three separate pianos into one part on p0020.

    When ``brace_ids`` is omitted, pairing falls back to clefs and warns. The
    fallback has to decide, without evidence, whether a three-staff system pairs
    its 2nd-3rd staves (singer on top) or its 1st-2nd (singer below), and it
    picks the first. That is right for most songs and silently wrong for a
    singer bracketed between two accompaniment groups.

    Raises ``ValueError`` if a brace covers non-adjacent staves, since a brace
    spans a contiguous run and anything else means the geometry is misread.
    """
    if brace_ids is not None:
        return _partitions_from_braces(len(clefs), brace_ids)
    warnings.warn(
        "brace_partitions: no brace geometry supplied; pairing staves by clef. "
        "In a three-staff system this cannot tell a singer on top from a singer "
        "below, and it picks 'on top', folding the vocal line into the "
        "accompaniment when the page actually says otherwise.",
        RuntimeWarning, stacklevel=2)
    return [(g, len(g) > 1) for g in _clef_groups(clefs)]


def _partitions_from_braces(
    count: int,
    brace_ids: Sequence[Optional[object]],
) -> Partition:
    """Partition by brace membership. Staves read in order, groups in order."""
    if len(brace_ids) != count:
        raise ValueError(
            "brace_ids has %d entries for %d staves; they are parallel "
            "sequences and must agree" % (len(brace_ids), count))
    buckets: Dict[object, List[int]] = {}
    seen: List[object] = []
    out: Partition = []
    for index, bid in enumerate(brace_ids):
        if bid is None:
            out.append(([index], False))
            continue
        if bid not in buckets:
            buckets[bid] = []
            seen.append(bid)
        buckets[bid].append(index)
    for bid in seen:
        members = buckets[bid]
        if members[-1] - members[0] + 1 != len(members):
            raise ValueError(
                "brace %r covers staves %s, which are not contiguous; a brace "
                "spans a continuous run, so the staff geometry is being "
                "misread" % (bid, members))
        out.append((members, True))
    out.sort(key=lambda item: item[0][0])
    return out


def _clef_groups(clefs: Sequence[ClefTuple]) -> List[List[int]]:
    """The old rule: a treble staff directly above a bass staff is one part.

    Kept only as a fallback. It reads ``(G, G, F)`` as "singer on top" and
    ``(G, F, G)`` as "singer below", which are the same observation -- a bass
    clef in last position -- so the two layouts are indistinguishable here.
    """
    groups: List[List[int]] = []
    i = 0
    while i < len(clefs):
        pair = (clefs[i], clefs[i + 1]) if i + 1 < len(clefs) else None
        if pair in GROUPS:
            groups.append([i, i + 1])
            i += 2
        else:
            groups.append([i])
            i += 1
    return groups


def role_staves(
    clefs: Sequence[ClefTuple],
    brace_ids: Optional[Sequence[Optional[object]]] = None,
) -> List[List[int]]:
    """Partition the staves of one system into parts.

    Thin wrapper over :func:`brace_partitions` for callers that only want the
    staff groupings and do not care whether a brace justified them.
    """
    return [group for group, _ in brace_partitions(clefs, brace_ids)]


def _name_group(braced: bool, ordinal: int) -> str:
    """Name a part, disambiguating repeats so two instruments cannot fuse.

    ``ordinal`` is this group's index among groups of the same kind *within one
    system*, so the first braced group is the same instrument in every system
    while a second braced group (a harp beside a piano) becomes its own part
    instead of being appended to the piano's.
    """
    base = TWO_STAFF_ROLE if braced else SOLO_ROLE
    return base if ordinal == 0 else "%s %d" % (base, ordinal + 1)


def system_offsets(measure_counts: Sequence[int]) -> Dict[int, int]:
    """First measure number of each system, in reading order.

    Each system shows the bars that follow the previous one, so measure numbers
    are local to a system and must be offset before they can be compared. The
    system that lasts longest sets the width of the gap; a staved part cannot
    play more bars than there is room for.
    """
    offsets: Dict[int, int] = {}
    running = 0
    for index, count in enumerate(measure_counts):
        offsets[index] = running + 1
        running += count or 0
    return offsets


def assemble_page(
    staves: Sequence[Part],
    systems: Sequence[int],
    clefs: Sequence[ClefTuple],
    brace_ids: Optional[Sequence[Optional[object]]] = None,
    id: str = "page",
    divisions: int = 4,
    beats: int = 4,
    beat_type: int = 4,
    key_fifths: int = 0,
    dpi: int = 300,
) -> Page:
    """Build a :class:`Page` whose parts are instruments, not staff fragments.

    ``staves`` are the per-staff fragments in reading order; ``systems`` and
    ``clefs`` are parallel sequences giving the system each staff was printed in
    and the clef on it. ``brace_ids`` is a third parallel sequence marking which
    brace covers each staff (``None`` for none); without it, clefs are used and
    :func:`brace_partitions` warns. Ids are compared within a system only.

    Every fragment keeps its notes -- this only decides *which staff belongs to
    which instrument*, never what is on it.
    """
    order: List[int] = []
    seen: Dict[int, None] = {}
    by_system: Dict[int, List[int]] = {}
    for idx, sys_no in enumerate(systems):
        if sys_no not in seen:
            seen[sys_no] = None
            order.append(sys_no)
        by_system.setdefault(sys_no, []).append(idx)
    order.sort()

    counts = [
        max((len(staves[i].measures) for i in by_system[s]), default=0)
        for s in order
    ]
    offsets = system_offsets(counts)

    roles: Dict[Tuple[bool, int], Part] = {}
    first_seen: Dict[Tuple[bool, int], int] = {}
    # Vertical order is evidence, not an accident of which system was seen
    # first: a part printed ABOVE another in the systems where both appear
    # stays above it on the page. p0020's system 1 shows the piano alone, so
    # first-appearance order puts Piano before Voice and engraves the singer
    # UNDER the accompaniment -- contradicting the same book that says
    # "piano under her". Systems with a single part abstain: with one part
    # printed there is nothing to compare.
    votes: Dict[Tuple[bool, int], List[int]] = {}
    for sys_index, sys_no in enumerate(order):
        idxs = by_system[sys_no]
        sub_ids = None if brace_ids is None else [brace_ids[i] for i in idxs]
        parts = brace_partitions([clefs[i] for i in idxs], sub_ids)
        offset = offsets[sys_index]
        # Ordinals count groups of the same kind *within this system*, so the
        # first braced group is one instrument throughout the page while a
        # second braced group stays a separate part instead of being appended
        # to the first one's notes.
        used: Dict[bool, int] = {True: 0, False: 0}
        for rank, (group, braced) in enumerate(parts):
            ordinal = used[braced]
            used[braced] += 1
            # Identity is (kind, ordinal), NOT the name: the name is derived
            # from the identity. Keying on name instead would fuse two
            # braced groups that happen to share a conventional label, and
            # keying on len(roles) instead would make every group a new part
            # and shatter the same instrument across systems.
            key = (braced, ordinal)
            if key not in first_seen:
                first_seen[key] = len(first_seen)
            if len(parts) > 1:
                votes.setdefault(key, []).append(rank)
            role = roles.get(key)
            if role is None:
                role = Part(id="R%d" % (len(roles) + 1),
                            name=_name_group(braced, ordinal))
                roles[key] = role
            wide = len(group) > 1
            for local, member in enumerate(group, start=1):
                staff = staves[idxs[member]]
                role.clef_by_staff[local] = clefs[idxs[member]] or ("G", 2)
                for meas in staff.measures:
                    role.measures.append(Measure(
                        number=offset + meas.number - 1,
                        divisions=divisions, beats=beats,
                        beat_type=beat_type, key_fifths=key_fifths,
                        notes=_restamp(meas.notes, local, wide)))
            role.system = sys_no
    page = Page(id=id, divisions=divisions, beats=beats, beat_type=beat_type,
                key_fifths=key_fifths, dpi=dpi)
    page.parts = _order_roles(roles, first_seen, votes)
    _pad(page, roles)
    return page


def _order_roles(
    roles: Dict[Tuple[bool, int], Part],
    first_seen: Dict[Tuple[bool, int], int],
    votes: Dict[Tuple[bool, int], List[int]],
) -> List[Part]:
    """Parts top-to-bottom: the vertical vote if every part has one, else
    first appearance.

    Every part must have comparable votes or NONE of them do -- mixing bases
    would claim an order no system ever printed. A part's rank is its median
    position among the groups of the multi-part systems it plays in (groups
    come from ``brace_partitions`` in reading order, so rank 0 is the top
    staff); ties fall back to first appearance, which is the abstention.
    Ordering by the id string is deliberately not done: "R10" sorts before
    "R2", so a ten-part page would come out scrambled.
    """
    keys = sorted(roles, key=lambda k: first_seen[k])
    if keys and all(k in votes for k in roles):
        def vertical(key: Tuple[bool, int]) -> Tuple[int, int]:
            ranks = sorted(votes[key])
            return (ranks[len(ranks) // 2], first_seen[key])
        keys = sorted(keys, key=vertical)
    return [roles[k] for k in keys]


def _restamp(notes: Sequence["object"], local: int, wide: bool) -> List["object"]:
    """Move each note onto the staff it now belongs to within its part.

    homr recognises a page one staff at a time, so every fragment's notes all
    carry ``staff=1`` -- each fragment *was* staff 1 of its own. Once two
    fragments are merged into one two-staff instrument those stamps are wrong:
    the piano's lower staff ends up printed on staff 1, giving one staff with
    both hands on it. That is the original defect, one layer up from where it
    first appeared.

    ``wide`` is False for a one-staff part, where no restamping happens: a lone
    staff is staff 1 by definition, and rewriting it would only hide a fragment
    that genuinely mislabelled itself.

    Returns new ``Note`` objects rather than mutating the input, because the
    fragments belong to the caller and are reused across systems.
    """
    if not wide and local == 1:
        return list(notes)
    return [replace(n, staff=local) for n in notes]


def _pad(page: Page, roles: Dict[str, Part]) -> None:
    """Give every part the same measure count, so a score is bar-aligned.

    The emitter renders one measure per source measure, so a part that plays
    only in the middle of a page would otherwise shift every following bar
    against the parts above it.
    """
    total = 0
    for part in page.parts:
        for meas in part.measures:
            total = max(total, meas.number)
    for part in page.parts:
        by_number: Dict[int, List[Measure]] = {}
        for meas in part.measures:
            by_number.setdefault(meas.number, []).append(meas)
        merged: List[Measure] = []
        for number in range(1, total + 1):
            found = by_number.get(number)
            if found:
                notes = [n for m in found for n in m.notes]
                if notes:
                    merged.append(Measure(
                        number=number, divisions=page.divisions,
                        beats=page.beats, beat_type=page.beat_type,
                        key_fifths=page.key_fifths, notes=notes))
                    continue
                # homr read this bar as EMPTY -- attributes, no notes at all.
                # A bar with zero ticks is not a bar: importers report it as a
                # short measure or drop the part's alignment. Silence is music,
                # so an empty bar is a whole-measure rest, exactly as if the bar
                # were missing from the fragment. The caller reports it.
                merged.append(Measure(
                    number=number, divisions=page.divisions,
                    beats=page.beats, beat_type=page.beat_type,
                    key_fifths=page.key_fifths, is_empty=True,
                    notes=[_full_measure_rest(number, page)]))
            else:
                merged.append(Measure(
                    number=number, divisions=page.divisions,
                    beats=page.beats, beat_type=page.beat_type,
                    key_fifths=page.key_fifths, is_empty=True,
                    notes=[_full_measure_rest(number, page)]))
        part.measures = merged


def _full_measure_rest(number: int, page: Page) -> "object":
    from .graph import Note
    return Note(staff=1, voice=1, onset=0,
                duration=page.divisions * page.beats,
                pitch=None, measure=number, is_rest=True)
