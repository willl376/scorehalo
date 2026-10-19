"""Trace paper, with arithmetic instead of a pencil.

A musician comparing a score against its source lays a transparent sheet over
the two and looks for the places where the lines do not coincide. This module
does the same thing without seeing anything: the five staff lines in each image
supply the registration marks, the transform that makes them coincide supplies
the "lay the sheet down", and the residual at every position supplies the
"where do they disagree".

The reason to want this is that arithmetic does not need an eye. Today's octave
bug -- a singer engraved an octave below where she sings -- took an inference
chain, two crop granularities, and a human adjudicator to establish. Here it is
a displacement histogram with a spike at seven staff positions.

WHAT THIS CAN AND CANNOT DO
---------------------------
It compares **ink**, not music. Two different notes on the same staff position
are indistinguishable to it, and a rest is not reliably separable from a note.
So it is sharp at *geometry* -- notes that are missing, notes that are invented,
notes at the wrong height, on the wrong staff, or an octave out -- and blind to
everything musical. It never renders an opinion about whether the music is right.

UNITS. Everything is reported in **staff positions**: one position is a step
from a line to the adjacent space, i.e. ``staff_space / 2``. That makes the
numbers musical rather than pixel-ish:

    1 position   a semitone-ish step: a wrong note, or a wrong clef's effect
    7 positions  exactly an octave
    2 positions  a third, the signature of a bass/treble clef confusion
"""

from __future__ import annotations

import glob
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

from . import layout

#: The height a staff is normalised to, in pixels. Arbitrary but fixed, so the
#: two images become directly comparable.
CANONICAL_SPACE = 20.0

#: Positions per octave: seven steps from line to line an octave away.
POSITIONS_PER_OCTAVE = 7


def _binarize(gray: np.ndarray) -> np.ndarray:
    """Ink as 0/255, Otsu, which suits both a scan and a clean engraving."""
    if gray.ndim == 3:
        gray = cv2.cvtColor(gray, cv2.COLOR_BGR2GRAY)
    _t, ink = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    return ink


def _median(xs):
    """Median of a list, without pulling in the statistics module."""
    xs = sorted(xs)
    n = len(xs)
    if n == 0:
        return 0.0
    mid = n // 2
    return xs[mid] if n % 2 else (xs[mid - 1] + xs[mid]) / 2.0


def staff_bands(gray: np.ndarray) -> List[dict]:
    """Every staff found, with its five line positions and its geometry."""
    result = layout.analyze_page(gray)
    bands = []
    for index, staff in enumerate(result["staves"]):
        ys = list(staff.get("ys") or [])
        if len(ys) < 2:
            continue
        bands.append({
            "index": index,
            "ys": ys,
            "top": float(min(ys)),
            "bot": float(max(ys)),
            "centre": float(sum(ys) / len(ys)),
            "x0": float(staff.get("x0", 0)),
            "x1": float(staff.get("x1", 0)),
            "space": float(result.get("staff_space") or 0.0),
        })
    return bands


def _noteheads(ink: np.ndarray, band: dict) -> List[Tuple[float, float]]:
    """Notehead centroids in one staff band, as (x_fraction, y).

    The five staff lines are erased first: left in place they fuse
    every notehead on the staff into one component and the comparison
    degenerates into measuring the lines themselves. A small opening
    then removes stems and barlines. What is left is roughly the
    noteheads -- the only ink whose vertical position is a pitch.
    """
    ys = band["ys"]
    mid = (band["top"] + band["bot"]) / 2.0
    half = len(ys) * CANONICAL_SPACE / 2.0 + 3 * CANONICAL_SPACE
    top = int(max(0, mid - half))
    bot = int(min(ink.shape[0], mid + half))
    left = int(max(0, band["x0"]))
    right = int(min(ink.shape[1], band["x1"]))
    crop = ink[top:bot, left:right].copy()
    if crop.size == 0:
        return []
    scale = CANONICAL_SPACE / max(1.0, (band["bot"] - band["top"]) / 4.0)
    # Erase the staff lines so noteheads are not fused to them.
    for y in ys:
        r = int(round(y - top))
        crop[max(0, r - 3):r + 4, :] = 0
    resized = cv2.resize(crop, None, fx=scale, fy=scale,
                         interpolation=cv2.INTER_LINEAR)
    # The staff's middle line, in resized coordinates, is the
    # origin every notehead position is measured from.
    centre_y = (mid - top) * scale
    # Remove stems and barlines: thin vertical structures.
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    opened = cv2.morphologyEx(resized, cv2.MORPH_OPEN, kernel)
    _n, _labels, stats, cent = cv2.connectedComponentsWithStats(
        opened, connectivity=8)
    height, width = opened.shape
    out = []
    for i in range(1, _n):
        _x, _y, w, h, _area = stats[i]
        if (CANONICAL_SPACE * 0.45 < h < CANONICAL_SPACE * 1.6
                and CANONICAL_SPACE * 0.45 < w < CANONICAL_SPACE * 2.0):
            # One staff position is a step from a line to the
            # adjacent space: staff_space / 2.
            position = (float(cent[i][1]) - centre_y) / (
                CANONICAL_SPACE / 2.0)
            out.append((float(cent[i][0]) / max(1, width - 1),
                        position))
    return out


