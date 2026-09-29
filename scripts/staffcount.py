"""Count musical staves in a page image.

Replaces compare._staff_like_rows, which is unusable as a staff counter: it
requires >10% of the page width to be dark in a row, so any page whose systems
are narrow (or whose lines are faint, as in a 200-pii photocopy) scores 0. It
also counts photo edges as staff bands.

Method: binarize adaptively, find rows that look like long horizontal runs,
then group them into staves by looking for the characteristic evenly-spaced
5-line pattern rather than just "dark rows".
"""

import numpy as np
from PIL import Image


def _as_array(img, thresh):
    if isinstance(img, (str, bytes)) or hasattr(img, "read"):
        a = np.asarray(Image.open(img).convert("L"))
    else:
        a = np.asarray(img.convert("L"))
    return a


def _row_line_score(img, thresh=200):
    """Per-row count of horizontal dark runs of length >= min_run."""
    a = _as_array(img, thresh)
    dark = a < thresh
    h, w = dark.shape
    min_run = max(8, w // 90)
    out = np.zeros(h, dtype=np.int32)
    for y in range(h):
        row = dark[y]
        if not row.any():
            continue
        # count runs
        idx = np.flatnonzero(np.diff(np.concatenate(([0], row.view(np.int8), [0]))))
        starts, ends = idx[0::2], idx[1::2]
        out[y] = int(((ends - starts) >= min_run).sum())
    return out, w


def count_staves(img, thresh=200, min_frac=0.004):
    """Return (staves, debug) where staves is an int estimate."""
    runs, w = _row_line_score(img, thresh)
    need = max(2, int(w * min_frac))
    cand = np.flatnonzero(runs >= need)
    if cand.size == 0:
        return 0, {"lines": 0, "groups": []}

    # group candidate rows that are close together (staff lines cluster)
    groups = []
    cur = [cand[0]]
    for y in cand[1:]:
        if y - cur[-1] <= 3:
            cur.append(y)
        else:
            groups.append(cur)
            cur = [y]
    groups.append(cur)

    # keep groups whose line spacing is consistent -> a real staff
    staves = 0
    kept = []
    for g in groups:
        lines = [y for y in g if runs[y] >= need]
        if len(lines) < 3:
            continue
        gaps = np.diff(lines)
        if gaps.size == 0:
            continue
        # consistent spacing: most gaps within 40% of the median
        med = np.median(gaps)
        good = int((np.abs(gaps - med) <= 0.4 * med).sum())
        if good < max(2, int(0.6 * gaps.size)):
            continue
        staves += max(1, round(len(lines) / 5))
        kept.append((lines[0], lines[-1], len(lines), round(float(med), 1)))

    return staves, {"lines": int(cand.size), "groups": kept}
