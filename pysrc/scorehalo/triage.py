"""CANDIDATE (probe only) -- triage with honest horizontal-band measurement.

Changes vs pysrc/scorehalo/triage.py:
  1. Row coverage is a FRACTION OF WIDTH (count_nonzero/w), not a sum of
     0/255 pixel values.  The old test `rows_sum > w*0.3` silently meant
     "coverage > 0.118%".
  2. Bands are located once (starts/stops), so a band's THICKNESS and the
     GAP BETWEEN BAND CENTRES are two different, correctly-named numbers.
     The old code measured thickness and called it an interline estimate.
  3. One-pixel bands are kept (old code dropped every g == 1).
  4. Keys are named for what they measure.  Legacy keys keep working so
     preprocess.is_music_page() can be switched over deliberately.
"""

import sys

import cv2
import numpy as np

from .graphics import photo_fraction

# a row counts as a full-width rule when this fraction of it is foreground
ROW_COVERAGE = 0.25


def _bands(row_mask):
    """(starts, thicknesses, centres) for runs of True in row_mask."""
    edges = np.diff(np.pad(row_mask.astype(np.int8), (1, 1)))
    starts = np.flatnonzero(edges == 1)
    stops = np.flatnonzero(edges == -1)          # exclusive
    return starts, stops - starts, (starts + stops - 1) / 2.0


def triage(path):
    img = cv2.imread(path)
    if img is None:
        return None
    h, w = img.shape[:2]
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # colorfulness (Hasler-Suesstrunk)
    r, g, b = (rgb[:, :, i].astype(np.float32) for i in range(3))
    rg = np.abs(r - g)
    yb = np.abs((r + g) / 2 - b)
    colorfulness = float(np.sqrt(rg.std() ** 2 + yb.std() ** 2)
                         + 0.3 * np.sqrt(rg.mean() ** 2 + yb.mean() ** 2))

    dark = (gray < 128)
    ink = float(dark.mean())

    thr = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                                cv2.THRESH_BINARY_INV, 45, 15)
    kw = max(1, w // 20)
    horiz = cv2.getStructuringElement(cv2.MORPH_RECT, (kw, 1))
    hlines = cv2.morphologyEx(thr, cv2.MORPH_OPEN, horiz)
    line_density = float((hlines > 0).mean())

    # ---- honest band geometry ------------------------------------------
    row_coverage = np.count_nonzero(hlines, axis=1) / float(w)
    starts, thicknesses, centres = _bands(row_coverage > ROW_COVERAGE)
    gaps = np.diff(centres) if centres.size > 1 else np.array([])
    med_thick = float(np.median(thicknesses)) if thicknesses.size else None
    med_gap = float(np.median(gaps)) if gaps.size else None

    return {
        "size": f"{w}x{h}",
        "colorfulness": round(colorfulness, 1),
        "ink%": round(ink * 100, 2),
        "h_line%": round(line_density * 100, 2),
        # honest names
        "horizontal_bands": int(starts.size),
        "median_band_thickness_px": (round(med_thick, 1) if med_thick is not None else None),
        "median_band_gap_px": (round(med_gap, 1) if med_gap is not None else None),
        "photo%": round(photo_fraction(gray), 2),
        # legacy keys, now carrying the corrected quantities
        "staff-like rows": int(starts.size),
        "interline_est_px": (round(med_gap, 1) if med_gap is not None else None),
    }


if __name__ == "__main__":
    for p in sys.argv[1:]:
        r = triage(p)
        print(p, "->", r if r else "UNREADABLE")
