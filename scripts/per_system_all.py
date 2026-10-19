"""Recognise a page SYSTEM BY SYSTEM instead of staff by staff.

The per-staff pipeline throws away every relationship that lives *between*
staves: cross-staff beams, shared barlines, a brace binding a piano's hands,
one voice spread across two staves. That information is not recoverable
downstream, because it was cropped away before the recogniser ever saw it.

A system is the natural unit: it is what a musician reads left to right, it is
what homr's own part semantics already assume (parts in lockstep, same time),
and it makes every structural problem *disappear* rather than get merged away:

  - measures align within a system for free, because homr emitted them together
  - time signature and key are stated once per system, not per staff
  - divisions is a property of the system, so the parts inside it agree

Sequential systems are concatenated in reading order, so a page still becomes
one timeline.

One braced group at a time
--------------------------
A system can carry more than one part -- this book prints a singer above a
braced piano -- and a crop of the whole system is not always read as three
staves. On p0035's first two systems homr found the three staves and then merged
them into ONE part (its log: "Found 2 connected staffs (after merging grand
staffs, multiple voices): [1, 1]"), folding the singer's 19 notes into the
piano's 80 with nothing lost and nothing left to notice. So the system is cut at
its braces, the printed boundary between instruments
(:func:`scorehalo.layout.split_system_by_brace`), each group is transcribed on
its own, and the transcriptions are merged back into one file per system
(:mod:`scorehalo.fragments`). A system no vertical rule separates yields one
group and keeps the old whole-system crop.

Pictured graphics never reach the engine
----------------------------------------
A page that is a picture (this book's photo plates, a cover) is skipped here as
well as in ``scorehalo convert``, because this script can be pointed at a
rendered book whose manifest predates the graphic gate. On a page that IS
music, :mod:`scorehalo.graphics` finds a photograph as a solid smooth mid-tone
blob, and that blob is erased from the image *before* it is cropped -- so homr
is never asked to read a picture. A region that overlaps a staff is left alone
(the staff reading wins) and a crop that is still mostly picture is skipped and
recorded. Nothing here raises: a crop that times out is recorded and the run
continues.

Usage:
    python scripts/per_system_all.py data/out-band --out data/out-system
"""

import argparse
import json
import os
import sys
import time
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "pysrc"))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from scorehalo import layout  # noqa: E402
from scorehalo import graphics  # noqa: E402
from scorehalo import fragments  # noqa: E402
from scorehalo.engine import run_homr  # noqa: E402

RESULT_SUFFIX = ".musicxml"

#: How the parts of one system are lettered when a system carries more than one
#: braced group: a, b, c ... from the top.
GROUP_LETTERS = "abcdefgh"

#: A system count above this on one page means the layout found "staves" that
#: are not staves -- a picture, a table, a decorated border. The measured
#: maximum on this book is 9 systems (p0035); 24 is a wide margin, and the page
#: is skipped and recorded rather than spending the engine on 24 bad crops.
MAX_SYSTEMS = 24


def xml_stats(path):
    """Count what a recognised fragment contains. Returns None if unreadable."""
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return None
    notes = root.findall(".//note")
    kinds = {"rest": 0, "chord": 0, "note": 0}
    for n in notes:
        if n.find("rest") is not None:
            kinds["rest"] += 1
        elif n.find("chord") is not None:
            kinds["chord"] += 1
        else:
            kinds["note"] += 1
    return {
        "parts": len(root.findall("part")),
        "measures": len(root.findall(".//measure")),
        "notes": len(notes),
        "sounding": kinds["note"],
        "rests": kinds["rest"],
        "chord_tones": kinds["chord"],
    }


def read_manifest(out_dir):
    with open(os.path.join(out_dir, "manifest.json"), encoding="utf-8") as fh:
        man = json.load(fh)
    return man["pages"] if isinstance(man, dict) else man


