"""Ask MuseScore itself whether a MusicXML file is sound.

MuseScore's headless importer has an ``mlog`` output format -- an "internal file
sanity check log (JSON)" -- that is a genuine third oracle next to
``scripts/validate_musicxml.py`` (structure) and the invariant checkers in
``tests/test_emit_invariants.py`` (values). It catches something neither of the
others does:

    {"error":"Measure 6, staff 3 incomplete. Expected: 4/4; Found: 12/8",
     "result":1}

That message is MuseScore summing the NOMINAL value of every note --
``<type>`` plus ``<dot>`` scaled by ``<time-modification>`` -- rather than the
``<duration>`` every other check reads. The two disagree exactly when a note is
written with a value other than the ticks it occupies, which is a real defect
(an importer will lay the bar out too long) that a cursor-based check cannot see
because the cursor lands on the barline correctly.

The verdict is JSON, so it is also read the one way that survives MuseScore's
exit code: on an import failure the log is still written and the process still
exits 0.

Batch mode (the reason this is fast)
------------------------------------
MuseScore 3.6 has a conversion JOB: ``-j <file>`` where the file is a JSON
ARRAY of ``{"in": ..., "out": ...}``. It processes every pair in ONE process,
so checking a whole book is one start-up rather than one per file (~8 s for
16 pages here, against ~1 min per page one at a time). This script always
drives that, even for a single file, and asks for the log by export name
(``-j``, not ``-o``) so nothing has to be inferred from the score.

The one trap in that mode: the runner STOPS at the first job it calls failed.
Measured -- p0020 converted (``success!``), p0021 reported ``failed!`` and
still wrote its log, and the two files after p0021 were never touched. So a
run is a prefix, not a sweep, and :func:`run_batch` re-offers the tail until
every file has been attempted.

Usage::

    scripts/musescore_check.py grid-p0020.musicxml             # one file
    scripts/musescore_check.py --all data/out-system/pages     # a book
    scripts/musescore_check.py --json a.musicxml b.musicxml    # for a caller

Exit status is 0 when every file is clean and 1 when any is flagged.

What was verified against this binary
-------------------------------------
* ``-o out.<ext>`` exports for ext in musicxml, mxl, pdf, png, mid (and
  ``.mlog``); a PNG is written page-suffixed (``out-1.png``), ``-r DPI`` sets
  its resolution and ``-T`` trims it.
* ``-j`` takes an ARRAY. A bare object fails with
  ``json file <...> is not an array`` and no output.
* It needs a real X (``DISPLAY=:0``, xcb): ``QT_QPA_PLATFORM=offscreen`` fails
  because only xcb ships. ``libjack.so.0``/``libnss3.so`` on stderr are benign.
* Structurally-broken input (e.g. a bar nobody could fill) makes it report the
  measure in the log rather than crash, which is why the log is the interface.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

#: Formats kept for reference when a caller wants more than the verdict.
EXPORTABLE = ("musicxml", "mxl", "pdf", "png", "mid", "mlog")


def find_musescore(explicit=None):
    """The MuseScore binary to use: the override, the env, else PATH."""
    for cand in (explicit, os.environ.get("MUSESCORE_APPIMAGE"),
                 os.environ.get("MUSESCORE")):
        if cand and os.path.exists(os.path.expanduser(cand)):
            return os.path.expanduser(cand)
    for name in ("mscore4portable", "musescore", "mscore",
                 "MuseScore-3.6.1.515740129-x86_64.AppImage"):
        hit = shutil.which(name) or shutil.which(
            os.path.join("~", "Downloads", name))
        if hit:
            return hit
    local = os.path.expanduser(
        "~/Downloads/MuseScore-3.6.1.515740129-x86_64.AppImage")
    return local if os.path.exists(local) else None


def read_mlog(path):
    """The parsed verdict at ``path``, or an error describing why not."""
    if not os.path.exists(path):
        return {"result": 1, "error": "MuseScore wrote no log for this file"}
    text = open(path, encoding="utf-8", errors="replace").read().strip()
    if not text:
        return {"result": 1, "error": "empty log"}
    try:
        return json.loads(text)
    except ValueError:
        return {"result": 1, "error": "unparseable log: %s" % text[:200]}


def _attempt(paths, musescore, display, timeout, td):
    """One ``-j`` run over ``paths``; return {path: log the run should write}.

    MuseScore's job runner is NOT a queue that drains: it stops at the first
    job it calls failed. Measured here -- p0020 converted (``success!``),
    p0021 reported ``failed!`` and still wrote its own log, and the two files
    after it were never touched. So a caller must re-offer the tail, which is
    what :func:`run_batch` does; treating this as a full sweep is how you
    silently report "no log" for files that were never read.
    """
    logs = {}
    jobs = []
    for i, path in enumerate(paths):
        log = os.path.join(td, "c%03d.mlog" % i)
        logs[path] = log
        jobs.append({"in": os.path.abspath(path), "out": log})
    jobfile = os.path.join(td, "job.json")
    with open(jobfile, "w", encoding="utf-8") as fh:
        json.dump(jobs, fh)
    env = dict(os.environ)
    env.setdefault("DISPLAY", display)
    try:
        subprocess.run([musescore, "-j", jobfile], env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=timeout * max(1, len(paths)), check=False)
    except subprocess.TimeoutExpired:
        return {}
    return {p: log for p, log in logs.items() if os.path.exists(log)}


def run_batch(paths, musescore, display=":0", timeout=180):
    """Check ``paths`` with as few MuseScore processes as it takes.

    One clean book is ONE process (a job runs every pair); each failure ends
    the run early, so the tail is re-offered until every file has a log.
    Files are always attempted in the given order, and a file is only ever
    reported once.
    """
    verdicts = {}
    with tempfile.TemporaryDirectory() as td:
        pending = list(paths)
        while pending:
            logs = _attempt(pending, musescore, display, timeout, td)
            done = [p for p in pending if p in logs]
            if not done:
                break
            for p in done:
                verdicts[p] = read_mlog(logs[p])
            pending = pending[len(done):]
        for p in paths:
            verdicts.setdefault(p, {"result": 1,
                                    "error": "MuseScore wrote no log"})
    return verdicts


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("paths", nargs="*", help="MusicXML files to check")
    ap.add_argument("--all", metavar="DIR",
                    help="check every *.musicxml directly under DIR")
    ap.add_argument("--musescore", default=None, help="path to MuseScore")
    ap.add_argument("--display", default=os.environ.get("MUSESCORE_DISPLAY",
                                                        ":0"))
    ap.add_argument("--timeout", type=int, default=180,
                    help="seconds per file; sizes the batch budget")
    ap.add_argument("--json", action="store_true",
                    help="print the verdicts as one JSON object")
    args = ap.parse_args()

    paths = list(args.paths)
    if args.all:
        paths += [os.path.join(args.all, f) for f in sorted(os.listdir(args.all))
                  if f.endswith(".musicxml")]
    if not paths:
        ap.error("give at least one file, or --all DIR")

    musescore = find_musescore(args.musescore)
    if not musescore:
        print("no MuseScore binary found (set MUSESCORE_APPIMAGE)",
              file=sys.stderr)
        return 2
    if not args.json:
        print("using %s" % musescore)

    verdicts = run_batch(paths, musescore, display=args.display,
                         timeout=args.timeout)
    clean = [p for p, v in verdicts.items() if v.get("result") == 0]
    bad = [p for p in paths if p not in clean]

    if args.json:
        print(json.dumps({p: verdicts[p] for p in paths}, indent=2))
    else:
        for path in paths:
            report = verdicts[path]
            name = os.path.basename(path)
            if report.get("result") == 0:
                print("%s: clean" % name)
            else:
                print("%s: %s" % (name, report.get("error", report)))
        print("\nMuseScore mlog: clean=%d flagged=%d" % (len(clean), len(bad)))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
