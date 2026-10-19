"""Count the same thing two ways, and refuse to proceed until the counts agree.

The rule this encodes
---------------------
In the Navy, figures are counted twice by independent hands. If the two counts
arrive at the same total, the number is accepted. If they do not, the number is
not used: you go back and find the discrepancy before going any further.

That is the discipline every recogniser needs and almost none has. A single
count tells you how many notes there are. A count taken twice tells you whether
that number means anything. ScoreHalo had six invariants and still shipped a
bass line an octave wrong, because all six were computed from one pipeline -- if
the pipeline is wrong, its own checks agree with it perfectly.

The one asset that made this cheap here: homr was run twice over the same page
with *different crops*, once per staff and once per system. Those are two
independent counts of one page, already sitting on disk, disagreeing in places.
Nothing needed to be invented; it needed to be compared.

What counts as comparable
-------------------------
Not every field can be compared across two runs, and pretending otherwise is
worse than not comparing at all. Measure counts cannot: homr emits extra
opening measures in a system-wide crop (a bar holding one or two notes) that
have no counterpart in a per-staff crop. Comparing them produces a large,
confident, meaningless difference. So each field declares whether it is
comparable, and an incomparable field is reported as *not compared* rather than
as agreement or disagreement. :class:`Tally` is where that choice lives, per
field, and it is the part most worth arguing about.

What this cannot do
-------------------
* It cannot say which count is right. Two wrongs that disagree point at the
  disagreement, not at the culprit -- though a disagreement localised to one
  system is a very good place to start looking.
* It cannot compare runs that share a fault. Two crops from the same mistaken
  layout analysis are not independent, and their agreement means nothing.
* It does not repair anything. It produces questions, in order of usefulness.

Examples
--------
>>> from scorehalo import reconcile
>>> left = reconcile.tally_musicxml("staff/s1_in.musicxml")
>>> right = reconcile.tally_musicxml("system/sys1_in.musicxml")
>>> report = reconcile.reconcile("per-staff", left, "per-system", right)
>>> report.agree("pitched")
True
>>> print(report.summarise())
"""
from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import xml.etree.ElementTree as ET

#: Fields compared by value across two counts.
COUNT_FIELDS = ("notes", "rests", "pitched")

#: Fields that cannot be compared across different crop granularities.
INCOMPARABLE_ACROSS_CROPS = ("measures",)


def _pitch_key(note) -> Optional[Tuple[str, str, str]]:
    element = note.find("pitch")
    if element is None:
        return None
    return (element.findtext("step") or "?", element.findtext("alter") or "",
            element.findtext("octave") or "?")


#: ``alter`` comes from ElementTree as TEXT, so it is a string here and not an
#: int. Keying an accidental lookup on ints therefore misses every time and
#: prints "B-13" where it meant "Bb3" -- which is how a pitch disagreement reads
#: as gibberish instead of as a note.
_ACCIDENTAL = {"-2": "bb", "-1": "b", "0": "", "1": "#", "2": "##", "": ""}


def pitch_name(step: str, alter, octave) -> str:
    """Render a :func:`_pitch_key` triple as a readable pitch name.

    Accepts ``alter`` as either text or int, because the key yields text and a
    caller holding a real ``<alter>`` value may hand over a number.
    """
    text = alter if isinstance(alter, str) else ("" if alter in (None, 0)
                                                 else str(alter))
    return "%s%s%s" % (step, _ACCIDENTAL.get(text, "?" + text), octave)


