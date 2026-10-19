"""Detect brace/bracket spans in the left margin of a page, and group staves.

Why this module exists
----------------------
A printed page states which staves belong to one instrument with a *brace*, not
with clefs. Pairing staves by clef (``role_staves`` in :mod:`scorehalo.grouping`)
is a guess that breaks the moment the layout is not the common one:

* a singer above an accompaniment is guessed correctly,
* a singer *between* two accompaniment staves, or *below* them, is guessed
  wrong -- the clef-based rule sees ``(G, G, F)`` and pairs staves 1 and 2,
  silently folding the vocal line into the piano.

Measured on p0020 (300dpi, ``data/out-band/render/p0020.png``):

===========  ==========================  ==========  ==========================
system       brace ink                  brace width  staves covered
===========  ==========================  ==========  ==========================
sys1         y=650..988                 27px         1, 2   (piano alone)
sys2         y=1635..1949               22px         4, 5   (s3 = the voice)
sys3         y=2600..2913               25px         7, 8   (s6 = the voice)
===========  ==========================  ==========  ==========================

Two facts fell out of that measurement, and both are load-bearing:

* **width separates a brace from noise.** Braces are 22-27px wide. Everything
  else in the margin -- a note stem, a stray pixel -- is <=1px. A first pass
  mistook a 1px mark for a brace stroke and nearly invented a voice part in
  sys3.
* **brace endpoints sit a few pixels INSIDE the staff bounds.** The brace for
  sys3 starts at ``y_top+6`` and ends at ``y_bot-24``. An endpoint test with a
  tight tolerance silently finds *no* brace in sys1/sys3, which would turn a
  piano-only intro into two unbraced staves. The failure is silent and
  confident, so the coverage test below is written to fail loudly instead.

The rule this module encodes: **the voice is the staff the brace does not
cover.** That is structural rather than musical, so it holds whether the singer
is above, below, or between the accompaniment staves.
"""
from typing import Dict, List, Optional, Sequence, Tuple

#: A brace is >= this many pixels across. Measured braces are 22-27px; the
#: widest non-brace mark observed in the p0020 margin is 1px. 8px sits an order
#: of magnitude from the noise and well below the real signal.
BRACE_MIN_WIDTH = 8

#: A brace stroke this short is a speck or a text fragment, not a brace.
BRACE_MIN_HEIGHT = 60


def brace_strokes(
    ink,
    staves: Sequence[dict],
    margin: int = 70,
    left: Optional[int] = None,
) -> List[Tuple[int, int]]:
    """Vertical extents of brace strokes in the left margin of one system.

    ``ink`` is a boolean array of the page (dark pixels True), ``staves`` the
    system's staff dicts with ``y_top``/``y_bot``/``x0`` keys, in reading order.

    Returns ``(y_start, y_end)`` pairs -- one per distinct brace. Strokes are
    returned in reading order and never overlap.
    """
    import numpy as np

    if not staves:
        return []
    y_lo = int(min(s["y_top"] for s in staves)) - 12
    y_hi = int(max(s["y_bot"] for s in staves)) + 12
    x_edge = int(left if left is not None else min(s["x0"] for s in staves))
    x_a = max(0, x_edge - margin)
    x_b = x_edge - 2
    if x_b <= x_a or y_hi <= y_lo:
        return []
    band = ink[y_lo:y_hi, x_a:x_b]
    if band.size == 0:
        return []
    rows = np.where(band.any(axis=1))[0]
    if not len(rows):
        return []
    rows = rows + y_lo
    runs: List[Tuple[int, int]] = []
    start = prev = int(rows[0])
    for r in rows[1:]:
        r = int(r)
        if r > prev + 3:
            runs.append((start, prev))
            start = r
        prev = r
    runs.append((start, prev))

    strokes: List[Tuple[int, int]] = []
    for a, b in runs:
        if b - a < BRACE_MIN_HEIGHT:
            continue
        cols = np.where(ink[a:b + 1, x_a:x_b].any(axis=0))[0]
        if not len(cols):
            continue
        # Width = number of pixel columns spanned (max - min + 1), so an
        # 8-pixel mark measures 8 and the threshold means what it says.
        width = int(cols.max() - cols.min()) + 1
        if width < BRACE_MIN_WIDTH:
            continue  # a stem or a speck: wide enough to see, too thin to brace
        strokes.append((a, b))
    return strokes


def brace_groups(
    ink,
    staves: Sequence[dict],
    margin: int = 70,
) -> List[List[int]]:
    """Group a system's staves (0-based, reading order) by brace membership.

    A brace spans from the top of some staff to the bottom of another, so it
    covers every staff in between *inclusive*. Staves no brace touches are left
    alone -- that is the singer.

    Raises ``ValueError`` if a stroke is wide enough to be a brace but covers no
    staff at all, because that combination means the geometry is being
    misread and silently returning "everything is a voice" is the worst
    possible answer.
    """
    strokes = brace_strokes(ink, staves, margin)
    groups: List[List[int]] = []
    for a, b in strokes:
        span = [i for i, s in enumerate(staves)
                if s["y_bot"] >= a - 30 and s["y_top"] <= b + 30]
        if not span:
            raise ValueError(
                "brace stroke y=%d..%d covers no staff of %d; the staff "
                "geometry and the ink do not agree" % (a, b, len(staves))
            )
        lo, hi = span[0], span[-1]
        groups.append(list(range(lo, hi + 1)))
    return groups