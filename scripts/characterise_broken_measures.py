"""Characterise homr's broken measures on real book pages.

The audit reports HOW MANY measures fail. This asks the question that actually
matters for accuracy: are those measures *wrong music* or merely *unspellable
notation*?

A measure is a ratio. Notated 23/4 quarters in a 4/4 bar is a ratio of 23/16 --
a scale factor, which is what §30 found on the fixtures (2*5/2, 32*1/6). Sounding
time can still be exact; only the printed bar is unrepresentable. That is a
notation-repair problem. A ratio that is not a small rational is different: the
content is garbage, and that IS an accuracy defect.

Method note (§26, the lesson that keeps biting): the control below is NOT
decorative. It runs this same analyzer over the known-good fixture scores that
scored 100% on self-diff. A known-good corpus must yield ZERO broken measures. If
it does not, the analyzer is wrong and its counts on the book pages are worthless.
That is why the control runs first and refuses to print book numbers if it fails.

Cursor arithmetic note (§29): one cursor per measure honouring <backup> and
<forward>, NOT a sum of durations. Summing fires on every good file. A <chord>
note shares its onset with the note before it and does not advance the cursor.
"""

import glob
import sys
import xml.etree.ElementTree as ET
from fractions import Fraction
from itertools import pairwise

# A ratio is "spellable" if it reduces to a small rational: numerator <= 4,
# denominator <= 8. These are the tuplet / scale-factor shapes (§30).
SPELLABLE_P = 4
SPELLABLE_Q = 8


def tag(e):
    return e.tag.split("}")[-1]


def text_of(e, name):
    for k in e:
        if tag(k) == name:
            return (k.text or "").strip()
    return None


class Meter:
    """Time signature + divisions, both inherited forward (§26)."""

    def __init__(self):
        self.beats, self.beat_type = 4, 4
        self.divisions = 1

    @property
    def quarters(self):
        return Fraction(self.beats * 4, self.beat_type)


def measure_cursor(measure, meter):
    """Walk one measure. Returns (total_durations, onsets_by_voice).

    onsets: list of (voice, onset_div, pitch_or_None) for non-chord pitched notes
    and rests, used for the duplicate/collision census.
    """
    pos = 0
    max_pos = 0
    events = []
    for el in measure:
        t = tag(el)
        if t == "attributes":
            d = text_of(el, "divisions")
            if d:
                meter.divisions = int(d)
            tm = next((k for k in el if tag(k) == "time"), None)
            if tm is not None:
                b, bt = text_of(tm, "beats"), text_of(tm, "beat-type")
                if b and bt:
                    meter.beats, meter.beat_type = int(b), int(bt)
        elif t == "backup":
            cur = 0
            for k in el:
                if tag(k) == "duration":
                    cur = int(k.text or 0)
            pos -= cur
        elif t == "forward":
            cur = 0
            for k in el:
                if tag(k) == "duration":
                    cur = int(k.text or 0)
            pos += cur
            max_pos = max(max_pos, pos)
        elif t == "note":
            dur = 0
            for k in el:
                if tag(k) == "duration":
                    dur = int(k.text or 0)
            is_chord = any(tag(k) == "chord" for k in el)
            is_rest = any(tag(k) == "rest" for k in el)
            voice = text_of(el, "voice") or "1"
            pitch = None
            p = next((k for k in el if tag(k) == "pitch"), None)
            if p is not None:
                step = text_of(p, "step") or "?"
                octv = text_of(p, "octave") or "?"
                pitch = f"{step}{octv}"
            if is_chord:
                # shares the previous onset; does NOT advance
                if events and not is_rest:
                    onset = events[-1][1]
                    events.append((voice, onset, pitch))
            else:
                events.append((voice, pos, pitch))
                pos += dur
                max_pos = max(max_pos, pos)
    return max_pos, events


