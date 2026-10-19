#!/usr/bin/env python3
"""Stitch assembled per-page MusicXML into complete songs.

A song spans several pages. Each PAGE-*.musicxml is already internally aligned
(every part has the same measure count -- per_staff_assemble/emit_page padded
the short ones). Stitching is therefore a fold across pages:

  * the canonical part list for a song is the UNION of its pages' part names
    (Voice, Voice 2, Piano, Piano 2), in engraving order (voice above piano);
  * each page contributes `width` measures to EVERY part -- real measures where
    the page has that part, whole-measure rests where it does not (the singer
    tacet on a piano-only page is silence, not absence);
  * measures are renumbered globally in lockstep, so bar N sounds in every
    part at the same instant.

Song boundaries come from the PDF text layer (the title printed at the head of
a page). A page MISSING from the run (p0023) is reported, never silently
skipped, because a dropped page shifts every following bar out of step.

Usage:
  python scripts/stitch_songs.py data/out-perstaff --songs data/songs.json \
         --out data/out-songs
"""

import argparse
import json
import os
import re
import sys
from dataclasses import replace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "pysrc"))

from scorehalo.emit import (  # noqa: E402
    GLOBAL_DIVISIONS, emit_page, parse_part)
from scorehalo.graph import Measure, Page, Part  # noqa: E402


def _order_key(name):
    """Engraving order: voices first, then pitched instruments, natural-sorted."""
    low = name.lower()
    group = 0 if low.startswith("voice") else 1
    return (group, name)


def _renumber(measures, start):
    """Re-number a run of measures from `start`, stamping notes to match.

    ``Note`` is frozen, so each note is rebuilt with ``replace``. The note's
    own ``measure`` field is what to_ly buckets by, so it must move with the
    bar or every note lands in the wrong measure downstream.
    """
    out = []
    for i, m in enumerate(measures):
        n = start + i
        new = replace(m, number=n)
        if any(note.measure != n for note in m.notes):
            new.notes = [replace(note, measure=n) for note in m.notes]
        out.append(new)
    return out


def _rest_run(count, models):
    """`count` whole-measure rests, each inheriting meter/key/clef from the
    template at its position in `models`.

    `models` is a page's real measures (any full-width part of that page
    shows the same bar lengths). Position matters: a bar lengthened to hold
    overrunning content must be padded as THAT length -- inheriting one
    fixed meter for every bar would shrink the extended bars and desync the
    parts from there on.
    """
    out = []
    for i in range(count):
        m = models[i] if models and i < len(models) else (
            models[-1] if models else None)
        out.append(Measure(
            number=0, divisions=GLOBAL_DIVISIONS,
            beats=m.beats if m else 4,
            beat_type=m.beat_type if m else 4,
            key_fifths=m.key_fifths if m else 0,
            clef=m.clef if m else ("G", 2),
            is_empty=True))
    return out


def load_page(staff_dir, stem):
    """Parse an assembled PAGE-*.musicxml into measures + per-staff clef maps.

    The clef map is returned separately because a page part carries one entry
    per staff ({1: G2, 2: F4} for a grand staff) while ``by_name`` flattens to
    measures; dropping the map is how a stitched song lost its
    ``<staves>2</staves>`` and stricter importers rejected its staff-2 notes.
    Only multi-staff maps are kept -- a lone staff needs no map.
    """
    path = os.path.join(staff_dir, "PAGE-%s.musicxml" % stem)
    if not os.path.exists(path):
        return None, None, {}
    parts = parse_part(path)
    by_name = {}
    clef_maps = {}
    width = 0
    for p in parts:
        by_name.setdefault(p.name, []).extend(p.measures)
        if p.name not in clef_maps and len(p.clef_by_staff or {}) > 1:
            clef_maps[p.name] = dict(p.clef_by_staff)
        width = max(width, len(p.measures))
    # Defensive pad: emit_page already equalised, but a short part here would
    # silently misalign the whole song, so top it up to the page width. The
    # pad bars inherit per-position meters from a full-width sibling so a
    # lengthened bar stays lengthened.
    for name, ms in by_name.items():
        if len(ms) < width:
            sibling = next((v for k, v in by_name.items()
                            if k != name and len(v) == width), None)
            by_name[name] = ms + _rest_run(
                width - len(ms),
                sibling[len(ms):] if sibling else None)
    return by_name, width, clef_maps


