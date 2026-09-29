"""Canon: let the engravers judge our own output, so we need no labels.

Two GPL engravers already encode centuries of notation law. LilyPond's barcheck
knows a measure must add up; MuseScore's importer knows a part must carry the
staves its voices imply. Neither was written to grade optical music recognition,
but both must silently REPAIR a malformed file before they can render it, and
every repair is a defect we can name, count and locate -- for free, forever,
with no ground truth.

This is the missing half of the accuracy work. We graded output against
fixtures we had to manufacture by engraving known music, which is both
expensive and optimistic: clean engraving is the easy end of the range. This
asks the complementary question -- not "is this the right note?" but "what did
the engraver have to fix?" -- and it can be asked of every real page forever.

The two oracles are deliberately kept in different domains, because neither one
sees the page image:

  structure / validity   MuseScore  (notation oracle; blind to perception)
  engraving             LilyPond   (notation oracle; blind to perception)
  what is really there  homr + the CV layout stage (perception)

A defect is only reported when the engravers agree, so a quirk of one
implementation cannot masquerade as a fact about the music.

The licence boundary does the same work as the architectural one. homr is
AGPL-3.0, MuseScore and LilyPond are GPL, and this repository is MIT. Every
one of those stays a separate process that is invoked and never linked, vendored
or imported, which is precisely what keeps this file MIT and what lets the
oracles be swapped without touching the pipeline.

Usage:
  scorehalo canon data/out-band
  scorehalo canon data/out-band --json /tmp/canon.json --lilypond
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

CANON_NOTE = "rests MuseScore inserted to complete a measure"


def _divisions(measure, carried):
    node = measure.find("attributes/divisions")
    if node is not None and node.text:
        try:
            return int(node.text)
        except ValueError:
            pass
    return carried or 1


def _pitch_name(note):
    pit = note.find("pitch")
    if pit is None:
        return "rest" if note.find("rest") is not None else "unpitched"
    step = pit.findtext("step", "")
    alter = pit.findtext("alter")
    octave = pit.findtext("octave", "")
    if alter in (None, ""):
        alter = "0"
    elif alter.startswith("-"):
        alter = "-" + alter[1:].lstrip("0") or "0"
    else:
        alter = "+" + alter.lstrip("0")
    return f"{step}{alter}{octave}"


def scan(path):
    """Return (rests, content, shape) for one MusicXML file.

    rests maps (measure, voice) -> tuple of rest durations in quarter notes.
    content counts (measure, voice, quarters, pitch) and deliberately ignores
    the <chord> flag: MuseScore re-emits the same chord with a different root
    marked, which is musically identical noise, not a defect.
    """
    root = ET.parse(path).getroot()
    rests = collections.defaultdict(list)
    content = collections.Counter()
    divisions = 1
    notes = 0
    measures = 0
    for part in root.findall("part"):
        for measure in part.findall("measure"):
            measures += 1
            divisions = _divisions(measure, divisions)
            for note in measure.findall("note"):
                notes += 1
                dur = note.findtext("duration")
                quarters = 0.0
                if dur:
                    try:
                        quarters = int(dur) / divisions
                    except (ValueError, ZeroDivisionError):
                        quarters = 0.0
                voice = note.findtext("voice") or "1"
                name = _pitch_name(note)
                key = (measure.get("number"), voice)
                if name == "rest":
                    rests[key].append(quarters)
                content[(measure.get("number"), voice, quarters, name)] += 1
    shape = {
        "parts": len(root.findall("part")),
        "measures": measures,
        "notes": notes,
        "staves": sorted({n.text for n in root.findall(".//attributes/staves") if n.text}),
        "voices": sorted({(n.text or "1") for n in root.findall(".//voice")}),
    }
    return rests, content, shape


def roundtrip(src, musescore_bin=None, timeout=300):
    """Import src into MuseScore and re-export it as MusicXML."""
    bin_ = musescore_bin or shutil.which("mscore4portable") or shutil.which("mscore")
    if not bin_:
        return None, "MuseScore binary not found; pass --musescore"
    work = tempfile.mkdtemp(prefix="canon-")
    exported = os.path.join(work, "reexport.musicxml")
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    last = ""
    for _ in range(3):
        try:
            r = subprocess.run(
                [bin_, "-o", exported, os.path.abspath(src)],
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
            )
        except subprocess.TimeoutExpired:
            last = "timeout"
            continue
        if os.path.exists(exported):
            return exported, None
        last = f"rc={r.returncode}: {r.stderr[-200:]}"
    shutil.rmtree(work, ignore_errors=True)
    return None, last


def lilypond_verdicts(path, timeout=300):
    """Ask LilyPond to engrave the page and report what it complained about."""
    m2l = shutil.which("musicxml2ly")
    lily = shutil.which("lilypond")
    if not m2l or not lily:
        return None
    work = tempfile.mkdtemp(prefix="canon-ly-")
    try:
        stem = os.path.join(work, "page")
        r = subprocess.run(
            [m2l, "-o", stem + ".ly", os.path.abspath(path)],
            capture_output=True, text=True, timeout=timeout,
        )
        if not os.path.exists(stem + ".ly"):
            return {"error": f"musicxml2ly rc={r.returncode}"}
        r = subprocess.run(
            [lily, "-dno-point-and-click", "-o", stem + ".pdf", stem + ".ly"],
            capture_output=True, text=True, timeout=timeout,
        )
        text = (r.stderr or "") + (r.stdout or "")
        keep = [
            line.strip() for line in text.splitlines()
            if any(w in line.lower() for w in ("barcheck", "slur", "warning", "error"))
        ]
        return {"rc": r.returncode, "complaints": keep}
    except subprocess.TimeoutExpired:
        return {"error": "timeout"}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def canon_page(path, musescore_bin=None, with_lilypond=False):
    report = {"page": os.path.basename(path)}
    try:
        in_rests, in_content, in_shape = scan(path)
    except (ET.ParseError, OSError) as e:
        report["ok"] = False
        report["error"] = f"unreadable: {e}"
        return report

    exported, err = roundtrip(path, musescore_bin)
    if not exported:
        report["ok"] = False
        report["error"] = err
        return report
    try:
        out_rests, out_content, out_shape = scan(exported)
    finally:
        work = os.path.dirname(exported)
        shutil.rmtree(work, ignore_errors=True)

    inserted = {}
    for key, durs in out_rests.items():
        extra = collections.Counter(durs) - collections.Counter(in_rests.get(key, ()))
        if extra:
            inserted[key] = sorted(extra.elements())

    lost = in_content - out_content
    gained = out_content - in_content
    report.update({
        "ok": True,
        "shape_in": in_shape,
        "shape_out": out_shape,
        "inserted_rests": {f"m{k[0]}/v{k[1]}": v for k, v in sorted(inserted.items())},
        "n_inserted_rests": sum(len(v) for v in inserted.values()),
        "n_voices_padded": len(inserted),
        "content_lost": sum(lost.values()),
        "content_gained": sum(gained.values()),
    })
    if in_shape["staves"] != out_shape["staves"]:
        report["staff_count_disagreement"] = {
            "ours": in_shape["staves"], "musescore": out_shape["staves"]}
    if with_lilypond:
        report["lilypond"] = lilypond_verdicts(path)
    return report


def cmd_canon(args):
    out_dir = os.path.abspath(args.out_dir)
    files = sorted(
        os.path.join(out_dir, f)
        for f in os.listdir(out_dir)
        if f.endswith(".musicxml") and not f.startswith("score")
    )
    if args.only:
        files = [f for f in files if any(o in f for o in args.only)]
    reports = []
    for f in files:
        rep = canon_page(f, args.musescore, args.lilypond)
        reports.append(rep)
        if not rep.get("ok"):
            print(f"  {rep['page']:<22} ERROR {rep.get('error')}")
            continue
        flag = "clean" if not rep["n_inserted_rests"] else f"{rep['n_inserted_rests']} inserted rests"
        print(f"  {rep['page']:<22} {flag}")
        for where, durs in rep["inserted_rests"].items():
            print(f"      {where}: {'+'.join(str(d) for d in durs)}q padding")
    total = sum(r.get("n_inserted_rests", 0) for r in reports)
    bad = sum(1 for r in reports if r.get("n_inserted_rests"))
    print(f"\n  {len(reports)} pages  |  {bad} need repair  |  {total} inserted rests total")
    if args.json:
        with open(args.json, "w") as fh:
            json.dump({"note": CANON_NOTE, "reports": reports}, fh, indent=1)
        print(f"  wrote {args.json}")
    return 0


def add_parser(sub):
    p = sub.add_parser(
        "canon",
        help="let MuseScore/LilyPond reveal what they had to repair in our output",
    )
    p.add_argument("out_dir", help="dir of per-page .musicxml files")
    p.add_argument("--musescore", help="path to mscore4portable/mscore")
    p.add_argument("--lilypond", action="store_true", help="also ask LilyPond to engrave each page")
    p.add_argument("--only", nargs="*", help="only pages whose name contains one of these")
    p.add_argument("--json", help="write the full report here")
    p.set_defaults(fn=cmd_canon)
    return p
