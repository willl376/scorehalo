"""Benchmark the OMR pipeline against exact ground truth.

Each fixture is a known piece of music (scripts/make_fixture.py) engraved by
MuseScore, optionally degraded to look like a 1972 photocopy, then pushed
through the real `scorehalo convert` path and scored note-by-note. Reports
pitch accuracy, duration accuracy, and the substitution classes so the errors
are actionable rather than a single opaque percentage.

Usage:
    python scripts/bench_fixtures.py [--cases scan,light,none] [--measures 6]
                                     [--seed 7] [--json data/bench.json]
"""

import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "pysrc"))
sys.path.insert(0, HERE)

from scorehalo.score import compare_streams, parse_streams  # noqa: E402

import make_fixture  # noqa: E402

PYTHON = os.path.join(ROOT, ".venv", "bin", "python")


def run_convert(image, out_dir, timeout=900):
    os.makedirs(out_dir, exist_ok=True)
    proc = subprocess.run(
        [PYTHON, "-m", "scorehalo.cli", "convert", image, "-o", out_dir, "--pages", "1"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return proc


def score_one(case, measures, seed, fixture_dir):
    name = f"{case}_m{measures}_s{seed}"
    truth = os.path.join(fixture_dir, f"{name}_truth.musicxml")
    pdf = os.path.join(fixture_dir, f"{name}_truth.pdf")
    clean = os.path.join(fixture_dir, f"{name}_clean.png")
    scan = os.path.join(fixture_dir, f"{name}.png")
    out_dir = os.path.join(ROOT, "data", "bench", name)

    make_fixture.build_truth_xml(truth, measures)
    ok, rc = make_fixture.render_truth_pdf(truth, pdf, None)
    if not ok:
        return {"case": case, "error": f"musescore rc={rc}"}
    make_fixture.pdf_to_png(pdf, clean)
    make_fixture.degrade(clean, scan, "none" if case == "none" else case, seed=seed)

    started = time.time()
    proc = run_convert(scan, out_dir)
    elapsed = time.time() - started
    pred = os.path.join(out_dir, "p0001.musicxml")
    if not os.path.exists(pred):
        return {
            "case": case,
            "error": "no prediction",
            "stdout": proc.stdout[-400:],
            "stderr": proc.stderr[-400:],
        }

    per_stream, totals = compare_streams(parse_streams(truth), parse_streams(pred))
    matched = totals["exact"]
    pred_n = totals["pred_notes"] or 1
    truth_n = totals["truth_notes"] or 1
    return {
        "case": case,
        "seconds": round(elapsed, 1),
        "truth_notes": totals["truth_notes"],
        "pred_notes": totals["pred_notes"],
        "exact": matched,
        "precision": round(100.0 * matched / pred_n, 1),
        "recall": round(100.0 * matched / truth_n, 1),
        "pitch_wrong": totals["pitch_wrong"],
        "duration_wrong": totals["duration_wrong"],
        "both_wrong": totals["both_wrong"],
        "missed": totals["missed"],
        "spurious": totals["spurious"],
        "streams": {str(k): v for k, v in per_stream.items()},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default="none,light,scan")
    ap.add_argument("--measures", type=int, default=6)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--json", default="data/bench/bench.json")
    args = ap.parse_args()

    fixture_dir = os.path.join(ROOT, "data", "fixtures")
    os.makedirs(fixture_dir, exist_ok=True)

    rows = []
    for case in args.cases.split(","):
        case = case.strip()
        print(f"[bench] case={case} ...", flush=True)
        row = score_one(case, args.measures, args.seed, fixture_dir)
        rows.append(row)
        if "error" in row:
            print(f"[bench] {case}: ERROR {row['error']}", flush=True)
        else:
            print(
                f"[bench] {case}: precision {row['precision']}%  recall {row['recall']}%  "
                f"(exact {row['exact']}/{row['pred_notes']}, dur-wrong {row['duration_wrong']}, "
                f"pitch-wrong {row['pitch_wrong']}, missed {row['missed']}, spurious {row['spurious']}, "
                f"{row['seconds']}s)",
                flush=True,
            )

    out_path = os.path.join(ROOT, args.json)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as fh:
        json.dump(rows, fh, indent=2)

    print()
    print("| case | notes | precision | recall | pitch wrong | dur wrong | missed | spurious | sec |")
    print("|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        if "error" in r:
            print(f"| {r['case']} | ERROR {r['error']} | | | | | | | |")
            continue
        print(
            f"| {r['case']} | {r['exact']}/{r['pred_notes']} | {r['precision']}% | {r['recall']}% | "
            f"{r['pitch_wrong']} | {r['duration_wrong']} | {r['missed']} | {r['spurious']} | {r['seconds']} |"
        )
    print(f"\nwrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
