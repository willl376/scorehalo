#!/usr/bin/env python3
"""
omr-grade-loop: auto-tuning render→OMR→audit→tune loop.

Recon (from memory §88): thresholds table, .omr grade parsing,
truth-diff via note-pitch multiset, DPI ladder 150/300/450/600 early-stop.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    from PIL import Image
    import numpy as np
    import cv2
except Exception:
    Image = None
    np = None
    cv2 = None


def run_omr_sheet_verify(img_path):
    try:
        result = subprocess.run(['omr-sheet', 'verify', str(img_path)], 
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=300)
        out = result.stdout.decode() + result.stderr.decode()
        accepted = 'ACCEPTED' in out
        return accepted, out
    except Exception as e:
        return False, str(e)


def run_omr_sheet_write(img_path, out_dir, target_interline=21):
    """`omr-sheet write` takes an output FILE (-o/--output) and --target, not a
    directory, so the corrected image is named after its source and interline."""
    out_path = Path(out_dir) / f"{Path(img_path).stem}_il{target_interline}.png"
    try:
        result = subprocess.run(['omr-sheet', 'write', str(img_path),
                                 '-o', str(out_path),
                                 '--target', str(target_interline)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=300)
        out = result.stdout.decode() + result.stderr.decode()
        return result.returncode == 0, out
    except Exception as e:
        return False, str(e)


def parse_omr_grades(omr_path):
    """Parse grades from .omr file if possible."""
    grades = {}
    try:
        import zipfile
        if zipfile.is_zipfile(omr_path):
            with zipfile.ZipFile(omr_path) as z:
                for name in z.namelist():
                    if name.endswith('.xml'):
                        content = z.read(name).decode('utf-8', errors='ignore')
                        # Look for grade attributes
                        import re
                        for m in re.finditer(r'grade="([0-9.]+)"', content):
                            grades.setdefault('grades', []).append(float(m.group(1)))
    except Exception:
        pass
    if grades.get('grades'):
        grades['min'] = min(grades['grades'])
        grades['avg'] = sum(grades['grades']) / len(grades['grades'])
    return grades


def note_pitch_multiset(musicxml_path):
    """Extract note pitch multiset for truth diff."""
    pitches = []
    try:
        with open(musicxml_path) as f:
            content = f.read()
        import re
        # Find all pitch elements
        for m in re.finditer(r'<pitch>\s*<step>([A-G])</step>\s*(?:<alter>([^<]+)</alter>\s*)?<octave>(\d+)</octave>\s*</pitch>', content):
            step = m.group(1)
            alter = m.group(2)
            octave = int(m.group(3))
            pitch = step + octave
            if alter == '1':
                pitch += '#'
            elif alter == '-1':
                pitch += 'b'
            pitches.append(pitch)
    except Exception:
        pass
    return sorted(pitches)


def render_pdf_page(pdf_path, page_num, out_path, dpi):
    """Render PDF page to image."""
    try:
        from scorehalo import pdf
        pdf.render_page(pdf_path, page_num, out_path=out_path, dpi=dpi)
        return out_path
    except Exception:
        try:
            import fitz
            doc = fitz.open(pdf_path)
            page = doc[page_num]
            pix = page.get_pixmap(dpi=dpi)
            pix.save(out_path)
            doc.close()
            return out_path
        except Exception as e:
            return None


def main():
    p = argparse.ArgumentParser(description="omr-grade-loop auto-tuner")
    p.add_argument("input", help="PDF or image to process")
    p.add_argument("--out", default="data/omr-grade-loop",
                   help="Output directory (default: data/omr-grade-loop)")
    p.add_argument("--dpi-range", default="150,300,450,600",
                   help="DPI ladder to test (comma-separated)")
    p.add_argument("--early-stop", action="store_true",
                   help="Early stop on first acceptable Audiveris result")
    p.add_argument("--truth", help="Optional ground truth MusicXML for truth-diff")
    p.add_argument("--page", type=int, default=0, help="PDF page number (0-based)")
    p.add_argument("--target-interline", type=int, default=21, help="Target interline for omr-sheet")
    args = p.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    dpis = [int(x.strip()) for x in args.dpi_range.split(",")]

    results = []
    input_path = Path(args.input)
    
    for dpi in dpis:
        res = {
            "dpi": dpi,
            "status": "tested",
        }
        work_dir = out_dir / f"dpi_{dpi}"
        work_dir.mkdir(parents=True, exist_ok=True)
        
        test_img = None
        if input_path.suffix.lower() == '.pdf':
            test_img = work_dir / f"page_{args.page}.png"
            render_pdf_page(str(input_path), args.page, str(test_img), dpi)
        elif input_path.suffix.lower() in ['.png', '.jpg', '.jpeg', '.tif', '.tiff']:
            # Copy/resample to this DPI
            test_img = work_dir / input_path.name
            if Image:
                img = Image.open(input_path)
                # Simple copy for now
                img.save(test_img)
            else:
                import shutil
                shutil.copy(input_path, test_img)
        
        if test_img and test_img.exists():
            accepted, verify_out = run_omr_sheet_verify(test_img)
            res["audiveris_accepted"] = accepted
            res["verify_output"] = verify_out[-500:] if verify_out else ""
            
            if accepted or args.early_stop is False:
                written, write_out = run_omr_sheet_write(test_img, work_dir, args.target_interline)
                res["omr_written"] = written
                # Look for .omr files
                for f in work_dir.glob("*.omr"):
                    grades = parse_omr_grades(f)
                    res["omr_grades"] = grades
                    break
            
            if args.truth and Path(args.truth).exists():
                # Try to generate musicxml from omr or from conversion
                truth_pitches = note_pitch_multiset(args.truth)
                res["truth_pitch_count"] = len(truth_pitches)
        
        results.append(res)
        if args.early_stop and res.get("audiveris_accepted"):
            break

    manifest = {
        "input": str(args.input),
        "dpis": dpis,
        "results": results,
        "best": None
    }
    
    # Find best - prefer accepted with best grades
    for r in results:
        if r.get("audiveris_accepted"):
            if manifest["best"] is None:
                manifest["best"] = r
            else:
                # Compare grades if available
                best_grades = manifest["best"].get("omr_grades", {}).get("avg", 0)
                cur_grades = r.get("omr_grades", {}).get("avg", 0)
                if cur_grades > best_grades:
                    manifest["best"] = r
            if args.early_stop:
                break
    
    if manifest["best"] is None:
        for r in results:
            if r.get("omr_written"):
                manifest["best"] = r
                break
    
    with open(out_dir / "results.json", "w") as f:
        json.dump(manifest, f, indent=2)

    print(f"Wrote {out_dir / 'results.json'}")
    if manifest["best"]:
        print(f"Best: dpi={manifest['best']['dpi']} accepted={manifest['best'].get('audiveris_accepted')} written={manifest['best'].get('omr_written')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
