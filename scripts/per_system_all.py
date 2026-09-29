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
from scorehalo.engine import run_homr  # noqa: E402

RESULT_SUFFIX = ".musicxml"


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

        t0 = time.time()
        boxes = layout.analyze_page(gray)
        with open(os.path.join(args.out, stem + ".boxes.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({k: v for k, v in boxes.items()
                       if k not in ("deskewed", "staves", "systems", "vspans")},
                      fh, indent=2, default=float)
        crops = layout.crop_systems(gray, boxes["systems"])
        print("%s: %d staves in %d systems (staff_space=%.1f, skew=%.2f) "
              "[layout %.1fs]"
              % (stem, boxes["n_staves"], boxes["n_systems"],
                 boxes["staff_space"], boxes["skew_deg"], time.time() - t0))

        for crop in crops:
            idx = crop["index"] + 1
            if (stem, idx) in done:
                print("  system %d: cached" % idx)
                continue
            dest = os.path.join(args.out, "crops", "%s.sys%d.png" % (stem, idx))
            cv2.imwrite(dest, crop["image"])
            work = os.path.join(args.out, "homr", stem)
            t1 = time.time()
            xml, log, rc = run_homr(dest, work, timeout=args.timeout)
            stats = xml_stats(xml) if xml else None
            rec = {
                "stem": stem, "system": idx, "n_staves": crop["n_staves"],
                "box": list(crop["box"]), "ok": xml is not None, "rc": rc,
                "xml": os.path.relpath(xml, args.out) if xml else None,
                "log": os.path.relpath(log, args.out) if log else None,
                "secs": round(time.time() - t1, 1), "stats": stats,
            }
            with open(jsonl, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec) + "\n")
            n = stats["notes"] if stats else 0
            print("  system %d (%d staves): rc=%s notes=%s %.0fs"
                  % (idx, crop["n_staves"], rc, n, rec["secs"]))


if __name__ == "__main__":
    main()
