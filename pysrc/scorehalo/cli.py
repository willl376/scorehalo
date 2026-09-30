"""scorehalo CLI: convert (PDF/images -> MusicXML) and serve (review UI)."""

import argparse
import os
import shutil
import sys
import json
import glob

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scorehalo import __version__, pdf, engine, validate, preprocess, repair


def parse_pages(spec, count):
    """Parse '1-5,8,20-24'. Returns sorted unique 1-based list within range."""
    out = set()
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            a, b, *_ = chunk.split("-")
            a, b = int(a), int(b)
            out.update(range(a, b + 1))
        else:
            out.add(int(chunk))
    out = {n for n in out if 1 <= n <= count}
    return sorted(out)


def resolve_path(p):
    """Absolute path for user-supplied input, with ~ expanded.

    os.path.abspath() alone is WRONG here: a leading '~' is not absolute, so
    abspath treats it as relative and joins it onto the current directory,
    yielding '/home/someone/~/Documents/book.pdf'. It then fails with a
    FileNotFoundError pointing at a path that never existed. expanduser
    first, then abspath.
    """
    return os.path.abspath(os.path.expanduser(p))


def suggest_path(path):
    """Human-readable guesses when a file isn't found.

    Two ways this goes wrong on Linux, both worth naming:
      * wrong case on the FILE  ('carpenters X.pdf' vs 'Carpenters X.pdf')
      * wrong case on the DIRECTORY, which is nastier because '~/documents'
        and '~/Documents' can BOTH exist, so the typo still looks plausible.
    """
    raw = os.path.expanduser(path)
    d, base = os.path.split(raw)
    fold = lambda s: s.casefold()                      # noqa: E731
    notes = []

    # 1. right directory, wrong-case filename
    if os.path.isdir(d):
        try:
            entries = os.listdir(d)
        except OSError:
            entries = []
        for h in [e for e in entries if fold(e) == fold(base)][:3]:
            notes.append(f"  found instead: {os.path.join(d, h)}")
        if notes:
            return "\n".join(notes)

    # 2. wrong-case directory, but ONLY when the file really is over there.
    # Reporting a bare "case difference?" for a file that is simply absent is
    # a false hint: on a machine where both ~/Documents and ~/documents
    # exist, it fires on every missing file under the correct one.
    parent = os.path.dirname(d.rstrip("/")) or "/"
    try:
        sibs = os.listdir(parent)
    except OSError:
        sibs = []
    here = os.path.basename(d.rstrip("/"))
    for s in sibs:
        if fold(s) == fold(here) and s != here:
            cand = os.path.join(parent, s, base)
            if os.path.exists(cand):
                notes.append(f"  found instead: {cand}")
            break
    return "\n".join(notes)


def require_input_file(path):
    """Return the resolved path, or exit with a message a human can act on."""
    src = resolve_path(path)
    if os.path.exists(src):
        return src
    print(f"scorehalo: no such file: {src}", file=sys.stderr)
    hint = suggest_path(path)
    if hint:
        print(hint, file=sys.stderr)
    raise SystemExit(2)


