#!/usr/bin/env python3
"""Paired, note-level A/B: ScoreHalo vs the locally patched Audiveris.

Why this exists
---------------
Engine agreement is not truth, and the old "~89-90% duration" figure was a
fixture bug. The only way to say a change is better is to run BOTH engines on
the SAME labelled image and score them against the SAME truth with
``score.py::compare_streams``.

Non-negotiables, learned the hard way (see bench_fixtures.py):

  * Every invocation gets a FRESH directory. A failed engine must never be
    scored against yesterday's prediction.
  * Return code is checked BEFORE scoring. rc != 0 is an error row, not a
    metric row.
  * Truth, image and prediction SHA-256 are recorded so a stale or swapped
    artifact cannot masquerade as this run's result.
  * Exceptions are per-case: one engine failing does not erase the other arm.

``parse_streams`` keys by (part, staff, voice), so a different part/staff
layout counts as misses/spurious even if the pitches are all there. That is a
property of the comparison, not a claim about either engine; read the per-stream
breakdown before drawing conclusions.

Usage:
    python scripts/ab_engines.py --cases scan --measures 6 --seed 7
    python scripts/ab_engines.py --cases none,light,scan --measures 6 \
        --json ~/scorehalo-upstream/reports/ab-engines.json
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import xml.etree.ElementTree as ET
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pysrc"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import make_fixture  # noqa: E402
from scorehalo.score import compare_streams, parse_streams  # noqa: E402

ALLOWED_CASES = {"none", "light", "scan"}
DEFAULT_AUDIVERIS = os.path.expanduser(
    "~/src/audiveris/app/build/install/app/bin/Audiveris")


def sha256(path):
    """Digest of a file on disk, so a report can prove which bytes it scored."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def extract_mxl(mxl_path, out_path):
    """Pull the score XML out of a compressed MusicXML, via container.xml.

    ``.mxl`` is a zip: META-INF/container.xml names the rootfile. Guessing the
    first ``.xml`` member happens to work for Audiveris but is wrong in
    general, so follow the pointer and refuse to write an unparseable result.
    """
    with zipfile.ZipFile(mxl_path) as archive:
        names = set(archive.namelist())
        rootfile = "META-INF/container.xml"
        if rootfile in names:
            container = ET.fromstring(archive.read(rootfile))
            candidates = [
                e.get("full-path") for e in container.iter()
                if e.tag.rsplit("}", 1)[-1] == "rootfile" and e.get("full-path")
            ]
            member = next((c for c in candidates if c in names), None)
        else:
            member = None
        if member is None:
            member = next(
                (n for n in sorted(names) if n.lower().endswith(".xml")
                 and not n.startswith("META-INF/")), None)
        if member is None:
            raise ValueError(f"no MusicXML rootfile in {mxl_path}")
        payload = archive.read(member)
    ET.fromstring(payload)                      # fail before writing a bad file
    with open(out_path, "wb") as handle:
        handle.write(payload)
    return out_path


def _write_log(outdir, argv, proc):
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, "engine.log"), "w") as handle:
        handle.write("argv: " + " ".join(map(str, argv)) + "\n")
        handle.write(f"rc: {proc.returncode}\n--- stdout ---\n{proc.stdout}"
                     f"\n--- stderr ---\n{proc.stderr}\n")


def run_scorehalo(image, outdir, timeout=900, runner=subprocess.run,
                  python=None):
    """Recognize one image with the production whole-page CLI arm."""
    os.mkdir(outdir)                            # fresh; never inherit output
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [os.path.join(ROOT, "pysrc"), env.get("PYTHONPATH", "")])
    argv = [python or sys.executable, "-m", "scorehalo.cli", "convert", image,
            "-o", outdir, "--pages", "1"]
    proc = runner(argv, cwd=ROOT, env=env, capture_output=True,
                  text=True, timeout=timeout)
    _write_log(outdir, argv, proc)
    pred = os.path.join(outdir, "p0001.musicxml")
    ok = proc.returncode == 0 and os.path.isfile(pred)
    return {"rc": proc.returncode,
            "pred": pred if ok else None,
            "log": os.path.join(outdir, "engine.log"),
            "stderr_tail": (proc.stderr or "")[-800:] if not ok else ""}


