"""scorehalo CLI: convert (PDF/images -> MusicXML) and serve (review UI)."""

import argparse
import os
import shutil
import sys
import json
import glob

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scorehalo import __version__, pdf, engine, validate, preprocess


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


def cmd_convert(args):
    args.out = os.path.abspath(args.out)
    src = os.path.abspath(args.input)
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

    manifest = {"version": __version__, "source": src, "pages": [], "ok": True}
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
        ok, rep = validate.validate(out_xml)
        entry.update({"status": "ok" if ok else "validate-warn", "validation": rep, "output": out_xml})
        print(f"[p{n}] OK: {rep.get('parts')} part(s), {rep.get('measures')} measure(s), {rep.get('notes')} notes"
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
            print(f"join skipped: {e}")
    return 0


def _do_join(out_dir):
    from scorehalo import join
    out_dir = os.path.abspath(out_dir)
    man_path = os.path.join(out_dir, "manifest.json")
    if not os.path.exists(man_path):
        print("no manifest.json; run convert first")
        return None
    with open(man_path) as fh:
        manifest = json.load(fh)
    xmls = [p["output"] for p in manifest["pages"] if p.get("status") == "ok" and p.get("output")]
    if not xmls:
        print("no successfully transcribed pages to join")
        return None
    out_xml = os.path.join(out_dir, "score.musicxml")
    out_mxl = os.path.join(out_dir, "score.mxl")
    join.join_pages(xmls, out_xml, out_mxl)
    print(f"joined {len(xmls)} pages -> {out_xml} (+ {out_mxl})")
    return out_xml


def cmd_join(args):
    return 0 if _do_join(args.out_dir) else 1


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
    join_cmd.set_defaults(fn=cmd_join)

    srv = sub.add_parser("serve", help="start the review UI")
    srv.add_argument("out_dir", nargs="?", default="scorehalo-out")
    srv.add_argument("--port", type=int, default=8001)
    srv.set_defaults(fn=cmd_serve)

    ap.add_argument("--version", action="version", version=f"scorehalo {__version__}")
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())