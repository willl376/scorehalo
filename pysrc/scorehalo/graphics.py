"""Bypass pictured graphics: find photographs and artwork, never read them.

Why this module exists
----------------------
The promise of this project is that a songbook page *with a picture on it* is
readable (README: "skips or masks non-music content"). Until now only the whole
page was judged: :func:`scorehalo.preprocess.is_music_page` throws away a page
whose statistics do not look like music, and nothing ever looked for a picture
*inside* a page that is otherwise music. A photograph pasted across the top of a
song, or the two-page photo inserts this book certainly has, went to homr as if
it were notation.

The signature
-------------
A photograph is a SMOOTH MID-TONE region. Photocopied notation is the opposite:
it is thin, near-black ink on near-white paper, so every part of a music page is
either paper (smooth but bright) or ink (mid-tone or dark but *high* local
contrast). That is the test used here, and it is the reverse of the first guess
("pictures are busy, so pictures have high texture"), which is simply wrong for
this book.

Measured, on 300 dpi renders of the 18 pages that carry this book's music and on
the 200 ppi scans of its photo and title pages (``fraction of page`` = share of
the central area that is smooth *and* mid-tone, at a 15x15 window):

===========================  =============================
page                         smooth mid-tone, central area
===========================  =============================
music (18 renders, p20..p36) 0.00% .. 0.47%
music (200 ppi, p20/p25/p28) 0.00% .. 0.08%
colour photo pages (p1..p18) **15.7% .. 62.8%**
blank / title pages          0.00% .. 0.13%
===========================  =============================

The two populations do not overlap by a factor of 30, so :data:`PAGE_PHOTO_PCT`
sits at 5% with room on both sides -- and it must stay above the music pages'
worst measured page (0.47%) by an order of magnitude, not by a hair.

The one trap that was measured and had to be designed around: **the scan edge is
itself a smooth mid-tone strip.** The first pass found a full-height strip at
``x=0`` (up to 363 px wide) and another at the right margin on twelve of the
eighteen music pages. Both are scanner shadow, not pictures. Two rules remove
them and nothing else:

* the page test reads the CENTRE 80% only, so a margin can never make a music
  page look like a photograph;
* a *region* must sit clear of the page border (``EDGE_FRAC``) to be masked.

After both, the same 18 renders measure **0 regions each**, and the picture
pages measure 1-3 solid regions, as the module's docstring table claims.
"""
from typing import Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

#: Local standard deviation below which an area is "smooth". A halftone or a
#: stroke edge is far above this; a photograph's tone is far below it.
PHOTO_MAX_STD = 28.0
#: Tone band of a photograph. Below the low end is solid ink (a black plate
#: page scans at 99% ink and must not read as a picture); above the high end is
#: paper.
PHOTO_MID_TONE = (40.0, 215.0)
#: Side of the local-statistics window, in pixels, at 200-300 dpi.
WINDOW = 15
#: The page-level test only looks at the centre, because the scan border is
#: itself a smooth mid-tone strip (measured: full-height, up to 363px wide).
PAGE_INSET = 0.10
#: A page whose central area is this photo-like is a picture, not a score.
#: Measured margin: music <= 0.47%, photo >= 15.7%.
PAGE_PHOTO_PCT = 5.0
#: A system crop this photo-like is a picture the mask could not remove
#: (because it overlapped a staff), so the crop is skipped rather than handed
#: to the engine. Measured on the 80 crops of this book's music: max 9.0%,
#: median 2.6%, and 0 crops carried a maskable region.
CROP_PHOTO_PCT = 30.0
#: A region worth masking covers at least this much of the page.
MIN_AREA_FRAC = 0.02
#: ... is not a thin strip (scan edges are full-height, a few hundred px wide)...
MAX_ASPECT = 6.0
#: ... and is a solid blob rather than a sprinkling of smooth pixels.
MIN_FILL = 0.5
#: How close to the page edge still counts as the page edge.
EDGE_FRAC = 0.02
#: Opening kernel: kills isolated smooth pixels before components are counted.
OPEN_PX = 9
#: A box must be at least as big as the graphic it locates. The raw component is
#: eroded by the smoothing window and the opening kernel -- a flat blob was
#: measured 5-7 px small per side -- so the reported box grows by this much per
#: side. (Before this, a page could be "bypassed" and still leave a border of
#: the picture on it: the very thing the module exists to prevent.)
MASK_PAD = WINDOW // 2 + 2


def smooth_mid(gray: np.ndarray, window: int = WINDOW,
               max_std: float = PHOTO_MAX_STD,
               mid: Tuple[float, float] = PHOTO_MID_TONE) -> np.ndarray:
    """Boolean mask: pixels that are locally smooth AND mid-toned."""
    f = cv2.GaussianBlur(gray, (3, 3), 0).astype(np.float32)
    mean = cv2.blur(f, (window, window))
    var = np.clip(cv2.blur(f * f, (window, window)) - mean * mean, 0.0, None)
    std = np.sqrt(var)
    return (std < max_std) & (mean > mid[0]) & (mean < mid[1])


def photo_fraction(gray: np.ndarray, inset: float = PAGE_INSET) -> float:
    """Percent of the page's CENTRE that is photo-like.

    The centre, not the whole page: a scan's own border is smooth and mid-tone,
    so a whole-page measure reports a picture on every page of a photocopy.
    """
    h, w = gray.shape[:2]
    y0, y1 = int(h * inset), int(h * (1 - inset))
    x0, x1 = int(w * inset), int(w * (1 - inset))
    centre = gray[y0:y1, x0:x1]
    if centre.size == 0:
        return 0.0
    return float(smooth_mid(centre).mean() * 100.0)


