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


def _staff_like_rows(img, minfrac=0.10):
    """Count distinct horizontal-line BANDS (clustered contiguous dark rows).

    A row counts as dark when more than `minfrac` of the SAMPLED pixels in it
    are below the ink threshold; adjacent qualifying rows are grouped into one
    band so a thick staff line (or several close lines) counts once.
    Resolution-proof: a 1 px antialiased line and a 4 px bold line both count
    as one band.

    The threshold is a fraction of the SAMPLE COUNT, not of the image width.
    This used to compare the sample count against `w * 0.10` while only
    sampling every 3rd pixel, so the real cut was 0.10 * w / (w/3) = 30% of
    the samples -- three times stricter than the 10% the name and the
    docstring claim. Clean vector renders passed 30%; the 200-pii Carpenters
    photocopy could not, so the metric went silent on exactly the input that
    needed it (staff_scan collapsed to 0-4) and compare reported "ok" for
    pages it had not actually measured. Fixed to use the sample count.
    """
    g = ImageOps.grayscale(img.convert("RGB"))
    w, h = g.size
    xs = range(0, w, 3)
    samples = len(xs)
    need = samples * minfrac
    rows = []
    for y in range(h):
        dark = sum(1 for x in xs if g.getpixel((x, y)) < 128)
        rows.append(dark > need)
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

    report = {
        "ink_scan": round(ink_a, 4),
        "ink_render": round(ink_b, 4),
        "ink_ratio": round(ink_ratio, 3),
        "staff_scan": staff_a,
        "staff_render": staff_b,
        "quadrant_deviance": round(quad_dev, 3),
        "issues": [],
    }
    # NOTE: no absolute thresholds here, deliberately. A degraded 200-pii
    # photocopy and a clean vector render are physically different objects:
    # measured across the 16 Carpenters pages, the render carries a
    # SYSTEMATICALLY larger staff-band count (every page 1.24x-2.94x, mean
    # 1.98) and systematically less ink (mean 0.59), because photocopy lines
    # bleed and blur while MuseScore's are hairline-crisp. Any fixed
    # threshold therefore measures the SCAN's degradation, not our
    # transcription, and flags or clears pages by how good the input
    # photocopy was. Flagging happens in cmd_compare against the corpus
    # median instead, so the bias cancels and only real outliers surface.
    return report


def _median(xs):
    s = sorted(xs)
    n = len(s)
    if not n:
        return 0.0
    mid = n // 2
    return float(s[mid]) if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def flag_outliers(reports):
    """Mark pages that deviate from the CORPUS median, not from a constant.

    The scan-vs-render ratios carry a large input-dependent offset (see
    _compare_pages), so only a page that is an outlier *relative to the rest
    of the same book* carries information. Returns {page_index: [issues]}.
    """
    ink = [r["ink_ratio"] for r in reports]
    staff = [(r["staff_render"] / max(1.0, r["staff_scan"])) for r in reports]
    if len(reports) < 4:      # too few to establish a baseline
        return {}
    med_ink, med_staff = _median(ink), _median(staff)
    flagged = {}
    for i, r in enumerate(reports):
        issues = []
        if med_ink > 0 and abs(r["ink_ratio"] - med_ink) / med_ink > 0.5:
            issues.append(
                f"ink ratio {r['ink_ratio']:.2f} vs book median {med_ink:.2f}")
        s_ratio = staff[i]
        if med_staff > 0 and abs(s_ratio - med_staff) / med_staff > 0.5:
            issues.append(
                f"staff ratio {s_ratio:.2f} vs book median {med_staff:.2f}")
        if r["quadrant_deviance"] > 0.12:
            issues.append(f"quadrant deviance {r['quadrant_deviance']:.2f}")
        if issues:
            flagged[i] = issues
    return flagged