def _record(jsonl, rec):
    """Append one line to the run journal.

    Every outcome goes through here -- recognised, skipped as a picture,
    skipped for finding too many systems, timed out -- so a resume can tell
    "already attempted" from "never reached", and a human can read what was
    bypassed instead of only what was produced.
    """
    with open(jsonl, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")


def page_stem(source_pdf, page):
    return "p%04d" % page


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("data_dir")
    ap.add_argument("--out", default="data/out-system")
    ap.add_argument("--pages", help="comma-separated page numbers, default all ok")
    ap.add_argument("-j", type=int, default=1)
    ap.add_argument("--timeout", type=int, default=600)
    args = ap.parse_args()

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "pysrc"))
    os.makedirs(args.out, exist_ok=True)
    for sub in ("crops", "homr", "xml"):
        os.makedirs(os.path.join(args.out, sub), exist_ok=True)

    pages = read_manifest(args.data_dir)
    if args.pages:
        wanted = {int(x) for x in args.pages.split(",")}
        pages = [p for p in pages if p["page"] in wanted]

    jsonl = os.path.join(args.out, "per_system.jsonl")
    done = set()
    if os.path.exists(jsonl):
        with open(jsonl, encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                done.add((rec["stem"], rec["system"]))

    for entry in pages:
        if entry.get("status") != "ok":
            continue
        page = entry["page"]
        stem = page_stem(entry.get("source_pdf", ""), page)
        render = os.path.join(args.data_dir, "render", stem + ".png")
        if not os.path.exists(render):
            print("  %s: no render, skipping" % stem)
            continue

        gray = cv2.imread(render, cv2.IMREAD_GRAYSCALE)
        if gray is None:
            print("  %s: unreadable render" % stem)
            continue
        if (stem, 0) in done:
            print("  %s: already recorded" % stem)
            continue

        photo_pct = graphics.photo_fraction(gray)
        if graphics.is_graphic_page(gray):
            _record(jsonl, {"stem": stem, "system": 0, "skipped": "graphic",
                            "photo%": round(photo_pct, 2), "ok": False})
            print("%s: pictured graphic (%.1f%% smooth mid-tone) -- bypassed"
                  % (stem, photo_pct))
            continue

        t0 = time.time()
        boxes = layout.analyze_page(gray)
        with open(os.path.join(args.out, stem + ".boxes.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({k: v for k, v in boxes.items()
                       if k not in ("deskewed", "staves", "systems", "vspans")},
                      fh, indent=2, default=float)
        # Every measurement above was taken on the deskewed page, so the crops
        # must come from that same frame. Cropping the raw page hands each
        # system the page's skew (p0022 is 2.5 deg -> ~100 px of drift end to
        # end), and the reader answers "6 staffs" for a 3-staff system.
        level = boxes["deskewed"]
        # Erase pictures before cropping, but only where they do not touch a
        # staff: a detected region that covers a staff means the two readings
        # disagree, and the staff is the one we trust.
        regions = graphics.keep_off_staves(graphics.find_graphics(level),
                                          boxes["staves"])
        if regions:
            level = graphics.blank(level, regions)
        crops = layout.crop_systems(level, boxes["systems"])
        print("%s: %d staves in %d systems (staff_space=%.1f, skew=%.2f) "
              "[layout %.1fs]%s"
              % (stem, boxes["n_staves"], boxes["n_systems"],
                 boxes["staff_space"], boxes["skew_deg"], time.time() - t0,
                 " [masked %d graphic region(s)]" % len(regions)
                 if regions else ""))

        if len(crops) > MAX_SYSTEMS:
            _record(jsonl, {"stem": stem, "system": 0, "ok": False,
                            "skipped": "too-many-systems",
                            "n_systems": len(crops), "n_staves": boxes["n_staves"],
                            "photo%": round(photo_pct, 2)})
            print("%s: %d systems detected (> %d) -- not staves, bypassed"
                  % (stem, len(crops), MAX_SYSTEMS))
            continue

        for crop in crops:
            idx = crop["index"] + 1
            if (stem, idx) in done:
                print("  system %d: cached" % idx)
                continue
            if graphics.is_graphic_crop(crop["image"]):
                _record(jsonl, {"stem": stem, "system": idx, "ok": False,
                                "n_staves": crop["n_staves"],
                                "box": list(crop["box"]),
                                "skipped": "graphic-crop"})
                print("  system %d (%d staves): pictured graphic -- bypassed"
                      % (idx, crop["n_staves"]))
                continue
            # Read the system one braced group at a time (split_system_by_brace).
            # A single group is the whole system under its historical filename,
            # so a page whose systems were already read correctly is unchanged.
            system = boxes["systems"][crop["index"]]
            groups = layout.split_system_by_brace(system, boxes["vspans"],
                                                  boxes["staff_space"])
            work = os.path.join(args.out, "homr", stem)
            t1 = time.time()
            reads = []
            written = []
            for gi, group in enumerate(groups):
                letter = "" if len(groups) == 1 else GROUP_LETTERS[gi]
                name = "%s.sys%d%s" % (stem, idx, letter)
                piece = layout.crop_systems(level, [group])
                if not piece:
                    reads.append({"group": name, "n_staves": group["n_staves"],
                                  "ok": False, "skipped": "empty-crop"})
                    continue
                dest = os.path.join(args.out, "crops", name + ".png")
                cv2.imwrite(dest, piece[0]["image"])
                xml, log, rc = run_homr(dest, work, timeout=args.timeout)
                stats = xml_stats(xml) if xml else None
                if xml:
                    written.append(xml)
                reads.append({
                    "group": name, "n_staves": group["n_staves"],
                    "ok": xml is not None, "rc": rc,
                    "notes": stats["notes"] if stats else 0,
                    "parts": stats["parts"] if stats else 0,
                    "log": os.path.relpath(log, args.out) if log else None,
                })
            # One file per system is what the rest of the pipeline reads, so the
            # groups' own fragments are merged into it and then moved aside --
            # `load_systems` globs `<stem>.sys*_in.musicxml`, which would
            # otherwise match the letters and read each group as a system.
            merged = os.path.join(work, "%s.sys%d_in.musicxml" % (stem, idx))
            n_parts = fragments.merge_fragments(written, merged) if written else 0
            if len(groups) > 1 and written:
                aside = os.path.join(work, "groups")
                os.makedirs(aside, exist_ok=True)
                for src in written:
                    os.replace(src, os.path.join(aside, os.path.basename(src)))
            stats = xml_stats(merged) if written else None
            rec = {
                "stem": stem, "system": idx, "n_staves": crop["n_staves"],
                "box": list(crop["box"]), "ok": stats is not None,
                "n_groups": len(groups), "groups": reads,
                "n_parts": n_parts, "rc": reads[0].get("rc") if reads else None,
                "masked": [list(r["box"]) for r in regions],
                "xml": os.path.relpath(merged, args.out) if stats else None,
                "log": reads[0].get("log") if reads else None,
                "secs": round(time.time() - t1, 1), "stats": stats,
            }
            _record(jsonl, rec)
            n = stats["notes"] if stats else 0
            failed = [r["group"] for r in reads if not r["ok"]]
            note = "  FAILED " + ",".join(failed) if failed else ""
            print("  system %d (%d staves, %d group(s)): parts=%s notes=%s "
                  "%.0fs%s"
                  % (idx, crop["n_staves"], len(groups), n_parts, n, rec["secs"],
                     note))


if __name__ == "__main__":
    main()