def is_graphic_page(gray: np.ndarray, threshold: float = PAGE_PHOTO_PCT) -> bool:
    """True when the page is a pictured graphic (photo, cover art, a plate)."""
    return photo_fraction(gray) >= threshold


def is_graphic_crop(gray: np.ndarray,
                    threshold: float = CROP_PHOTO_PCT) -> bool:
    """True when a system crop is mostly picture, so the engine should skip it.

    A crop is already tight to its staff, so it is measured edge to edge
    (a 2% inset only avoids the crop's own padded rim).
    """
    return photo_fraction(gray, inset=0.02) >= threshold


def find_graphics(gray: np.ndarray, min_area_frac: float = MIN_AREA_FRAC,
                  max_aspect: float = MAX_ASPECT, min_fill: float = MIN_FILL,
                  edge_frac: float = EDGE_FRAC,
                  window: int = WINDOW,
                  pad: int = MASK_PAD) -> List[Dict]:
    """Pictured graphics on a page, largest first, as boxes to mask.

    Each entry: ``{"box": (x, y, w, h), "area_frac": float, "fill": float}``.
    Regions are NOT blanked here; :func:`blank` does that, and
    :func:`keep_off_staves` decides which ones are safe.

    Blobs that run into the page border are dropped rather than reported: on
    this kind of scan the page's own edge is a smooth mid-tone strip, so a
    border-touching blob is almost always the scanner, not a picture.
    """
    h, w = gray.shape[:2]
    cand = smooth_mid(gray, window=window).astype(np.uint8)
    if not cand.any():
        return []
    cand = cv2.morphologyEx(
        cand, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (OPEN_PX, OPEN_PX)))
    n, _labels, stats, _ = cv2.connectedComponentsWithStats(cand, 8)
    page_area = float(h * w)
    out = []
    for i in range(1, n):
        x, y, bw, bh, area = (int(v) for v in stats[i])
        if area / page_area < min_area_frac:
            continue
        if max(bw, bh) / max(1, min(bw, bh)) > max_aspect:
            continue
        fill = area / float(max(1, bw * bh))
        if fill < min_fill:
            continue
        if (x <= edge_frac * w or y <= edge_frac * h
                or x + bw >= (1 - edge_frac) * w
                or y + bh >= (1 - edge_frac) * h):
            continue
        out.append({"box": (max(0, x - pad), max(0, y - pad),
                            min(w, x + bw + pad) - max(0, x - pad),
                            min(h, y + bh + pad) - max(0, y - pad)),
                    "area_frac": round(area / page_area, 4),
                    "fill": round(fill, 2)})
    out.sort(key=lambda d: -d["area_frac"])
    return out


def keep_off_staves(regions: Sequence[Dict],
                    staff_boxes: Sequence[Sequence[int]] = (),
                    pad: int = 0) -> List[Dict]:
    """The regions that do not touch a staff, i.e. safe to erase.

    A picture is only masked where no staff band overlaps it. If a detected
    region covers a staff the two readings disagree -- most likely the detector
    is wrong -- so the staff wins and the region is left for a human to look at.
    """
    if not staff_boxes:
        return list(regions)
    kept = []
    for reg in regions:
        x, y, w, h = reg["box"]
        if any(_overlaps((x - pad, y - pad, w + 2 * pad, h + 2 * pad), sb)
               for sb in staff_boxes):
            continue
        kept.append(reg)
    return kept


def _overlaps(a: Sequence[int], b) -> bool:
    """Do a ``(x, y, w, h)`` box and a layout box touch?

    The second argument is either a layout dict (``layout._finish_staff``:
    ``x0, y_top, x1, y_bot``) or a plain ``(x0, y0, x1, y1)`` box with ``x1``
    and ``y1`` exclusive.
    """
    ax, ay, aw, ah = a
    if isinstance(b, dict):
        bx0, by0 = b["x0"], b["y_top"]
        bx1, by1 = b.get("x1", b["x0"]), b.get("y_bot", b["y_top"])
    else:
        bx0, by0, bx1, by1 = b
    return ax < bx1 and bx0 < ax + aw and ay < by1 and by0 < ay + ah


def blank(gray: np.ndarray, regions: Sequence[Dict],
          value: int = 255) -> np.ndarray:
    """A copy of ``gray`` with each region filled with paper.

    Slicing is clamped to the image: a region running off the page is a bug
    upstream, and erasing the part that is on the page beats raising in the
    middle of a book.
    """
    out = gray.copy()
    h, w = out.shape[:2]
    for reg in regions:
        x, y, bw, bh = reg["box"]
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(w, x + bw), min(h, y + bh)
        if x1 > x0 and y1 > y0:
            out[y0:y1, x0:x1] = value
    return out


def bypass(gray: np.ndarray, staff_boxes: Sequence[Sequence[int]] = (),
           value: int = 255) -> Tuple[np.ndarray, List[Dict]]:
    """Erase pictured graphics off a music page; return (page, what was erased).

    This is the function the pipeline calls: it masks the pictures and reports
    what it masked, so a caller can log it rather than lose the fact silently.
    """
    regions = keep_off_staves(find_graphics(gray), staff_boxes)
    if not regions:
        return gray, []
    return blank(gray, regions, value=value), regions


if __name__ == "__main__":  # pragma: no cover - a probe, not a pipeline stage
    import sys

    for path in sys.argv[1:]:
        img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if img is None:
            print("%s: UNREADABLE" % path)
            continue
        regs = find_graphics(img)
        print("%s: photo=%.2f%% graphic_page=%s regions=%d %s"
              % (path, photo_fraction(img), is_graphic_page(img), len(regs),
                 [r["box"] for r in regs]))
