#!/usr/bin/env python3
"""Assemble a page from per-staff fragments, roles from brace geometry.

The page is read ONE STAFF AT A TIME, each crop is a single staff, and the
assembly decides which staff belongs to which instrument. The role evidence is
the BRACE, not the clef or the part label: a brace covers the contiguous run of
staves it spans, and the staff no brace touches is the singer. That is
structural, so it holds whether the singer is above, between, or below the
accompaniment -- the case a clef-based guess gets wrong on p0020 (sys2/sys3:
(G, G, F) pairs staves 1-2 "singer on top" and folds the vocal line into the
piano; the brace says voice = staff 3).

Why per-staff at all: per-system crops are poisoned for clefs. homr stamps its
first-read clef onto every staff of a system (``vocabulary.py``), so the
piano's bass staff opens as treble and part of what is bass ink gets read as
treble or dropped. Single-staff crops have no such ambiguity: each fragment
carries exactly the clef homr read on that one staff (8/8 on p0020).

Inputs:
  data/out-perstaff/<stem>.boxes.json   staff geometry + grouping (e.g. [2,3,3])
  data/out-perstaff/<stem>.deskew.png   the deskewed page, for brace ink
  data/out-perstaff/homr/<stem>/<stem>.sN_in.musicxml   single-staff fragments

The meter is the modal meter across the FRAGMENT measures (page-wide decision,
made before assembly, the same rule the system-level build uses), and brace
membership per system is read from the ink. Both are printed so a lie is
visible: a wrong grouping or a missed brace changes the report before it
changes any note.
"""

import argparse
import glob
import json
import os
import re
import sys
from collections import Counter
from dataclasses import replace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "pysrc"))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from scorehalo.brace import brace_groups  # noqa: E402
from scorehalo.emit import GLOBAL_DIVISIONS, emit_page, parse_part  # noqa: E402
from scorehalo.grouping import assemble_page  # noqa: E402
from scorehalo.meter import (extend_overfull_bars,  # noqa: E402
                             format_bar_extensions, format_overrides,
                             reconcile_meter)


def _natural_key(path):
    return [int(x) if x.isdigit() else x
            for x in re.split(r"(\d+)", os.path.basename(path))]


def staff_systems(grouping):
    """Parallel sequence: the system each staff (in reading order) was printed in."""
    out = []
    for sysno, n in enumerate(grouping, start=1):
        out.extend([sysno] * n)
    return out


def brace_ids_for(ink, staves_meta, sys_of):
    """Per-staff brace ids: equal ids mark staves under the same brace.

    ``None`` is a staff no brace touches -- the singer. Ids are only compared
    within one system (matching the same instrument across systems is not
    something a brace can tell you).

    A system whose brace ink is not legible -- the detector returns only
    one-staff fragments, or nothing (p0021, p0025, p0035, p0036 print their
    braces faintly) -- is read from the book's layout instead of being left as
    one part per staff. Across the run every legible system (58 of 64) braces
    the accompaniment *under* the singer: the lower staves are one group and the
    top staff is left unbraced. The illegible systems take that same shape, so a
    page does not shatter into "Voice / Voice 2 / Piano / Piano 2" because one
    system's brace was too faint to measure. This only decides which staff joins
    which part: a system's measure offsets are fixed by the system itself, so
    part membership never moves a note in time.
    """
    ids: list = [None] * len(sys_of)
    for sysno in sorted(set(sys_of)):
        idxs = [i for i, s in enumerate(sys_of) if s == sysno]
        staves = [staves_meta[i] for i in idxs]
        groups = [g for g in brace_groups(ink, staves) if len(g) > 1]
        if not groups and len(staves) > 1:
            groups = ([list(range(len(staves)))] if len(staves) == 2
                      else [list(range(1, len(staves)))])
        for gi, group in enumerate(groups):
            for local in group:
                ids[idxs[local]] = (sysno, gi)
    return ids