def cmd_convert(args):
    args.out = resolve_path(args.out)
    src = require_input_file(args.input)
    if src.lower().endswith(".pdf"):
        total = pdf.page_count(src)
        pages = parse_pages(args.pages, total)
        print(f"{src}: {total} pages, converting {len(pages)}: {pages[:8]}{'...' if len(pages) > 8 else ''}")
        out_dir = args.out
        render_dir = os.path.join(out_dir, "render")
        work_dir = os.path.join(out_dir, "homr")
        os.makedirs(out_dir, exist_ok=True)
        rendered = pdf.render_range(src, pages, render_dir, dpi=args.dpi)
        items = [(n, path) for n, path, _ in rendered]
    else:
        # single image input
        items = [(1, src)]
        out_dir = args.out
        render_dir = os.path.join(out_dir, "render")
        work_dir = os.path.join(out_dir, "homr")
        os.makedirs(out_dir, exist_ok=True)

    manifest = {"version": __version__, "source": src, "dpi": args.dpi, "pages": [], "ok": True}
    manifest_path = os.path.join(out_dir, "manifest.json")
    if os.path.exists(manifest_path):
        with open(manifest_path) as fh:
            manifest = json.load(fh)
        done = {p["page"] for p in manifest["pages"]}
        items = [(n, img) for n, img in items if n not in done]
        if items:
            print(f"resuming: {len(done)} pages already done, {len(items)} remaining")

    for n, img in items:
        entry = {"page": n, "image": img, "status": "pending"}
        print(f"[p{n}] load {os.path.basename(img)}")
        music_ok, stats_note, _verdict = preprocess.is_music_page(img)
        entry["stats"] = stats_note if isinstance(stats_note, dict) else {"note": stats_note}
        if not music_ok:
            entry["status"] = "skipped-not-music"
            print(f"[p{n}] {stats_note}")
            manifest["pages"].append(entry)
            continue

        # optional enhancement pass produces the actual engine input
        engine_img = img
        if args.enhance:
            enh_path = os.path.join(out_dir, "enhanced", f"p{n:04d}.png")
            preprocess.enhance(img, enh_path)
            engine_img = enh_path
            entry["enhanced"] = enh_path

        print(f"[p{n}] running homr (cpu)...")
        xml_path, log_path, rc = engine.run_homr(engine_img, work_dir, timeout=args.timeout)
        if xml_path is None:
            entry["status"] = f"homr-failed(rc={rc})"
            entry["log"] = log_path
            print(f"[p{n}] homr failed rc={rc}; log={os.path.basename(log_path)}")
            manifest["pages"].append(entry)
            continue

        out_xml = os.path.join(out_dir, f"p{n:04d}.musicxml")
        shutil.move(xml_path, out_xml)
        tuplets = repair.repair_time_modifications(out_xml)
        ok, rep = validate.validate(out_xml)
        entry.update({"status": "ok" if ok else "validate-warn", "validation": rep,
                      "output": out_xml, "tuplets_repaired": tuplets})
        print(f"[p{n}] OK: {rep.get('parts')} part(s), {rep.get('measures')} measure(s), {rep.get('notes')} notes"
              + (f", {tuplets} tuplet(s) typed" if tuplets else "")
              + ("" if ok else f" WARN: {rep.get('errors')}"))
        if not ok:
            entry["status"] = "validate-warn"
        manifest["pages"].append(entry)

    manifest["ok"] = all(p.get("status") == "ok" or p.get("status") == "skipped-not-music" for p in manifest["pages"])
    man_path = os.path.join(out_dir, "manifest.json")
    with open(man_path, "w") as fh:
        json.dump(manifest, fh, indent=2)
    print(f"\nmanifest: {man_path}")
    ok_count = sum(1 for p in manifest["pages"] if p.get("status") == "ok")
    print(f"pages ok: {ok_count}/{len(manifest['pages'])}; "
          f"validate --out-dir {out_dir} --all to eyeball")
    if ok_count >= 1:
        try:
            _do_join(args.out)
        except Exception as e:
            # Do not fail quietly here. The joiner merges per-STAFF
            # fragments, so it loses everything that lives between staves and
            # its output is rejected by LilyPond's musicxml2ly on real pages
            # (TypeError in group_tuplets). The supported path is the
            # system-level graph: scripts/per_system_all.py then
            # scripts/build_page_graph.py.
            print(f"\njoin: FAILED ({type(e).__name__}: {e})")
            print("join is DEPRECATED -- it merges per-staff fragments and its "
                  "output does not survive musicxml2ly. Use the system-level "
                  "path instead:  scripts/per_system_all.py <data> --out "
                  "data/out-system  &&  scripts/build_page_graph.py "
                  "data/out-system --stem all --report")
    return 0


def _pdf_page_size_pts(src):
    """Return (width, height) of the first page, in points (1/72in)."""
    try:
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(src)
        pg = pdf[0]
        w, h = pg.get_size()
        pdf.close()
        # pypdfium2 returns raw page geometry in points
        return (round(w, 2), round(h, 2))
    except Exception:
        return (None, None)


