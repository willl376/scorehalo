"""compare: render the joined score with MuseScore and diff it against the
source pages for structural/graphical fidelity (the "identical twin" check).

For each machine-transcribed page we compare the ORIGINAL scan with the
MuseScore render of the corresponding page:

  - ink coverage (ratio of dark pixels)
  - staff-like rows (long horizontal line scan)
  - a coarse 3x3 quadrant ink histogram vs. the scan's, to catch
    content that moved/vanished between source and engraving

We do not claim pixel equality (OMR is semantic, not photographic); the scores
flag pages whose render diverges from the source so a human can eyeball them.
"""

import math
import os
import shutil
import subprocess
import sys

from PIL import Image, ImageOps

from scorehalo import preprocess as pp


def _dark_ratio(img):
    g = ImageOps.grayscale(img)
    px = g.getdata()
    dark = sum(1 for v in px if v < 128)
    return dark / max(1, len(px))


def _staff_like_rows(img):
    """Count distinct horizontal-line BANDS (clustered contiguous dark rows).

    Rows are sampled as dark when >30% of a horizontal sample is below the
    ink threshold; adjacent qualifying rows are grouped into one band so a
    thick staff line (or several close lines) counts once. Resolution-proof:
    a 1 px antialiased line and a 4 px bold line both count as one band.
    """
    g = ImageOps.grayscale(img.convert("RGB"))
    w, h = g.size
    rows = []
    for y in range(h):
        dark = sum(1 for x in range(0, w, 3) if g.getpixel((x, y)) < 128)
        rows.append(dark > w * 0.10)
    bands = 0
    in_band = False
    gap = 0
    for dark in rows:
        if dark:
            in_band = True
            gap = 0
        elif in_band:
            gap += 1
            if gap > 6:
                bands += 1
                in_band = False
                gap = 0
    if in_band:
        bands += 1
    return bands