def _run_length_encode(mask: np.ndarray) -> List[Tuple[int, int]]:
    """Collapse a column mask into (start_row, length) runs."""
    rows = np.where(mask > 0)[0]
    if rows.size == 0:
        return []
    runs = []
    start = prev = int(rows[0])
    for r in rows[1:]:
        r = int(r)
        if r == prev + 1:
            prev = r
            continue
        runs.append((start, prev - start + 1))
        start = prev = r
    runs.append((start, prev - start + 1))
    return runs


@dataclass
class BandComparison:
    """One staff compared against its counterpart."""

    source_index: int
    target_index: int
    x_shift: float
    source_events: int
    target_events: int
    matched: int
    unmatched_source: int
    unmatched_target: int
    #: Vertical displacement in staff positions, signed. -7 means the target
    #: sits an octave BELOW the source.
    displacements: List[float] = field(default_factory=list)
    #: The systematic displacement, from histogram overlap.
    position_shift: int = 0
    #: The target's notehead-height histogram.
    position_histogram: Dict[int, int] = field(default_factory=dict)
    #: How many target noteheads the peak shift explains.
    position_support: int = 0

    @property
    def displacement_histogram(self) -> Dict[int, int]:
        hist: Dict[int, int] = {}
        for value in self.displacements:
            key = int(round(value))
            hist[key] = hist.get(key, 0) + 1
        return dict(sorted(hist.items()))

    def summary(self) -> str:
        lines = [
            "staff %d vs %d: %d source noteheads, %d target, "
            "%d matched by x"
            % (self.source_index, self.target_index, self.source_events,
               self.target_events, self.matched),
        ]
        # The systematic shift is the headline: it survives the
        # reflow that defeats note-by-note matching.
        if self.position_histogram:
            lines.append(
                "    systematic shift %+d positions (%d of %d target "
                "noteheads explained)"
                % (self.position_shift, self.position_support,
                   self.target_events))
            top = sorted(self.position_histogram.items(),
                         key=lambda kv: -kv[1])[:5]
            lines.append("    target notehead heights: %s"
                         % ", ".join("%+d:%d" % (k, v)
                                     for k, v in top))
            if abs(self.position_shift) == POSITIONS_PER_OCTAVE:
                lines.append("      !! shift is EXACTLY AN OCTAVE")
            elif self.position_shift == 0:
                lines.append("      heights agree")
        else:
            lines.append("    no noteheads to compare")
        return "\n".join(lines)


def _match_columns(source: List[Tuple[float, float]],
                   target: List[Tuple[float, float]],
                   tolerance: float = 0.02
                   ) -> Tuple[List[Tuple[float, float, float, float]], int, int]:
    """Pair events by relative x with a two-pointer sweep.

    Both lists are in x order; the sweep pairs each source event with the
    nearest unused target event within ``tolerance`` (a fraction of the band
    width). Returns ``(sx, sy, tx, ty)`` tuples plus the two unmatched counts.

    A greedy all-pairs match is O(n^2) over every ink column -- which is what
    stalled the first attempt on a real page. The sweep is O(n log n) and
    pairs the same events, because a target behind ``sx - tolerance`` can
    never serve any later source event either.
    """
    source = sorted(source)
    target = sorted(target)
    used: set = set()
    matched: List[Tuple[float, float, float, float]] = []
    j = 0
    for sx, sy in source:
        while j < len(target) and target[j][0] < sx - tolerance:
            j += 1
        best = None
        k = j
        while k < len(target) and target[k][0] <= sx + tolerance:
            if k not in used:
                dx = target[k][0] - sx
                if best is None or abs(dx) < abs(best[0]):
                    best = (dx, k, target[k][0], target[k][1])
            k += 1
        if best is None:
            continue
        used.add(best[1])
        matched.append((sx, sy, best[2], best[3]))
    return matched, len(source) - len(matched), len(target) - len(used)


