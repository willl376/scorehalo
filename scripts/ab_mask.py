#!/usr/bin/env python3
"""Paired A/B of pictured-graphic masking on the SAME labelled image.

This is deliberately narrower than ``ab_engines.py``: both arms use ScoreHalo,
the only difference is ``convert``'s default masking versus the explicit
``--no-mask-graphics`` control. If masking is to become a production default,
it must first win (or at least not lose) on a labelled page containing a
picture the detector actually finds.

As with the engine A/B:

  * truth and image SHA-256 are recorded;
  * each arm gets a FRESH directory;
  * return code is checked before scoring, and an error is an error row;
  * the detected picture boxes are recorded, so a no-op mask cannot be
    mistaken for a successful one.

Usage:
    python scripts/ab_mask.py --musescore PATH --json reports/mask-ab.json
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback

import cv2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pysrc"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import make_fixture  # noqa: E402
from ab_engines import run_scorehalo, score_prediction, sha256  # noqa: E402
from scorehalo import graphics  # noqa: E402


def build_illustrated_fixture(run_dir, measures, seed, musescore):
    """Render exact truth, degrade to a scan, and paste a detectable picture."""
    fixture_dir = os.path.join(run_dir, "fixture")
    os.makedirs(fixture_dir)
    name = f"illustrated_m{measures}_s{seed}"
    truth = os.path.join(fixture_dir, f"{name}_truth.musicxml")
    pdf = os.path.join(fixture_dir, f"{name}_truth.pdf")
    clean = os.path.join(fixture_dir, f"{name}_clean.png")
    image = os.path.join(fixture_dir, f"{name}.png")

    make_fixture.build_truth_xml(truth, measures)
    ok, rc = make_fixture.render_truth_pdf(truth, pdf, musescore)
    if not ok:
        raise RuntimeError(f"MuseScore rc={rc}")
    make_fixture.pdf_to_png(pdf, clean)
    meta = make_fixture.degrade(clean, image, "illustration", seed=seed)

    gray = cv2.imread(image, cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise RuntimeError(f"could not read fixture image {image}")
    regions = graphics.find_graphics(gray)
    if not regions:
        raise RuntimeError("fixture picture was not detected; A/B would be a no-op")

    return {
        "truth": truth, "pdf": pdf, "image": image, "degrade": meta,
        "detected_regions": regions,
        "sha256": {k: sha256(p) for k, p in
                   (("truth", truth), ("pdf", pdf), ("image", image))},
    }


def markdown_report(row, run_id):
    a, b = row["arms"]["masked"], row["arms"]["unmasked"]
    lines = [
        f"# Masking A/B — run `{run_id}`", "",
        "Both arms recognized the **same** labelled scan with the same "
        "ScoreHalo CLI. The control adds `--no-mask-graphics` and "
        "`--no-photo-gate` (the gate otherwise skips any page with 5%+ "
        "picture-like pixels, leaving no prediction to score). `exact` "
        "requires pitch and duration. This is a fixture measurement, not a "
        "claim of source accuracy.", "",
        f"- picture boxes detected: `{row.get('detected_regions')}`",
        f"- image SHA-256: `{row.get('sha256', {}).get('image', 'unavailable')}`",
        f"- truth SHA-256: `{row.get('sha256', {}).get('truth', 'unavailable')}`", "",
        "| arm | rc | exact | precision | recall | sec |",
        "|---|---|---|---|---|---|",
    ]
    for label, arm in (("masked (default)", a), ("unmasked (control)", b)):
        if "error" in arm:
            lines.append(f"| {label} | ERROR: {str(arm['error'])[:80]} "
                         f"| | | | {arm.get('seconds', '')} |")
        else:
            lines.append(
                f"| {label} | {arm['rc']} | {arm['exact']}/"
                f"{arm['truth_notes']} | {arm['exact_precision']}% | "
                f"{arm['exact_recall']}% | {arm['seconds']} |")
    lines.append("")
    if "exact" in a and "exact" in b:
        delta = a["exact"] - b["exact"]
        lines += [
            f"Delta exact (masked - unmasked): **{delta:+d}**.", "",
            "A positive delta supports making masking the default; a negative "
            "delta means the picture's context helped the engine, so masking "
            "must stay opt-in. Equal scores are not evidence that masking is "
            "harmless on real photographs.", "",
        ]
    else:
        lines += ["No score comparison: at least one arm failed.", ""]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--measures", type=int, default=6)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--musescore", default=shutil.which("musescore3")
                    or shutil.which("musescore"))
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--json", default="reports/mask-ab.json")
    ap.add_argument("--run-id", default="")
    args = ap.parse_args(argv)

    if args.measures < 1:
        ap.error("--measures must be positive")
    if not args.musescore or not os.path.isfile(args.musescore):
        ap.error("MuseScore executable not found; use --musescore PATH")

    runs = os.path.join(ROOT, "data", "mask-runs")
    os.makedirs(runs, exist_ok=True)
    if args.run_id:
        run_dir = os.path.join(runs, args.run_id)
        try:
            os.mkdir(run_dir)
        except FileExistsError:
            ap.error("run already exists; choose a new --run-id")
    else:
        run_dir = tempfile.mkdtemp(
            prefix=time.strftime("%Y%m%d-%H%M%S-"), dir=runs)
    run_id = os.path.basename(run_dir)

    row = {"case": "illustration", "measures": args.measures, "seed": args.seed,
           "arms": {}}
    try:
        fixture = build_illustrated_fixture(
            run_dir, args.measures, args.seed, args.musescore)
        row.update(fixture)
        # The control must see the picture; without --no-photo-gate the page
        # gate would skip it before the engine, so no note-level score exists.
        for label, extra in (("masked", ()),
                             ("unmasked", ("--no-mask-graphics",
                                           "--no-photo-gate"))):
            outdir = os.path.join(run_dir, label)
            os.makedirs(os.path.dirname(outdir), exist_ok=True)
            print(f"[mask-ab] {label} ...", flush=True)
            started = time.perf_counter()
            try:
                arm = run_scorehalo(fixture["image"], outdir,
                                    timeout=args.timeout, extra_args=extra)
                arm = score_prediction(fixture["truth"], arm["pred"], arm,
                                       started, time.perf_counter() - started)
                if label == "masked":
                    log = open(os.path.join(outdir, "engine.log")).read()
                    arm["mask_logged"] = "masked 1 pictured graphic" in log
                else:
                    arm["control_logged"] = "--no-mask-graphics" in open(
                        os.path.join(outdir, "engine.log")).read()
            except subprocess.TimeoutExpired as exc:
                arm = {"error": f"timeout after {args.timeout}s: {exc}",
                       "seconds": args.timeout, "rc": None, "pred": None}
            except Exception as exc:
                arm = {"error": f"{type(exc).__name__}: {exc}",
                       "traceback": traceback.format_exc(), "rc": None,
                       "pred": None}
            row["arms"][label] = arm
            print(f"[mask-ab]   {label}: "
                  f"{arm.get('error') or str(arm.get('exact')) + '/' + str(arm.get('truth_notes'))}",
                  flush=True)
        if "exact" in row["arms"]["masked"] and "exact" in row["arms"]["unmasked"]:
            row["delta_exact"] = (row["arms"]["masked"]["exact"]
                                  - row["arms"]["unmasked"]["exact"])
    except Exception as exc:
        row["error"] = f"{type(exc).__name__}: {exc}"
        row["traceback"] = traceback.format_exc()

    out_path = (os.path.join(ROOT, args.json) if args.json
                else os.path.join(run_dir, "report.json"))
    md_path = os.path.splitext(out_path)[0] + ".md"
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w") as fh:
        json.dump({"run_id": run_id, "row": row}, fh, indent=2)
    with open(md_path, "w") as fh:
        fh.write(markdown_report(row, run_id))
    print(markdown_report(row, run_id))
    print(f"[mask-ab] wrote {out_path} and {md_path}", flush=True)
    return 1 if "error" in row or any(
            "error" in arm for arm in row["arms"].values()) else 0


if __name__ == "__main__":
    sys.exit(main())