def run_audiveris(image, outdir, timeout=900, runner=subprocess.run,
                  binary=None):
    """Recognize one image with the patched Audiveris batch CLI arm."""
    os.mkdir(outdir)                            # fresh; never inherit output
    binary = binary or DEFAULT_AUDIVERIS
    env = dict(os.environ)
    # JAVA_TOOL_OPTIONS breaks Audiveris' Gradle version probe; JAVA_OPTS does not.
    env["JAVA_OPTS"] = "-Djava.awt.headless=true"
    argv = [binary, "-batch", "-export", "-output", outdir, image]
    proc = runner(argv, cwd=outdir, env=env, capture_output=True,
                  text=True, timeout=timeout)
    _write_log(outdir, argv, proc)
    stem = os.path.splitext(os.path.basename(image))[0]
    mxl = os.path.join(outdir, stem + ".mxl")
    pred = os.path.join(outdir, stem + ".extracted.musicxml")
    error = ""
    ok = proc.returncode == 0 and os.path.isfile(mxl)
    if ok:
        try:
            extract_mxl(mxl, pred)
        except Exception as exc:
            ok, error = False, f"mxl extraction failed: {exc}"
    return {"rc": proc.returncode,
            "pred": pred if ok else None,
            "log": os.path.join(outdir, "engine.log"),
            "stderr_tail": error or ((proc.stderr or "")[-800:] if not ok else "")}


def score_prediction(truth_path, pred_path, arm_row, started, elapsed):
    """Fill in note-level metrics, or record why they cannot be computed."""
    row = dict(arm_row)
    row["seconds"] = round(elapsed, 1)
    if row.get("pred") is None:
        row["error"] = f"no usable prediction (rc={row.get('rc')})"
        return row
    per_stream, totals = compare_streams(
        parse_streams(truth_path), parse_streams(row["pred"]))
    pred_n, truth_n = totals["pred_notes"], totals["truth_notes"]
    matched = totals["exact"]

    def pct(num, den):
        return round(100.0 * num / den, 1) if den else None

    row.update({
        "truth_notes": truth_n, "pred_notes": pred_n, "exact": matched,
        "exact_precision": pct(matched, pred_n),
        "exact_recall": pct(matched, truth_n),
        "pitch_wrong": totals["pitch_wrong"],
        "duration_wrong": totals["duration_wrong"],
        "both_wrong": totals["both_wrong"],
        "missed": totals["missed"], "spurious": totals["spurious"],
        "streams": {str(k): v for k, v in per_stream.items()},
        "pred_sha256": sha256(row["pred"]),
    })
    return row


def score_case(case, measures, seed, run_dir, musescore, audiveris,
               timeout=900, python=None):
    """Build one labelled fixture, then run both engines on that exact image."""
    name = f"{case}_m{measures}_s{seed}"
    fixture_dir = os.path.join(run_dir, name, "fixture")
    os.makedirs(fixture_dir)
    truth = os.path.join(fixture_dir, f"{name}_truth.musicxml")
    pdf = os.path.join(fixture_dir, f"{name}_truth.pdf")
    clean = os.path.join(fixture_dir, f"{name}_clean.png")
    image = os.path.join(fixture_dir, f"{name}.png")

    make_fixture.build_truth_xml(truth, measures)
    ok, rc = make_fixture.render_truth_pdf(truth, pdf, musescore)
    if not ok:
        return {"case": case, "error": f"musescore rc={rc}"}
    make_fixture.pdf_to_png(pdf, clean)
    degrade_meta = make_fixture.degrade(
        clean, image, case if case != "none" else "none", seed=seed)

    row = {
        "case": case, "measures": measures, "seed": seed,
        "truth": truth, "image": image,
        "sha256": {
            "truth": sha256(truth), "pdf": sha256(pdf), "image": sha256(image),
        },
        "degrade": degrade_meta,
        "arms": {},
    }
    for label, runner_fn in (("scorehalo", run_scorehalo),
                             ("audiveris", run_audiveris)):
        outdir = os.path.join(run_dir, name, label)
        print(f"[ab] {name} / {label} ...", flush=True)
        started = time.perf_counter()
        try:
            kwargs = {"timeout": timeout, "python": python} \
                if label == "scorehalo" else {"timeout": timeout,
                                              "binary": audiveris}
            arm = runner_fn(image, outdir, **kwargs)
            arm = score_prediction(truth, arm["pred"], arm, started,
                                   time.perf_counter() - started)
        except subprocess.TimeoutExpired as exc:
            arm = {"error": f"timeout after {timeout}s: {exc}",
                   "seconds": timeout, "rc": None, "pred": None}
        except Exception as exc:
            arm = {"error": f"{type(exc).__name__}: {exc}",
                   "traceback": traceback.format_exc(), "rc": None,
                   "pred": None}
        row["arms"][label] = arm
        if "error" in arm:
            print(f"[ab]   ERROR {arm['error']}", flush=True)
        else:
            print(f"[ab]   exact {arm['exact']}/{arm['truth_notes']} "
                  f"P={arm['exact_precision']}% R={arm['exact_recall']}% "
                  f"({arm['seconds']}s)", flush=True)
    a = row["arms"].get("scorehalo", {})
    b = row["arms"].get("audiveris", {})
    if "exact" in a and "exact" in b:
        row["delta_exact"] = a["exact"] - b["exact"]
        row["delta_precision"] = round(
            (a["exact_precision"] or 0) - (b["exact_precision"] or 0), 1)
    return row


