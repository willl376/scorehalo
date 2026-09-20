"""Video/photo-page preprocessor for the restorer stage.

Designed for the 'photo/graphics' case: faint 200ppi scans, color covers,
shadowed margins. KISS v1: adaptive contrast + gentle sharpen (toggles);
region triage reports whether a page is 'music-like' before we burn CPU on it.
"""

import os
import sys
import numpy as np
import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scorehalo.triage import triage


def enhance(input_path, out_path, contrast=True, sharpen=True):
    """Light restoration pass. Returns output path."""
    img = cv2.imread(input_path, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"can't read {input_path}")
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    if contrast:
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(16, 16))
        gray = clahe.apply(gray)
    if sharpen:
        blur = cv2.GaussianBlur(gray, (5, 5), 0)
        gray = cv2.addWeighted(gray, 1.35, blur, -0.35, 0)
    cv2.imwrite(out_path, gray)
    return out_path


def is_music_page(input_path, min_hline=0.8, min_ink=0.3):
    """Cheap gate to skip clearly non-music pages (photos, covers, blanks).

    Uses long-horizontal-line density + ink as a staff proxy, plus a
    strong-color check for photograph pages.
    """
    stats = triage(input_path)
    if stats is None:
        return False, stats, "unreadable"
    h = stats["h_line%"]
    ink = stats["ink%"]
    color = stats["colorfulness"]
    # Photo page: heavily saturated + few long lines -> skip before we spend
    # precious CPU guessing at a photograph.
    if color > 15 and h < 2.0:
        return False, stats, "skip: color-photo page"
    if stats.get("staff-like rows", 0) >= 4 and h >= min_hline and ink >= min_ink:
        return True, stats, "music-like"
    if stats.get("interline_est_px") and stats["interline_est_px"] > 3:
        return True, stats, "music-like(thinner staves)"
    return False, stats, "skip: not music-like"