def analyse(path):
    tree = ET.parse(path)
    root = tree.getroot()
    meter = Meter()
    broken = []      # (part, measure_no, notated_q, expected_q, ratio)
    dup_same_onset = 0
    dup_adjacent = 0
    total_measures = 0

    for pi, part in enumerate(root.iter("part")):
        meter = Meter()
        for mi, meas in enumerate(part.iter("measure"), 1):
            total_measures += 1
            total_div, events = measure_cursor(meas, meter)
            expected_div = meter.quarters * meter.divisions
            if expected_div:
                notated_q = Fraction(total_div, meter.divisions)
                ratio = notated_q / meter.quarters
                if ratio != 1:
                    broken.append((pi, mi, notated_q, meter.quarters, ratio))
            # duplicate / collision census. MUST be per-voice: comparing the flat
            # stream pairs a treble note against a bass note and invents collisions.
            by_voice = {}
            for v, onset, pitch in events:
                if pitch is not None:
                    by_voice.setdefault(v, []).append((onset, pitch))
            for seq in by_voice.values():
                seq.sort(key=lambda e: e[0])
                for (onset_a, a), (onset_b, b) in pairwise(seq):
                    if a == b:
                        dup_adjacent += 1
                        if onset_a == onset_b:
                            dup_same_onset += 1

    return {
        "path": path,
        "measures": total_measures,
        "broken": broken,
        "dup_adjacent": dup_adjacent,
        "dup_same_onset": dup_same_onset,
    }


def spellable(ratio):
    r = abs(ratio)
    return r.numerator <= SPELLABLE_P and r.denominator <= SPELLABLE_Q


def main():
    root_dir = sys.argv[1] if len(sys.argv) > 1 else "data/out-band"

    # ---- CONTROL FIRST. Known-good fixtures must be clean. -------------------
    ctrl = sorted(glob.glob("data/fixtures/*_truth.musicxml"))
    if not ctrl:
        print("CONTROL UNAVAILABLE: no fixtures. Refusing to print book numbers.")
        return 2
    ctrl_broken = 0
    ctrl_measures = 0
    for p in ctrl:
        r = analyse(p)
        ctrl_broken += len(r["broken"])
        ctrl_measures += r["measures"]
    print(f"CONTROL: {len(ctrl)} known-good fixtures, {ctrl_measures} measures")
    print(f"CONTROL: {ctrl_broken} broken measures  (MUST be 0)")
    if ctrl_broken:
        print("CONTROL FAILED -> analyzer is unsound; book numbers withheld.")
        return 1
    print("control passed.\n")

    # ---- Book pages ----------------------------------------------------------
    pages = sorted(glob.glob(f"{root_dir}/p*.musicxml"))
    tot_broken = tot_unspell = tot_spell = 0
    tot_dup = tot_coll = 0
    tot_m = 0
    ratios = {}
    print(f"{'page':<8}{'meas':>6}{'broken':>8}{'spellable':>11}{'other':>7}{'dup':>6}{'collide':>8}")
    for p in pages:
        r = analyse(p)
        sp = sum(1 for b in r["broken"] if spellable(b[4]))
        other = len(r["broken"]) - sp
        tot_broken += len(r["broken"])
        tot_unspell += other
        tot_spell += sp
        tot_dup += r["dup_adjacent"]
        tot_coll += r["dup_same_onset"]
        tot_m += r["measures"]
        for b in r["broken"]:
            ratios[str(b[4])] = ratios.get(str(b[4]), 0) + 1
        print(f"{p.split('/')[-1][:7]:<8}{r['measures']:>6}{len(r['broken']):>8}"
              f"{sp:>11}{other:>7}{r['dup_adjacent']:>6}{r['dup_same_onset']:>8}")

    print(f"\nTOTAL {tot_m} measures, {tot_broken} broken "
          f"({tot_spell} small-rational/scale, {tot_unspell} other), "
          f"{tot_dup} adjacent-dupes of which {tot_coll} share an onset")
    print(f"broken rate: {tot_broken/tot_m*100:.1f}% of measures")
    print("\nratio census (top 15):")
    for k, v in sorted(ratios.items(), key=lambda kv: -kv[1])[:15]:
        print(f"   {k:>10}  x{v}")


if __name__ == "__main__":
    sys.exit(main())
