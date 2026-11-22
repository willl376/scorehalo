"""Fallback-only rotation search for the whole-page music gate.

Problem measured 2026-11-22
---------------------------
``preprocess.is_music_page`` measures long horizontal runs to decide whether a
page is music. A few degrees of skew makes that statistic zero, so ``convert``
exits rc=0 with ``pages ok: 0/1`` and no prediction at all -- zero notes, not a
low score. On the labelled fixture even 0.5 deg was enough; a real scanned
page survived to 2 deg and failed at 3 deg. homr itself read the same skewed
pixels perfectly (37/37), so the loss is entirely at the gate.

Policy
------
This is a FALLBACK, not a preprocessing default. If the gate already accepts
the page, the caller's image is copied through byte-for-byte and no rotation
is chosen: an earlier always-rotate version needlessly turned a straight real
scan by -0.5 deg and was discarded.

When the gate rejects, search -5..+5 deg in 0.5 deg steps and keep the angle
that maximises the gate's OWN staff-like-row statistic (tie-break on line
coverage). If no angle looks like music, return the original image and let the
gate reject it -- covers, blank pages and text pages must stay skipped.

The engine, validator and note logic are untouched. The caller records the
chosen angle and before/after gate stats in the manifest so the decision is
auditable. Isolated evidence:
``~/Documents/omr_workspace/deskew-experiment-2026-11-22/REPORT.md``.
"""

from __future__ import annotations

import os
import shutil
import tempfile

import cv2

from scorehalo import triage

DEFAULT_ANGLES = tuple(a / 2.0 for a in range(-10, 11))
MIN_STAFF_ROWS = 4


def prepare_for_gate(src, dst, gate_ok, angles=None, tmp_dir=None):
    """Return a rotation decision and write the image the gate should see.

    ``gate_ok`` is the unchanged gate's decision on ``src``; passing it in
    avoids running the gate twice. ``dst`` always receives an image: the
    original bytes when the gate passed (or no angle helped), otherwise the
    levelled copy.

    Returns a dict with ``activated``, ``chosen_angle``, ``before``/``after``
    triage stats, ``search_angles``, ``saved`` and ``gate_reason``.
    """
    src, dst = os.fspath(src), os.fspath(dst)
    angles = DEFAULT_ANGLES if angles is None else tuple(angles)
    parent = os.path.dirname(dst)
    if parent:
        os.makedirs(parent, exist_ok=True)

    gray = cv2.imread(src, cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise ValueError(f"cannot read {src}")

    tmp_dir = tmp_dir or tempfile.mkdtemp(prefix="gate-rotation-")
    os.makedirs(tmp_dir, exist_ok=True)
    probe = os.path.join(tmp_dir, "probe.png")

    def _stats(image):
        if not cv2.imwrite(probe, image):
            raise OSError(f"cv2.imwrite failed: {probe}")
        return triage.triage(probe)

    before = _stats(gray)
    reason = "gate accepted" if gate_ok else "gate rejected"
    if gate_ok:
        shutil.copyfile(src, dst)
        return {"activated": False, "chosen_angle": None, "before": before,
                "after": before, "search_angles": [], "saved": dst,
                "gate_reason": reason}

    best = None
    for angle in angles:
        if abs(angle) < 1e-9:
            rotated = gray
        else:
            h, w = gray.shape
            matrix = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
            rotated = cv2.warpAffine(gray, matrix, (w, h),
                                     flags=cv2.INTER_LINEAR,
                                     borderMode=cv2.BORDER_REPLICATE)
        stats = _stats(rotated)
        score = ((stats or {}).get("staff-like rows", 0),
                 (stats or {}).get("h_line%", 0.0))
        if best is None or score > best[0]:
            best = (score, angle, rotated, stats)

    _score, angle, chosen, after = best
    if (after or {}).get("staff-like rows", 0) >= MIN_STAFF_ROWS:
        if not cv2.imwrite(dst, chosen):
            raise OSError(f"cv2.imwrite failed: {dst}")
    else:
        angle, after = None, before
        shutil.copyfile(src, dst)
    return {"activated": True, "chosen_angle": angle, "before": before,
            "after": after, "search_angles": [float(a) for a in angles],
            "saved": dst, "gate_reason": reason}
