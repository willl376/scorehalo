"""Staff and system detection on a rendered score page.

Why this exists: homr's own layout stage under-detects staves on degraded
songbook pages (p0020: it emitted 2 staves where the page has 8). We cannot
patch homr's weights, but staff detection is a classical CV problem, so we do
the geometry ourselves and hand homr tightly-cropped systems.

Method (classic, interpretable, no learned weights):
  1. Otsu binarize (ink -> white on black after invert).
  2. Morphological opening with a long horizontal kernel: this erases note
     heads, stems, beams and text, leaving only long horizontal rules.
  3. Connected components filtered by aspect ratio + length -> staff-line
     candidates, each with a y-centre and an x-extent.
  4. Group lines into staves by vertical spacing (staff_space, estimated from
     the data as the median small gap).
  5. Group staves into systems by x-overlap and vertical proximity.

All thresholds are expressed relative to measured image geometry so the same
code works at any dpi or page size.
"""

import numpy as np

try:
    import cv2
except ImportError as exc:  # pragma: no cover
    raise SystemExit("OpenCV (cv2) is required for layout detection") from exc


# ---------------------------------------------------------------- binarize


def binarize_gray(gray):
    """Otsu-binarize; return an image where INK is 255 (white) and paper 0."""
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    _, ink = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    return ink


def estimate_skew(ink, limit=3.0):
    """Estimate small page skew in degrees using the dominant line angle.

    Staff lines are the strongest near-horizontal structure on the page, so the
    median angle of long horizontal runs is a good skew proxy.
    """
    h, w = ink.shape
    lines = cv2.HoughLinesP(
        ink, 1, np.pi / 1800, threshold=int(w * 0.25),
        minLineLength=int(w * 0.3), maxLineGap=6,
    )
    if lines is None or len(lines) < 5:
        return 0.0
    # OpenCV 5 returns (N, 4); older versions return (N, 1, 4)
    lines = np.asarray(lines).reshape(-1, 4)
    angs = []
    for x1, y1, x2, y2 in lines:
        a = np.degrees(np.arctan2(y2 - y1, x2 - x1))
        if abs(a) <= limit:
            angs.append(a)
    if not angs:
        return 0.0
    return float(np.median(angs))


def heal_line_gaps(gray, runs, tol=6.0, min_gap=6, min_piece_frac=0.05,
                   thickness=3):
    """Draw across breaks in detected horizontal rules.

    A staff whose printed line is interrupted -- by a fold, a tear, a white
    repair, or (on p0022) a blank patch through the whole system -- reads as
    *two* staves: the reader groups by continuity, not by spacing, so the two
    halves of a 3-staff system become six staffs, they all collide, and the
    system is emitted as one part. Healing the break restores the one thing the
    reader needs here: an unbroken rule.

    Only *detected* rules are healed (``runs`` from :func:`find_ink_lines`),
    and only between their own pieces, within ``tol`` of each other and each
    longer than ``min_piece_frac`` of the page. Printed notation is never
    touched, so a page with no break comes back unchanged.

    Returns ``(gray, n_pixels)`` -- ``gray`` is the input when nothing was
    filled, and the caller's array is never overwritten.
    """
    if gray is None or not runs:
        return gray, 0
    h, w = gray.shape
    min_piece = w * min_piece_frac
    groups = []
    for run in sorted(runs):
        if groups and abs(run[0] - groups[-1][-1][0]) <= tol:
            groups[-1].append(run)
        else:
            groups.append([run])
    out = gray
    filled = 0
    for group in groups:
        pieces = sorted((r for r in group if r[2] - r[1] >= min_piece),
                        key=lambda r: r[1])
        for a, b in zip(pieces, pieces[1:]):
            # find_ink_lines returns x1 as one past the last inked column.
            x0, x1 = int(a[2]), int(b[1])
            if x1 - x0 < min_gap:
                continue
            y = int(round((a[0] + b[0]) / 2.0))
            half = thickness // 2
            top, bot = max(0, y - half), min(h, y + half + 1)
            if bot <= top:
                continue
            if out is gray:
                out = gray.copy()
            out[top:bot, x0:x1] = 0
            filled += (bot - top) * (x1 - x0)
    return out, filled


def deskew(gray, angle_deg):
    """Rotate the grayscale image by -angle to level staff lines."""
    if abs(angle_deg) < 0.05:
        return gray
    h, w = gray.shape
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle_deg, 1.0)
    return cv2.warpAffine(
        gray, m, (w, h), flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REPLICATE,
    )


