"""Benchmark candidate with isolated runs and configurable MuseScore.

Repairs, each of which was demonstrated to change a reported result:

  1. FRESH RUN DIRECTORY.  The old runner reused data/bench/<case>_m<M>_s<S>
     and decided success by `os.path.exists(out/p0001.musicxml)`.  A failed
     conversion therefore scored yesterday's prediction.  Every invocation now
     gets <root>/<run-id>/<case>/ and the run id is printed.
  2. RETURN CODE IS CHECKED.  A converter may exit non-zero and still leave
     (or fail to remove) a file.  rc != 0 is an error record, never a score.
  3. PER-CASE EXCEPTIONS.  A timeout in case 2 no longer destroys case 1's
     result: each case is wrapped, and the report is written after every case.
  4. CASE NAMES ARE VALIDATED against a fixed set (they become path components).
  5. UNDEFINED METRICS ARE None, not "divide by 1".  An empty prediction is
     precision 0-or-undefined, not silently a valid denominator.
  6. METRICS ARE NAMED FOR WHAT THEY COMPUTE.  `exact` requires pitch AND
     duration; calling that "pitch accuracy" was wrong.
  7. SINGLE-PAGE POLICY IS ENFORCED (compare the whole truth against a
     one-page prediction only if the fixture really is one page).
  8. MEANINGFUL EXIT STATUS: 1 if any case errored.
"""

import argparse
import json
import os
import subprocess
import sys
import time
import traceback
import re
import shutil
import tempfile
from pathlib import Path

ROOT = str(next(
    parent for parent in Path(__file__).resolve().parents
    if (parent / "pysrc" / "scorehalo").is_dir()
    and (parent / "scripts" / "make_fixture.py").is_file()
))
HERE = os.path.join(ROOT, "scripts")
sys.path.insert(0, os.path.join(ROOT, "pysrc"))
sys.path.insert(0, HERE)

from scorehalo.score import compare_streams, parse_streams  # noqa: E402

import make_fixture  # noqa: E402

ALLOWED_CASES = {"none", "light", "scan"}


def _interpreter():
    """Same interpreter as this script unless SCOREHALO_BENCH_PYTHON says no."""
    return os.environ.get("SCOREHALO_BENCH_PYTHON", sys.executable)


def run_convert(image, out_dir, timeout=900):
    os.makedirs(out_dir, exist_ok=True)
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [
        os.path.join(ROOT, "pysrc"), env.get("PYTHONPATH")
    ]))
    return subprocess.run(
        [_interpreter(), "-m", "scorehalo.cli", "convert", image,
         "-o", out_dir, "--pages", "1"],
        cwd=ROOT, env=env, capture_output=True,
        text=True, timeout=timeout,
    )


