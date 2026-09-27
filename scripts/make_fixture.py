"""Build a labeled OMR fixture: known MusicXML -> MuseScore PDF -> degraded scan PNG.

Accuracy needs labels, so this generates them: a short piece whose notes are
known exactly, engraved by MuseScore at 300 dpi, then degraded to imitate the
1972 photocopy scans in the Carpenters book (200-ppi resample, blur, JPEG
artifacts, skew, faded contrast). Running the pipeline over the degraded image
gives a true note-level score against exact ground truth.

Usage:
    python scripts/make_fixture.py [--out data/fixtures] [--name page001]
                                   [--degrade scan|light|none]
"""

import argparse
import json
import os
import random
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from fractions import Fraction

import pypdfium2 as pdfium
from PIL import Image, ImageEnhance, ImageFilter

DIVISIONS = 4
TYPE_NAMES = {
    Fraction(4): "whole", Fraction(3): "half", Fraction(2): "half",
    Fraction(3, 2): "quarter", Fraction(1): "quarter",
    Fraction(3, 4): "eighth", Fraction(1, 2): "eighth", Fraction(1, 4): "16th",
}

MELODY = [
    ("G4", 1), ("C5", 1), ("E5", 1), ("D5", 1),
    ("C5", Fraction(1, 2)), ("B4", Fraction(1, 2)), ("A4", 1), ("G4", 1),
    ("A4", 1), ("C5", 1), ("F#5", 1), ("E5", 1),
    ("D5", Fraction(1, 2)), ("D5", Fraction(1, 2)), ("C5", 1), ("B4", 1),
    ("C5", 1), ("E5+G5", 1), ("A5", 1), ("E5", 1),
    ("D5", 1), ("C5", 1), ("A4", 1), ("B4", 1),
]

BASS = [
    ("C3", 2), ("G3", 2), ("A2", 2), ("E3", 2), ("F2", 2), ("C3", 2),
    ("G2", 2), ("D3", 2), ("E2", 2), ("B2", 2), ("F2", 2), ("C3", 2),
    ("C3", 2), ("G3", 2), ("F2", 2), ("C3", 2), ("G2", 2), ("D3", 2),
]


def _pitch(token):
    letter = token[0].upper()
    alter = 0
    if len(token) > 1 and token[1] in "#b":
        alter = 1 if token[1] == "#" else -1
    octave = int(token[-1])
    return letter, alter, octave


ACCIDENTAL_NAMES = {1: "sharp", -1: "flat"}


def _note_xml(token, beats, staff, voice, tie=False):
    beats = Fraction(beats)
    dur = int(beats * DIVISIONS)
    dotted = (beats * 2) % 2 == 1
    type_name = TYPE_NAMES[beats]
    heads = token.split("+")
    blocks = []
    for pos, head in enumerate(heads):
        out = ["      <note>"]
        if pos:
            out.append("        <chord/>")
        if head == "rest":
            out.append("        <rest/>")
        else:
            letter, alter, octave = _pitch(head)
            out += ["        <pitch>", f"          <step>{letter}</step>"]
            if alter:
                out.append(f"          <alter>{alter}</alter>")
            out += [f"          <octave>{octave}</octave>", "        </pitch>"]
        out.append(f"        <duration>{dur}</duration>")
        if tie:
            out += ['        <tie type="start"/>', '        <notations><tied type="start"/></notations>']
        if dotted:
            out.append("        <dot/>")
        out.append(f"        <voice>{voice}</voice>")
        out.append(f"        <type>{type_name}</type>")
        if head != "rest":
            _, alter, _octave = _pitch(head)
            if alter in ACCIDENTAL_NAMES:
                out.append(f'        <accidental>{ACCIDENTAL_NAMES[alter]}</accidental>')
        out.append(f"        <staff>{staff}</staff>")
        out.append("      </note>")
        blocks.append("\n".join(out))
    return "\n".join(blocks)


def _measures(spec, count):
    out, idx = [], 0
    for _ in range(count):
        cur, used = [], Fraction(0)
        while used < 4 and idx < len(spec):
            note, dur = spec[idx]
            if used + dur > 4:
                break
            cur.append((note, dur))
            used += dur
            idx += 1
        if used < 4:
            cur.append(("rest", 4 - used))
        out.append(cur)
    return out


