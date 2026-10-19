"""Replay the real homr symbol sequences through pristine and patched code.

Run from the scorehalo repo root with the two homr trees on sys.path:

    .venv/bin/python scripts/replay_barlength.py

Prints, per system, the nominator each version computes from the same real
symbols. A difference means the patch changes behaviour on real data; no
difference means it does not, whatever a synthetic repro suggested.
"""

import glob
import os
import subprocess
import sys
from fractions import Fraction

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
#: The code that actually produced the MusicXML: homr as installed in the venv,
#: which is what ``engine.run_homr`` invokes. NOT homr-main, which is a newer
#: upstream checkout that nothing imports -- I spent a patch on it before
#: noticing, and the patch was inert.
INSTALLED = os.path.join(REPO, ".venv/lib/python3.12/site-packages")
PATCHED = "/tmp/homr-patched"

CHILD = r'''
import sys, glob, os
sys.path.insert(0, sys.argv[1])
from fractions import Fraction
from homr.transformer.vocabulary import EncodedSymbol, remove_duplicated_symbols
from homr.music_xml_generator import (group_into_chords, add_tuplet_start_stop,
                                       find_division_and_time_signature_nominator)
sys.path.insert(0, %(repo)r)
from scorehalo.homr_log import read_log

for path in sorted(glob.glob(%(logs)r)):
    syms = read_log(path)
    enc = [EncodedSymbol(rhythm=s[0], pitch=s[1], lift=s[2],
                         articulation=s[3], slur=s[4], position=s[5])
           for s in syms]
    groups = add_tuplet_start_stop(group_into_chords(remove_duplicated_symbols(enc)))
    div, nom = find_division_and_time_signature_nominator(groups)
    # build_time_signature() recovers beats as int(nominator * denominator).
    print("%%s|%%s|%%s" %% (os.path.basename(path).split(".")[0], nom, nom * 4))
''' % {"repo": REPO, "logs": os.path.join(REPO, "data/out-system/homr/p0020/*.homr.log")}


def run(tree):
    out = subprocess.run([sys.executable, "-c", CHILD, tree],
                         capture_output=True, text=True)
    if out.returncode != 0:
        print(out.stderr[-2000:])
        raise SystemExit("child failed for %s" % tree)
    rows = {}
    for line in out.stdout.splitlines():
        name, nom, quarters = line.split("|")
        rows[name] = (nom, quarters)
    return rows


def main():
    before = run(INSTALLED)
    after = run(PATCHED)
    print("Symbol sequences replayed from the real homr logs (system crops).")
    print("'quarters' is the bar length homr measures for the whole part.")
    print()
    print("%-10s %-26s %-26s %s" % ("crop", "INSTALLED", "PATCHED", "verdict"))
    print("-" * 74)
    for name in sorted(before):
        b = before[name][1] + " quarters"
        a = after.get(name, ("?", "?"))[1] + " quarters"
        print("%-10s %-26s %-26s %s" % (name, b, a,
                                        "same" if before[name] == after.get(name) else "CHANGED"))
    print()
    print("The MusicXML on disk declares, for the denominator /4:")
    import xml.etree.ElementTree as ET
    for system in (1, 2, 3):
        path = os.path.join(REPO, "data/out-system/homr/p0020/p0020.sys%d_in.musicxml" % system)
        root = ET.parse(path).getroot()
        for block in root.findall(".//measure//attributes"):
            time = block.find("time")
            if time is not None:
                print("  sys%d: <beats>%s <beat-type>%s  ->  %s/%s"
                      % (system, time.findtext("beats"), time.findtext("beat-type"),
                         time.findtext("beats"), time.findtext("beat-type")))
                break
        else:
            print("  sys%d: no <time> declared" % system)


if __name__ == "__main__":
    main()