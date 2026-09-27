"""Probe: does cropping systems recover staves homr skipped on a whole page?

Context. homr reads only the FIRST system of a multi-system page (p0020: 2 of 8
staves, confirmed by Wilbur). Two candidate causes:

  A) the segmenter inside homr gives up after the first system  -> cropping the
     page into systems should hand homr a page it can finish
  B) the pixels are degraded                                -> cropping will not
     help, and the image pipeline is at fault

This script tests both cheaply:
  1. is the renderer deterministic?            (a silent confound if not)
  2. extract the NATIVE embedded scan          (bypasses renderer + upsampling)
  3. run the layout detector on render vs native
  4. crop each detected system
  5. run homr on each crop AND on the native image; compare against the
     existing whole-page baseline

The verdict is printed, not inferred. A run that recovers no staves is a real
result and must be reported as such -- do not paper over it.

Usage:
  python scripts/crop_probe.py --render data/out-band/render/p0020.png \
      --baseline data/out-band/p0020.musicxml
  python scripts/crop_probe.py ... --no-homr     # geometry only, fast
"""

import argparse
import hashlib
import os
import sys
import xml.etree.ElementTree as ET

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "pysrc"))
from scorehalo.layout import analyze_page, crop_systems  # noqa: E402
from scorehalo.engine import run_homr  # noqa: E402

# No machine-specific path here. Point --pdf at your own scan, or set
# SCOREHALO_PDF; the default below is only a placeholder.
DEFAULT_PDF = os.environ.get("SCOREHALO_PDF", "")


def xml_stats(path):
    """parts / max-staves / measures / sounding-note count for a MusicXML file."""
    if not path or not os.path.exists(path):
        return None
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return None
    parts = root.findall("part")
    staves = 1
    for p in parts:
        for m in p.findall("measure"):
            a = m.find("attributes/staves")
            if a is not None and a.text:
                try:
                    staves = max(staves, int(a.text))
                except ValueError:
                    pass
    return {
        "parts": len(parts),
        "staves": staves,
        "measures": max((len(p.findall("measure")) for p in parts), default=0),
        "notes": sum(1 for n in root.iter("note") if n.find("chord") is None),
    }