@dataclass
class Tally:
    """One independent count of one body of MusicXML.

    ``comparable`` records, per field, whether that field may be compared with
    the other count. The default is chosen so that the same crop granularity
    compares everything, and a cross-granularity comparison narrows itself
    rather than reporting confident nonsense.
    """

    label: str
    measures: int = 0
    notes: int = 0
    rests: int = 0
    pitched: int = 0
    #: Pitch multiset, as (step, alter, octave) -> count. The finest thing two
    #: runs can be compared on, and the one that localises a disagreement to
    #: actual notes rather than to a total.
    pitches: Counter = field(default_factory=Counter)
    #: staff -> note count, for a run covering more than one staff.
    by_staff: Dict[str, int] = field(default_factory=dict)
    comparable: Tuple[str, ...] = COUNT_FIELDS

    def count(self, name: str) -> int:
        return int(getattr(self, name))

    def pitch_diff(self, other: "Tally") -> Dict[Tuple[str, str, str], int]:
        """Signed multiset difference against ``other``.

        Positive means this tally has notes ``other`` lacks; negative means the
        reverse. Zero entries are dropped, so the result names only the notes
        that actually differ and in which direction.
        """
        difference: Counter = Counter()
        for key in set(self.pitches) | set(other.pitches):
            delta = self.pitches.get(key, 0) - other.pitches.get(key, 0)
            if delta:
                difference[key] = delta
        return dict(difference)


def _compare_fields(left: Tally, right: Tally) -> Tuple[str, ...]:
    """The fields both tallies agree are comparable."""
    return tuple(name for name in COUNT_FIELDS
                 if name in left.comparable and name in right.comparable)


def tally_musicxml(path: str, label: Optional[str] = None,
                   comparable: Optional[Sequence[str]] = None) -> Tally:
    """Count one MusicXML file.

    A file, not a page: the two runs are separate files per crop, and counting
    them together is the caller's job via :func:`combine`. Counting the whole
    page in one go would destroy the very thing reconciliation needs -- the
    knowledge of *which* crop disagreed.
    """
    root = ET.parse(path).getroot()
    tally = Tally(label=label or os.path.basename(path))
    tally.measures = len(root.findall(".//measure"))
    for note in root.findall(".//note"):
        tally.notes += 1
        staff = note.findtext("staff") or "1"
        tally.by_staff[staff] = tally.by_staff.get(staff, 0) + 1
        if note.find("rest") is not None:
            tally.rests += 1
            continue
        tally.pitched += 1
        key = _pitch_key(note)
        if key is not None:
            tally.pitches[key] += 1
    if comparable is not None:
        tally.comparable = tuple(comparable)
    return tally


def combine(tallies: Iterable[Tally], label: Optional[str] = None) -> Tally:
    """Sum several tallies into one, for comparing a whole run's totals.

    Staff counts are kept as strings and summed numerically, so a per-staff run
    and a per-system run both reduce to "how many notes in total" -- but
    per-staff staffs are numbered 1 in every fragment while a per-system crop
    numbers them 1 and 2, so :attr:`Tally.by_staff` is only meaningful within
    one granularity. It is kept for per-file drill-down, not for cross-run
    comparison.
    """
    tallies = list(tallies)
    total = Tally(label=label or "+".join(t.label for t in tallies))
    for tally in tallies:
        total.measures += tally.measures
        total.notes += tally.notes
        total.rests += tally.rests
        total.pitched += tally.pitched
        total.pitches.update(tally.pitches)
    shared = set(tallies[0].comparable) if tallies else set(COUNT_FIELDS)
    for tally in tallies[1:]:
        shared &= set(tally.comparable)
    total.comparable = tuple(name for name in COUNT_FIELDS if name in shared)
    return total


@dataclass
class Disagreement:
    """One field the two counts did not agree on."""

    field_name: str
    left: int
    right: int

    @property
    def delta(self) -> int:
        return self.right - self.left

    def __str__(self) -> str:
        return ("%s: %d vs %d (%+d)"
                % (self.field_name, self.left, self.right, self.delta))


