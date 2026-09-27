"""Does the layout detector's geometry hold up across the corpus?

A real music staff is not "some lines that are roughly near each other" -- it is
5 lines at a near-perfectly EQUAL vertical spacing. That is a hard, checkable
arithmetic constraint, so we do not need Wilbur's eyes to catch most bad
detections: a genuine staff has 5 lines whose gaps agree to within a pixel or
two; a bracket arm, a beam, a text rule, a photo border or a table line does
not.

This reads the SAME pipeline the converter uses (analyze_page) rather than
re-deriving lines, so the numbers reported here are the numbers that matter.

Usage:
  python scripts/validate_layout.py data/out-band --json /tmp/out.json
"""

import argparse
import json
import os
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "pysrc"))
from scorehalo.layout import analyze_page  # noqa: E402

CACHE = "/tmp/opencode/pagecache"


def load_gray(man, page, name):
    cp = f"{CACHE}/{name}.png"
    if os.path.exists(cp):
        return np.array(Image.open(cp).convert("L"))
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(man["source"])
    gray = np.array(pdf[page - 1].render(scale=300 / 72).to_pil().convert("L"))
    Image.fromarray(gray).save(cp)
    return gray


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("out_dir")
    ap.add_argument("--json", default="")
    ap.add_argument("--resid-tol", type=float, default=4.0,
                    help="max px deviation of any staff gap from that staff's mean")
    ap.add_argument("--width-frac", type=float, default=0.45,
                    help="min staff width as a fraction of page width")
    args = ap.parse_args(argv)

    man = json.load(open(os.path.join(args.out_dir, "manifest.json")))
    recs = [p for p in man["pages"] if p.get("status") == "ok"]
    os.makedirs(CACHE, exist_ok=True)
    rows = []

    print(f"  {'page':>6} {'staves':>6} {'sys':>4} {'space':>6} {'mean':>6} "
          f"{'sliv':>5} {'worstR':>7} {'minW':>5}  flags")
    for p in recs:
        name = os.path.splitext(os.path.basename(p["image"]))[0]
        gray = load_gray(man, p["page"], name)
        w = gray.shape[1]
        r = analyze_page(gray, min_staff_lines=5)
        staves = r["staves"]
        if not staves:
            print(f"  {name:>6} {0:>6} {0:>4}  {'-':>6}  {'-':>6}  {'-':>5}  "
                  f"{'-':>7}  {'-':>5}  NO STAVES")
            continue
        mean_sp = float(np.mean([s["spacing"] for s in staves]))
        flags = []
        for i, s in enumerate(staves):
            why = []
            if s["n_lines"] != 5:
                why.append(f"lines={s['n_lines']}")
            if s["resid"] > args.resid_tol:
                why.append(f"resid={s['resid']:.1f}")
            if s["width"] < args.width_frac * w:
                why.append(f"narrow={s['width']}")
            if abs(s["spacing"] - r["staff_space"]) > 0.25 * r["staff_space"]:
                why.append(f"sp={s['spacing']:.1f}!={r['staff_space']:.1f}")
            if why:
                flags.append((i + 1, why))
        worst = max(s["resid"] for s in staves)
        minw = min(s["width"] for s in staves)
        grp = [len(sys_["staves"]) if isinstance(sys_, dict) and "staves" in sys_
               else 1 for sys_ in r["systems"]]
        print(f"  {name:>6} {len(staves):>6} {len(r['systems']):>4} "
              f"{r['staff_space']:>6.1f} {mean_sp:>6.1f} "
              f"{r['n_slivers_removed']:>5} {worst:>7.1f} {minw:>5}  {len(flags)}")
        for idx, why in flags:
            print(f"        staff {idx}: {', '.join(why)}")
        rows.append({"page": name, "n_staves": len(staves),
                     "n_systems": len(r["systems"]),
                     "grouping": "+".join(map(str, grp)),
                     "global_space": r["staff_space"], "mean_space": mean_sp,
                     "n_slivers_removed": r["n_slivers_removed"],
                     "worst_resid": worst, "min_width": minw,
                     "flags": [{"staff": i, "why": w} for i, w in flags],
                     "staves": staves})

    clean = [r for r in rows if not r["flags"]]
    print()
    print(f"  pages with NO flagged staves : {len(clean)} of {len(rows)}")
    if clean:
        sp = [r["global_space"] for r in clean]
        print(f"  staff_space on clean pages  : "
              f"{min(sp):.1f} .. {max(sp):.1f} px")
    if args.json:
        json.dump(rows, open(args.json, "w"), indent=2)
        print(f"  wrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