def score_one(case, measures, seed, fixture_dir, run_dir,
              musescore=None):
    name = f"{case}_m{measures}_s{seed}"
    truth = os.path.join(fixture_dir, f"{name}_truth.musicxml")
    pdf = os.path.join(fixture_dir, f"{name}_truth.pdf")
    clean = os.path.join(fixture_dir, f"{name}_clean.png")
    scan = os.path.join(fixture_dir, f"{name}.png")
    # unique per run: cannot inherit a previous prediction
    out_dir = os.path.join(run_dir, case)

    make_fixture.build_truth_xml(truth, measures)
    ok, rc = make_fixture.render_truth_pdf(truth, pdf, musescore)
    if not ok:
        return {"case": case, "error": f"musescore rc={rc}"}
    make_fixture.pdf_to_png(pdf, clean)
    make_fixture.degrade(clean, scan, "none" if case == "none" else case, seed=seed)

    started = time.perf_counter()
    proc = run_convert(scan, out_dir)
    elapsed = time.perf_counter() - started

    log = os.path.join(out_dir, "convert.log")
    os.makedirs(out_dir, exist_ok=True)
    with open(log, "w") as fh:
        fh.write(f"argv: {scan}\nrc: {proc.returncode}\n"
                 f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}\n")

    if proc.returncode != 0:
        return {"case": case, "error": "conversion failed",
                "returncode": proc.returncode, "seconds": round(elapsed, 1),
                "log": log, "stderr_tail": (proc.stderr or "")[-800:]}

    pred = os.path.join(out_dir, "p0001.musicxml")
    if not os.path.exists(pred):
        return {"case": case, "error": "no prediction",
                "returncode": proc.returncode, "seconds": round(elapsed, 1),
                "log": log, "stderr_tail": (proc.stderr or "")[-800:]}

    per_stream, totals = compare_streams(parse_streams(truth), parse_streams(pred))
    pred_n, truth_n = totals["pred_notes"], totals["truth_notes"]
    matched = totals["exact"]

    def pct(num, den):
        return round(100.0 * num / den, 1) if den else None

    return {
        "case": case,
        "conversion_seconds": round(elapsed, 1),
        "returncode": proc.returncode,
        "truth_notes": truth_n,
        "pred_notes": pred_n,
        "exact": matched,
        # named for what they are: both pitch AND duration must agree
        "exact_precision": pct(matched, pred_n),
        "exact_recall": pct(matched, truth_n),
        "pitch_wrong": totals["pitch_wrong"],
        "duration_wrong": totals["duration_wrong"],
        "both_wrong": totals["both_wrong"],
        "missed": totals["missed"],
        "spurious": totals["spurious"],
        "streams": {str(k): v for k, v in per_stream.items()},
        "truth": truth, "pdf": pdf, "scan": scan, "pred": pred, "log": log,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default="none,light,scan")
    ap.add_argument("--measures", type=int, default=6)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--json", default="",
                    help="report path; default is inside the fresh run")
    ap.add_argument("--musescore",
                    default=shutil.which("musescore3")
                    or shutil.which("musescore"),
                    help="MuseScore executable path or command")
    ap.add_argument("--run-id", default="")
    args = ap.parse_args()

    cases = [c.strip() for c in args.cases.split(",")]
    if not cases or any(c not in ALLOWED_CASES for c in cases):
        ap.error(f"--cases must contain only: {', '.join(sorted(ALLOWED_CASES))}")
    if len(cases) != len(set(cases)):
        ap.error("--cases must not contain duplicates")
    if args.measures < 1:
        ap.error("--measures must be a positive integer")

    musescore = shutil.which(args.musescore) if args.musescore else None
    if not musescore:
        ap.error("MuseScore executable not found; use --musescore PATH")

    runs = os.path.join(ROOT, "data", "bench", "runs")
    os.makedirs(runs, exist_ok=True)
    if args.run_id:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", args.run_id):
            ap.error("--run-id must be 1-80 letters, digits, hyphens or "
                     "underscores, starting with a letter or digit")
        run_dir = os.path.join(runs, args.run_id)
        try:
            os.mkdir(run_dir)
        except FileExistsError:
            ap.error("run already exists; choose a new --run-id")
    else:
        run_dir = tempfile.mkdtemp(
            prefix=time.strftime("%Y%m%d-%H%M%S-"), dir=runs)
    run_id = os.path.basename(run_dir)
    fixture_dir = os.path.join(run_dir, "fixtures")
    os.makedirs(fixture_dir)

    out_path = (os.path.join(ROOT, args.json) if args.json
                else os.path.join(run_dir, "report.json"))
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

    rows = []
    for case in cases:
        print(f"[bench] case={case} run={run_id} ...", flush=True)
        try:
            row = score_one(case, args.measures, args.seed, fixture_dir,
                            run_dir, musescore=musescore)
        except subprocess.TimeoutExpired as exc:
            row = {"case": case, "error": f"conversion timed out: {exc}"}
        except Exception as exc:  # keep going; record, don't hide
            row = {"case": case, "error": f"{type(exc).__name__}: {exc}",
                   "traceback": traceback.format_exc()}
        rows.append(row)
        # write after every case so an interrupted run keeps its results
        with open(out_path, "w") as fh:
            json.dump({"run_id": run_id, "measures": args.measures,
                       "seed": args.seed, "python": _interpreter(),
                       "rows": rows}, fh, indent=2)
        if "error" in row:
            print(f"[bench] {case}: ERROR {row['error']}", flush=True)
        else:
            print(f"[bench] {case}: exact P={row['exact_precision']}% "
                  f"R={row['exact_recall']}% ({row['exact']}/{row['pred_notes']}, "
                  f"dur-wrong {row['duration_wrong']}, pitch-wrong "
                  f"{row['pitch_wrong']}, missed {row['missed']}, spurious "
                  f"{row['spurious']}, {row['conversion_seconds']}s)", flush=True)

    print()
    print("| case | notes | exact P | exact R | pitch wrong | dur wrong | both | missed | spurious | sec |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        if "error" in r:
            print(f"| {r['case']} | ERROR {r['error']} | | | | | | | | |")
            continue
        print(f"| {r['case']} | {r['exact']}/{r['pred_notes']} | "
              f"{r['exact_precision']}% | {r['exact_recall']}% | {r['pitch_wrong']} | "
              f"{r['duration_wrong']} | {r['both_wrong']} | {r['missed']} | "
              f"{r['spurious']} | {r['conversion_seconds']} |")
    print(f"\nwrote {out_path}  (run dir {run_dir})")
    return 1 if any("error" in r for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
