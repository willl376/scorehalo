"""Check detected staff geometry and report every selected page.

Zero-staff pages and page errors remain in the report. Non-finite values
are flagged and exported as JSON null. Cache keys include the resolved
source path, source-content hash, page, DPI, and cache-format version.

JSON output is an object containing metadata and a rows list.
Reports are written through a temporary file before replacement.

Exit status:
  0: completed without page errors; geometry flags are report-only by default.
  1: page errors, or --fail-on-flags finds flags or no selected pages.
  2: handled input, argument, or report-writing errors.

Tidy geometry is not proof that every musical staff was found correctly.
"""

import argparse
import numbers
import tempfile
from pathlib import Path
import hashlib
import json
import math
import os
import sys

import numpy as np
from PIL import Image

ROOT = next(
    p for p in Path(__file__).resolve().parents
    if (p / "pysrc/scorehalo").is_dir()
    and (p / "scripts/make_fixture.py").is_file()
)
sys.path.insert(0, str(ROOT / "pysrc"))
from scorehalo.layout import analyze_page  # noqa: E402

CACHE_VERSION = 3


def cache_path(cache_dir, man, page, name, dpi):
    src = Path(man["source"]).resolve(strict=True)
    content = hashlib.sha256()
    with src.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            content.update(block)
    identity = json.dumps(
        [str(src), content.hexdigest(), page, dpi, CACHE_VERSION],
        separators=(",", ":"),
    )
    tag = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return os.path.join(cache_dir, tag + ".png")


def load_gray(man, page, name, cache_dir, dpi):
    if type(page) is not int or page < 1:
        raise ValueError("page must be a positive 1-based integer")
    if type(dpi) is not int or dpi < 1:
        raise ValueError("dpi must be a positive integer")

    cp = cache_path(cache_dir, man, page, name, dpi) if cache_dir else None
    if cp and os.path.isfile(cp):
        try:
            with Image.open(cp) as image:
                return np.array(image.convert("L"))
        except OSError:
            pass

    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(man["source"])
    try:
        if page > len(doc):
            raise ValueError(
                f"page {page} out of range (document has {len(doc)})"
            )
        pdf_page = doc[page - 1]
        try:
            bitmap = pdf_page.render(scale=dpi / 72)
            try:
                image = bitmap.to_pil()
                try:
                    gray = np.array(image.convert("L"))
                finally:
                    image.close()
            finally:
                bitmap.close()
        finally:
            pdf_page.close()
    finally:
        doc.close()

    if cp:
        directory = os.path.dirname(cp)
        os.makedirs(directory, exist_ok=True)
        fd, temporary = tempfile.mkstemp(
            prefix=".render-", suffix=".png", dir=directory
        )
        os.close(fd)
        try:
            Image.fromarray(gray).save(temporary, format="PNG")
            os.replace(temporary, cp)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return gray


def finite_positive(value):
    return (
        isinstance(value, numbers.Real)
        and not isinstance(value, (bool, np.bool_))
        and math.isfinite(value)
        and value > 0
    )



def nonfinite_paths(value, path="row"):
    if isinstance(value, np.ndarray):
        value = value.tolist()
    elif isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float):
        return [path] if not math.isfinite(value) else []
    if isinstance(value, dict):
        return [
            found
            for key, item in value.items()
            for found in nonfinite_paths(item, f"{path}.{key}")
        ]
    if isinstance(value, (list, tuple)):
        return [
            found
            for index, item in enumerate(value)
            for found in nonfinite_paths(item, f"{path}[{index}]")
        ]
    return []


def json_safe(value):
    if isinstance(value, np.ndarray):
        return json_safe(value.tolist())
    if isinstance(value, np.generic):
        return json_safe(value.item())
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if value is None or isinstance(value, (str, bool, int)):
        return value
    raise TypeError(f"unsupported report value: {type(value).__name__}")