def _quadrants(img):
    """ink fraction per 3x3 quadrant grid for shape comparison."""
    g = ImageOps.grayscale(img)
    w, h = g.size
    out = []
    for gy in range(3):
        for gx in range(3):
            box = (w * gx // 3, h * gy // 3, w * (gx + 1) // 3, h * (gy + 1) // 3)
            crop = g.crop(box)
            dark = sum(1 for v in crop.getdata() if v < 128)
            out.append(dark / max(1, len(crop.getdata())))
    return out


def compare_pages(src_png, render_png):
    """Give deviance scores between a source scan page and the MuseScore
    render of the same page. Returns (report_dict, verdict)."""
    a = Image.open(src_png).convert("L")
    b = Image.open(render_png).convert("L")

    # align both to a common width for fair quadrant/ink math.
    # LANCZOS can antialias 1px staff lines out of existence on one side,
    # so use Image.BOX (area average) which preserves thin-line coverage
    # symmetrically for both scan and render.
    W = 1200
    a = a.resize((W, int(a.height * W / a.width)), Image.BOX)
    b = b.resize((W, int(b.height * W / b.width)), Image.BOX)

    ink_a, ink_b = _dark_ratio(a), _dark_ratio(b)
    staff_a, staff_b = _staff_like_rows(a), _staff_like_rows(b)
    qa, qb = _quadrants(a), _quadrants(b)

    quad_dev = sum(abs(x - y) for x, y in zip(qa, qb)) / 9
    ink_ratio = ink_b / max(1e-6, ink_a)
    staff_ratio = staff_b / max(1e-6, staff_a)

    issues = []
    if ink_ratio < 0.5:
        issues.append(f"render ink {ink_b:.1%} << scan {ink_a:.1%}")
    elif ink_ratio > 1.6:
        issues.append(f"render ink {ink_b:.1%} >> scan {ink_a:.1%}")
    # Staff-like rows: only meaningful when the scan has enough readable ink
    # (degraded/photo scans with thin staff lines undercount badly, so the
    # signal must be ignored then rather than flag every page as diverged).
    if staff_a >= 4 and abs(staff_a - staff_b) > 0.5 * max(staff_a, staff_b):
        issues.append(f"staff-like rows {staff_a} -> {staff_b}")
    if quad_dev > 0.12:
        issues.append(f"quadrant deviance {quad_dev:.2f}")

    report = {
        "ink_scan": round(ink_a, 4),
        "ink_render": round(ink_b, 4),
        "ink_ratio": round(ink_ratio, 3),
        "staff_scan": staff_a,
        "staff_render": staff_b,
        "quadrant_deviance": round(quad_dev, 3),
        "issues": issues,
    }
    verdict = "ok" if not issues else ("warn" if ink_ratio >= 0.3 else "defect")
    return report, verdict


def render_score_musescore(mxl_path, out_pdf, musescore_bin=None):
    """Render a .mxl/.musicxml to PDF via the MuseScore AppImage, headless."""
    bin_ = musescore_bin or shutil.which("mscore4portable") or shutil.which("mscore")
    if not bin_:
        raise SystemExit(
            "MuseScore binary not found; set --musescore or install the AppImage"
        )
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    last = ""
    for attempt in range(3):
        r = subprocess.run(
            [bin_, "-o", out_pdf, mxl_path],
            capture_output=True,
            text=True,
            timeout=300,
            env=env,
        )
        if os.path.exists(out_pdf):
            return out_pdf
        last = f"{r.returncode}: {r.stderr[-300:]}"
    raise SystemExit(f"MuseScore render failed after 3 attempts ({last})")


def cmd_compare(args):
    out_dir = os.path.abspath(args.out_dir)
    import json

    man_path = os.path.join(out_dir, "manifest.json")
    if not os.path.exists(man_path):
        raise SystemExit("no manifest.json; run convert first")
    with open(man_path) as fh:
        manifest = json.load(fh)

    mxl = os.path.join(out_dir, "score.mxl")
    if not os.path.exists(mxl):
        raise SystemExit("no score.mxl; run join first")

    work = args.render_dir or os.path.join(out_dir, "compare")
    os.makedirs(work, exist_ok=True)
    pdf = os.path.join(work, "score-render.pdf")
    print(f"rendering {mxl} with MuseScore -> {pdf}")
    render_score_musescore(mxl, pdf, args.musescore)

    render_dir = os.path.join(work, "pages")
    os.makedirs(render_dir, exist_ok=True)
    # clear previous renders so sorted() aligns cleanly with this run
    for old in os.listdir(render_dir):
        os.remove(os.path.join(render_dir, old))
    dpi = manifest.get("dpi") or 150
    print(f"rendering pages at {dpi} dpi ({dpi} == source scan dpi)")
    subprocess.run(
        ["pdftoppm", "-png", "-r", str(dpi), pdf, os.path.join(render_dir, "pg")],
        check=True,
        timeout=180,
    )

    render_pages = sorted(
        f for f in os.listdir(render_dir) if f.startswith("pg-") and f.endswith(".png")
    )
    # NOTE: pdftoppm zero-pads by total count ("pg-1.png", "pg-02.png"...);
    # sort by the numeric suffix so render[i] aligns with the i-th ok page.
    def _num(f):
        return int(f.split("-")[1].split(".")[0])

    render_pages = sorted(render_pages, key=_num)

    ok = [p for p in manifest["pages"] if p.get("status") == "ok"]
    if len(render_pages) != len(ok):
        print(
            f"WARN pagination diverged: source {len(ok)} page(s) -> "
            f"MuseScore {len(render_pages)} page(s)"
        )

    results = []
    for i, p in enumerate(ok):
        n = p["page"]
        src = p.get("image") or os.path.join(out_dir, "render", f"p{n:04d}.png")
        if i >= len(render_pages):
            results.append({"page": n, "verdict": "no-render", "report": {}})
            continue
        rp = os.path.join(render_dir, render_pages[i])
        if not os.path.exists(rp):
            results.append({"page": n, "verdict": "no-render", "report": {}})
            continue
        report, verdict = compare_pages(src, rp)
        results.append({"page": n, "verdict": verdict, "report": report})
        marks = ", ".join(report.get("issues", [])) or "clean"
        print(f"[p{n}] {verdict:<7} -> {marks}")

    okc = sum(1 for r in results if r["verdict"] == "ok")
    warn = sum(1 for r in results if r["verdict"] == "warn")
    defect = sum(1 for r in results if r["verdict"] in ("defect", "no-render"))
    summary = {
        "out_dir": out_dir,
        "transcribed": len(ok),
        "ok": okc,
        "warn": warn,
        "defect": defect,
        "results": results,
    }
    with open(os.path.join(work, "compare.json"), "w") as fh:
        json.dump(summary, fh, indent=2)
    print(f"\ncompare report -> {os.path.join(work, 'compare.json')}")
    print(f"ok {okc} / warn {warn} / defect {defect} (of {len(ok)} transcribed)")
    return 0 if defect == 0 else 1