def _position_shift(source: List[Tuple[float, float]],
                    target: List[Tuple[float, float]]
                    ) -> Tuple[int, Dict[int, int], int]:
    """Systematic vertical displacement, in staff positions.

    A scan and an engraving reflow differently, so the k-th note of
    one is not the k-th note of the other and note-by-note x-matching
    fails. A systematic pitch error does not care: it shifts every
    notehead by the same amount.

    Binning both staves' notehead heights into a histogram over staff
    positions, the systematic error is the shift that makes the two
    histograms overlap most. This is the stronger test: a
    nearest-neighbour match only measures agreement of *range*, which
    two unrelated staves can share, whereas overlap of the *shape*
    means the same notes sit at the same heights.

    Returns the peak displacement, the target's height histogram, and
    how many target noteheads the peak explains.
    """
    if not source or not target:
        return 0, {}, 0
    src = [p for _x, p in source]
    tgt = [p for _x, p in target]

    def hist(xs: List[float]) -> Dict[int, int]:
        out: Dict[int, int] = {}
        for v in xs:
            key = int(round(v))
            out[key] = out.get(key, 0) + 1
        return out

    sh, th = hist(src), hist(tgt)
    best_shift, best_overlap = 0, -1
    span = POSITIONS_PER_OCTAVE * 2          # two octaves either way
    for d in range(-span, span + 1):
        overlap = 0
        for p, count in th.items():
            overlap += min(count, sh.get(p + d, 0))
        if overlap > best_overlap:
            best_overlap = overlap
            best_shift = d
    return best_shift, th, best_overlap


def compare_staff(source_gray: np.ndarray, target_gray: np.ndarray,
                  pairs: Optional[Sequence[Tuple[int, int]]] = None,
                  ) -> List[BandComparison]:
    """Compare staff bands of two images.

    ``pairs`` is a sequence of ``(source_band_index, target_band_index)``.
    When omitted, bands are paired by position in the page's staff
    sequence -- the only correspondence available without the caller
    supplying one, since the two images are at different scales.
    """
    # analyze_page wants grayscale and binarizes internally; feeding it
    # an already-binary image sends its line-finding into a loop.
    src_bands = staff_bands(source_gray)
    tgt_bands = staff_bands(target_gray)
    src_ink = _binarize(source_gray)
    tgt_ink = _binarize(target_gray)

    if pairs is None:
        # Default to ordinal pairing: source band i against target band i.
        # Cross-image pairing by vertical centre cannot work -- the two
        # images are at different scales -- so position in the page's
        # staff sequence is the only correspondence available without
        # the caller supplying one.
        pairs = [(b["index"], b["index"])
                 for b in src_bands[:min(len(src_bands), len(tgt_bands))]]

    results = []
    for si, ti in pairs:
        s_band = next((b for b in src_bands if b["index"] == si), None)
        t_band = next((b for b in tgt_bands if b["index"] == ti), None)
        if s_band is None or t_band is None:
            continue
        # Convert vertical pixel offsets into staff positions.
        half = CANONICAL_SPACE / 2.0
        s_cols = _noteheads(src_ink, s_band)
        t_cols = _noteheads(tgt_ink, t_band)
        matched, un_s, un_t = _match_columns(s_cols, t_cols)
        positions = [(sy - ty) / half for _sx, sy, _tx, ty in matched]
        shifts = [tx - sx for sx, _sy, tx, _ty in matched]
        x_shift = _median(shifts) if shifts else 0.0
        p_shift, p_hist, p_support = _position_shift(s_cols, t_cols)
        results.append(BandComparison(
            source_index=si, target_index=ti, x_shift=x_shift,
            source_events=len(s_cols), target_events=len(t_cols),
            matched=len(matched), unmatched_source=un_s, unmatched_target=un_t,
            displacements=positions,
            position_shift=p_shift, position_histogram=p_hist,
            position_support=p_support,
        ))
    return results


def load(path: str) -> np.ndarray:
    image = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise SystemExit("cannot read image: %s" % path)
    return image