def drop_phantom_bars(staves, sys_of, empty_bars):
    """Remove bars that are empty in EVERY staff of their system.

    Silence anyone played is music -- a bar with notes in even one staff of
    the system stays. But a bar with no notes in ANY staff is not something
    the page said: it is homr finding two barlines with nothing between them
    (p0020 sys2 bar 1: all three staves empty, and the engraver said
    "remove the measure with the rests present"). Such a bar throws every
    following bar out of step with the scan -- the voice then enters a bar
    late against the book.

    Returns the dropped ``(system, bar)`` pairs. Surviving bars are
    renumbered and their notes re-stamped, because ``Note.measure`` is later
    matched against ``Measure.number`` (to_ly buckets by it).
    """
    size = {s: sum(1 for x in sys_of if x == s) for s in set(sys_of)}
    seen = {}
    for staff_no, sysno, bar, _name in empty_bars:
        seen[(sysno, bar)] = seen.get((sysno, bar), 0) + 1
    phantom = sorted(k for k, v in seen.items() if v == size[k[0]])
    if not phantom:
        return []
    drop = {}
    for sysno, bar in phantom:
        drop.setdefault(sysno, set()).add(bar)
    for i, p in enumerate(staves):
        drops = drop.get(sys_of[i])
        if not drops:
            continue
        kept = [m for j, m in enumerate(p.measures, start=1)
                if j not in drops]
        for new_no, m in enumerate(kept, start=1):
            m.number = new_no
            if m.notes and any(n.measure != new_no for n in m.notes):
                m.notes = [replace(n, measure=new_no) for n in m.notes]
        p.measures = kept
    return phantom


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("staff_dir", help="data/out-perstaff")
    ap.add_argument("--stem", default="p0020")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    with open(os.path.join(args.staff_dir,
                           "%s.boxes.json" % args.stem)) as fh:
        meta = json.load(fh)
    grouping = meta["grouping"]
    staves_meta = meta["staves"]
    sys_of = staff_systems(grouping)
    if len(sys_of) != len(staves_meta):
        raise SystemExit("grouping %s sums to %d, but %d staff boxes listed"
                         % (grouping, len(sys_of), len(staves_meta)))

    ink = np.array(cv2.imread(meta["deskew"], cv2.IMREAD_GRAYSCALE)) < 128
    brace_ids = brace_ids_for(ink, staves_meta, sys_of)
    for sysno in sorted(set(sys_of)):
        idxs = [i for i, s in enumerate(sys_of) if s == sysno]
        print("  sys%d: staves %s brace_ids %s"
              % (sysno, ",".join(str(i + 1) for i in idxs),
                 ",".join("_" if brace_ids[i] is None else "b%d" % brace_ids[i][1]
                          for i in idxs)))

    files = sorted(glob.glob(os.path.join(args.staff_dir, "homr", args.stem,
                                          "%s.s*_in.musicxml" % args.stem)),
                   key=_natural_key)
    if len(files) != len(staves_meta):
        raise SystemExit("expected %d fragments, found %d (%s)"
                         % (len(staves_meta), len(files), args.stem))

    staves, clefs = [], []
    empty_bars = []
    for i, f in enumerate(files):
        parts = parse_part(f, staff_index=i + 1, system=sys_of[i])
        p = parts[0]
        staves.append(p)
        clef = (p.clef_by_staff.get(1)
                or (p.measures[0].clef if p.measures else None)
                or ("G", 2))
        clefs.append(clef)
        for j, m in enumerate(p.measures, start=1):
            if not m.notes:
                empty_bars.append((i + 1, p.system, j, p.name))
        print("  staff %2d sys%d: %-6s %2d measures %3d notes clef %s%d"
              % (i + 1, sys_of[i], p.name, len(p.measures), len(p.notes),
                 clef[0], clef[1]))

    # Phantom bars go before the MISS report: a bar nobody played in any
    # staff is not silence to preserve, it is a miscount to remove.
    phantom = drop_phantom_bars(staves, sys_of, empty_bars)
    phantom_keys = set(phantom)
    if phantom:
        print("  DROP: %d phantom bar(s) -- empty in EVERY staff of their "
              "system, removed:" % len(phantom))
        for sysno, bar in phantom:
            print("    sys%d bar %d" % (sysno, bar))
    kept_empty = [e for e in empty_bars
                  if (e[1], e[2]) not in phantom_keys]
    if kept_empty:
        print("  MISS: %d bar(s) homr read as EMPTY (attributes, no notes); "
              "emitted as whole-measure rests:" % len(kept_empty))
        for staff, sysno, bar, name in kept_empty:
            print("    staff %d sys%d bar %d (%s)" % (staff, sysno, bar, name))

    meters = Counter()
    for p in staves:
        for m in p.measures:
            meters[(m.beats, m.beat_type)] += 1

    beats, beat_type = (meters.most_common(1)[0][0] if meters else (4, 4))
    print("  meters seen: %s -> page %d/%d"
          % (dict(meters), beats, beat_type))

    key_fifths = 0
    for p in staves:
        for m in p.measures:
            if m.key_fifths:
                key_fifths = m.key_fifths
                break
        if key_fifths:
            break

    page = assemble_page(staves, sys_of, clefs, brace_ids,
                         id=args.stem, divisions=GLOBAL_DIVISIONS,
                         beats=beats, beat_type=beat_type,
                         key_fifths=key_fifths)

    overrides = reconcile_meter(page)
    if overrides:
        print("  " + format_overrides(overrides).replace("\n", "\n  "))

    # homr sometimes writes a bar longer than the printed meter holds.
    # Reconcile can only unify declarations; this extends the barline
    # itself (page-wide) so the overrunning content survives instead of
    # being dropped at the barline.
    extended, unrepairable = extend_overfull_bars(page)
    if extended or unrepairable:
        print("  " + format_bar_extensions(extended,
                                           unrepairable).replace("\n", "\n  "))

    # The engraver wants the voice stave GONE from the two-staff intro
    # system. Dropping her leading bars was tried and MEASURED to be wrong:
    # MuseScore aligns parts by ORDER, not by measure number -- with P1
    # first declaring bar 5 the roundtrip put her notes at bars 1-5, i.e.
    # she sang straight through the piano intro, and the render still
    # counted three staves on top ([3,3,3]). Keep every part aligned
    # 1..width here; the intro system becomes two-staff at RENDER time
    # (hide empty staves), which is where the book's "staff not printed"
    # actually lives. Do not trim again.

    xml = emit_page(page)
    out = args.out or os.path.join(args.staff_dir,
                                   "PAGE-%s.musicxml" % args.stem)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(xml)
    clamped = getattr(emit_page, "last_clamped", 0)
    if clamped:
        print("  NOTE: %d note(s) clamped at a barline -- homr overflowed "
              "the meter." % clamped)

    print("  roles:")
    for p in page.parts:
        print("    %-8s clef_by_staff=%-12s measures=%d notes=%d"
              % (p.name, p.clef_by_staff, len(p.measures), len(p.notes)))
    print("  parts=%d notes=%d measures=%d"
          % (len(page.parts), len(page.notes),
             sum(len(p.measures) for p in page.parts)))
    print("  wrote %s (%d bytes)" % (out, os.path.getsize(out)))
    return 0


if __name__ == "__main__":
    sys.exit(main())