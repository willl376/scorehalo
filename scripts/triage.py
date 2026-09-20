"""Programmatic page triage: classify page regions + report staff/photo stats.

KISS version of "what is on this page": measure colorfulness, ink density,
horizontal-line density (staff lines), and rough interline spacing.
"""

import sys
import numpy as np
import cv2


def triage(path):
    img = cv2.imread(path)
    if img is None:
        return None
    h, w = img.shape[:2]
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # colorfulness
    r, g, b = rgb[:, :, 0].astype(np.float32), rgb[:, :, 1].astype(np.float32), rgb[:, :, 2].astype(np.float32)
    rg = np.abs(r - g)
    yb = np.abs((r + g) / 2 - b)
    colorfulness = float(np.sqrt(rg.std() ** 2 + yb.std() ** 2) + 0.3 * np.sqrt(rg.mean() ** 2 + yb.mean() ** 2))

    # ink density: dark pixels
    dark = (gray < 128)
    ink = float(dark.mean())

    # horizontal line detection (staff lines)
    thr = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 45, 15)
    horiz = cv2.getStructuringElement(cv2.MORPH_RECT, (w // 20, 1))
    hlines = cv2.morphologyEx(thr, cv2.MORPH_OPEN, horiz)
    line_density = float((hlines > 0).mean())

    # estimate vertical run pattern of long horizontal lines (interline heuristic)
    rows_sum = hlines.sum(axis=1)
    row_mask = (rows_sum > w * 0.3).astype(np.uint8)
    gaps = []
    start = None
    for i, v in enumerate(row_mask):
        if v and start is None:
            start = i
        elif not v and start is not None:
            gaps.append(i - start)
            start = None
    if start is not None:
        gaps.append(h - start)
    line_thicknesses = [g for g in gaps if g > 1]
    interline_est = None
    if len(line_thicknesses) >= 4:
        interline_est = float(np.mean(sorted(line_thicknesses)[:-1]))  # mean of smaller runs

    return {
        "size": f"{w}x{h}",
        "colorfulness": round(colorfulness, 1),
        "ink%": round(ink * 100, 2),
        "h_line%": round(line_density * 100, 2),
        "staff-like rows": len(line_thicknesses),
        "interline_est_px": (round(interline_est, 1) if interline_est else None),
    }


if __name__ == "__main__":
    for p in sys.argv[1:]:
        r = triage(p)
        print(p, "->", r if r else "UNREADABLE")