def build_truth_xml(path, measures=4):
    melody = _measures(MELODY, measures)
    bass = _measures(BASS, measures)

    body = []
    for i in range(measures):
        body.append(f'    <measure number="{i + 1}">')
        if i == 0:
            body.append(
                "      <attributes>\n"
                f"        <divisions>{DIVISIONS}</divisions>\n"
                "        <key><fifths>0</fifths></key>\n"
                "        <time><beats>4</beats><beat-type>4</beat-type></time>\n"
                "        <staves>2</staves>\n"
                '        <clef number="1"><sign>G</sign><line>2</line></clef>\n'
                '        <clef number="2"><sign>F</sign><line>4</line></clef>\n'
                "      </attributes>"
            )
        for note, dur in melody[i]:
            body.append(_note_xml(note, dur, 1, 1))
        upper = int(sum(Fraction(d) for _, d in melody[i]) * DIVISIONS)
        if upper:
            body.append(f"      <backup><duration>{upper}</duration></backup>")
        for note, dur in bass[i]:
            body.append(_note_xml(note, dur, 2, 5))
        body.append("    </measure>")

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 4.0 Partwise//EN" '
        '"http://www.musicxml.org/dtds/partwise.dtd">\n'
        '<score-partwise version="4.0">\n'
        "  <part-list>\n"
        '    <score-part id="P1">\n'
        "      <part-name>Fixture</part-name>\n"
        '      <score-instrument id="P1-I1"><instrument-name>Voice and Piano</instrument-name></score-instrument>\n'
        '      <midi-instrument id="P1-I1"><midi-channel>1</midi-channel><midi-program>53</midi-program></midi-instrument>\n'
        "    </score-part>\n"
        "  </part-list>\n"
        '  <part id="P1">\n'
        + "\n".join(body)
        + "\n  </part>\n"
        "</score-partwise>\n"
    )
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(xml)
    ET.fromstring(xml)
    return measures


def render_truth_pdf(truth_xml, out_pdf, musescore_bin, timeout=240):
    binary = musescore_bin or os.path.expanduser("~/.local/opt/musescore/bin/mscore4portable")
    rc = None
    for _ in range(3):
        try:
            proc = subprocess.run([binary, "-o", out_pdf, truth_xml], capture_output=True, timeout=timeout)
            rc = proc.returncode
        except subprocess.TimeoutExpired:
            rc = "timeout"
        if os.path.exists(out_pdf) and os.path.getsize(out_pdf) > 1000:
            return True, rc
        time.sleep(2)
    return False, rc


def pdf_to_png(pdf, png, dpi=300):
    doc = pdfium.PdfDocument(pdf)
    bitmap = doc[0].render(scale=dpi / 72.0)
    bitmap.to_pil().save(png)
    doc.close()


def degrade(png, out_png, mode="scan", seed=7):
    im = Image.open(png).convert("RGB")
    rng = random.Random(seed)
    meta = {"mode": mode, "in_size": list(im.size)}
    if mode == "none":
        im.save(out_png)
        meta["out_size"] = list(im.size)
        return meta
    w, h = im.size
    if mode == "scan":
        scale = 200 / 300.0
        im = im.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        im = im.resize((w, h), Image.BICUBIC)
        im = im.filter(ImageFilter.GaussianBlur(radius=0.6))
        im = ImageEnhance.Contrast(im).enhance(0.86)
        im = ImageEnhance.Brightness(im).enhance(1.04)
        im = im.rotate(rng.uniform(-0.35, 0.35), resample=Image.BICUBIC, fillcolor=(255, 255, 255))
        px = im.load()
        dots = int(w * h * 0.0015)
        for _ in range(dots):
            x, y = rng.randrange(w), rng.randrange(h)
            r, g, b = px[x, y]
            n = rng.randint(-26, 26)
            px[x, y] = (max(0, min(255, r + n)), max(0, min(255, g + n)), max(0, min(255, b + n)))
        im.save(out_png, quality=38)
        meta["noise_dots"] = dots
    else:
        im = im.filter(ImageFilter.GaussianBlur(radius=0.25))
        im = ImageEnhance.Contrast(im).enhance(0.97)
        im.save(out_png, quality=88)
        meta["noise_dots"] = 0
    meta["out_size"] = list(im.size)
    return meta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/fixtures")
    ap.add_argument("--name", default="page001")
    ap.add_argument("--measures", type=int, default=4)
    ap.add_argument("--degrade", default="scan", choices=["scan", "light", "none"])
    ap.add_argument("--musescore", default=os.path.expanduser("~/.local/opt/musescore/bin/mscore4portable"))
    args = ap.parse_args()

    outdir = os.path.abspath(args.out)
    os.makedirs(outdir, exist_ok=True)
    truth = os.path.join(outdir, f"{args.name}_truth.musicxml")
    pdf = os.path.join(outdir, f"{args.name}_truth.pdf")
    clean = os.path.join(outdir, f"{args.name}_clean.png")
    scan = os.path.join(outdir, f"{args.name}.png")

    build_truth_xml(truth, args.measures)
    ok, rc = render_truth_pdf(truth, pdf, args.musescore)
    if not ok:
        print(f"FAIL: MuseScore did not produce {pdf} (rc={rc})", file=sys.stderr)
        return 1
    pdf_to_png(pdf, clean)
    meta = degrade(clean, scan, args.degrade)
    meta.update({"truth": truth, "pdf": pdf, "clean": clean, "measures": args.measures})
    with open(os.path.join(outdir, f"{args.name}_fixture.json"), "w") as fh:
        json.dump(meta, fh, indent=2)
    print(json.dumps(meta, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
