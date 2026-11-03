"""Compare crop recognition attempts using structural output statistics.

XML counts are not counts of physical staff regions recovered.
Every invocation uses a fresh run directory beneath --out.
An embedded image sample is not assumed to represent the complete page.

Exit codes: 0 completed; 1 failed attempts or no usable crops;
2 input, argument, or report-writing errors.
Completion does not establish transcription accuracy.
"""

import argparse
import json
import math
import os
from pathlib import Path
import sys
import tempfile

ROOT = next(
    p for p in Path(__file__).resolve().parents
    if (p / "pysrc/scorehalo").is_dir()
    and (p / "scripts/make_fixture.py").is_file()
)
sys.path.insert(0, str(ROOT / "pysrc"))
from reporting_stats import xml_stats


def clean_json(value):
    if isinstance(value, dict):
        return {str(k): clean_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean_json(v) for v in value]
    if hasattr(value, "item"):
        return clean_json(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def write_report(path, report):
    payload = json.dumps(clean_json(report), indent=2, allow_nan=False) + "\n"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=path.name + ".", suffix=".tmp", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def geometry_summary(result):
    return clean_json({
        "detected_staves": int(result["n_staves"]),
        "detected_systems": int(result["n_systems"]),
        "grouping": [int(s["n_staves"]) for s in result["systems"]],
        "staff_space_px": result["staff_space"],
        "skew_deg": result["skew_deg"],
    })


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pdf", default=os.environ.get("SCOREHALO_PDF", ""))
    ap.add_argument("--page", type=int, required=True)
    ap.add_argument("--render", required=True)
    ap.add_argument("--baseline", default="")
    ap.add_argument("--out", default="/tmp/opencode/run")
    ap.add_argument("--json", default="")
    ap.add_argument("--no-homr", action="store_true")
    ap.add_argument("--per-staff", action="store_true")
    ap.add_argument("--pad-frac", type=float, default=0.06)
    ap.add_argument("--timeout", type=int, default=900)
    args = ap.parse_args(argv)
    if args.page < 1:
        ap.error("--page must be positive")
    if args.timeout <= 0:
        ap.error("--timeout must be positive")
    if not math.isfinite(args.pad_frac) or not 0 <= args.pad_frac <= 1:
        ap.error("--pad-frac must be finite and between 0 and 1")
    if not args.pdf or not Path(args.pdf).is_file():
        ap.error("--pdf must name an existing PDF")
    if not Path(args.render).is_file():
        ap.error("--render must name an existing image")

    try:
        parent = Path(args.out).resolve()
        parent.mkdir(parents=True, exist_ok=True)
        run = Path(tempfile.mkdtemp(prefix="crop-probe-", dir=parent))
        report_path = (
            Path(args.json).resolve() if args.json
            else run / "report.json"
        )
        if report_path.exists():
            raise ValueError(f"refusing to replace existing report: {report_path}")
        if not report_path.parent.is_dir():
            raise ValueError("report parent directory does not exist")
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    report = {
        "metadata": {
            "report_format_version": 1,
            "run_directory": str(run),
            "source_pdf": str(Path(args.pdf).resolve()),
            "source_page": args.page,
            "render": str(Path(args.render).resolve()),
            "render_check_dpi": 300,
            "mode": "per-staff" if args.per_staff else "per-system",
            "recognition_requested": not args.no_homr,
            "limitations": [
                "Detected geometry is not ground truth.",
                "XML counts do not establish transcription accuracy.",
                "XML status ok is not full MusicXML validation.",
                "part_measures sums measures across parts.",
                "Embedded-image sampling may not represent the page.",
                "The supplied render is not proven to match the PDF page.",
            ],
        },
        "baseline": (
            {
                "path": str(Path(args.baseline).resolve()),
                "xml": xml_stats(args.baseline),
            } if args.baseline else {"status": "not_requested"}
        ),
        "geometry": {},
        "crops": [],
        "attempts": [],
        "warnings": [],
        "errors": [],
    }
    rc = 0
    pdf = page = None
    try:
        import numpy as np
        from PIL import Image
        import pypdfium2 as pdfium
        from scorehalo.layout import analyze_page, crop_systems
        from scorehalo.engine import run_homr

        pdf = pdfium.PdfDocument(args.pdf)
        if not 1 <= args.page <= len(pdf):
            raise ValueError(f"page {args.page} out of range")
        page = pdf[args.page - 1]
        fingerprints = []
        for _ in range(2):
            bitmap = page.render(scale=300 / 72)
            try:
                image = bitmap.to_pil()
                try:
                    fingerprints.append(
                        (image.mode, image.size, image.tobytes())
                    )
                finally:
                    image.close()
            finally:
                bitmap.close()
        report["render_deterministic"] = (
            fingerprints[0] == fingerprints[1]
        )
        del fingerprints
        if not report["render_deterministic"]:
            report["warnings"].append("Repeated PDF renders differ")

        with Image.open(args.render) as image:
            gray = np.array(image.convert("L"))
        result = analyze_page(gray)
        report["geometry"]["render"] = geometry_summary(result)

        embedded_path = None
        try:
            image_object = next(
                (
                    obj for obj in page.get_objects()
                    if isinstance(obj, pdfium.PdfImage)
                ),
                None,
            )
            if image_object is not None:
                bitmap = image_object.get_bitmap()
                try:
                    image = bitmap.to_pil()
                    try:
                        embedded_path = run / "embedded-sample.png"
                        image.convert("RGB").save(embedded_path)
                        embedded_gray = np.array(image.convert("L"))
                    finally:
                        image.close()
                finally:
                    bitmap.close()
                report["geometry"]["embedded_sample"] = geometry_summary(
                    analyze_page(embedded_gray)
                )
                report["warnings"].append(
                    "Only the first embedded image was sampled; "
                    "orientation, coverage and resolution are not verified"
                )
            else:
                report["warnings"].append("No embedded image was sampled")
        except Exception as exc:
            report["warnings"].append(
                f"Embedded-image sampling: {type(exc).__name__}: {exc}"
            )

        if args.per_staff:
            height, width = result["deskewed"].shape
            crops = []
            for index, staff in enumerate(result["staves"]):
                top = max(0, int(staff["y_top"] - height * args.pad_frac / 2))
                bottom = min(
                    height, int(staff["y_bot"] + height * args.pad_frac / 2)
                )
                left = max(0, int(staff["x0"] - width * args.pad_frac))
                right = min(width, int(staff["x1"] + width * args.pad_frac))
                if bottom - top < 20 or right - left < 20:
                    continue
                crops.append({
                    "index": index, "n_staves": 1,
                    "box": (left, top, right, bottom),
                    "image": result["deskewed"][top:bottom, left:right].copy(),
                })
        else:
            crops = crop_systems(
                result["deskewed"], result["systems"],
                pad_frac=args.pad_frac,
            )

        for crop in crops:
            path = run / f"crop-{crop['index'] + 1:03d}.png"
            Image.fromarray(crop["image"]).save(path)
            report["crops"].append({
                "index": int(crop["index"]),
                "path": str(path),
                "detected_staves": int(crop["n_staves"]),
                "box": crop.get("box"),
            })
        if not crops:
            report["errors"].append("No usable crops were produced")
            rc = 1

        if not args.no_homr:
            tasks = [
                (f"crop-{c['index'] + 1:03d}", c["path"], "crop")
                for c in report["crops"]
            ]
            if embedded_path is not None:
                tasks.append((
                    "embedded-sample", str(embedded_path), "embedded_sample"
                ))
            for label, image_path, kind in tasks:
                work = Path(tempfile.mkdtemp(
                    prefix=f"engine-{label}-", dir=run
                ))
                attempt = {
                    "label": label, "kind": kind,
                    "input": image_path, "work_directory": str(work),
                    "returncode": None, "xml_path": None,
                    "log_path": None, "completed_with_structural_output": False,
                }
                try:
                    xml, log, engine_rc = run_homr(
                        image_path, str(work), timeout=args.timeout
                    )
                    attempt.update({
                        "returncode": engine_rc,
                        "xml_path": str(xml) if xml else None,
                        "log_path": str(log) if log else None,
                        "xml": xml_stats(xml),
                    })
                    attempt["completed_with_structural_output"] = (
                        engine_rc == 0 and attempt["xml"]["status"] == "ok"
                    )
                except Exception as exc:
                    attempt["error"] = f"{type(exc).__name__}: {exc}"
                if not attempt["completed_with_structural_output"]:
                    rc = 1
                report["attempts"].append(attempt)
                print(json.dumps(clean_json(attempt)), flush=True)
    except Exception as exc:
        report["errors"].append(f"{type(exc).__name__}: {exc}")
        rc = 2
    finally:
        for resource in (page, pdf):
            if resource is not None:
                try:
                    resource.close()
                except Exception as exc:
                    report["errors"].append(
                        f"resource close: {type(exc).__name__}: {exc}"
                    )
                    rc = 2

    if (
        args.baseline
        and report["baseline"]["xml"]["status"] != "ok"
        and rc == 0
    ):
        rc = 1
    report["summary"] = {
        "crops_created": len(report["crops"]),
        "recognition_attempts": len(report["attempts"]),
        "attempts_with_structural_output": sum(
            a["completed_with_structural_output"]
            for a in report["attempts"]
        ),
        "exit_status": rc,
    }
    try:
        write_report(report_path, report)
    except Exception as exc:
        print(f"ERROR writing report: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report["summary"], indent=2), flush=True)
    for message in report["warnings"]:
        print(f"WARNING: {message}", flush=True)
    for message in report["errors"]:
        print(f"ERROR: {message}", flush=True)
    print("No recovered-staff count or causal verdict is inferred.", flush=True)
    print(f"Report: {report_path}", flush=True)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
