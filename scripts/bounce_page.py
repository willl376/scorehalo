"""Bounce the assembled music back onto the page's pixels.

The question this answers is not "is the MusicXML valid" (that is what an
``.mlog`` answers) and not "does the engraver print three staves" (that is the
render oracle in ``docs/musescore-headless.md``). It is the musician's
question: **is the music we emitted the music printed on the page?** A musician
lays a transparent sheet over the two and looks for the lines that do not
coincide; :mod:`scorehalo.tracing` does the same with arithmetic.

Three things make that possible here, and none of them are free:

1. The page is read system by system, so system k of a page owns a contiguous
   block of the emitted measures. Re-engraving the page with a forced system
   break at each of those boundaries (``<print new-system="yes"/>``) makes
   MuseScore lay the music out in the SAME systems the book printed, which is
   what makes a staff-to-staff correspondence even defined. Without it the
   engraver reflows nine measures onto two systems and no pairing exists.
2. The render is rasterised at the scans' own scale (300 dpi gives MuseScore a
   20.5 px staff space against the scans' 19.5). tracing normalises heights,
   but its notehead detector erases the staff lines by a fixed +/-3 px, so a
   render at half scale loses its noteheads to its own staff lines.
3. The scan is compared in its DESKEWED frame -- the frame its staff boxes
   were measured in -- because tracing measures the band from `analyze_page`
   (deskewed) and then indexes the pixels it is handed. Handing it the raw,
   tilted page puts the band in the wrong place by the skew.

WHAT THE NUMBERS MEAN
- `page`/`engr`: noteheads near the staff (within five staff positions: the
  five lines plus a ledger step) on each side. The underlying detector's band
  is deliberately WIDER (+/-5.5 spaces) so that an octave displacement stays
  inside it; that width also admits the neighbouring staves and, on the page,
  the printed lyrics and chord symbols, which is why a raw count would not be
  comparable and a core count is reported instead.
- `x-pair`: events paired left-to-right within 2% of the system width.
- `same`: of those pairs, how many sit at identical height. The rest differ by
  a median N positions.
- `shift`: the systematic vertical displacement -- the peak of the height
  histogram overlap, i.e. the tool's designed verdict. 0 means the music is
  engraved where the page prints it; 7 means an octave away.

WHAT IT CANNOT SAY: tracing compares ink, not music. Same-height notes are
indistinguishable, and a rest or a lyric letter can be counted as a notehead
(they cannot be reliably separated). It is sharp at geometry -- notes missing,
invented, on the wrong staff, or an octave out -- and silent about the rest.

    scorehalo-bounce data/out-song --scan-dir data/out-band/render \\
        --pages 20,21,22
"""

import argparse
import glob
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "pysrc"))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from scorehalo import layout  # noqa: E402
from scorehalo import tracing  # noqa: E402

DEFAULT_APPIMAGE = os.path.expanduser(
    "~/Downloads/MuseScore-3.6.1.515740129-x86_64.AppImage")
#: Render at the scans' own scale: MuseScore's staff space lands at 20.5 px
#: against the scans' 19.5.
RENDER_DPI = 300
#: The staff is four staff positions across (-4..+4); five keeps a ledger step.
CORE_POSITIONS = 5.0


# ----------------------------------------------------------------- the page


def system_measure_counts(system_dir, stem):
    """How many measures each of the page's systems contributed.

    This is what makes the engraving reflow like the book: the systems were
    recognised separately and assembled in order, so system k owns the next
    ``counts[k]`` measures of the emitted page.
    """
    pattern = os.path.join(system_dir, "homr", stem, "%s.sys*_in.musicxml" % stem)
    files = sorted(glob.glob(pattern),
                   key=lambda p: int(re.search(r"sys(\d+)", p).group(1)))
    counts = []
    for path in files:
        root = ET.parse(path).getroot()
        parts = root.findall("part")
        counts.append(len(parts[0].findall("measure")) if parts else 0)
    return counts


def write_bounced(musicxml, counts, out_path):
    """Copy the emitted page with a forced system break at each boundary."""
    tree = ET.parse(musicxml)
    root = tree.getroot()
    bounds, acc = [], 0
    for n in counts[:-1]:
        acc += n
        bounds.append(acc)
    for part in root.findall("part"):
        measures = part.findall("measure")
        for b in bounds:
            if b < len(measures):
                el = ET.Element("print")
                el.set("new-system", "yes")
                measures[b].insert(0, el)
    tree.write(out_path, encoding="utf-8", xml_declaration=True)
    return out_path


