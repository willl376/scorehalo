"""Recognise every detected staff on every converted page, in parallel.

The layout detector is now geometrically trustworthy (see validate_layout.py:
167 staves across 16 pages, 3 benign outliers, zero hallucinations), so the
remaining bottleneck is homr at roughly 75s per staff. 167 staves on 4 cores is
about an hour, so this runs a process pool with one task per staff and appends
each result to a JSONL file as it lands -- a crash or a reboot costs at most one
staff, not the whole run.

Page analysis is done ONCE in the parent: the deskewed page image and the staff
boxes are cached to disk, so the workers only crop and run homr.

Usage:
  python scripts/per_staff_all.py data/out-band --out data/out-perstaff -j 4
"""

import argparse
import json
import os
import sys
import time
from multiprocessing import Pool

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "pysrc"))
sys.path.insert(0, HERE)
from scorehalo.layout import analyze_page  # noqa: E402
from scorehalo.engine import run_homr  # noqa: E402
from crop_probe import xml_stats  # noqa: E402


def prep_pages(render_dir, manifest, out):
    """Detect staves once per page; cache deskewed image + staff boxes."""
    man = json.load(open(manifest))
    pages = []
    for p in man["pages"]:
        if p.get("status") != "ok":
            continue
        name = os.path.splitext(os.path.basename(p["image"]))[0]
        rp = os.path.join(render_dir, f"{name}.png")
        if not os.path.exists(rp):
            print(f"  !! missing render {rp} -- skipping")
            continue
        gray = np.array(Image.open(rp).convert("L"))
        r = analyze_page(gray, min_staff_lines=5)
        dp = os.path.join(out, f"{name}.deskew.png")
        Image.fromarray(r["deskewed"]).save(dp)
        meta = {"page": name, "deskew": dp, "render": rp,
                "n_staves": r["n_staves"], "n_systems": r["n_systems"],
                "grouping": [len(s["staves"]) for s in r["systems"]],
                "staff_space": r["staff_space"],
                "n_slivers_removed": r["n_slivers_removed"],
                "staves": r["staves"]}
        json.dump(meta, open(os.path.join(out, f"{name}.boxes.json"), "w"),
                  indent=2)
        pages.append(meta)
        print(f"  {name}: {r['n_staves']} staves, grouping "
              f"{'+'.join(map(str, meta['grouping']))}, "
              f"{r['n_slivers_removed']} slivers removed")
    return pages


def work(task):
    name, idx, out, pad, timeout = task
    meta = json.load(open(os.path.join(out, f"{name}.boxes.json")))
    desk = np.array(Image.open(meta["deskew"]).convert("L"))
    st = meta["staves"][idx]
    h, w = desk.shape
    # identical crop geometry to crop_probe.py, which is what proved 8/8 on p0020
    pv, ph = h * pad * 0.5, w * pad
    t = int(max(0, st["y_top"] - pv))
    b = int(min(h, st["y_bot"] + pv))
    l = int(max(0, st["x0"] - ph))
    rr = int(min(w, st["x1"] + ph))
    rec = {"page": name, "staff": idx + 1, "n_lines": st["n_lines"],
           "spacing": st["spacing"], "resid": st["resid"], "width": st["width"]}
    if b - t < 20 or rr - l < 20:
        rec.update(ok=False, why="crop too small")
        return rec
    crop = desk[t:b, l:rr].copy()
    cp = os.path.join(out, "crops", f"{name}.s{idx + 1}.png")
    os.makedirs(os.path.dirname(cp), exist_ok=True)
    Image.fromarray(crop).save(cp)
    rec["crop_px"] = [int(rr - l), int(b - t)]
    try:
        xml, log, rc = run_homr(cp, os.path.join(out, "homr", name),
                                timeout=timeout)
    except Exception as e:  # a worker must never take down the pool
        rec.update(ok=False, why=f"{type(e).__name__}: {e}")
        return rec
    stats = xml_stats(xml)
    rec.update(rc=rc, stats=stats, ok=(rc == 0 and stats is not None))
    if xml:
        rec["xml"] = os.path.relpath(xml, out)
        d = os.path.join(out, "xml", f"{name}.s{idx + 1}.musicxml")
        os.makedirs(os.path.dirname(d), exist_ok=True)
        with open(xml) as f, open(d, "w") as g:
            g.write(f.read())
        rec["kept"] = os.path.relpath(d, out)
    return rec


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("out_dir", help="the converted run, e.g. data/out-band")
    ap.add_argument("--out", default="data/out-perstaff")
    ap.add_argument("-j", "--jobs", type=int, default=4)
    ap.add_argument("--pad-frac", type=float, default=0.12)
    ap.add_argument("--timeout", type=int, default=900)
    args = ap.parse_args(argv)

    os.makedirs(args.out, exist_ok=True)
    render_dir = os.path.join(args.out_dir, "render")
    manifest = os.path.join(args.out_dir, "manifest.json")
    jl = os.path.join(args.out, "per_staff.jsonl")

    done = set()
    if os.path.exists(jl):
        for line in open(jl):
            try:
                r = json.loads(line)
                done.add((r["page"], r["staff"]))
            except Exception:
                pass

    print("== analysing pages (once) ==")
    pages = prep_pages(render_dir, manifest, args.out)
    total = sum(p["n_staves"] for p in pages)
    tasks = [(p["page"], i, args.out, args.pad_frac, args.timeout)
             for p in pages for i in range(p["n_staves"])]
    todo = [t for t in tasks if (t[0], t[1] + 1) not in done]
    print(f"\n== {len(todo)} staves to run ({len(done)} already done, "
          f"{total} total) on {args.jobs} workers ==\n")

    t0 = time.time()
    n = 0
    with open(jl, "a") as sink, Pool(args.jobs) as pool:
        for rec in pool.imap_unordered(work, todo):
            sink.write(json.dumps(rec) + "\n")
            sink.flush()
            n += 1
            s = rec.get("stats") or {}
            flag = "" if rec.get("ok") else f"  <<< {rec.get('why', 'rc=' + str(rec.get('rc')))}"
            el = time.time() - t0
            eta = el / n * (len(todo) - n)
            print(f"  [{n:>3}/{len(todo)}] {rec['page']} s{rec['staff']:<2} "
                  f"rc={rec.get('rc')} parts={s.get('parts')} "
                  f"staves={s.get('staves')} meas={s.get('measures')} "
                  f"notes={s.get('notes')}{flag}  "
                  f"[{el / 60:.0f}m elapsed, ~{eta / 60:.0f}m left]", flush=True)

    print("\n== summary ==")
    recs = [json.loads(x) for x in open(jl)]
    bypage = {}
    for r in recs:
        bypage.setdefault(r["page"], []).append(r)
    tot_ok = tot_notes = 0
    for pg in sorted(bypage):
        rs = bypage[pg]
        ok = [r for r in rs if r.get("ok")]
        notes = sum((r["stats"] or {}).get("notes", 0) for r in ok)
        tot_ok += len(ok)
        tot_notes += notes
        bad = [r for r in rs if not r.get("ok")]
        note = "" if not bad else f"   FAILED: {[r['staff'] for r in bad]}"
        print(f"  {pg}: {len(ok)}/{len(rs)} ok, {notes} notes{note}")
    print(f"\n  TOTAL: {tot_ok}/{len(recs)} staves recognised, "
          f"{tot_notes} notes, {(time.time() - t0) / 60:.1f} min")
    return 0


if __name__ == "__main__":
    sys.exit(main())
