"""RECOVERY CANDIDATE -- isolated attempts and checked resume identity.

Repairs, each demonstrated by tests/t6_resume.py or by reading engine.py:

  1. RESUME ONLY VERIFIED SUCCESSES.  The old code added every JSONL record to
     `done`, so a timeout / empty crop / engine crash was skipped forever and
     the hole silently survived into the assembled score.  Now a task is only
     "done" if ok is true AND its retained XML still exists on disk.
     --retry-failed is therefore the default; --skip-failed restores the old
     behaviour explicitly if a run genuinely wants it.
  2. THE WHOLE WORKER IS PROTECTED.  The old try/except wrapped only run_homr;
     a corrupt boxes.json, an unreadable deskew PNG, a bad staff index, or an
     XML copy failure escaped and could take the pool down.
  3. PER-TASK ENGINE DIRECTORY.  run_homr stages <basename>_in.png and names
     the result <basename>.musicxml, so crops are unique and this is not a
     live race today -- but it becomes one the moment the crop name changes.
     Isolation costs nothing: homr/<page>/s<NN>.
  4. JSONL IS REPAIRED, NOT ASSUMED.  A truncated final line (power cut) used
     to be tolerated at startup and then crash the summary, or worse, swallow
     the next record appended after it.  We truncate the partial tail to the
     last newline, keep it as *.damaged, and report malformed interior lines.
  5. SUMMARY COUNTS CURRENT TASKS ONCE.  Expected / verified / failed / missing
     are separate numbers, and success is labelled "tasks completed with
     parseable output" rather than "staves recognised".
  6. ARGUMENTS AND GEOMETRY ARE VALIDATED, and the crop box + engine log are
     recorded so a result can be reproduced without guessing.
"""

import argparse
import hashlib
import shutil
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
import json
import os
import sys
import time
from multiprocessing import Pool

import numpy as np
from PIL import Image

ROOT = next(
    p for p in Path(__file__).resolve().parents
    if (p / "pysrc/scorehalo").is_dir()
    and (p / "scripts/make_fixture.py").is_file()
)
sys.path.insert(0, str(ROOT / "pysrc"))
sys.path.insert(0, str(ROOT / "scripts"))
from scorehalo.layout import analyze_page  # noqa: E402
from scorehalo.engine import run_homr  # noqa: E402
from scorehalo import graphics  # noqa: E402


def xml_stats(path):
    """Structural statistics, never a claim of transcription accuracy."""
    if not path or not os.path.isfile(path):
        return {"status": "missing"}
    try:
        root = ET.parse(path).getroot()
        tag = root.tag
        namespace = tag[1:].split("}", 1)[0] if tag.startswith("{") else ""
        def q(name):
            return "{%s}%s" % (namespace, name) if namespace else name
        if tag != q("score-partwise"):
            raise ValueError("expected score-partwise MusicXML")
        parts = root.findall(q("part"))
        measures = [
            m for part in parts for m in part.findall(q("measure"))
        ]
        if not parts or not measures:
            raise ValueError("no musical parts or measures")
        notes = [
            n for measure in measures for n in measure.findall(q("note"))
        ]
        pitched = sum(n.find(q("pitch")) is not None for n in notes)
        rests = sum(n.find(q("rest")) is not None for n in notes)
        return {
            "status": "ok",
            "parts": len(parts),
            "measures": len(measures),
            "note_elements": len(notes),
            "pitched_notes": pitched,
            "rests": rests,
            "has_pitched_notes": pitched > 0,
            "notes": len(notes),
        }
    except (OSError, ET.ParseError, ValueError) as exc:
        return {"status": "invalid", "why": str(exc)}


def retained_ok(record, out):
    if record.get("ok") is not True or record.get("rc") != 0:
        return False
    kept = record.get("kept")
    if not isinstance(kept, str) or not kept:
        return False
    base = Path(out).resolve()
    path = (base / kept).resolve()
    if base not in path.parents:
        return False
    return xml_stats(str(path)).get("status") == "ok"