def _do_join(out_dir, allow_partial=False):
    from scorehalo import join
    out_dir = resolve_path(out_dir)
    man_path = os.path.join(out_dir, "manifest.json")
    if not os.path.exists(man_path):
        print("no manifest.json; run convert first")
        return None
    with open(man_path) as fh:
        manifest = json.load(fh)
    candidates = [p for p in manifest["pages"] if p.get("output")]
    good = [p for p in candidates if p.get("status") == "ok"]
    dropped = [p for p in candidates if p.get("status") != "ok"]

    # Never silently emit a partial score. A page can fail validation for a
    # cosmetic reason (an unbalanced slur) and dropping it can discard most of
    # the book -- so refuse, and say exactly what would be lost.
    if dropped and not allow_partial:
        lost = sum(p.get("validation", {}).get("notes", 0) or 0 for p in dropped)
        kept = sum(p.get("validation", {}).get("notes", 0) or 0 for p in good)
        print(f"refusing to join: {len(dropped)} of {len(candidates)} pages did "
              f"not validate, which would silently drop {lost} notes "
              f"(keeping only {kept})", file=sys.stderr)
        for p in dropped:
            errs = p.get("validation", {}).get("errors") or ["(no detail)"]
            print(f"  p{p['page']:04d}: {'; '.join(errs)}", file=sys.stderr)
        print("  fix the pages, or re-run with --allow-partial to join the "
              "valid ones anyway", file=sys.stderr)
        return None

    xmls = [p["output"] for p in good]
    if not xmls:
        print("no successfully transcribed pages to join")
        return None
    if dropped:
        lost = sum(p.get("validation", {}).get("notes", 0) or 0 for p in dropped)
        print(f"WARNING: joining {len(xmls)} of {len(candidates)} pages; "
              f"{lost} notes from {len(dropped)} page(s) are NOT in the output",
              file=sys.stderr)
    out_xml = os.path.join(out_dir, "score.musicxml")
    out_mxl = os.path.join(out_dir, "score.mxl")
    join.join_pages(xmls, out_xml, out_mxl)
    print(f"joined {len(xmls)} pages -> {out_xml} (+ {out_mxl})")
    return out_xml


def cmd_join(args):
    return 0 if _do_join(args.out_dir, getattr(args, "allow_partial", False)) else 1


def cmd_validate(args):
    total = ok = fail = 0
    for xml in sorted(glob.glob(os.path.join(args.out_dir, "*.musicxml"))):
        total += 1
        fine, rep = validate.validate(xml)
        if fine:
            ok += 1
        else:
            fail += 1
            print(f"{os.path.basename(xml)}: " + "; ".join(rep["errors"]))
    print(f"{ok}/{total} valid MusicXML outputs")
    return 0 if fail == 0 else 1


def cmd_audit(args):
    from scorehalo.audit import audit_dir, format_audit

    result = audit_dir(args.out_dir, top=args.top)
    print(format_audit(result))
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(result, fh, indent=2)
        print(f"\nwrote {args.json}")
    return 0


def cmd_score(args):
    from scorehalo.score import format_report, score_files

    per_stream, totals = score_files(args.truth, args.pred)
    print(format_report(args.truth, args.pred, per_stream, totals))
    return 0 if totals["missed"] == 0 and totals["both_wrong"] == 0 else 1


def cmd_hear(args):
    from scorehalo.hear import audition

    audition(
        args.score,
        work_dir=args.out,
        musescore_bin=args.musescore,
        soundfont=args.soundfont,
        player=args.player,
        keep=args.keep,
        play_it=not args.no_play,
        engraver=args.engraver,
    )
    return 0


def cmd_pitch(args):
    from scorehalo.pitch import main as pitch_main

    argv = []
    if args.wav:
        argv.append(args.wav)
    if args.record:
        argv.append("--record")
    argv += ["--secs", str(args.secs)]
    if args.target:
        argv += ["--target", args.target]
    if args.fmin:
        argv += ["--fmin", str(args.fmin)]
    if args.fmax:
        argv += ["--fmax", str(args.fmax)]
    if args.selftest:
        argv.append("--selftest")
    return pitch_main(argv)