# ------------------------------------------------------------- line finding


def _longest_run(row):
    """Longest run of set pixels in a 1-D 0/255 row."""
    idx = np.flatnonzero(row)
    if idx.size == 0:
        return 0
    breaks = np.flatnonzero(np.diff(idx) > 1)
    starts = np.concatenate(([0], breaks + 1))
    ends = np.concatenate((breaks, [idx.size - 1]))
    return int((idx[ends] - idx[starts] + 1).max())


def _split_blob_lines(mask, y0, min_len, gap=3):
    """Read the staff lines back out of a blob that fused several of them.

    Returns ``[(y_center, thickness), ...]`` in the page's coordinates, given
    a ``mask`` whose first row sits at ``y0``.

    Dense notation keeps the ink between two staff lines connected, so the
    horizontal opening never separates them: the pair arrives as ONE
    component 15-22 px tall, taller than a staff line may be, and is dropped.
    Losing a blob like that loses the whole staff, and on these pages the
    staff that gets lost is the voice above the piano -- which is how the
    singer disappears from the part. The lines are still inside it, though:
    a staff line is a row carrying a long horizontal run, while the rows
    between lines carry only note ink. So scan the blob's rows for their
    longest run and keep the ones that clear ``min_len``, clustered into
    lines.
    """
    rows = [r for r in range(mask.shape[0])
            if _longest_run(mask[r]) >= min_len]
    clusters = []
    for r in rows:
        if clusters and r - clusters[-1][-1] <= gap:
            clusters[-1].append(r)
        else:
            clusters.append([r])
    return [(y0 + (c[0] + c[-1]) / 2.0, len(c)) for c in clusters]