def read_latest(path):
    latest = {}
    if not os.path.exists(path):
        return latest
    with open(path, encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise ValueError("record is not an object")
                page = record["page"]
                staff = record["staff"]
                if not isinstance(page, str) or not page:
                    raise ValueError("invalid page name")
                if type(staff) is not int or staff < 1:
                    raise ValueError("invalid staff number")
            except (ValueError, KeyError, TypeError) as exc:
                raise ValueError(f"{path}, line {number}: {exc}") from exc
            latest[(page, staff)] = record
    return latest


def file_hash(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_run_identity(args, only):
    """Fail closed before reusing or rewriting an existing run."""
    source = Path(args.out_dir).resolve()
    manifest = source / "manifest.json"
    with manifest.open(encoding="utf-8") as handle:
        man = json.load(handle)

    inputs = []
    seen = set()
    for page in man["pages"]:
        if page.get("status") != "ok":
            continue
        name = Path(page["image"]).stem
        if only is not None and name not in only:
            continue
        if name in seen:
            raise ValueError(f"duplicate page stem: {name}")
        seen.add(name)
        image = source / "render" / (name + ".png")
        if not image.is_file():
            raise ValueError(f"required render missing: {image}")
        inputs.append([name, file_hash(image)])

    if not inputs:
        raise ValueError("no matching successful pages with rendered inputs")

    code = {
        str(p.relative_to(ROOT)): file_hash(p)
        for p in sorted((ROOT / "pysrc/scorehalo").rglob("*.py"))
    }
    identity = {
        "schema": 3,
        "source": str(source),
        "manifest_hash": file_hash(manifest),
        "inputs": sorted(inputs),
        "pad_frac": args.pad_frac,
        "min_staff_lines": args.min_staff_lines,
        "mask_graphics": getattr(args, "mask_graphics", False),
        "timeout": args.timeout,
        "engine_id": args.engine_id,
        "runner_hash": file_hash(__file__),
        "project_code": code,
    }

    out = Path(args.out)
    marker = out / "resume_identity.json"
    if marker.exists():
        previous = json.loads(marker.read_text(encoding="utf-8"))
        if previous != identity:
            raise ValueError(
                "input/configuration identity changed; use a NEW --out folder"
            )
    else:
        if any(out.iterdir()):
            raise ValueError(
                "existing output has no matching identity; use a NEW --out folder"
            )
        with marker.open("x", encoding="utf-8") as handle:
            json.dump(identity, handle, indent=2)


RESUME_SCHEMA = 3       # bump when a record's meaning changes


def prep_pages(render_dir, manifest, out, min_staff_lines=5, pages=None,
               mask_graphics=False):
    """Detect staves once per page; cache deskewed image + staff boxes.

    ``mask_graphics`` is OPT-IN. A pictured region is erased only where it does
    not overlap a detected staff (the staff wins on disagreement), and the
    unmasked deskew is kept on disk. The masked image and every erased box are
    recorded in ``boxes.json`` so the decision can be audited or A/B'd.
    """
    with open(manifest) as fh:
        man = json.load(fh)
    pages_out = []
    skipped = []
    for p in man["pages"]:
        if p.get("status") != "ok":
            skipped.append((p.get("page"), p.get("status")))
            continue
        name = os.path.splitext(os.path.basename(p["image"]))[0]
        if pages is not None and name not in pages:
            continue
        rp = os.path.join(render_dir, f"{name}.png")
        if not os.path.exists(rp):
            print(f"  !! missing render {rp} -- skipping")
            skipped.append((p.get("page"), "render missing"))
            continue
        with Image.open(rp) as im:
            gray = np.array(im.convert("L"))
        r = analyze_page(gray, min_staff_lines=min_staff_lines)
        dp = os.path.join(out, f"{name}.deskew.png")
        Image.fromarray(r["deskewed"]).save(dp)
        deskew_used, regions = dp, []
        if mask_graphics:
            masked, regions = graphics.bypass(r["deskewed"], r["staves"])
            if regions:
                deskew_used = os.path.join(out, f"{name}.deskew.masked.png")
                Image.fromarray(masked).save(deskew_used)
        meta = {"page": name, "deskew": deskew_used, "render": rp,
                "n_staves": len(r["staves"]),
                "n_systems": r["n_systems"],
                "grouping": [s["n_staves"] for s in r["systems"]],
                "staff_space": r["staff_space"],
                "n_slivers_removed": r["n_slivers_removed"],
                "min_staff_lines": min_staff_lines,
                "mask_graphics": bool(mask_graphics),
                "deskew_raw": dp,
                "graphics_masked": regions,
                "staves": r["staves"]}
        with open(os.path.join(out, f"{name}.boxes.json"), "w") as fh:
            json.dump(meta, fh, indent=2)
        pages_out.append(meta)
        print(f"  {name}: {meta['n_staves']} staves, grouping "
              f"{'+'.join(map(str, meta['grouping']))}, "
              f"{r['n_slivers_removed']} slivers removed"
              + (f", {len(regions)} graphic region(s) masked" if regions else ""))
    return pages_out, skipped


def work(task):
    name, idx, out, pad, timeout = task
    rec = {"page": name, "staff": idx + 1, "schema": RESUME_SCHEMA}
    try:
        return _work(rec, name, idx, out, pad, timeout)
    except Exception as exc:          # a worker must never take down the pool
        rec.update(ok=False, why=f"{type(exc).__name__}: {exc}")
        return rec


def _work(rec, name, idx, out, pad, timeout):
    with open(os.path.join(out, f"{name}.boxes.json")) as fh:
        meta = json.load(fh)
    staves = meta["staves"]
    if not 0 <= idx < len(staves):
        rec.update(ok=False, why=f"staff index {idx} outside 0..{len(staves) - 1}")
        return rec
    st = staves[idx]
    with Image.open(meta["deskew"]) as im:
        desk = np.array(im.convert("L"))
    h, w = desk.shape
    pv, ph = h * pad * 0.5, w * pad
    t = int(max(0, st["y_top"] - pv))
    b = int(min(h, st["y_bot"] + pv))
    l = int(max(0, st["x0"] - ph))
    rr = int(min(w, st["x1"] + ph))
    rec.update(n_lines=st["n_lines"], spacing=st["spacing"],
               resid=st["resid"], width=st["width"],
               crop_box=[l, t, rr, b])
    if b - t < 20 or rr - l < 20:
        rec.update(ok=False, why="crop too small")
        return rec

    crop = desk[t:b, l:rr].copy()
    cp = os.path.join(out, "crops", f"{name}.s{idx + 1}.png")
    os.makedirs(os.path.dirname(cp), exist_ok=True)
    Image.fromarray(crop).save(cp)
    rec["crop_px"] = [int(rr - l), int(b - t)]

    task_dir = os.path.join(out, "homr", name, f"s{idx + 1:03d}")
    os.makedirs(task_dir, exist_ok=True)
    engine_out = tempfile.mkdtemp(prefix="attempt-", dir=task_dir)
    xml, log, rc = run_homr(cp, engine_out, timeout=timeout)
    rec["log"] = os.path.relpath(log, out) if log else None
    stats = xml_stats(xml)
    # ok == the task produced parseable MusicXML.  That is NOT accuracy;
    # the summary says "parseable output", never "correctly recognised".
    rec.update(rc=rc, stats=stats,
               ok=(rc == 0 and stats.get("status") == "ok"))
    if rec["ok"]:
        rec["xml"] = os.path.relpath(xml, out)
        d = os.path.join(out, "xml", f"{name}.s{idx + 1}.musicxml")
        os.makedirs(os.path.dirname(d), exist_ok=True)
        with open(xml) as f, open(d, "w") as g:
            g.write(f.read())
        rec["kept"] = os.path.relpath(d, out)
    return rec


def repair_jsonl(path):
    """Preserve valid final JSON; archive and remove only a torn final line."""
    if not os.path.exists(path):
        return []
    raw = Path(path).read_bytes()
    if not raw:
        return []

    repaired = raw
    tail = None
    if not raw.endswith(b"\n"):
        cut = raw.rfind(b"\n") + 1
        last = raw[cut:]
        try:
            json.loads(last.decode("utf-8"))
        except (ValueError, UnicodeError):
            repaired = raw[:cut]
            tail = last
        else:
            repaired = raw + b"\n"

    for number, line in enumerate(repaired.splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line.decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError("record is not an object")
            if not isinstance(value.get("page"), str) or not value["page"]:
                raise ValueError("invalid page")
            if type(value.get("staff")) is not int or value["staff"] < 1:
                raise ValueError("invalid staff")
        except (ValueError, UnicodeError) as exc:
            raise ValueError(
                f"refusing malformed log: {path}, line {number}: {exc}"
            ) from exc

    if repaired != raw:
        backup = path + ".before-repair-" + str(time.time_ns())
        shutil.copy2(path, backup)
        if tail is not None:
            Path(backup + ".tail").write_bytes(tail)
        with open(path, "wb") as handle:
            handle.write(repaired)
            handle.flush()
            os.fsync(handle.fileno())
        print(f"  log repaired; original preserved as {backup}")
    return []


def already_done(path, require_output=True):
    latest = read_latest(path)
    out = os.path.dirname(path)
    done = {
        key: record for key, record in latest.items()
        if retained_ok(record, out)
    }
    return done, len(latest) - len(done)


def main(argv=None):
    ap = argparse.ArgumentParser(description="recognise every detected staff")
    ap.add_argument("out_dir", help="the converted run, e.g. data/out-band")
    ap.add_argument("--out", default="data/out-perstaff")
    ap.add_argument("-j", "--jobs", type=int, default=4)
    ap.add_argument("--pad-frac", type=float, default=0.12)
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--min-staff-lines", type=int, default=5)
    ap.add_argument("--mask-graphics", action="store_true",
                    help="opt-in: erase pictured regions that do not touch a "
                         "staff before staff crops are made; the raw deskew "
                         "and every erased box are kept for audit")
    ap.add_argument("--pages", default="")
    ap.add_argument("--engine-id", required=True,
                    help="stable model/environment label; change it "
                         "when models or the engine environment change")
    ap.add_argument("--force", action="store_true",
                    help="re-run every staff, ignoring the JSONL")
    ap.add_argument("--skip-failed", action="store_true",
                    help="do NOT retry staff whose record says ok=false "
                         "(the old behaviour; leaves holes in the score)")
    args = ap.parse_args(argv)

    if args.jobs < 1:
        ap.error("-j/--jobs must be >= 1")
    if args.timeout <= 0:
        ap.error("--timeout must be positive")
    if not np.isfinite(args.pad_frac) or args.pad_frac < 0:
        ap.error("--pad-frac must be finite and non-negative")
    if args.min_staff_lines < 1:
        ap.error("--min-staff-lines must be >= 1")

    only = None
    if args.pages.strip():
        only = {"p%04d" % int(x) for x in args.pages.split(",") if x.strip()}

    os.makedirs(args.out, exist_ok=True)
    render_dir = os.path.join(args.out_dir, "render")
    manifest = os.path.join(args.out_dir, "manifest.json")
    jl = os.path.join(args.out, "per_staff.jsonl")
    verify_run_identity(args, only)
    repair_jsonl(jl)

    done, n_failed_hist = already_done(jl, require_output=True)
    if args.skip_failed:
        done.update(read_latest(jl))
    if n_failed_hist and not args.skip_failed:
        print(f"  {n_failed_hist} previously failed/incomplete staff "
              f"will be retried (use --skip-failed to keep them)")

    print("== analysing pages (once) ==")
    pages, skipped_pages = prep_pages(render_dir, manifest, args.out,
                                      min_staff_lines=args.min_staff_lines,
                                      pages=only,
                                      mask_graphics=args.mask_graphics)
    tasks = [(p["page"], i, args.out, args.pad_frac, args.timeout)
             for p in pages for i in range(len(p["staves"]))]
    todo = list(tasks) if args.force else [t for t in tasks
                                  if (t[0], t[1] + 1) not in done]
    if not tasks:
        raise ValueError("no staff tasks detected; inspect layout outputs")
    print(f"\n== {len(todo)} staff tasks to run; "
          f"{len(tasks) - len(todo)} not scheduled; "
          f"{len(tasks)} expected; {args.jobs} workers ==\n")

    t0 = time.time()
    n = 0
    with open(jl, "a") as sink, Pool(args.jobs) as pool:
        for rec in pool.imap_unordered(work, todo):
            sink.write(json.dumps(rec) + "\n")
            sink.flush()
            try:
                os.fsync(sink.fileno())
            except OSError:
                pass
            n += 1
            s = rec.get("stats") or {}
            flag = "" if rec.get("ok") else f"  <<< {rec.get('why', 'rc=' + str(rec.get('rc')))}"
            el = time.time() - t0
            eta = el / n * (len(todo) - n)
            print(f"  [{n:>3}/{len(todo)}] {rec['page']} s{rec['staff']:<2} "
                  f"rc={rec.get('rc')} parts={s.get('parts')} "
                  f"pitched={s.get('pitched_notes')} meas={s.get('measures')} "
                  f"notes={s.get('notes')}{flag}  "
                  f"[{el / 60:.0f}m elapsed, ~{eta / 60:.0f}m left]", flush=True)

    # Use the same retained-output rule as resume.
    latest = read_latest(jl)

    print("\n== summary ==")
    tot_ok = tot_notes = 0
    missing = []
    failed = []
    for p in pages:
        pg = p["page"]
        n_exp = len(p["staves"])
        ok = notes = 0
        bad = []
        for i in range(1, n_exp + 1):
            r = latest.get((pg, i))
            if r is None:
                missing.append((pg, i))
                continue
            if retained_ok(r, args.out):
                ok += 1
                kept_stats = xml_stats(os.path.join(args.out, r["kept"]))
                notes += kept_stats["note_elements"]
            else:
                bad.append(i)
                failed.append((pg, i, r.get("why") or "rc=%s" % r.get("rc")))
        tot_ok += ok
        tot_notes += notes
        line = f"  {pg}: {ok}/{n_exp} tasks with parseable output, {notes} notes"
        if bad:
            line += f"   FAILED: {bad}"
        print(line)
    print(f"\n  TOTAL: {tot_ok}/{len(tasks)} tasks completed with parseable "
          f"output, {tot_notes} notes, {(time.time() - t0) / 60:.1f} min")
    if missing:
        print(f"  MISSING (never produced a record): {len(missing)}  "
              f"{missing[:10]}")
    if failed:
        print(f"  FAILED: {len(failed)}  {failed[:10]}")
    if skipped_pages:
        print(f"  pages not analysed: {len(skipped_pages)}  {skipped_pages[:10]}")
    print("  NOTE: 'parseable output' is an execution status, not accuracy.")
    return 1 if (missing or failed) else 0


if __name__ == "__main__":
    sys.exit(main())
