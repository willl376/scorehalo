"""Run both of Wilbur's checks on a page, and report what they found.

Two ideas, one module:

1. **Learn the page's recurring patterns, then flag the system that breaks
   them.** A page repeats: same staves, same clefs, same meter. When a system
   stops matching, something changed -- a missed staff, a wrong clef, a meter
   homr misread. ``scorehalo.pattern`` finds those.

2. **Count the same data twice and refuse to agree with yourself.** The same
   page is cropped two independent ways -- per system and per staff -- and both
   runs are counted. If the two counts disagree, one of them is wrong and the
   page needs a human. ``scorehalo.reconcile`` does that.

Neither check needs a reference score, which is the point: both are label-free
and both run on every page without being asked.

The two checks are independent and neither subsumes the other. Reconciliation
catches a run that dropped a note; pattern analysis catches a system that is
structurally unlike the rest of the page even when both runs agree about it.
"""

from __future__ import annotations

import glob
import json
import os
from typing import Dict, List, Optional

from .emit import parse_part
from .graph import Page
from .pattern import DeviationReport, find_deviations
from .reconcile import Reconciliation, pitch_name, reconcile_crops

#: Severity follows scorehalo.audit's convention: 3 is data loss, 2 is a
#: probable misread, 1 is worth a look.
SEVERITY = {
    "count_disagreement": 3,
    "pitch_disagreement": 3,
    "missing_fragment": 3,
    "pattern_deviation": 2,
    "weak_pattern": 1,
}


def boxes_path(system_root: str, page_id: str) -> Optional[str]:
    """The layout record for a page: staff and system counts as detected.

    These are the *segmentation's own* counts, written before recognition, so
    they are the only available answer to "how much of this page was there to
    read?". Without them a low note total is ambiguous between "the recogniser
    missed notes" and "the cropper never found the staves", and those are very
    different bugs.
    """
    for candidate in (
            os.path.join(system_root, "%s.boxes.json" % page_id),
            os.path.join(system_root, "homr", "%s.boxes.json" % page_id)):
        if os.path.exists(candidate):
            return candidate
    return None


def coverage_flags(page_id: str, system_dir: str, perstaff_dir: str,
                   system_root: Optional[str] = None) -> List[dict]:
    """Did each crop actually cover the whole page?

    Separated from the reconciliation on purpose. Measured across the 16 pages:
    the per-system run found every system on all 16, while the per-staff run
    missed staves on 5 of them -- p0022 lost 11 of 12. So a page where the two
    runs' note totals differ is, on those 5, reporting a *segmentation* miss
    and not a recognition disagreement. Reporting both as one "disagreement"
    overstates the problem and points the reader at the wrong layer.
    """
    path = boxes_path(system_root, page_id) if system_root else None
    if path is None:
        return []
    try:
        with open(path) as fh:
            boxes = json.load(fh)
    except (OSError, ValueError):
        return []

    flags: List[dict] = []
    wanted = (("n_staves", perstaff_dir, "per-staff"),
              ("n_systems", system_dir, "per-system"))
    for key, directory, label in wanted:
        expected = boxes.get(key)
        if expected is None:
            continue
        found = len(glob.glob(os.path.join(directory, "*_in.musicxml")))
        if found < expected:
            flags.append({
                "check": "coverage_miss",
                "severity": 3,
                "where": "%s: %s crop" % (page_id, label),
                "detail": "%d of %d %s found; %d missing -- this is a "
                          "segmentation miss, not a note disagreement"
                          % (found, expected,
                             "staves" if key == "n_staves" else "systems",
                             expected - found),
            })
    return flags


def _reconciliation_flags(page_id: str, result, covered: bool = True) -> List[dict]:
    """Flags from counting the same page two independent ways.

    The two runs are compared as TOTALS, not file by file. They crop the page
    differently -- three per-system files against eight per-staff files on
    p0020 -- so per-file pairing reports every fragment as present on one side
    only, which is a fact about the cropping and not about the music.
    """
    flags: List[dict] = []
    where = "%s: two independent crops" % page_id
    # When a crop missed staves, say so on every count line rather than letting
    # the number look like a reading error.
    caveat = "" if covered else " [a crop missed staves -- see coverage_miss]"
    for disagreement in result.disagreements:
        is_pitch = disagreement.field_name == "pitched"
        flags.append({
            "check": "pitch_disagreement" if is_pitch else "count_disagreement",
            "severity": SEVERITY["pitch_disagreement" if is_pitch
                                 else "count_disagreement"],
            "where": where,
            "detail": "%s: per-system %d vs per-staff %d (%+d)%s"
                      % (disagreement.field_name, disagreement.left,
                         disagreement.right, disagreement.delta, caveat),
        })
    if result.pitch_difference:
        for (step, alter, octave), delta in sorted(result.pitch_difference.items()):
            if not delta:
                continue
            flags.append({
                "check": "pitch_disagreement",
                "severity": SEVERITY["pitch_disagreement"],
                "where": where,
                "detail": "pitch %s differs by %+d between the two runs%s"
                          % (pitch_name(step, alter, octave), delta, caveat),
            })
    return flags