def engrave(musicxml, out_pdf, appimage, display=":0"):
    """MuseScore -> PDF -> grayscale raster. Returns the raster path or None.

    The raster name carries the dpi, so a cached raster taken at another scale
    can never be mistaken for this one.
    """
    stale = (not os.path.exists(out_pdf)
             or os.path.getmtime(out_pdf) < os.path.getmtime(musicxml))
    if stale:
        if os.path.exists(out_pdf):
            os.remove(out_pdf)
        env = dict(os.environ)
        env["DISPLAY"] = display
        subprocess.run([appimage, "-o", out_pdf, musicxml],
                       capture_output=True, text=True, timeout=300, env=env)
    if not (os.path.exists(out_pdf) and os.path.getsize(out_pdf) > 1024):
        return None
    prefix = "%s-d%d" % (out_pdf[:-4], RENDER_DPI)
    pages = sorted(glob.glob(prefix + "-*.pgm"))
    if not pages or os.path.getmtime(pages[0]) < os.path.getmtime(out_pdf):
        for old in pages:
            os.remove(old)
        subprocess.run(["pdftoppm", "-gray", "-r", str(RENDER_DPI),
                        out_pdf, prefix], capture_output=True, timeout=300)
        pages = sorted(glob.glob(prefix + "-*.pgm"))
    return pages[0] if pages else None


# ------------------------------------------------------- staff correspondence


def staves_by_system(boxes):
    """Each system's staff indices, top to bottom."""
    staves = sorted(boxes["staves"], key=lambda st: st["y_top"])
    out = []
    for system in boxes["systems"]:
        out.append([i for i, st in enumerate(staves)
                    if st["y_top"] >= system["y_top"] - 1
                    and st["y_bot"] <= system["y_bot"] + 1])
    return out


def roles(n_staves):
    """What each staff in a system of this size plays, top to bottom."""
    if n_staves >= 3:
        middle = ["piano-treble"] + ["piano-upper"] * (n_staves - 3)
        return ["voice"] + middle + ["piano-bass"]
    if n_staves == 2:
        return ["piano-treble", "piano-bass"]
    return ["staff-%d" % i for i in range(n_staves)]


def bottom_pairs(scan_boxes, render_boxes):
    """Pair the two pages' staves, system by system, from the BOTTOM up.

    The grid seats every system on a bottom-aligned set of rows (see
    :mod:`scorehalo.grid`), so the correspondence is bottom-aligned too: a
    system the book printed as two staves -- the piano introduction -- matches
    the lower two of the engraving's three, and the silent voice staff above
    them pairs with nothing.
    """
    scan_systems = staves_by_system(scan_boxes)
    render_systems = staves_by_system(render_boxes)
    pairs = []
    for number, (s_idx, r_idx) in enumerate(zip(scan_systems, render_systems),
                                            start=1):
        label = roles(len(s_idx))
        for depth, (si, ri) in enumerate(zip(reversed(s_idx), reversed(r_idx))):
            pairs.append((si, ri, number, label[len(s_idx) - 1 - depth]))
    return pairs, scan_systems, render_systems


# ------------------------------------------------------------------- measure


def core(events):
    """The events that lie on the staff itself (plus a ledger step)."""
    return [e for e in events if abs(e[1]) <= CORE_POSITIONS]


def compare_pair(scan_ink, render_ink, scan_band, render_band):
    """One staff, page against engraving.

    ``_match_columns`` pairs by x; ``_position_shift`` is the height verdict on
    the full (wide-band) event sets, exactly as `tracing.compare_staff` uses
    them, so these numbers mean what that module's docstring says they mean.
    """
    source = tracing._noteheads(scan_ink, scan_band)
    target = tracing._noteheads(render_ink, render_band)
    s_core, t_core = core(source), core(target)
    matched, _un_s, _un_t = tracing._match_columns(s_core, t_core)
    displacements = [int(round((sy - ty) / (tracing.CANONICAL_SPACE / 2.0)))
                     for _sx, sy, _tx, ty in matched]
    histogram = {}
    for d in displacements:
        histogram[d] = histogram.get(d, 0) + 1
    shift, _heights, support = tracing._position_shift(source, target)
    return {
        "page_events": len(s_core),
        "engraved_events": len(t_core),
        "x_matched": len(matched),
        "same_height": histogram.get(0, 0),
        "displacement_histogram": dict(sorted(histogram.items())),
        "systematic_shift": shift,
        "shift_support": support,
        "target_events_wide": len(target),
    }


