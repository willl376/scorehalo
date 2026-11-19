#!/usr/bin/env python3
"""Pair unbalanced slurs in a MusicXML file without changing notes.

This is an opt-in repair: it drops orphan slur marks and keeps every matched
pair's source ``number``. Renumbering pairs is deliberately not offered; it
crashed MuseScore 3.6.2 on two real band scores in the 2026-11-19 A/B.

Example:
    python scripts/repair_slurs.py score.musicxml \
        --out score.slurs.musicxml --report score.slurs.json
"""

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pysrc"))

from scorehalo.slurs import repair_file, summary  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", help="MusicXML file to repair")
    ap.add_argument("--out", required=True, help="repaired MusicXML output")
    ap.add_argument("--report", help="optional JSON report (full pair detail)")
    args = ap.parse_args()

    result = repair_file(args.input, args.out, args.report)
    print(json.dumps(summary(result), indent=2))


if __name__ == "__main__":
    main()