def find_ink_lines(ink, min_len_frac=0.18, max_thick=14, max_blob_factor=12):
    """Isolate long horizontal rules -> list of (y_center, x0, x1, thick)."""
    h, w = ink.shape
    klen = max(30, int(w * 0.10))
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (klen, 1))
    horiz = cv2.morphologyEx(ink, cv2.MORPH_OPEN, kernel)
    horiz = cv2.morphologyEx(
        horiz, cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (max(9, klen // 6), 1)),
    )
    n, labels, stats, _ = cv2.connectedComponentsWithStats(horiz, 8)
    out = []
    min_len = int(w * min_len_frac)
    for i in range(1, n):
        x, y, cw, ch, _ = stats[i]
        if cw < min_len:
            continue
        if cw / max(1, ch) < 8:  # must be line-like, not a blob
            continue
        if ch <= max_thick:
            out.append((y + ch / 2.0, x, x + cw, ch))
            continue
        # Too tall for one staff line: either several lines fused by dense
        # notation, or something that is not a staff at all (a page border, a
        # shaded band). A border touches the paper edge, and a blob that is
        # not staff lines has no rows of long runs, so both are excluded.
        if ch > max_blob_factor * max_thick or y < 3 or y + ch > h - 3:
            continue
        for cy, thick in _split_blob_lines(labels[y:y + ch] == i, y, min_len):
            out.append((cy, x, x + cw, thick))
    out.sort()
    return out


def merge_collinear(lines, tol=6.0):
    """Merge line candidates that share a y-band but are horizontally split."""
    merged = []
    for yc, x0, x1, th in lines:
        placed = False
        for m in merged:
            if abs(m["y"] - yc) <= tol and x0 <= m["x1"] + 40 and x1 >= m["x0"] - 40:
                m["x0"] = min(m["x0"], x0)
                m["x1"] = max(m["x1"], x1)
                m["y"] = (m["y"] * m["n"] + yc) / (m["n"] + 1)
                m["n"] += 1
                placed = True
                break
        if not placed:
            merged.append({"y": yc, "x0": x0, "x1": x1, "n": 1, "th": th})
    merged.sort(key=lambda d: d["y"])
    return merged


def dedupe_slivers(lines, min_sep):
    """Drop near-duplicate line candidates that cannot both be staff lines.

    A real staff's five lines are one staff_space apart, so two candidates
    closer together than min_sep (< staff_space) cannot both belong to the same
    staff -- one of them is a binarization sliver, a beam fragment, or a
    ledger line sitting beside a genuine staff line.

    We keep the LONGER of the pair: staff lines span the system width, whereas
    beams and ledger fragments do not. This matters more than it sounds --
    without it, a single 2px sliver corrupts the median-gap estimate for the
    whole page (p0022 measured 11.7px against a true 20px), and pushes a real
    5-line staff to 6 "lines", which then fails every geometry check.
    """
    if not lines or min_sep <= 0:
        return list(lines)
    kept = []
    for ln in sorted(lines, key=lambda d: d["y"]):
        clash = None
        for k in kept:
            if abs(k["y"] - ln["y"]) < min_sep:
                clash = k
                break
        if clash is None:
            kept.append(ln)
        elif (ln["x1"] - ln["x0"]) > (clash["x1"] - clash["x0"]):
            kept[kept.index(clash)] = ln
    kept.sort(key=lambda d: d["y"])
    return kept


def estimate_staff_space(lines, min_gap=3, max_gap=60):
    """Median of the small gaps between consecutive lines = staff spacing."""
    gaps = [
        lines[i + 1]["y"] - lines[i]["y"]
        for i in range(len(lines) - 1)
        if min_gap <= lines[i + 1]["y"] - lines[i]["y"] <= max_gap
    ]
    return float(np.median(gaps)) if gaps else 0.0


# ------------------------------------------------------------ staff finding


def group_staves(lines, staff_space=None, max_ratio=1.9, min_lines=3):
    """Group line candidates into staves by vertical spacing."""
    if not lines:
        return []
    if staff_space is None or staff_space <= 0:
        staff_space = estimate_staff_space(lines)
    if staff_space <= 0:
        return []
    staves = []
    cur = [lines[0]]
    for ln in lines[1:]:
        gap = ln["y"] - cur[-1]["y"]
        if gap <= max_ratio * staff_space:
            cur.append(ln)
        else:
            if len(cur) >= min_lines:
                staves.append(_finish_staff(cur))
            cur = [ln]
    if len(cur) >= min_lines:
        staves.append(_finish_staff(cur))
    return staves


def _finish_staff(group):
    ys = [g["y"] for g in group]
    gaps = [ys[i + 1] - ys[i] for i in range(len(ys) - 1)]
    spacing = float(np.mean(gaps)) if gaps else 0.0
    x0 = int(min(g["x0"] for g in group))
    x1 = int(max(g["x1"] for g in group))
    return {
        "y_top": group[0]["y"] - group[0]["th"] / 2.0,
        "y_bot": group[-1]["y"] + group[-1]["th"] / 2.0,
        "y_center": float(np.mean(ys)),
        "x0": x0,
        "x1": x1,
        "n_lines": len(group),
        # The arithmetic of the staff, kept so callers can check it rather than
        # re-measure: a real staff is 5 lines at a near-equal spacing.
        "ys": [round(y, 2) for y in ys],
        "spacing": round(spacing, 2),
        "spread": round(max(gaps) - min(gaps), 2) if gaps else 0.0,
        "resid": round(max(abs(g - spacing) for g in gaps), 2) if gaps else 0.0,
        "width": x1 - x0,
    }


# ----------------------------------------------------------- system finding


def group_systems(staves, staff_space, max_gap_factor=7.0, min_overlap=0.55):
    """Group staves into systems: vertical proximity + horizontal overlap."""
    if not staves:
        return []
    systems = []
    cur = [staves[0]]
    for st in staves[1:]:
        prev = cur[-1]
        gap = st["y_top"] - prev["y_bot"]
        ov = _x_overlap(prev, st)
        close = gap <= max_gap_factor * staff_space
        if close and ov >= min_overlap:
            cur.append(st)
        else:
            systems.append(_finish_system(cur))
            cur = [st]
    systems.append(_finish_system(cur))
    return systems


def find_vertical_spans(ink, min_len_frac=0.02, max_thick=16, rel_to=None):
    """Isolate long vertical rules (barlines, brackets, braces).

    Returns list of (x_center, y_top, y_bot, thick).

    These are the *real* signal for grouping staves into systems on this kind
    of page: vertical gap is not discriminative (inter-system gaps can be
    SMALLER than within-system ones), but a system is bound by a bracket,
    brace or barline that spans its staves.
    """
    h, w = ink.shape
    klen = max(20, int(h * (rel_to or 0.04)))
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, klen))
    vert = cv2.morphologyEx(ink, cv2.MORPH_OPEN, kernel)
    vert = cv2.morphologyEx(
        vert, cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(9, klen // 6))),
    )
    n, labels, stats, _ = cv2.connectedComponentsWithStats(vert, 8)
    out = []
    min_len = int(h * min_len_frac)
    for i in range(1, n):
        x, y, cw, ch, _ = stats[i]
        if ch < min_len or cw > max_thick:
            continue
        if ch / max(1, cw) < 6:
            continue
        out.append((x + cw / 2.0, float(y), float(y + ch), cw))
    out.sort(key=lambda t: t[1])
    return out


def group_systems_by_bracket(staves, vspans, staff_space, edge_margin=None):
    """Group staves into systems using vertical rules that span multiple staves.

    Two staves join the same system when some vertical rule's y-span covers
    both staff bands. Union-find over staves.
    """
    if not staves:
        return []
    if edge_margin is None:
        xs = [s["x0"] for s in staves] + [s["x1"] for s in staves]
        edge_margin = (min(xs) - 12, max(xs) + 12)
    n = len(staves)
    parent = list(range(n))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    groups = {}
    # A rule links two staves when it runs from at/above the first staff's
    # centre down to at/below the second's -- i.e. it spans BOTH staff bands.
    for i in range(n):
        for j in range(i + 1, n):
            for (vx, vtop, vbot, _) in vspans:
                if vx <= edge_margin[0] or vx >= edge_margin[1]:
                    continue  # page-border / scan-edge artifact
                if vtop <= staves[i]["y_center"] and vbot >= staves[j]["y_center"]:
                    union(i, j)
                    break

    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    systems = []
    for _, idxs in sorted(groups.items(), key=lambda kv: staves[kv[1][0]]["y_top"]):
        g = [staves[i] for i in sorted(idxs, key=lambda k: staves[k]["y_top"])]
        systems.append(_finish_system(g))
    return systems


def split_system_by_brace(system, vspans, staff_space):
    """The staves of ONE system split at its braces, top group first.

    A brace (or a bracket) is the printed boundary between instruments, and it
    is what tells a singer's staff from the piano's: the singer's bar lines stop
    at their own staff, while the piano's are drawn across the pair, so a
    vertical rule that spans two staff bands links exactly the staves that are
    one instrument.

    This is :func:`group_systems_by_bracket` asked the same question about a
    single system, and it is the granularity homr needs: it reads a lone staff
    and a braced pair reliably, while a whole three-staff system on a dense page
    it reads and then merges into one part (see :mod:`scorehalo.fragments`).

    When no vertical rule links two staves the system is returned WHOLE: nothing
    was measured about the part boundary, and splitting into singletons would
    assert that every staff is its own instrument -- for a piano, a worse error
    than the merge this exists to avoid.
    """
    groups = group_systems_by_bracket(system["staves"], vspans,
                                      staff_space or 20.0)
    if not any(len(g["staves"]) > 1 for g in groups):
        return [system]
    return sorted(groups, key=lambda g: min(s["y_top"] for s in g["staves"]))


def _x_overlap(a, b):
    lo = max(a["x0"], b["x0"])
    hi = min(a["x1"], b["x1"])
    inter = max(0, hi - lo)
    narrow = min(a["x1"] - a["x0"], b["x1"] - b["x0"])
    return inter / narrow if narrow > 0 else 0.0


def _join_systems(above, below):
    """One system from two, keeping the staves in printed order."""
    return _finish_system(sorted(above["staves"] + below["staves"],
                                 key=lambda s: s["y_top"]))


def attach_orphan_staves(systems, max_lonely=1):
    """Fold a one-staff system into the system it belongs to.

    A system is found by the vertical rules spanning it, and on this sort of
    page the rule that binds the accompaniment spans only the piano pair: the
    voice staff's bar lines are ~70 px long, while the vertical opening that
    finds rules runs a kernel 4% of the page height (~132 px), so they are
    erased. The voice staff is then left alone -- a one-staff "system"
    sitting directly above the accompaniment it sings with -- and the grid,
    which fits every system onto the page's tallest layout, sees alternating
    one- and two-staff systems. The tallest layout is then two rows, the
    voice row is not in the grid at all, and the vocal part disappears from
    the piece; on the pages this was written for, that is the whole bug.

    A song grows upwards: the accompaniment is printed at the foot of a
    system and whatever the page adds is stacked above it, so a lone staff is
    the top line of the system below. A system at the very foot of the page
    has nothing below it and joins the one above instead. ``max_lonely`` is
    the largest a system may be to count as a stray: a real system on these
    pages is never smaller than a braced pair, so a pair is never dissolved
    into an orphan, and two lone staves in a row are left as they are rather
    than fused on no evidence.
    """
    n = len(systems)
    joined = [False] * n
    out = []
    for i in range(n):
        if joined[i]:
            continue
        sysd = systems[i]
        if sysd["n_staves"] > max_lonely:
            out.append(sysd)
            continue
        below = i + 1 < n and not joined[i + 1] and systems[i + 1]["n_staves"] > max_lonely
        if below:
            joined[i + 1] = True
            out.append(_join_systems(sysd, systems[i + 1]))
        elif out and out[-1]["n_staves"] > max_lonely:
            out[-1] = _join_systems(out[-1], sysd)
        else:
            out.append(sysd)
    return out


def _finish_system(group):
    return {
        "staves": group,
        "n_staves": len(group),
        "y_top": min(s["y_top"] for s in group),
        "y_bot": max(s["y_bot"] for s in group),
        "x0": min(s["x0"] for s in group),
        "x1": max(s["x1"] for s in group),
    }


# ------------------------------------------------------------------ facade


def analyze_page(gray, min_staff_lines=3, max_gap_factor=7.0,
                 min_len_frac=0.18, want_skew=True):
    """Full pipeline on a grayscale page image.

    Returns a dict with staves, systems, staff_space, skew and the staff
    bounding boxes (in pixels) ready for cropping.
    """
    skew = estimate_skew(binarize_gray(gray)) if want_skew else 0.0
    level = deskew(gray, skew)
    ink = binarize_gray(level)
    raw = find_ink_lines(ink, min_len_frac=min_len_frac)
    # Close breaks in the printed staff lines before anything is cropped from
    # this image: an interrupted staff is read as two staves. The measurements
    # below still come from the unhealed page -- only the kept image changes.
    level, n_healed = heal_line_gaps(level, raw)
    lines = merge_collinear(raw)
    # Two-pass spacing. Pass 1 estimates staff spacing while ignoring tight
    # slivers; pass 2 deletes near-duplicate lines that are too close together
    # to both be staff lines, then re-estimates. Without pass 2 a single 2px
    # sliver is absorbed into a real 5-line staff, which then reports 6 lines
    # and a wrong mean spacing.
    rough = estimate_staff_space(lines, min_gap=8.0)
    n_slivers = 0
    if rough > 0:
        before = len(lines)
        lines = dedupe_slivers(lines, rough * 0.45)
        n_slivers = before - len(lines)
    space = estimate_staff_space(lines, min_gap=rough * 0.5 if rough > 0 else 3)
    staves = group_staves(lines, space or None, min_lines=min_staff_lines)
    vspans = find_vertical_spans(ink)
    systems = group_systems_by_bracket(staves, vspans, space or 20.0)
    if not systems or len(systems) == len(staves):
        # no bracket spanned anything: fall back to the gap heuristic
        alt = group_systems(staves, space or 20.0, max_gap_factor=max_gap_factor)
        if alt and len(alt) < len(systems if systems else alt):
            systems = alt
    systems = attach_orphan_staves(systems)
    return {
        "skew_deg": round(skew, 3),
        "staff_space": round(space, 2),
        "n_lines": len(lines),
        "n_vspans": len(vspans),
        "vspans": vspans,
        "staves": staves,
        "systems": systems,
        "n_staves": len(staves),
        "n_systems": len(systems),
        "n_slivers_removed": n_slivers,
        "n_healed_px": n_healed,
        "deskewed": level,
    }


def crop_systems(gray, systems, pad_frac=0.06, min_staff_lines=3):
    """Crop each system (plus its staff lines) to a separate image."""
    h, w = gray.shape
    crops = []
    for i, sysd in enumerate(systems):
        top = int(max(0, sysd["y_top"] - h * pad_frac * 0.5))
        bot = int(min(h, sysd["y_bot"] + h * pad_frac * 0.5))
        left = int(max(0, sysd["x0"] - w * pad_frac))
        right = int(min(w, sysd["x1"] + w * pad_frac))
        if bot - top < 20 or right - left < 20:
            continue
        crops.append({
            "index": i,
            "n_staves": sysd["n_staves"],
            "box": (left, top, right, bot),
            "image": gray[top:bot, left:right].copy(),
        })
    return crops