def render_score_musescore(mxl_path, out_pdf, musescore_bin=None):
    """Render a .mxl/.musicxml to PDF via the MuseScore AppImage, headless.

    The output path is removed before EVERY attempt. MuseScore can fail
    headlessly (its import dialog is silent) and leave no file at all, so a
    stale PDF from a previous run would otherwise be reported as success --
    which is exactly how a wrong render silently becomes a "verified" one.
    """
    bin_ = musescore_bin or shutil.which("mscore4portable") or shutil.which("mscore")
    if not bin_:
        raise SystemExit(
            "MuseScore binary not found; set --musescore or install the AppImage"
        )
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    last = ""
    for attempt in range(3):
        if os.path.exists(out_pdf):
            os.remove(out_pdf)
        r = subprocess.run(
            [bin_, "-o", out_pdf, mxl_path],
            capture_output=True,
            text=True,
            timeout=300,
            env=env,
        )
        if os.path.exists(out_pdf) and os.path.getsize(out_pdf) > 1024:
            return out_pdf
        last = f"rc={r.returncode} {r.stderr[-300:]}"
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

    ok = [p for p in manifest["pages"] if p.get("status") == "ok"]
    if not ok:
        raise SystemExit("no transcribed pages in manifest; nothing to compare")

    work = args.render_dir or os.path.join(out_dir, "compare")
    os.makedirs(work, exist_ok=True)
    dpi = manifest.get("dpi") or 150

    # ---- the joined score, rendered ONLY as a pagination check -------------
    # Reflow is expected and correct: joining 16 scanned pages into one
    # score will not reproduce 16 rendered pages. So the joined render is
    # measured as a whole and is never page-paired against a scan.
    jpdf = os.path.join(work, "score-render.pdf")
    print(f"rendering joined score with MuseScore -> {jpdf}")
    render_score_musescore(mxl, jpdf, args.musescore)
    jdir = os.path.join(work, "joined")
    os.makedirs(jdir, exist_ok=True)
    for old in os.listdir(jdir):
        os.remove(os.path.join(jdir, old))
    joined_pages = 0
    try:
        subprocess.run(
            ["pdftoppm", "-png", "-r", str(dpi), jpdf, os.path.join(jdir, "pg")],
            check=True, timeout=180,
        )
        joined_pages = len([f for f in os.listdir(jdir) if f.endswith(".png")])
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        # The joined render is a DIAGNOSTIC (pagination sanity), not the source
        # of any per-page verdict, so failing to rasterise it must not abort
        # the real comparison below.
        print(f"WARN could not rasterise the joined score ({exc}); "
              "per-page comparison continues")
    print(
        f"joined score: {len(ok)} source page(s) -> {joined_pages} rendered "
        f"page(s)  [reflow is expected; not page-paired]"
    )

    # ---- per-page render: a genuine 1:1 pairing ---------------------------
    # Each page is rendered from ITS OWN MusicXML, so render[i] is provably
    # page ok[i]. Index-pairing the reflowed joined score was the bug this
    # replaces: with 16 source pages vs 24 render pages it scored page N
    # against an unrelated page and reported confident nonsense.
    pdir = os.path.join(work, "pages")
    os.makedirs(pdir, exist_ok=True)
    for old in os.listdir(pdir):
        os.remove(os.path.join(pdir, old))

    results = []
    for p in ok:
        n = p["page"]
        src = p.get("image") or os.path.join(out_dir, "render", f"p{n:04d}.png")
        if not os.path.exists(src):
            results.append({"page": n, "verdict": "no-scan", "report": {}})
            print(f"[p{n}] no-scan -> source image missing")
            continue
        mxml = os.path.join(out_dir, f"p{n:04d}.musicxml")
        if not os.path.exists(mxml):
            results.append({"page": n, "verdict": "no-musicxml", "report": {}})
            print(f"[p{n}] no-musicxml -> cannot render this page in isolation")
            continue
        try:
            ppdf = render_score_musescore(mxml, os.path.join(pdir, f"p{n:04d}.pdf"),
                                          args.musescore)
        except SystemExit as exc:
            results.append({"page": n, "verdict": "no-render", "report": {},
                            "error": str(exc)})
            print(f"[p{n}] no-render -> {exc}")
            continue
        stem = ppdf[:-4]
        try:
            subprocess.run(
                ["pdftoppm", "-png", "-r", str(dpi), ppdf, stem],
                check=True, timeout=180)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            results.append({"page": n, "verdict": "no-render", "report": {},
                            "error": f"raster failed: {exc}"})
            print(f"[p{n}] no-render -> raster failed: {exc}")
            continue
        raster = sorted(f for f in os.listdir(pdir) if f.startswith(f"p{n:04d}-")
                        and f.endswith(".png"))
        if not raster:
            results.append({"page": n, "verdict": "no-render", "report": {}})
            print(f"[p{n}] no-render -> MuseScore produced no raster")
            continue
        rp = os.path.join(pdir, raster[0])
        report = compare_pages(src, rp)
        results.append({"page": n, "verdict": "measured", "report": report})

    # Second pass: flag pages that are outliers against the book's own
    # median. Done after all pages are measured because the baseline is
    # corpus-relative (see flag_outliers).
    measured = [(i, r) for i, r in enumerate(results)
                if r["verdict"] == "measured"]
    flags = flag_outliers([r["report"] for _, r in measured])
    for slot, (i, r) in enumerate(measured):
        issues = flags.get(slot, [])
        r["report"]["issues"] = issues
        r["verdict"] = "review" if issues else "clean"
        marks = ", ".join(issues) or "no outlier vs book median"
        print(f"[p{r['page']:04d}] {r['verdict']:<8} -> {marks}")

    okc = sum(1 for r in results if r["verdict"] == "clean")
    warn = sum(1 for r in results if r["verdict"] == "review")
    bad = sum(1 for r in results
              if r["verdict"] not in ("clean", "review"))
    summary = {
        "out_dir": out_dir,
        "transcribed": len(ok),
        "clean_vs_book_median": okc,
        "review": warn,
        "failed": bad,
        "ok": okc,
        "warn": warn,
        "defect": bad,
        "paired_by": "per-page render of each page's own MusicXML",
        "source_pages": len(ok),
        "joined_render_pages": joined_pages,
        "joined_pages_are_paired": False,
        "what_this_does_not_prove": (
            "These are gross structural measurements (ink coverage, staff-band "
            "count, quadrant distribution) of a degraded scan against a clean "
            "vector render. A 'clean' verdict means the page is not an outlier "
            "against the rest of THIS book. It does NOT mean the notes, pitches "
            "or rhythm are correct."),
        "measured_blind_spots": (
            "benchmarked against labelled fixtures (scripts/bench_compare.py): "
            "this check catches 19/30 deliberately broken pages (recall 63%) "
            "but is nearly BLIND to two whole defect classes -- wrong pitches "
            "read at the wrong interval (1/6 caught) and a page transcribed as "
            "all rests (1/6). A semitone misread leaves the note count, staff "
            "position and ink coverage essentially unchanged, so no ink/staff "
            "threshold can ever see it; that is a property of raster "
            "measurement, not a bug to tune away. Use 'scorehalo score "
            "--truth --pred' when labels exist, and human review otherwise."),
        "results": results,
    }
    with open(os.path.join(work, "compare.json"), "w") as fh:
        json.dump(summary, fh, indent=2)
    print(f"\ncompare report -> {os.path.join(work, 'compare.json')}")
    print(f"clean {okc} / review {warn} / failed {bad} (of {len(ok)} transcribed)")
    print("NOTE: 'clean' = not an outlier vs this book's median. It does NOT")
    print("      mean the transcription is correct. Human review required.")
    print(f"side-by-side pairs -> {pdir}")
    return 0 if bad == 0 else 1