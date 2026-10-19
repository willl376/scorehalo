#!/usr/bin/env python3
"""Audit the meter integrity of stitched songs: does every bar sum correctly?

The assembler logs "N note(s) clamped at a barline -- homr overflowed the
meter" -- that is a RHYTHM error baked into the MusicXML: the notes in that bar
add to more (or less) than the time signature allows, and the overflow was
clamped. Pitches can still be right while the rhythm is wrong.

This walks every part of every song, sums each bar's note durations against the
declared meter, and reports the bars that do not balance. It needs no MuseScore
-- it is pure MusicXML arithmetic -- so it is the cheapest honest signal of how
trustworthy each song's rhythm is.

Usage:
  python scripts/audit_meter.py data/out-songs
"""

import argparse
import glob
import os
import sys
import xml.etree.ElementTree as ET


def ticks(el, divisions):
    d = el.findtext("duration")
    if d is None:
        return 0
    return int(round(int(d) * 24 / divisions))


def audit_file(path):
    """Return (bars_checked, bad_bars) where bad_bars = [(part, measure, got, want)]."""
    root = ET.parse(path).getroot()
    bad = []
    checked = 0
    for part in root.findall("part"):
        pid = part.get("id")
        divisions = 24
        beats, beat_type = 4, 4
        for m in part.findall("measure"):
            for attrs in m.findall("attributes"):
                if attrs.findtext("divisions"):
                    divisions = int(attrs.findtext("divisions"))
                if attrs.find("time") is not None:
                    beats = int(attrs.findtext("time/beats") or 4)
                    beat_type = int(attrs.findtext("time/beat-type") or 4)
            want = beats * 24 * 4 // beat_type
            got = 0
            for el in m:
                if el.tag == "note":
                    if el.find("chord") is not None:
                        continue          # a chord tone shares its onset
                    got += ticks(el, divisions)
                elif el.tag == "forward":
                    got += ticks(el, divisions)
                # <backup> rewinds the cursor but does not add to the bar's
                # total time; a well-formed bar's SPAN is what must match.
            # a multi-staff part writes each staff then <backup>s; the bar's
            # span is the max cursor, which for our emitter == the max over
            # staves. Approximate span as max(got, want) is unsafe, so recompute
            # the true span by walking cursor-wise:
            span = _bar_span(m, divisions)
            checked += 1
            if span != want:
                bad.append((pid, m.get("number"), span, want))
    return checked, bad


def _bar_span(m, divisions):
    """Cursor-wise span of a measure: max reach over every staff/voice."""
    cursor = 0
    top = 0
    for el in m:
        if el.tag == "backup":
            cursor -= ticks(el, divisions)
        elif el.tag == "forward":
            cursor += ticks(el, divisions)
            top = max(top, cursor)
        elif el.tag == "note":
            dur = ticks(el, divisions)
            if el.find("chord") is None:
                cursor += dur
            top = max(top, cursor)
    return top


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("song_dir", help="data/out-songs")
    ap.add_argument("--verbose", action="store_true", help="list every bad bar")
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(args.song_dir, "*.musicxml")))
    if not files:
        print("no songs in %s" % args.song_dir)
        return 1

    grand_checked = grand_bad = 0
    summary = []
    for f in files:
        checked, bad = audit_file(f)
        grand_checked += checked
        grand_bad += len(bad)
        name = os.path.basename(f)[:-9]
        pct = (100.0 * (checked - len(bad)) / checked) if checked else 0.0
        summary.append((name, checked, len(bad), pct))
        if bad and args.verbose:
            print("  %s:" % name)
            for pid, num, span, want in bad[:40]:
                print("    %s meas %s: span=%d want=%d (off by %+d)"
                      % (pid, num, span, want, span - want))
            if len(bad) > 40:
                print("    ... and %d more" % (len(bad) - 40))

    print("\n=== rhythm integrity (bars summing to their meter) ===")
    for name, checked, nbad, pct in summary:
        print("  %-38s %5d bars  %4d wrong  %5.1f%% clean"
              % (name, checked, nbad, pct))
    overall = (100.0 * (grand_checked - grand_bad) / grand_checked
               if grand_checked else 0.0)
    print("  %-38s %5d bars  %4d wrong  %5.1f%% clean"
          % ("TOTAL", grand_checked, grand_bad, overall))
    return 0


if __name__ == "__main__":
    sys.exit(main())