def detect_and_report(tag, gray):
    r = analyze_page(gray)
    grp = "+".join(str(s["n_staves"]) for s in r["systems"]) or "-"
    print(f"  DETECT {tag:>10}: staves={r['n_staves']}  grouping={grp:<18} "
          f"space={r['staff_space']}px  skew={r['skew_deg']}deg")
    return r


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pdf", default=DEFAULT_PDF)
    ap.add_argument("--page", type=int, required=True, help="1-based PDF page")
    ap.add_argument("--render", required=True, help="the 300dpi render PNG")
    ap.add_argument("--baseline", default="", help="whole-page homr MusicXML")
    ap.add_argument("--out", default="/tmp/opencode/run")
    ap.add_argument("--no-homr", action="store_true")
    ap.add_argument("--per-staff", action="store_true",
                    help="crop each STAFF separately instead of each system")
    ap.add_argument("--pad-frac", type=float, default=0.06)
    ap.add_argument("--timeout", type=int, default=900)
    args = ap.parse_args(argv)

    import pypdfium2 as pdfium
    os.makedirs(args.out, exist_ok=True)
    idx = args.page - 1
    pdf = pdfium.PdfDocument(args.pdf)
    if not 0 <= idx < len(pdf):
        raise SystemExit(f"page {args.page} out of range (len={len(pdf)})")
    pg = pdf[idx]

    print(f"  page {args.page}  (0-based index {idx})")
    renders = [pg.render(scale=300 / 72).to_pil() for _ in range(2)]
    same = renders[0].tobytes() == renders[1].tobytes()
    print(f"  DETERMINISM: render is {'deterministic' if same else 'NON-DETERMINISTIC'}"
          + ("" if same else "  <-- confound, renderer is unstable"))
    del renders

    imgs = [x for x in pg.get_objects() if "Image" in type(x).__name__]
    native = None
    if imgs:
        o = imgs[0]
        try:
            native = o.extract().to_pil()
        except Exception as exc:
            print(f"  native extract().to_pil() failed ({exc}); trying get_bitmap()")
            try:
                native = o.get_bitmap().to_pil()
            except Exception as exc2:
                print(f"  native get_bitmap() also failed ({exc2}); continuing without it")
        if native is not None:
            native.convert("RGB").save(f"{args.out}/native.png")
            print(f"  NATIVE: {native.size}  filters={list(o.get_filters())}")
    else:
        print("  no embedded image object on this page (vector page?)")

    gray = np.array(Image.open(args.render).convert("L"))
    a = detect_and_report("render300", gray)
    if native is not None:
        b = detect_and_report("native200", np.array(native.convert("L")))
        if a["n_staves"] and b["n_staves"] and a["n_staves"] != b["n_staves"]:
            print("  !! detector disagrees between render and native -- "
                  "the detector is resolution-sensitive")
    target = a["n_staves"] or 1

    crops = crop_systems(a["deskewed"], a["systems"], pad_frac=args.pad_frac)
    if args.per_staff:
        # A 3-staff system still yields only 2 staves out of homr, so go one
        # level finer: isolate each individual staff.
        h, w = a["deskewed"].shape
        pv, ph = h * args.pad_frac * 0.5, w * args.pad_frac
        crops = []
        for i, s in enumerate(a["staves"]):
            t = int(max(0, s["y_top"] - pv))
            b = int(min(h, s["y_bot"] + pv))
            l = int(max(0, s["x0"] - ph))
            r = int(min(w, s["x1"] + ph))
            if b - t < 20 or r - l < 20:
                continue
            crops.append({"index": i, "n_staves": 1,
                          "image": a["deskewed"][t:b, l:r].copy()})
        print(f"  per-staff mode: {len(crops)} crops from {a['n_staves']} staves")
    for c in crops:
        p = f"{args.out}/sys{c['index'] + 1}.png"
        Image.fromarray(c["image"]).save(p)
        print(f"  wrote {p}  {c['image'].shape[1]}x{c['image'].shape[0]}"
              f"  ({c['n_staves']} staff)")
    if args.no_homr:
        return 0

    print("\n  running homr (slow)...", flush=True)
    res = {}
    fails = []

    def go(label, path):
        # run_homr returns (xml_path_or_None, log_path, returncode) -- note the
        # docstring in engine.py still claims it returns a bare path.
        xml, log, rc = run_homr(path, f"{args.out}/homr", timeout=args.timeout)
        st = xml_stats(xml)
        print(f"    {label:>24}: rc={rc} {st}", flush=True)
        if rc != 0 or st is None:
            tail = ""
            if log and os.path.exists(log):
                with open(log, errors="replace") as fh:
                    tail = "".join(fh.readlines()[-8:]).strip()
            fails.append((label, rc, tail))
        res[label] = st
        return st

    if args.baseline:
        res["BASELINE whole page"] = xml_stats(args.baseline)
        print(f"    {'BASELINE whole page':>24}: {res['BASELINE whole page']}")
    for c in crops:
        go(f"crop sys{c['index'] + 1} ({c['n_staves']}st)",
           f"{args.out}/sys{c['index'] + 1}.png")
    if native is not None:
        go("native 200dpi", f"{args.out}/native.png")

    if fails:
        print("\n  FAILURES (last lines of homr log):")
        for label, rc, tail in fails:
            print(f"    --- {label} rc={rc}")
            for line in tail.splitlines():
                print(f"        {line}")

    print()
    got = sum((v or {}).get("staves", 0) for k, v in res.items() if k.startswith("crop"))
    print(f"\n  VERDICT: cropping recovered {got} of {target} staves")
    if got >= target:
        print("    -> SEGMENTATION-WAS-WHOLE-PAGE-ONLY : scale this to every page")
    elif got > (res.get("BASELINE whole page") or {}).get("staves", 0):
        print("    -> PARTIAL : cropping helps but is insufficient")
    else:
        print("    -> NO RECOVERY : cropping is the wrong lever; the segmenter "
              "inside homr is the wall. Report this, do not work around it.")
    print(f"\n  look at {args.out}/sys2.png and {args.out}/sys3.png (the six staves)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