def write_report(path, payload):
    encoded = json.dumps(
        json_safe(payload), indent=2, allow_nan=False
    )
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=".layout-report-", dir=str(target.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("out_dir")
    ap.add_argument("--json", default="")
    ap.add_argument("--resid-tol", type=float, default=4.0,
                    help="maximum accepted detector resid value, in pixels")
    ap.add_argument("--width-frac", type=float, default=0.45,
                    help="min staff width as a fraction of page width")
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--cache", default="/tmp/opencode/pagecache")
    ap.add_argument("--fail-on-flags", action="store_true",
                    help="exit 1 when any staff is flagged (default: report only)")
    args = ap.parse_args(argv)

    if not math.isfinite(args.resid_tol) or args.resid_tol < 0:
        ap.error("--resid-tol must be finite and non-negative")
    if not math.isfinite(args.width_frac) or not 0 < args.width_frac <= 1:
        ap.error("--width-frac must be > 0 and <= 1")
    if args.dpi <= 0:
        ap.error("--dpi must be positive")

    man_path = os.path.join(args.out_dir, "manifest.json")
    try:
        with open(man_path, encoding="utf-8") as fh:
            man = json.load(fh)
        if not isinstance(man, dict) or not isinstance(man.get("pages"), list):
            raise ValueError("manifest must contain a pages list")
        for item in man["pages"]:
            if not isinstance(item, dict):
                raise ValueError("manifest page entries must be objects")
            if not isinstance(item.get("status", "?"), str):
                raise ValueError("page status must be text")
            if item.get("status") == "ok":
                if not isinstance(item.get("image"), str) or not item["image"]:
                    raise ValueError("selected page requires an image name")
                if type(item.get("page")) is not int or item["page"] < 1:
                    raise ValueError("selected page requires a positive page number")
    except (OSError, ValueError) as exc:
        print(f"cannot read {man_path}: {exc}", file=sys.stderr)
        return 2

    if args.cache:
        os.makedirs(args.cache, exist_ok=True)

    status_count = {}
    for p in man.get("pages", []):
        status_count[p.get("status", "?")] = status_count.get(p.get("status", "?"), 0) + 1
    recs = [p for p in man["pages"] if p.get("status") == "ok"]

    print(f"  manifest pages      : {len(man.get('pages', []))}")
    print(f"  selected (status ok): {len(recs)}")
    for k, v in sorted(status_count.items()):
        print(f"    status {k!r:<12}: {v}")
    print()
    print(f"  {'page':>6} {'staves':>6} {'sys':>4} {'space':>6} {'mean':>6} "
          f"{'sliv':>5} {'worstR':>7} {'minW':>5}  flags")

    rows = []
    n_error = 0
    for p in recs:
        name = os.path.splitext(os.path.basename(p["image"]))[0]
        row = {"page": name, "pdf_page": p.get("page"),
               "n_staves": 0, "n_systems": 0, "grouping": "",
               "flags": [], "staves": [], "error": None}
        try:
            gray = load_gray(man, p.get("page"), name, args.cache, args.dpi)
            w = gray.shape[1]
            r = analyze_page(gray, min_staff_lines=5)
            staves = r["staves"]
            row.update(n_staves=len(staves),
                       n_systems=len(r["systems"]),
                       grouping="+".join(str(s["n_staves"]) for s in r["systems"]),
                       global_space=float(r["staff_space"]),
                       n_slivers_removed=int(r["n_slivers_removed"]),
                       staves=[{k: (float(v) if isinstance(v, (int, float)) else v)
                                for k, v in s.items()} for s in staves])

            if not staves:
                row["flags"].append({"staff": None, "why": ["no_staves"]})
                print(f"  {name:>6} {0:>6} {0:>4}  {'-':>6}  {'-':>6}  {'-':>5}  "
                      f"{'-':>7}  {'-':>5}  NO STAVES")
                rows.append(row)
                continue

            mean_sp = float(np.mean([s["spacing"] for s in staves]))
            row["mean_space"] = mean_sp
            for i, s in enumerate(staves):
                why = []
                for key in ("resid", "spacing", "width"):
                    if not finite_positive(s.get(key)) and not (
                            key == "resid" and s.get(key) == 0):
                        why.append(f"{key}=invalid({s.get(key)!r})")
                if s["n_lines"] != 5:
                    why.append(f"lines={s['n_lines']}")
                if finite_positive(s.get("resid")) and s["resid"] > args.resid_tol:
                    why.append(f"resid={s['resid']:.1f}")
                if finite_positive(s.get("width")) and s["width"] < args.width_frac * w:
                    why.append(f"narrow={s['width']:.0f}")
                if (finite_positive(s.get("spacing")) and finite_positive(r["staff_space"])
                        and abs(s["spacing"] - r["staff_space"]) > 0.25 * r["staff_space"]):
                    why.append(f"sp={s['spacing']:.1f}!={r['staff_space']:.1f}")
                if why:
                    row["flags"].append({"staff": i + 1, "why": why})

            row["worst_resid"] = float(max(s["resid"] for s in staves))
            row["min_width"] = float(min(s["width"] for s in staves))
            print(f"  {name:>6} {len(staves):>6} {len(r['systems']):>4} "
                  f"{r['staff_space']:>6.1f} {mean_sp:>6.1f} "
                  f"{r['n_slivers_removed']:>5} {row['worst_resid']:>7.1f} "
                  f"{row['min_width']:>5.0f}  {len(row['flags'])}")
            for f in row["flags"]:
                print(f"        staff {f['staff']}: {', '.join(f['why'])}")
        except Exception as exc:                     # one bad page != dead run
            n_error += 1
            row["error"] = f"{type(exc).__name__}: {exc}"
            row["flags"].append({"staff": None, "why": [f"error: {row['error']}"]})
            print(f"  {name:>6}  ERROR {row['error']}")
        rows.append(row)

    for row in rows:
        problems = nonfinite_paths(row)
        if not finite_positive(row.get("global_space")):
            row["flags"].append({
                "staff": None,
                "why": ["global staff spacing is missing or invalid"],
            })
        if problems:
            row["flags"].append({
                "staff": None,
                "why": ["non-finite measurement: " + p for p in problems],
            })
    clean = [r for r in rows if not r["flags"]]
    flagged = [r for r in rows if r["flags"] and not r["error"]]
    print()
    print(f"  pages examined                : {len(rows)}")
    print(f"  pages with no GEOMETRY flags  : {len(clean)}")
    print(f"  pages with flagged staves     : {len(flagged)}")
    print(f"  pages that raised an error    : {n_error}")
    if clean:
        sp = [r["global_space"] for r in clean if "global_space" in r]
        if sp:
            print(f"  staff_space on clean pages    : {min(sp):.1f} .. {max(sp):.1f} px")
    print("  NOTE: tidy geometry is not proof of correctness -- a missed staff "
          "or a non-musical 5-line rule can still pass.")

    if args.json:
        try:
            write_report(args.json, {
                "manifest_pages": len(man["pages"]),
                "selected": len(recs),
                "status_counts": status_count,
                "rows": rows,
                "nonfinite_values_exported_as": "null; see row flags",
            })
        except (OSError, TypeError, ValueError) as exc:
            print(f"cannot write report: {exc}", file=sys.stderr)
            return 2
        print(f"  wrote {args.json}")

    if n_error:
        return 1
    if args.fail_on_flags and (
            not rows or flagged or any(r["n_staves"] == 0 for r in rows)):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
