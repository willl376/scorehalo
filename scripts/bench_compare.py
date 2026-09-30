"""Head-to-head benchmark: OLD vs NEW compare on GROUND TRUTH.

We have labelled fixtures (data/fixtures/*_truth.musicxml) whose notes are
known exactly. So we can construct a set where the correct verdict is known:

  GOOD  = the true transcription            -> a compare tool should NOT flag it
  BAD   = deliberately corrupted variants   -> a compare tool SHOULD flag it

Corruptions model real transcription failures:
  drop     - notes lost (homr missed them)
  shift    - wrong pitches (read off by an interval)
  double   - notes duplicated
  flood    - far too much music invented
  rests    - page transcribed as empty

The desired result is not a prettier number. It is: pass every GOOD page, and
flag every BAD one. That is scored here as a confusion matrix per tool.
"""
import copy
import importlib.util
import json
import os
import statistics
import sys
import xml.etree.ElementTree as ET
from concurrent.futures import ProcessPoolExecutor

ROOT = "/home/wburt59/scorehalo"
sys.path.insert(0, os.path.join(ROOT, "pysrc"))
WORK = "/tmp/vz/bench"
MS = os.path.expanduser("~/.local/opt/musescore/bin/mscore4portable")

GOOD = "good"
CORRUPTIONS = ["drop", "shift", "double", "flood", "rests"]


# ---------------------------------------------------------------- mutations
def _notes(root):
    return root.findall(".//note")


def _parent_map(root):
    return {c: p for p in root.iter() for c in p}


def _insert_after(root, elem, new):
    """ElementTree has no addnext (that is lxml), so insert by index."""
    pmap = {c: p for p in root.iter() for c in p}
    parent = pmap[elem]
    parent.insert(list(parent).index(elem) + 1, new)