@dataclass
class Reconciliation:
    """The verdict of counting one body of data twice."""

    left: Tally
    right: Tally
    disagreements: List[Disagreement] = field(default_factory=list)
    #: pitch multiset difference, populated only when a pitch check was asked for.
    pitch_difference: Optional[Dict[Tuple[str, str, str], int]] = None
    #: Fields present in both counts but deliberately not compared.
    skipped: Tuple[str, ...] = ()

    def agree(self, name: str = "pitched") -> bool:
        """True when the named field matched.

        A field that could not be compared does **not** count as agreement. The
        first version of this said "matched, or was not comparable at all", which
        made a run that measured nothing come back agreeing with everything.
        """
        return (self.measured
                and not any(d.field_name == name for d in self.disagreements))

    @property
    def twin_counts(self) -> bool:
        """True when every comparable count field agreed.

        Note this ignores the pitch multiset: agreeing on *how many* is a
        weaker result than agreeing on *which*, and :attr:`twin_pitches` is the
        stronger one. A page whose counts match but whose notes differ has
        traded one note for another and lost information, which is exactly the
        failure a count alone would hide.
        """
        return self.measured and not self.disagreements and not self.skipped

    @property
    def measured(self) -> bool:
        """True when at least one side actually counted something.

        Without this, a run that measured *nothing* read as agreement: the
        comparable-field intersection comes back empty, nothing is compared,
        no disagreement is recorded, and ``agree()`` returns True. For a
        double-count check that is the worst possible failure -- the run that
        found nothing is precisely the one that must be caught, and it was
        being reported as a clean pass.
        """
        return bool(self.left.comparable) or bool(self.right.comparable)

    @property
    def twin_pitches(self) -> bool:
        """True when the two counts agree on the notes themselves."""
        return (self.measured and not self.disagreements and not self.skipped
                and not self.pitch_difference)

    def describe_pitches(self) -> str:
        if not self.pitch_difference:
            return "pitch multisets are identical"
        parts = []
        for (step, alter, octave), delta in sorted(self.pitch_difference.items()):
            name = "%s%s%s" % (step, {"-1": "b", "0": "", "1": "#", "": ""}.get(alter, alter), octave)
            parts.append("%s %+d" % (name, delta))
        return "pitches differ: " + ", ".join(parts)

    def summarise(self) -> str:
        """Report, leading with the verdict a reader needs first."""
        if self.twin_pitches:
            return ("%s and %s: IDENTICAL TWINS (%d notes, %d pitched)"
                    % (self.left.label, self.right.label,
                       self.left.notes, self.left.pitched))
        if not self.measured:
            return ("%s and %s: NEITHER RUN MEASURED ANYTHING -- this is not "
                    "agreement, it is an absent count"
                    % (self.left.label, self.right.label))
        lines = ["%s and %s DO NOT AGREE" % (self.left.label, self.right.label)]
        for disagreement in self.disagreements:
            lines.append("  " + str(disagreement))
        if self.pitch_difference is not None:
            lines.append("  " + self.describe_pitches())
        if self.skipped:
            lines.append("  not compared (crop granularity differs): %s"
                         % ", ".join(self.skipped))
        return "\n".join(lines)


def reconcile(left: Tally, right: Tally,
              compare_pitches: bool = True) -> Reconciliation:
    """Compare two counts and report where they part company.

    ``compare_pitches`` defaults on because the total count agreeing while the
    notes differ is the interesting case, not the reassuring one.
    """
    report = Reconciliation(left=left, right=right)
    if not report.measured:
        # Neither side counted anything. Comparing nothing reports nothing
        # wrong, so say so in words rather than emitting a clean bill.
        report.disagreements.append(Disagreement("notes measured", 0, 0))
        report.skipped = COUNT_FIELDS
        return report

    if not left.comparable or not right.comparable:
        # One side measured nothing. Compare everything, so its zeros surface as
        # real disagreements instead of disappearing into the "not compared"
        # bucket -- an unrun crop is exactly the thing that must be reported.
        fields = COUNT_FIELDS
    else:
        fields = _compare_fields(left, right)
    report.skipped = tuple(name for name in COUNT_FIELDS if name not in fields)
    for name in fields:
        a, b = left.count(name), right.count(name)
        if a != b:
            report.disagreements.append(Disagreement(name, a, b))
    if compare_pitches:
        difference = left.pitch_diff(right)
        if difference:
            report.pitch_difference = difference
    return report


