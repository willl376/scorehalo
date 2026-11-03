"""Report detected page geometry and independent MusicXML structural counts.

No staff-loss, staff-recovery, or transcription-accuracy claims are made.
Exit codes: 0 = selected pages processed without geometry/XML errors;
1 = page errors, unusable XML, or no selected pages;
2 = input, argument, or report-writing errors.

XML status 'ok' means supported XML containing parts and measures, not
schema validity or accurate transcription. Zero-note output is reported.
"""

import argparse
from collections import Counter
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
    path = Path(path)
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


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("out_dir", nargs="?", default="data/out-band")
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--json", default="")
    ap.add_argument(
        "--cache", default="",
        help="compatibility option; this audit always renders fresh pages"
    )
    args = ap.parse_args(argv)
    if args.dpi <= 0:
        ap.error("--dpi must be positive")

    out = Path(args.out_dir).resolve()
    manifest_path = out / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            raise ValueError("manifest must be an object")
        pages = manifest.get("pages")
        if not isinstance(pages, list):
            raise ValueError("manifest pages must be a list")
        if any(not isinstance(p, dict) for p in pages):
            raise ValueError("every manifest page must be an object")
        selected = [p for p in pages if p.get("status") == "ok"]
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    rows = []
    pdf = None
    exit_code = 0
    try:
        if selected:
            import numpy as np
            import pypdfium2 as pdfium
            from scorehalo.layout import analyze_page

            source = manifest.get("source")
            if not isinstance(source, str) or not source.strip():
                raise ValueError("manifest source must be a nonempty PDF path")
            # Preserve the old audit's cwd-relative source-path convention.
            source_path = Path(source).resolve()
            if not source_path.is_file():
                raise ValueError(f"source PDF missing: {source_path}")
            pdf = pdfium.PdfDocument(str(source_path))
        else:
            source_path = None
            exit_code = 1

        if args.cache:
            print("NOTE: --cache accepted; cached images are not read or written.")

        print("page       detected  grouping       xml-status   pitched  rests")
        for number, record in enumerate(selected, 1):
            row = {
                "selected_index": number,
                "source_page": record.get("page"),
                "image": record.get("image"),
                "geometry_status": "error",
                "geometry": None,
                "xml": {"status": "unresolved"},
                "errors": [],
            }

            try:
                image = record.get("image")
                if not isinstance(image, str) or not image.strip():
                    raise ValueError("page image must be a nonempty string")
                name = Path(image).stem
                xml_path = out / (name + ".musicxml")
                row["page"] = name
                row["xml_path"] = str(xml_path)
                row["xml"] = xml_stats(xml_path)
            except Exception as exc:
                row["errors"].append(f"XML lookup: {type(exc).__name__}: {exc}")

            page = bitmap = None
            try:
                one_based = record.get("page")
                if (
                    isinstance(one_based, bool)
                    or not isinstance(one_based, int)
                    or not 1 <= one_based <= len(pdf)
                ):
                    raise ValueError(f"invalid 1-based PDF page: {one_based!r}")
                page = pdf[one_based - 1]
                bitmap = page.render(scale=args.dpi / 72.0)
                image = bitmap.to_pil()
                try:
                    gray = np.array(image.convert("L"))
                finally:
                    image.close()
                result = analyze_page(gray)
                row["geometry"] = clean_json({
                    "detected_staves": int(result["n_staves"]),
                    "detected_systems": int(result["n_systems"]),
                    "grouping": [
                        int(s["n_staves"]) for s in result["systems"]
                    ],
                    "staff_space_px": result["staff_space"],
                    "skew_deg": result["skew_deg"],
                })
                row["geometry_status"] = "ok"
            except Exception as exc:
                row["errors"].append(
                    f"geometry: {type(exc).__name__}: {exc}"
                )
            finally:
                for resource in (bitmap, page):
                    if resource is not None:
                        try:
                            resource.close()
                        except Exception as exc:
                            row["errors"].append(
                                f"resource close: {type(exc).__name__}: {exc}"
                            )

            if row["errors"] or row["xml"]["status"] != "ok":
                exit_code = 1
            rows.append(row)

            geometry = row["geometry"] or {}
            grouping = "+".join(
                str(n) for n in geometry.get("grouping", [])
            ) or "-"
            stats = row["xml"]
            label = row.get("page", f"record-{number}")
            print(
                f"{label:<10} "
                f"{str(geometry.get('detected_staves', '-')):>8}  "
                f"{grouping:<13} "
                f"{stats['status']:<12} "
                f"{str(stats.get('pitched_notes', '-')):>7}  "
                f"{str(stats.get('rests', '-')):>5}"
            )
            for error in row["errors"]:
                print(f"  ERROR: {error}")
            if stats.get("why"):
                print(f"  XML: {stats['why']}")

        measured = [
            r for r in rows if r["geometry_status"] == "ok"
        ]
        summary = {
            "manifest_pages": len(pages),
            "selected_pages": len(selected),
            "geometry_pages_processed": len(measured),
            "zero_detected_staff_pages": sum(
                r["geometry"]["detected_staves"] == 0 for r in measured
            ),
            "geometry_error_pages": len(rows) - len(measured),
            "pages_with_processing_errors": sum(
                bool(r["errors"]) for r in rows
            ),
            "xml_status_counts": dict(Counter(
                r["xml"]["status"] for r in rows
            )),
            "xml_ok_zero_note_pages": sum(
                r["xml"]["status"] == "ok"
                and r["xml"]["note_elements"] == 0 for r in rows
            ),
        }
        report = {
            "metadata": {
                "report_format_version": 1,
                "manifest": str(manifest_path),
                "source": str(source_path) if source_path else None,
                "dpi": args.dpi,
                "cache_policy": "disabled; fresh rendering",
                "limitations": [
                    "Geometry is detector output, not ground truth.",
                    "XML counts do not establish transcription accuracy.",
                    "XML status ok is not full MusicXML validation.",
                    "part_measures sums measures across musical parts.",
                ],
            },
            "summary": summary,
            "rows": rows,
        }
        print("\nSummary:")
        print(json.dumps(summary, indent=2))
        print(
            "No staff-loss or recovery percentage is calculated. "
            "Grouping variation is not a detector-validation test."
        )
        if args.json:
            write_report(args.json, report)
            print(f"Wrote {args.json}")
        return exit_code
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    finally:
        if pdf is not None:
            pdf.close()


if __name__ == "__main__":
    raise SystemExit(main())