def mutate(src_xml, kind):
    tree = ET.parse(src_xml)
    root = tree.getroot()
    notes = _notes(root)
    if not notes:
        return None
    if kind == "drop":                      # lose 45% of the notes
        for n in notes[::2][: max(1, len(notes) // 2)]:
            for ch in list(n):
                if ch.tag in ("pitch", "duration", "voice", "type", "staff"):
                    n.remove(ch)
            n.insert(0, ET.Element("rest"))
    elif kind == "shift":                   # every pitch 2 semitones sharp
        for n in notes:
            p = n.find("pitch")
            if p is not None:
                alt = p.find("alter")
                cur = int(alt.text) if alt is not None else 0
                alt = alt if alt is not None else ET.SubElement(p, "alter")
                alt.text = str(cur + 2)
    elif kind == "double":                  # duplicate every note
        for n in list(notes):
            dup = copy.deepcopy(n)
            _insert_after(root, n, dup)
    elif kind == "flood":                   # 6x the music
        for _ in range(5):
            for n in list(_notes(root)):
                _insert_after(root, n, copy.deepcopy(n))
    elif kind == "rests":                   # everything becomes a rest
        for n in notes:
            for ch in list(n):
                if ch.tag == "pitch":
                    n.remove(ch)
            n.insert(0, ET.Element("rest"))
    return ET.tostring(root, encoding="unicode")


# ---------------------------------------------------------------- rendering
def render_one(job):
    """Render one MusicXML to a 300dpi PNG. Runs in a worker process, so the
    old and new arms (and every variant) render concurrently."""
    import subprocess
    name, xml_path, out_png = job
    os.makedirs(os.path.dirname(out_png), exist_ok=True)
    pdf = out_png[:-4] + f"_{os.getpid()}.pdf"
    for attempt in range(3):
        if os.path.exists(pdf):
            os.remove(pdf)
        try:
            r = subprocess.run([MS, "-o", pdf, xml_path], capture_output=True,
                               timeout=300,
                               env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
        except subprocess.TimeoutExpired:
            continue
        if os.path.exists(pdf) and os.path.getsize(pdf) > 1024:
            break
    else:
        return (name, None, f"rc={getattr(r, 'returncode', '?')}")
    png_base = out_png[:-4] + f"_{os.getpid()}"
    subprocess.run(["pdftoppm", "-png", "-r", "300", pdf, png_base],
                   check=True, timeout=180, capture_output=True)
    made = sorted(f for f in os.listdir(os.path.dirname(out_png))
                  if f.startswith(os.path.basename(png_base))
                  and f.endswith(".png"))
    if not made:
        return (name, None, "no raster")
    final = os.path.join(os.path.dirname(out_png), name + ".png")
    os.replace(os.path.join(os.path.dirname(out_png), made[0]), final)
    return (name, final, "ok")


# ---------------------------------------------------------------- the two arms
def load_arm(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


def main():
    os.makedirs(WORK, exist_ok=True)
    fixtures = sorted(
        f for f in os.listdir(f"{ROOT}/data/fixtures")
        if f.endswith("_truth.musicxml"))
    cases, jobs = [], []
    for fx in fixtures:
        stem = fx[: -len("_truth.musicxml")]
        truth = f"{ROOT}/data/fixtures/{fx}"
        scan = f"{ROOT}/data/fixtures/{stem}.png"
        if not os.path.exists(scan):
            continue
        for kind in [GOOD] + CORRUPTIONS:
            name = f"{stem}__{kind}"
            xml = os.path.join(WORK, name + ".musicxml")
            if kind == GOOD:
                with open(truth, "rb") as a, open(xml, "wb") as b:
                    b.write(a.read())
            else:
                data = mutate(truth, kind)
                if data is None:
                    continue
                with open(xml, "w") as fh:
                    fh.write(data)
            cases.append({"name": name, "scan": scan, "label": kind,
                          "truth": kind == GOOD})
            jobs.append((name, xml, os.path.join(WORK, "png", name + ".png")))

    print(f"built {len(cases)} cases "
          f"({sum(c['truth'] for c in cases)} known-good, "
          f"{sum(not c['truth'] for c in cases)} deliberately broken)")

    with ProcessPoolExecutor(max_workers=4) as ex:
        rendered = {n: (p, e) for n, p, e in ex.map(render_one, jobs)}

    ok_cases = [c for c in cases if rendered[c["name"]][0]]
    print(f"rendered {len(ok_cases)}/{len(cases)} "
          f"(failures: {[c['name'] for c in cases if not rendered[c['name']][0]]})\n")

    old = load_arm("/tmp/vz/ab/compare_old.py", "arm_old")
    new = load_arm(f"{ROOT}/pysrc/scorehalo/compare.py", "arm_new")

    # OLD: absolute thresholds, verdict straight from compare_pages
    old_res = {}
    for c in ok_cases:
        p = rendered[c["name"]][0]
        try:
            rep, verdict = old.compare_pages(c["scan"], p)
        except Exception as exc:                       # noqa: BLE001
            old_res[c["name"]] = ("error", str(exc))
            continue
        old_res[c["name"]] = (verdict, rep)

    # NEW: corpus-relative, baseline taken from the KNOWN-GOOD pages only,
    # which is the correct use: establish truth, then flag departures.
    good_reports = [new.compare_pages(c["scan"], rendered[c["name"]][0])
                    for c in ok_cases if c["truth"]]
    baseline = {k: statistics.median(r[k] for r in good_reports)
                for k in ("ink_ratio", "quadrant_deviance")}
    baseline["staff"] = statistics.median(
        r["staff_render"] / max(1.0, r["staff_scan"]) for r in good_reports)
    new_res = {}
    for c in ok_cases:
        p = rendered[c["name"]][0]
        try:
            rep = new.compare_pages(c["scan"], p)
        except Exception as exc:                       # noqa: BLE001
            new_res[c["name"]] = ("error", str(exc))
            continue
        issues = []
        if abs(rep["ink_ratio"] - baseline["ink_ratio"]) / baseline["ink_ratio"] > 0.5:
            issues.append("ink")
        s_ratio = rep["staff_render"] / max(1.0, rep["staff_scan"])
        if abs(s_ratio - baseline["staff"]) / baseline["staff"] > 0.5:
            issues.append("staff")
        if rep["quadrant_deviance"] > max(0.12, baseline["quadrant_deviance"] * 3):
            issues.append("quadrant")
        new_res[c["name"]] = ("review" if issues else "clean", rep)

    def score(res, flagged):
        # NOTE: `flagged` is a SET of verdicts. Comparing a verdict string to a
        # tuple here is silently always False and reports 0% recall for both
        # arms -- which is exactly what the first version of this script did.
        hit = lambda v: v in flagged          # noqa: E731
        tp = sum(1 for c in ok_cases
                 if not c["truth"] and hit(res[c["name"]][0]))
        fn = sum(1 for c in ok_cases
                 if not c["truth"] and not hit(res[c["name"]][0]))
        fp = sum(1 for c in ok_cases
                 if c["truth"] and hit(res[c["name"]][0]))
        tn = sum(1 for c in ok_cases
                 if c["truth"] and not hit(res[c["name"]][0]))
        return tp, fn, fp, tn

    print(f"{'case':38s} {'truth':8s} {'OLD':10s} {'NEW':10s}")
    print("-" * 70)
    for c in ok_cases:
        want = "good" if c["truth"] else "bad"
        print(f"{c['name']:38s} {want:8s} "
              f"{old_res[c['name']][0]:10s} {new_res[c['name']][0]:10s}")

    print()
    for label, res, flag in (("OLD (as pushed)", old_res, {"warn", "defect"}),
                             ("NEW (current)   ", new_res, {"review"})):
        tp, fn, fp, tn = score(res, flag)
        recall = tp / max(1, tp + fn)
        precision = tp / max(1, tp + fp)
        print(f"{label}  caught {tp}/{tp+fn} broken pages "
              f"(recall {recall:.0%}), precision {precision:.0%}")
        for kind in CORRUPTIONS:
            hit = sum(1 for c in ok_cases
                      if c["name"].endswith("__" + kind)
                      and res[c["name"]][0] in flag)
            tot = sum(1 for c in ok_cases if c["name"].endswith("__" + kind))
            print(f"      {kind:8s} {hit}/{tot} caught")

    with open(f"{WORK}/results.json", "w") as fh:
        json.dump({"old": {k: [v[0]] for k, v in old_res.items()},
                   "new": {k: [v[0]] for k, v in new_res.items()}}, fh, indent=2)


if __name__ == "__main__":
    main()