def stitch_song(staff_dir, title, stems, missing_report):
    """Fold a song's pages into one Page. Returns (Page, {name: measures}).

    Two passes. The first gathers the UNION of part names across all pages and
    each page's width. The second concatenates every part across every page in
    lockstep, inserting whole-measure rests wherever a part is absent on a page.

    One pass is not enough: a part that first appears on a LATER page (Voice 2
    enters on p0021) would miss the rests for the pages before it, so it came
    out shorter than its neighbours and MuseScore rejected the song (rc=40) --
    parts of a score-partwise must all span the same number of bars.
    """
    pages = []
    all_names = set()
    staff_maps = {}
    for stem in stems:
        by_name, width, clef_maps = load_page(staff_dir, stem)
        if by_name is None:
            missing_report.append((title, stem))
            continue
        pages.append((by_name, width))
        all_names.update(by_name)
        # First page that states the map wins; every page of one book agrees.
        for n, m in clef_maps.items():
            staff_maps.setdefault(n, m)

    names = sorted(all_names, key=_order_key)
    canon = {n: [] for n in names}

    offset = 0
    for by_name, width in pages:
        # Every part on an assembled page carries the same bar lengths, so a
        # full-width part's measures are this page's meter profile -- the
        # template for a missing part's rest-run. Inheriting a fixed meter
        # instead (the old per-part `model`) padded extended bars back to the
        # page meter and desynced the tacet part from there on.
        profile = next((v for v in by_name.values() if len(v) == width), None)
        for n in names:
            if n in by_name:
                canon[n] += _renumber(by_name[n], offset + 1)
            else:
                canon[n] += _rest_run(width, profile)
        offset += width

    parts = []
    for i, name in enumerate(names, start=1):
        part = Part(id="P%d" % i, name=name)
        part.measures = canon[name]
        cmap = staff_maps.get(name)
        if cmap:
            # A grand staff must re-declare <staves> and BOTH clefs when the
            # song is re-emitted, not only in its pages: the notes still
            # carry staff=2 stamps, and an importer reading a one-staff part
            # rejects them ("illegal staff '2'").
            part.clef_by_staff.update(cmap)
        else:
            # carry the first real measure's clef map so the emitter declares it
            for m in canon[name]:
                if not m.is_empty and m.clef:
                    part.clef_by_staff.setdefault(1, m.clef)
                    break
        parts.append(part)
    page = Page(id=title, parts=parts, divisions=GLOBAL_DIVISIONS)
    return page, {n: len(canon[n]) for n in names}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("staff_dir", help="data/out-perstaff")
    ap.add_argument("--songs", required=True,
                    help="JSON: [{\"title\":..., \"pages\":[...]}]")
    ap.add_argument("--out", default="data/out-songs")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    with open(args.songs) as fh:
        songs = json.load(fh)
    missing = []
    index = []
    for entry in songs:
        title = entry["title"]
        stems = entry["pages"]
        page, counts = stitch_song(args.staff_dir, title, stems, missing)
        if not page.parts:
            print("SKIP %-40s (no assembled pages)" % title)
            continue
        xml = emit_page(page)
        slug = re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-").lower()
        out = os.path.join(args.out, "%s.musicxml" % slug)
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(xml)
        bars = max(len(p.measures) for p in page.parts)
        notes = len(page.notes)
        index.append({"title": title, "slug": slug, "pages": stems,
                      "parts": [p.name for p in page.parts],
                      "measures": bars, "notes": notes, "file": out})
        print("  %-40s %2d part(s) %3d bars %4d notes  <- %s"
              % (title, len(page.parts), bars, notes, ",".join(stems)))

    json.dump(index, open(os.path.join(args.out, "index.json"), "w"), indent=2)
    print("\n  wrote %d songs -> %s" % (len(index), args.out))
    if missing:
        print("  MISSING pages (not in the per-staff run):")
        for title, stem in missing:
            print("    %-40s %s" % (title, stem))
    return 0


if __name__ == "__main__":
    sys.exit(main())