def _pattern_flags(page_id: str, report: DeviationReport) -> List[dict]:
    """Flags from the page's own recurring structure."""
    flags: List[dict] = []
    if report.weak:
        flags.append({
            "check": "weak_pattern",
            "severity": SEVERITY["weak_pattern"],
            "where": page_id,
            "detail": "too few systems share a pattern for a deviation to be "
                      "meaningful; %d system(s) examined" % len(report.observed),
        })
    for deviation in report.deviations:
        # Deviation carries `broke` -- a list of feature phrases saying what
        # changed -- not a single `reason` string. Reading a field that does not
        # exist raises; do not assume one.
        detail = "; ".join(deviation.broke) if deviation.broke else (
            "matches no pattern on this page")
        flags.append({
            "check": "pattern_deviation",
            "severity": SEVERITY["pattern_deviation"],
            "where": "%s: system %d" % (page_id, deviation.system),
            "detail": "%s (%s)" % (detail, deviation.signature.describe()),
        })
    return flags


def page_from_system_fragments(directory: str, pattern: str = "*_in.musicxml") -> Page:
    """A page that still knows how many systems it has.

    The assembled page is the wrong input for pattern analysis. Assembling
    merges each role's measures across the whole page and stamps every part
    ``system=1``, so grouping by system -- which is exactly what
    :func:`scorehalo.pattern.find_deviations` does -- finds ONE system and
    reports "too few systems share a pattern" on every page in the corpus.

    The per-system fragments are the only place the system count survives, so
    they are read here *unmerged*: every fragment keeps its own system number and
    its own parts, which is the shape the detector expects. Nothing is grouped by
    role, because role identity is not what pattern analysis compares -- system
    structure is.
    """
    from glob import glob

    paths = sorted(glob(os.path.join(directory, pattern)))
    parts = []
    for number, path in enumerate(paths, start=1):
        for part in parse_part(path, staff_index=1, system=number):
            parts.append(part)
    return Page(id=os.path.basename(directory.rstrip("/")), parts=parts)


def review_page(page_id: str, system_dir: str, perstaff_dir: str,
                assembled_path: Optional[str] = None,
                system_root: Optional[str] = None) -> dict:
    """Both checks on one page. Returns a report in scorehalo.audit's shape.

    ``assembled_path`` is the page MusicXML built by ``build_page_graph.py``.
    Without it the pattern check is skipped -- reconciliation needs only the two
    fragment directories, and a page that was never assembled is itself worth
    saying so rather than silently checking half of it.

    ``system_root`` is the directory holding ``<page>.boxes.json``, which is
    what makes the coverage check possible. Without it a low note total cannot
    be told apart from a crop that never found the staves.
    """
    flags: List[dict] = []
    crops = reconcile_crops(system_dir, perstaff_dir,
                            left_label="%s per-system" % page_id,
                            right_label="%s per-staff" % page_id)
    # Coverage first: if a crop never found the staves, its lower totals are
    # explained by that, and calling the page a note disagreement sends the
    # reader to the wrong layer of the pipeline.
    coverage = coverage_flags(page_id, system_dir, perstaff_dir,
                              system_root) if system_root else []
    flags.extend(coverage)
    fully_covered = not any(f["check"] == "coverage_miss" for f in coverage)
    flags.extend(_reconciliation_flags(page_id, crops, fully_covered))

    patterns = None
    page = page_from_system_fragments(system_dir)
    if page.parts:
        patterns = find_deviations(page)
        flags.extend(_pattern_flags(page_id, patterns))
    else:
        flags.append({
            "check": "missing_fragment",
            "severity": SEVERITY["missing_fragment"],
            "where": page_id,
            "detail": "no per-system fragments in %s; pattern check skipped"
                      % system_dir,
        })

    return {
        "page": page_id,
        "flags": flags,
        "crops": crops,
        "patterns": patterns,
    }


def reconciliation_verdict(report: dict) -> str:
    """One line: did the two crops agree, and on what.

    ``twin_counts`` and ``twin_pitches`` are deliberately separate. Counts that
    match while the notes differ is *worse* than counts that differ, because it
    means one note was traded for another and the loss is invisible to a
    headcount.
    """
    crops = report.get("crops")
    return crops.summarise() if crops is not None else "(not reconciled)"


def discover_pages(system_root: str, perstaff_root: str) -> List[str]:
    """Page ids present under both fragment roots, in reading order.

    A page is only reviewable when BOTH crops exist: reconciliation pairs files
    by basename, and a page present on one side alone is a pipeline failure, not
    a musical one -- it is reported by the caller, not silently skipped here.
    """
    def ids(root: str) -> set:
        out = set()
        base = os.path.join(root, "homr")
        if not os.path.isdir(base):
            return out
        for name in os.listdir(base):
            if os.path.isdir(os.path.join(base, name)):
                out.add(name)
        return out

    return sorted(ids(system_root) & ids(perstaff_root))


def format_review(results: List[dict]) -> str:
    """Multi-line report for a terminal."""
    if not results:
        return "review: no pages found"
    lines = ["review: %d page(s)" % len(results), ""]
    for result in results:
        lines.append("%s: %d flag(s)" % (result["page"], len(result["flags"])))
        for flag in result["flags"]:
            lines.append("   [%d] %s: %s -- %s"
                         % (flag["severity"], flag["check"], flag["where"],
                            flag["detail"]))
        patterns = result.get("patterns")
        if patterns is not None:
            lines.append("   patterns: %d examined, %d repeating, %d deviation(s)"
                         % (len(patterns.observed), len(patterns.repeats()),
                            len(patterns.deviations)))
        lines.append("   " + reconciliation_verdict(result))
        lines.append("")
    return "\n".join(lines)