# --------------------------------------------------------------- the driver


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("data_dir", help="assembled pages, e.g. data/out-song")
    ap.add_argument("--scan-dir", default="data/out-band/render",
                    help="300 dpi page rasters (render/pNNNN.png)")
    ap.add_argument("--pages", default="20,21,22", help="page numbers")
    ap.add_argument("--out", default=None, help="default: DATA_DIR/bounce")
    ap.add_argument("--musescore", default=os.environ.get(
        "MUSESCORE_APPIMAGE", DEFAULT_APPIMAGE))
    ap.add_argument("--json", default=None, help="where to write the report")
    args = ap.parse_args()

    out_dir = args.out or os.path.join(args.data_dir, "bounce")
    os.makedirs(out_dir, exist_ok=True)
    report = {}

    for number in [int(p) for p in args.pages.split(",") if p.strip()]:
        stem = "p%04d" % number
        scan_path = os.path.join(args.scan_dir, stem + ".png")
        musicxml = os.path.join(args.data_dir, "grid-%s.musicxml" % stem)
        if not (os.path.exists(scan_path) and os.path.exists(musicxml)):
            print("%s: no page or no assembly -- skipped" % stem)
            continue

        counts = system_measure_counts(args.data_dir, stem)
        bounced = write_bounced(musicxml, counts,
                                os.path.join(out_dir, stem + ".musicxml"))
        raster = engrave(bounced, os.path.join(out_dir, stem + ".pdf"),
                         args.musescore)
        if raster is None:
            print("%s: MuseScore produced no render -- skipped" % stem)
            continue

        scan_boxes = layout.analyze_page(
            cv2.imread(scan_path, cv2.IMREAD_GRAYSCALE))
        scan_level = scan_boxes["deskewed"]
        render_gray = cv2.imread(raster, cv2.IMREAD_GRAYSCALE)
        render_boxes = layout.analyze_page(render_gray)

        pairs, scan_systems, render_systems = bottom_pairs(scan_boxes,
                                                           render_boxes)
        scan_ink = tracing._binarize(scan_level)
        render_ink = tracing._binarize(render_gray)
        scan_bands = {b["index"]: b for b in tracing.staff_bands(scan_level)}
        render_bands = {b["index"]: b for b in tracing.staff_bands(render_gray)}

        print("%s  systems: page %s  engraving %s  (%d measures)"
              % (stem, [len(s) for s in scan_systems],
                 [len(s) for s in render_systems], sum(counts)))
        if len(scan_systems) != len(render_systems):
            print("   !! the two pages do not agree on how many systems there"
                  " are: pairing what lines up")
        print("   %-4s %-14s %5s %5s %7s %7s %6s"
              % ("sys", "staff", "page", "engr", "x-pair", "same-ht", "shift"))
        page = {"systems": [len(s) for s in scan_systems],
                "engraved_systems": [len(s) for s in render_systems],
                "measures": sum(counts), "staves": []}
        for si, ri, number_, role in pairs:
            if si not in scan_bands or ri not in render_bands:
                continue
            verdict = compare_pair(scan_ink, render_ink, scan_bands[si],
                                   render_bands[ri])
            verdict.update({"staff": role, "system": number_,
                            "scan_index": si, "render_index": ri})
            page["staves"].append(verdict)
            flag = ""
            if abs(verdict["systematic_shift"]) == tracing.POSITIONS_PER_OCTAVE:
                flag = "  !! OCTAVE"
            elif verdict["systematic_shift"] == 0:
                flag = "  ok"
            print("   %-4d %-14s %5d %5d %7d %7s %+6d%s"
                  % (number_, role, verdict["page_events"],
                     verdict["engraved_events"], verdict["x_matched"],
                     "%d/%d" % (verdict["same_height"], verdict["x_matched"]),
                     verdict["systematic_shift"], flag))
        report[stem] = page

        side = os.path.join(out_dir, stem + "-side-by-side.png")
        cv2.imwrite(side, side_by_side(scan_level, render_gray))
        print("   wrote %s, %s, %s" % (bounced, raster, side))

    where = args.json or os.path.join(out_dir, "bounce.json")
    with open(where, "w") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
    print("\nwrote %s" % where)
    return 0


def side_by_side(scan, render, max_width=1500):
    """The page and its engraving, at one width: lay one over the other."""
    def fit(img):
        scale = max_width / float(img.shape[1])
        if scale < 1.0:
            img = cv2.resize(img, None, fx=scale, fy=scale,
                             interpolation=cv2.INTER_AREA)
        return img

    scan, render = fit(scan), fit(render)
    height = max(scan.shape[0], render.shape[0])
    left = np.full((height, scan.shape[1]), 255, np.uint8)
    left[:scan.shape[0]] = scan
    right = np.full((height, render.shape[1]), 255, np.uint8)
    right[:render.shape[0]] = render
    return np.hstack([left, np.zeros((height, 8), np.uint8), right])


if __name__ == "__main__":
    raise SystemExit(main())
