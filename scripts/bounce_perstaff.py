#!/usr/bin/env python3
"""Bounce the PER-STAFF pages back onto the scan.

``bounce_page`` audits the system-level grid (``data/out-song/grid-*.musicxml``).
The songs the Carpenters work delivers are built from the PER-STAFF assembly
(``data/out-perstaff/PAGE-*.musicxml``) instead, and the two assemblies differ:
per-staff reads one staff at a time, so homr cannot stamp one system's first
clef onto every staff of that system (the bug the assembler's docstring names).
The accuracy question therefore has to be asked of the per-staff pages -- the
ones that actually became songs -- not of the grid.

Everything sharp (ink pairing, height verdict, system pairing) lives in
``bounce_page``; this wrapper only changes the two inputs that tie it to the
system-level path:

  * the assembly file        grid-<stem>  ->  PAGE-<stem>
  * the per-system measure
    counts                   <stem>.sys*_in  ->  the per-STAFF fragments,
                                                 grouped by ``boxes.json`` and
                                                 phantom-dropped exactly as
                                                 per_staff_assemble.py did.

Usage:
  python scripts/bounce_perstaff.py data/out-perstaff --pages 20,21,22
"""

import argparse
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "pysrc"))

import bounce_page as B  # noqa: E402
from per_staff_assemble import (  # noqa: E402
    staff_systems, drop_phantom_bars)
from scorehalo.emit import parse_part  # noqa: E402


def _natural_key(path):
    m = re.search(r"\.s(\d+)_in", os.path.basename(path))
    return int(m.group(1)) if m else 0


def perstaff_system_counts(staff_dir, stem):
    """Measures each printed system contributed to PAGE-<stem>.musicxml.

    Mirrors per_staff_assemble.main(): parse every fragment, drop the bars that
    are empty in EVERY staff of their system, then take each system's widest
    staff. The result is the boundary set ``write_bounced`` breaks on, so the
    engraving reflows into the systems the book printed.
    """
    with open(os.path.join(staff_dir, "%s.boxes.json" % stem)) as fh:
        meta = json.load(fh)
    grouping = meta["grouping"]
    sys_of = staff_systems(grouping)
    files = sorted(glob.glob(os.path.join(
        staff_dir, "homr", stem, "%s.s*_in.musicxml" % stem)), key=_natural_key)
    staves, empty = [], []
    for i, f in enumerate(files):
        parts = parse_part(f, staff_index=i + 1, system=sys_of[i])
        p = parts[0]
        staves.append(p)
        for j, m in enumerate(p.measures, start=1):
            if not m.notes:
                empty.append((i + 1, p.system, j, p.name))
    drop_phantom_bars(staves, sys_of, empty)
    return [max((len(staves[i].measures) for i in range(len(sys_of))
                 if sys_of[i] == s), default=0) for s in sorted(set(sys_of))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("staff_dir", help="data/out-perstaff")
    ap.add_argument("--scan-dir", default="data/out-band/render")
    ap.add_argument("--pages", default="20,21,22")
    ap.add_argument("--out", default=None)
    ap.add_argument("--musescore", default=os.environ.get(
        "MUSESCORE", B.DEFAULT_APPIMAGE))
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    out_dir = args.out or os.path.join(args.staff_dir, "bounce")
    os.makedirs(out_dir, exist_ok=True)
    report = {}

    for number in [int(p) for p in args.pages.split(",") if p.strip()]:
        stem = "p%04d" % number
        scan_path = os.path.join(args.scan_dir, stem + ".png")
        musicxml = os.path.join(args.staff_dir, "PAGE-%s.musicxml" % stem)
        if not (os.path.exists(scan_path) and os.path.exists(musicxml)):
            print("%s: no scan or no per-staff assembly -- skipped" % stem)
            continue

        counts = perstaff_system_counts(args.staff_dir, stem)
        bounced = B.write_bounced(musicxml, counts,
                                  os.path.join(out_dir, stem + ".musicxml"))
        raster = B.engrave(bounced, os.path.join(out_dir, stem + ".pdf"),
                           args.musescore)
        if raster is None:
            print("%s: MuseScore produced no render -- skipped" % stem)
            continue

        scan_boxes = B.layout.analyze_page(
            B.cv2.imread(scan_path, B.cv2.IMREAD_GRAYSCALE))
        scan_level = scan_boxes["deskewed"]
        render_gray = B.cv2.imread(raster, B.cv2.IMREAD_GRAYSCALE)
        render_boxes = B.layout.analyze_page(render_gray)

        pairs, scan_systems, render_systems = B.bottom_pairs(scan_boxes,
                                                             render_boxes)
        scan_ink = B.tracing._binarize(scan_level)
        render_ink = B.tracing._binarize(render_gray)
        scan_bands = {b["index"]: b for b in B.tracing.staff_bands(scan_level)}
        render_bands = {b["index"]: b for b in B.tracing.staff_bands(render_gray)}

        print("%s  systems: page %s  engraving %s  (counts %s = %d)"
              % (stem, [len(s) for s in scan_systems],
                 [len(s) for s in render_systems], counts, sum(counts)))
        if len(scan_systems) != len(render_systems):
            print("   !! the two pages do not agree on how many systems there"
                  " are: pairing what lines up")
        print("   %-4s %-14s %5s %5s %7s %7s %6s"
              % ("sys", "staff", "page", "engr", "x-pair", "same-ht", "shift"))
        page = {"systems": [len(s) for s in scan_systems],
                "engraved_systems": [len(s) for s in render_systems],
                "counts": counts, "measures": sum(counts), "staves": []}
        for si, ri, number_, role in pairs:
            if si not in scan_bands or ri not in render_bands:
                continue
            verdict = B.compare_pair(scan_ink, render_ink, scan_bands[si],
                                     render_bands[ri])
            verdict.update({"staff": role, "system": number_,
                            "scan_index": si, "render_index": ri})
            page["staves"].append(verdict)
            flag = ""
            if abs(verdict["systematic_shift"]) == B.tracing.POSITIONS_PER_OCTAVE:
                flag = "  !! OCTAVE"
            elif verdict["systematic_shift"] == 0:
                flag = "  ok"
            print("   %-4d %-14s %5d %5d %7d %7s %+6d%s"
                  % (number_, role, verdict["page_events"],
                     verdict["engraved_events"], verdict["x_matched"],
                     "%d/%d" % (verdict["same_height"], verdict["x_matched"]),
                     verdict["systematic_shift"], flag))
        report[stem] = page

        side = os.path.join(out_dir, stem + "-side-by-side.png")
        B.cv2.imwrite(side, B.side_by_side(scan_level, render_gray))
        print("   wrote %s, %s" % (os.path.join(out_dir, stem + ".musicxml"),
                                   side))

    where = args.json or os.path.join(out_dir, "bounce-perstaff.json")
    with open(where, "w") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
    print("\nwrote %s" % where)
    return 0


if __name__ == "__main__":
    sys.exit(main())