def tally_directory(directory: str, label: Optional[str] = None,
                    pattern: str = "*_in.musicxml") -> Tally:
    """Count every fragment in a directory and sum them into one total.

    This is the page-level counterpart to :func:`tally_musicxml`, and it exists
    because the two crops of a page have **different granularity** and so can
    never be paired file by file:

        data/out-system/p0020/    p0020.sys1_in.musicxml  (several staves)
                                 p0020.sys2_in.musicxml
                                 p0020.sys3_in.musicxml
        data/out-perstaff/p0020/  p0020.s1_in.musicxml .. p0020.s8_in.musicxml

    Three files against eight, with nothing in common to pair on. Pairing by
    basename therefore reports every fragment as "present on one side only" --
    twelve spurious "file present" flags on p0020 and no musical signal at all.
    The comparison is only meaningful once each side is reduced to one number,
    which is what this does.

    Returns an empty labelled tally for a directory with no matching files, so a
    missing run reads as "counted nothing" and disagrees loudly rather than
    silently agreeing with nothing.
    """
    from glob import glob

    paths = sorted(glob(os.path.join(directory, pattern)))
    tallies = [tally_musicxml(path) for path in paths]
    if not tallies:
        return Tally(label=label or os.path.basename(directory), comparable=())
    return combine(tallies, label=label or os.path.basename(directory))


def reconcile_crops(left_dir: str, right_dir: str,
                    left_label: Optional[str] = None,
                    right_label: Optional[str] = None,
                    pattern: str = "*_in.musicxml",
                    compare_pitches: bool = True) -> Reconciliation:
    """Reconcile two crops of the same page by comparing their totals.

    The granularity argument is in :func:`tally_directory`. In short: a
    per-system run and a per-staff run disagree about how many files a page has,
    so they are summed each into a single tally first and those two totals are
    reconciled. This is the check that actually means something -- "both runs
    found 128 pitched notes" -- where per-file pairing can only ever mean
    "these two runs did not crop the same way".
    """
    left = tally_directory(left_dir, left_label, pattern)
    right = tally_directory(right_dir, right_label, pattern)
    return reconcile(left, right, compare_pitches=compare_pitches)


def reconcile_directories(left_dir: str, right_dir: str,
                          pattern: str = "*_in.musicxml",
                          compare_pitches: bool = True) -> List[Reconciliation]:
    """Reconcile two runs that cropped the page the same way.

    Pairs files by basename, so a missing file on either side is itself
    reported -- silently pairing nothing would read as "they agree".
    """
    def listing(directory: str) -> Dict[str, str]:
        from glob import glob

        return {os.path.basename(path): path
                for path in sorted(glob(os.path.join(directory, pattern)))}

    left_files, right_files = listing(left_dir), listing(right_dir)
    out: List[Reconciliation] = []
    for name in sorted(set(left_files) | set(right_files)):
        if name not in left_files:
            out.append(Reconciliation(
                left=Tally(label=name + " (absent)", comparable=()),
                right=tally_musicxml(right_files[name], name),
                disagreements=[Disagreement("file present", 0, 1)],
                skipped=COUNT_FIELDS,
            ))
            continue
        if name not in right_files:
            out.append(Reconciliation(
                left=tally_musicxml(left_files[name], name),
                right=Tally(label=name + " (absent)", comparable=()),
                disagreements=[Disagreement("file present", 1, 0)],
                skipped=COUNT_FIELDS,
            ))
            continue
        out.append(reconcile(tally_musicxml(left_files[name], name),
                             tally_musicxml(right_files[name], name),
                             compare_pitches=compare_pitches))
    return out