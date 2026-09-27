"""Cross-check the layout detector against homr across a converted run.

Why: the detector's parameters were tuned on ONE labeled page (p0020, which
Wilbur labeled 2+3+3 = 8 staves). A single page is an anecdote. This script
runs the detector over every converted page and reports, per page:

  * detected staff count and system grouping
  * homr's staff count (from the generated MusicXML)
  * how many staves homr LOST

Two things make the result trustworthy rather than merely flattering:
  1. If the grouping is identical on every page, that is a BUG SIGNAL, not a
     finding -- a detector that always says 2+3+3 has learned nothing.
  2. Every page must actually be measured; verify the counts vary.

Usage: python scripts/layout_audit.py [out_dir] [--dpi 300] [--json out.json]
"""

import argparse
import json
import os
import sys
import xml.etree.ElementTree as ET

import numpy as np
import pypdfium2 as pdfium

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "pysrc"))
from scorehalo.layout import analyze_page  # noqa: E402


def homr_max_staves(musicxml_path):
    """Largest <staves> declared by homr in the file (1 if none)."""
    if not os.path.exists(musicxml_path):
        return -1
    try:
        root = ET.parse(musicxml_path).getroot()
    except ET.ParseError:
        return -1
    best = 0
    for part in root.findall("part"):
        for m in part.findall("measure"):
            a = m.find("attributes/staves")
            if a is not None and a.text:
                try:
                    best = max(best, int(a.text))
                except ValueError:
                    pass
    return best or 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("out_dir", nargs="?", default="data/out-band")
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--json", default="")
    ap.add_argument("--cache", default="", help="dir for rendered page images")
    args = ap.parse_args(argv)

    man_path = os.path.join(args.out_dir, "manifest.json")
    with open(man_path) as fh:
        man = json.load(fh)
    recs = [p for p in man["pages"] if p.get("status") == "ok"]
    if not recs:
        raise SystemExit(f"no pages with status 'ok' in {man_path}")
    src_pdf = man.get("source")
    if not src_pdf or not os.path.exists(src_pdf):
        raise SystemExit(f"source PDF missing: {src_pdf}")
    pdf = pdfium.PdfDocument(src_pdf)
    scale = args.dpi / 72.0
    # manifest "page" is 1-BASED (p0020 -> page 20); pypdfium2 is 0-based.
    # Getting this wrong silently analyzes the neighbouring page, so do it once
    # here and loudly rather than at every use site.
    def page_image(one_based_page):
        idx = one_based_page - 1
        if not 0 <= idx < len(pdf):
            raise SystemExit(f"page {one_based_page} out of range (len={len(pdf)})")
        return pdf[idx].render(scale=scale).to_pil()

    if args.cache:
        os.makedirs(args.cache, exist_ok=True)

    rows = []
    print(f"  {'page':>6} {'detected':>9} {'grouping':>18} {'homr':>5}  verdict")
    for p in recs:
        name = os.path.splitext(os.path.basename(p["image"]))[0]
        cache = os.path.join(args.cache, f"{name}.png") if args.cache else ""
        gray = None
        if cache and os.path.exists(cache):
            from PIL import Image
            gray = np.array(Image.open(cache).convert("L"))
        else:
            gray = np.array(page_image(p["page"]).convert("L"))
            if cache:
                from PIL import Image
                Image.fromarray(gray).save(cache)
        r = analyze_page(gray)
        grp = "+".join(str(s["n_staves"]) for s in r["systems"])
        h = homr_max_staves(os.path.join(args.out_dir, f"{name}.musicxml"))
        lost = r["n_staves"] - h if h > 0 else 0
        rows.append({
            "page": name, "src": p["page"], "detected": r["n_staves"],
            "grouping": grp, "n_systems": r["n_systems"],
            "staff_space": r["staff_space"], "skew": r["skew_deg"],
            "homr": h, "lost": lost,
        })
        print(f"  {name:>6} {r['n_staves']:>9} {grp:>18} {h:>5}  "
              f"{'ok' if lost <= 0 else f'LOST {lost}'}")

    counts = [r["detected"] for r in rows]
    grps = [r["grouping"] for r in rows]
    tot_d = sum(counts)
    tot_h = sum(r["homr"] for r in rows if r["homr"] > 0)
    lost_pages = [r for r in rows if r["lost"] > 0]
    print()
    print(f"  pages measured      : {len(rows)}")
    print(f"  staves detected     : {tot_d}   (homr declared {tot_h})")
    if tot_d:
        print(f"  homr recovery       : {100.0 * tot_h / tot_d:.1f}% of detected staves")
    print(f"  pages with loss     : {len(lost_pages)} of {len(rows)}")
    print(f"  distinct counts     : {sorted(set(counts))}")
    print(f"  distinct groupings  : {len(set(grps))} of {len(rows)}")
    if len(set(grps)) <= 1:
        print("  VERDICT: identical grouping on every page -> BUG SIGNAL, "
              "the detector is not actually discriminating")
    else:
        print("  VERDICT: groupings vary across pages -> not trivially overfit")
    if args.json:
        with open(args.json, "w") as fh:
            json.dump({"rows": rows, "total_detected": tot_d,
                       "total_homr": tot_h}, fh, indent=2)
        print(f"  wrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