def markdown_report(rows, run_id):
    lines = [
        f"# Paired engine A/B — run `{run_id}`", "",
        "Both engines saw the **same** labelled image. `exact` requires pitch "
        "and duration; streams are (part, staff, voice), so a different "
        "part layout counts as misses/spurious. This is a comparison of "
        "these fixtures, not a claim of source accuracy.", "",
        "| case | arm | rc | notes | exact | exact P | exact R | pitch | dur | missed | spurious | sec |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        for label in ("scorehalo", "audiveris"):
            arm = r.get("arms", {}).get(label, {})
            if "error" in arm:
                lines.append(f"| {r['case']} | {label} | ERROR: "
                             f"{str(arm['error'])[:80]} | | | | | | | | | |")
                continue
            lines.append(
                f"| {r['case']} | {label} | {arm.get('rc')} | "
                f"{arm.get('exact')}/{arm.get('truth_notes')} | "
                f"{arm.get('exact')} | {arm.get('exact_precision')}% | "
                f"{arm.get('exact_recall')}% | {arm.get('pitch_wrong')} | "
                f"{arm.get('duration_wrong')} | {arm.get('missed')} | "
                f"{arm.get('spurious')} | {arm.get('seconds')} |")
    lines += ["", "Positive `delta_exact` favours ScoreHalo; negative favours "
              "Audiveris. Errors are rows too — an engine that cannot run "
              "does not silently score zero.", ""]
    for r in rows:
        # A case can fail before both arms produced numbers (musescore rc,
        # timeout, no prediction). A report must still be writable.
        if r.get("delta_exact") is None or r.get("delta_precision") is None:
            continue
        lines.append(f"- {r['case']}: delta_exact = {r['delta_exact']:+d}, "
                     f"delta_precision = {r['delta_precision']:+.1f} pp")
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cases", default="scan",
                    help="comma-separated: none,light,scan")
    ap.add_argument("--measures", type=int, default=6)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--musescore", default=shutil.which("musescore3")
                    or shutil.which("musescore"))
    ap.add_argument("--audiveris", default=DEFAULT_AUDIVERIS)
    ap.add_argument("--timeout", type=int, default=900,
                    help="seconds per engine call")
    ap.add_argument("--json", default="",
                    help="report path; default is inside the fresh run dir")
    ap.add_argument("--run-id", default="")
    args = ap.parse_args(argv)

    cases = [c.strip() for c in args.cases.split(",") if c.strip()]
    if not cases or any(c not in ALLOWED_CASES for c in cases):
        ap.error(f"--cases must contain only: {', '.join(sorted(ALLOWED_CASES))}")
    if len(cases) != len(set(cases)):
        ap.error("--cases must not contain duplicates")
    if args.measures < 1:
        ap.error("--measures must be a positive integer")
    if not args.musescore or not os.path.isfile(args.musescore):
        ap.error("MuseScore executable not found; use --musescore PATH")
    if not os.path.isfile(args.audiveris):
        ap.error("Audiveris launcher not found; use --audiveris PATH")

    runs = os.path.join(ROOT, "data", "ab-runs")
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

    out_path = (os.path.join(ROOT, args.json) if args.json
                else os.path.join(run_dir, "report.json"))
    md_path = os.path.splitext(out_path)[0] + ".md"
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

    rows = []
    for case in cases:
        try:
            row = score_case(case, args.measures, args.seed, run_dir,
                             args.musescore, args.audiveris,
                             timeout=args.timeout)
        except Exception as exc:
            row = {"case": case, "error": f"{type(exc).__name__}: {exc}",
                   "traceback": traceback.format_exc()}
        rows.append(row)
        with open(out_path, "w") as fh:
            json.dump({"run_id": run_id, "measures": args.measures,
                       "seed": args.seed, "rows": rows}, fh, indent=2)
        with open(md_path, "w") as fh:
            fh.write(markdown_report(rows, run_id))
        print(f"[ab] wrote {out_path} and {md_path}", flush=True)

    print(markdown_report(rows, run_id))
    return 1 if any("error" in r for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