def cmd_serve(args):
    from scorehalo.ui import serve_ui
    return serve_ui(args.out_dir, args.port)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="scorehalo", description="Photo/graphics-tolerant OMR to MusicXML")
    sub = ap.add_subparsers(dest="cmd", required=True)

    conv = sub.add_parser("convert", help="PDF/image -> per-page MusicXML")
    conv.add_argument("input")
    conv.add_argument("--out", "-o", default="scorehalo-out")
    conv.add_argument("--pages", default="1-128", help="page spec e.g. 3-8,16")
    conv.add_argument("--dpi", type=int, default=300)
    conv.add_argument("--enhance", action="store_true", help="contrast+sharpen before OMR")
    conv.add_argument("--timeout", type=int, default=600)
    conv.set_defaults(fn=cmd_convert)

    val = sub.add_parser("validate", help="re-check MusicXML outputs in an out dir")
    val.add_argument("out_dir")
    val.add_argument("--all", action="store_true", help="also validate skipped/failed entries files")
    val.set_defaults(fn=cmd_validate)

    join_cmd = sub.add_parser("join", help="assemble per-page MusicXML into one score + .mxl")
    join_cmd.add_argument("out_dir")
    join_cmd.add_argument("--allow-partial", action="store_true",
                          help="join only the pages that validated, even if "
                               "that silently omits notes (warns loudly)")
    join_cmd.set_defaults(fn=cmd_join)

    from scorehalo.compare import cmd_compare

    cmp_cmd = sub.add_parser(
        "compare",
        help="render score.mxl with MuseScore and diff vs source pages",
    )
    cmp_cmd.add_argument("out_dir")
    cmp_cmd.add_argument("--render-dir", help="where to keep rendered pages (default <out>/compare)")
    cmp_cmd.add_argument("--musescore", help="path to MuseScore binary (auto-detected)")
    cmp_cmd.set_defaults(fn=cmd_compare)

    au = sub.add_parser("audit", help="label-free structural audit of an out dir")
    au.add_argument("out_dir")
    au.add_argument("--top", type=int, default=25, help="how many suspects to list")
    au.add_argument("--json", help="write the full report here")
    au.set_defaults(fn=cmd_audit)

    sc = sub.add_parser("score", help="note-level diff of a prediction against a reference MusicXML")
    sc.add_argument("--truth", required=True, help="reference MusicXML (labels)")
    sc.add_argument("--pred", required=True, help="MusicXML to grade")
    sc.set_defaults(fn=cmd_score)

    hear = sub.add_parser(
        "hear", help="audition a score as audio (MusicXML -> MIDI -> WAV -> TV/speakers)"
    )
    hear.add_argument("score", help=".musicxml or .mxl to play")
    hear.add_argument("--musescore", help="path to MuseScore binary (auto-detected)")
    hear.add_argument("--soundfont", help="path to a General MIDI soundfont")
    hear.add_argument("--player", default="pw-play", help="playback command")
    hear.add_argument("--no-play", action="store_true", help="render only, no playback")
    hear.add_argument("--keep", action="store_true", help="keep the temp dir")
    hear.add_argument("--out", help="directory for the .mid/.wav (default: temp)")
    hear.add_argument(
        "--engraver",
        choices=("auto", "musescore", "lilypond"),
        default="auto",
        help="importer for the MIDI step: auto = MuseScore, fall back to LilyPond",
    )
    hear.set_defaults(fn=cmd_hear)

    pt = sub.add_parser(
        "pitch", help="monophonic pitch tracking: hum/whistle a melody -> note sequence"
    )
    pt.add_argument("wav", nargs="?", help="WAV to analyze (or use --record)")
    pt.add_argument("--record", action="store_true", help="record from the mic first")
    pt.add_argument("--secs", type=float, default=8.0, help="recording seconds")
    pt.add_argument("--target", help="PipeWire source id (default: system default)")
    pt.add_argument("--fmin", type=float, help="lowest detectable Hz (default 65)")
    pt.add_argument("--fmax", type=float, help="highest detectable Hz (default 1400)")
    pt.add_argument("--selftest", action="store_true", help="verify on synthetic tones")
    pt.set_defaults(fn=cmd_pitch)

    srv = sub.add_parser("serve", help="start the review UI")
    srv.add_argument("out_dir", nargs="?", default="scorehalo-out")
    srv.add_argument("--port", type=int, default=8001)
    srv.set_defaults(fn=cmd_serve)

    from scorehalo.toly_cmd import add_parser as add_toly
    add_toly(sub)

    from scorehalo.canon import add_parser as add_canon
    add_canon(sub)

    ap.add_argument("--version", action="version", version=f"scorehalo {__version__}")
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())