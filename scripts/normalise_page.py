"""Assemble a page onto ONE bottom-aligned staff grid.

This is the ``grid`` path: instead of trusting each system's own part split
(which homr makes by clef and which folds a voice into the piano), every
system's staves are re-seated onto the tallest layout the piece uses, aligned
to the bottom. See :mod:`scorehalo.grid` for why.

Usage::

    scorehalo-normalise data/out-system --stem p0020
    # a human who knows the page can force a row's clef:
    scorehalo-normalise data/out-system --stem p0020 \\
        --clef '{"0": ["F", 4]}'

``--clef`` takes a JSON object mapping a row counted FROM THE BOTTOM (row 0 is
the lowest printed staff) to a ``[sign, line]`` clef. It exists for the case the
grid cannot settle honestly: a row whose clef genuinely differs between systems
(the report names it) and a human decides which one the page means.
"""

import argparse
import glob
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "pysrc"))

from scorehalo import grid  # noqa: E402
from scorehalo.emit import GLOBAL_DIVISIONS, emit_page, parse_part  # noqa: E402


def load_systems(system_dir, stem):
    """Every system fragment of ``stem`` as its list of graph Parts."""
    files = sorted(glob.glob(os.path.join(system_dir, "homr", stem,
                                          "%s.sys*_in.musicxml" % stem)))
    if not files:
        raise SystemExit("no system fragments for %s in %s" % (stem, system_dir))
    return [parse_part(f, staff_index=1, system=i)
            for i, f in enumerate(files, start=1)]


def page_context(systems):
    """The meter and key the whole page agrees on, by majority vote."""
    meters = Counter()
    fifths = Counter()
    for parts in systems:
        for p in parts:
            for m in p.measures:
                meters[(m.beats, m.beat_type)] += 1
                fifths[m.key_fifths] += 1
    (beats, beat_type) = meters.most_common(1)[0][0] if meters else (4, 4)
    key = fifths.most_common(1)[0][0] if fifths else 0
    return beats, beat_type, key, meters


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("system_dir", help="data/out-system")
    ap.add_argument("--stem", default="p0020",
                    help="page stem, or 'all' to scan every assembled page")
    ap.add_argument("--out", default=None)
    ap.add_argument("--clef", default=None,
                    help='JSON {"from_bottom_row": [sign, line]} clef overrides')
    args = ap.parse_args()
    if args.stem == "all":
        return scan_all(args.system_dir)
    return build_one(args)


def build_one(args):
    systems = load_systems(args.system_dir, args.stem)
    rows_by_system = [grid.system_rows(parts) for parts in systems]
    beats, beat_type, key, meters = page_context(systems)

    print("%s:" % args.stem)
    print("  " + grid.report(rows_by_system, GLOBAL_DIVISIONS,
                             beats, beat_type).replace("\n", "\n  "))
    print("  meters seen: %s -> page %d/%d, key fifths %+d"
          % (dict(meters), beats, beat_type, key))
    if len(meters) > 1:
        print("  NOTE: systems disagree on meter; the grid uses the majority.")

    overrides = {}
    if args.clef:
        for k, v in json.loads(args.clef).items():
            overrides[int(k)] = (v[0], int(v[1]))

    page = grid.normalise(args.stem, rows_by_system, divisions=GLOBAL_DIVISIONS,
                          beats=beats, beat_type=beat_type, key_fifths=key,
                          clef_overrides=overrides)

    for p in page.parts:
        clefs = ", ".join("%d=%s%d" % (s, c[0], c[1])
                          for s, c in sorted(p.clef_by_staff.items()))
        changes = ", ".join("bar %d staff %d -> %s%d" % (b, s, c[0], c[1])
                            for b, s, c in getattr(p, "clef_changes", []))
        print("  part %-6s %-5s %d bar(s), clefs {%s}%s"
              % (p.id, p.name, len(p.measures), clefs,
                 "  changes: " + changes if changes else ""))

    out = args.out or os.path.join(args.system_dir, "grid-%s.musicxml" % args.stem)
    xml = emit_page(page)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(xml)
    print("  wrote %s (%d bytes, %d notes)"
          % (out, os.path.getsize(out), len(page.notes)))
    return 0


def scan_all(system_dir):
    """Report the staff grid of every page, and the book's tallest layout.

    This is the "scan the pages first" step: before a single note is placed,
    read how many staves each system prints, so the piece can be laid out on the
    tallest one. A page whose systems disagree is one piece on a grid taller
    than some of its systems; a page where every system agrees needs no grid.
    """
    homr = os.path.join(system_dir, "homr")
    stems = sorted(d for d in os.listdir(homr)
                   if glob.glob(os.path.join(homr, d, "*.sys*_in.musicxml")))
    print("  %-7s %5s %5s %4s %4s  %-14s %-24s %s"
          % ("page", "rows", "acc", "solo", "beat", "systems", "beats/row",
             "conflicts"))
    print("  " + "-" * 90)
    tallest = 0
    needs_grid = 0
    for stem in stems:
        try:
            systems = load_systems(system_dir, stem)
        except SystemExit:
            continue
        rows_by_system = [grid.system_rows(parts) for parts in systems]
        total, accomp = grid.grid_shape(rows_by_system)
        counts = [len(r) for r in rows_by_system]
        conflicts = grid.clef_conflicts(rows_by_system, total, accomp)
        varies = len(set(counts)) > 1
        needs_grid += 1 if varies else 0
        tallest = max(tallest, total)
        # Beats on each stave line, bottom row first: the SHAPE is `rows`, the
        # CONTENT is this. A row of 0 beats was printed but never sounds.
        totals = grid.beat_totals(rows_by_system, GLOBAL_DIVISIONS)
        content = grid.content_rows(rows_by_system, GLOBAL_DIVISIONS)
        beats_txt = ",".join("r%d=%s" % (i, grid._fmt_beats(b))
                             for i, b in enumerate(totals))
        flag = "varies " + str(counts) if varies else "uniform"
        print("  %-7s %5d %5d %4d %4d  %-14s %-24s %s"
              % (stem, total, accomp, total - accomp, content, flag, beats_txt,
                 "; ".join("row%d:%s" % (c["from_bottom"],
                                         ",".join(sorted(c["clefs"])))
                           for c in conflicts)))
    print("\n  %d page(s); %d with systems of differing staff count; "
          "tallest layout %d row(s)."
          % (len(stems), needs_grid, tallest))
    print("  `beat` = most rows that carry any beats in one system; "
          "`beats/row` = beats that add up on each grid row (row 0 = bottom).")
    return 0



if __name__ == "__main__":
    main()
