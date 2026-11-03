"""Build a labeled OMR fixture: known MusicXML -> MuseScore PDF -> degraded scan PNG.

Accuracy needs labels, so this generates them: a short piece whose notes are
known exactly, engraved by MuseScore at 300 dpi, then degraded to imitate the
1972 photocopy scans in the Carpenters book (200-ppi resample, blur, JPEG
artifacts, skew, faded contrast). Running the pipeline over the degraded image
gives a true note-level score against exact ground truth.

Usage:
    python scripts/make_fixture.py [--out data/fixtures] [--name page001]
                                   [--degrade scan|light|none]

Four defects were fixed after measurement (see data/perplexity-probe/REPORT.md):

1. Duration -> notation is a TABLE (type name + dot count), not a parity test.
   `dotted = (beats*2) % 2 == 1` marked a half-beat eighth as dotted (3/8)
   while writing <duration>2</duration>, and never marked a dotted eighth at
   all.  Every half-beat note in MELODY was affected.
2. Note children are emitted in MusicXML order:
      duration, tie, instrument, voice, type, dot*, accidental, ..., staff,
      beam*, notations*
   The old order put <dot/> before <voice>/<type> and <notations> before them.
3. Degradation can actually produce JPEG artefacts: Pillow picks the encoder
   from the FILENAME, so `im.save("x.png", quality=38)` wrote a lossless PNG
   with the quality argument ignored.  We round-trip through BytesIO/JPEG and
   then save PNG.
4. render_truth_pdf refuses to accept a PDF it did not just produce: it renders
   to a fresh temp path, verifies a successful return code AND that the file
   opens and has the expected page count, and only then moves it into place.
   A stale PDF from a previous run can no longer be scored as this run's truth.
   It also refuses multi-page fixtures, because pdf_to_png only reads page 1
   while the benchmark scores the whole truth XML.
"""

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from fractions import Fraction
from io import BytesIO

import pypdfium2 as pdfium
from PIL import Image, ImageEnhance, ImageFilter

DIVISIONS = 4

# beats -> (written type, number of dots).  One table, one truth.
NOTE_VALUES = {
    Fraction(4): ("whole", 0),
    Fraction(3): ("half", 1),
    Fraction(2): ("half", 0),
    Fraction(3, 2): ("quarter", 1),
    Fraction(1): ("quarter", 0),
    Fraction(3, 4): ("eighth", 1),
    Fraction(1, 2): ("eighth", 0),
    Fraction(1, 4): ("16th", 0),
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


def _note_xml(token, beats, staff, voice, tie_start=False, tie_stop=False):
    beats = Fraction(beats)
    if beats not in NOTE_VALUES:
        raise ValueError(f"duration {beats} has no notation in NOTE_VALUES")
    type_name, dot_count = NOTE_VALUES[beats]
    dur = beats * DIVISIONS
    if dur.denominator != 1:
        raise ValueError(f"duration {beats} is not representable "
                         f"with divisions={DIVISIONS}")
    dur = int(dur)

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
        # sound tie(s) before voice, per the MusicXML note sequence
        if tie_stop:
            out.append('        <tie type="stop"/>')
        if tie_start:
            out.append('        <tie type="start"/>')

        out.append(f"        <voice>{voice}</voice>")
        out.append(f"        <type>{type_name}</type>")
        out += ["        <dot/>"] * dot_count

        if head != "rest":
            _, alter, _octave = _pitch(head)
            if alter in ACCIDENTAL_NAMES:
                out.append(f'        <accidental>{ACCIDENTAL_NAMES[alter]}</accidental>')

        out.append(f"        <staff>{staff}</staff>")

        # notated ties go in <notations>, which is last
        if tie_start or tie_stop:
            tied = []
            if tie_stop:
                tied.append('<tied type="stop"/>')
            if tie_start:
                tied.append('<tied type="start"/>')
            out.append("        <notations>" + "".join(tied) + "</notations>")

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


def render_truth_pdf(truth_xml, out_pdf, musescore_bin, timeout=240,
                     expected_pages=1):
    """Render, accepting ONLY a PDF this call produced."""
    binary = (musescore_bin
              or os.path.expanduser("~/.local/opt/musescore/bin/mscore4portable"))
    last = None
    for attempt in range(3):
        tmpdir = tempfile.mkdtemp(prefix="fixture_render_")
        tmp_pdf = os.path.join(tmpdir, "truth.pdf")
        try:
            proc = subprocess.run([binary, "-o", tmp_pdf, truth_xml],
                                  capture_output=True, timeout=timeout)
            last = proc.returncode
            if proc.returncode != 0 or not os.path.exists(tmp_pdf):
                print(f"  [render] attempt {attempt + 1}: rc={proc.returncode} "
                      f"{(proc.stderr or b'')[-300:]!r}", file=sys.stderr)
                continue
            # the PDF must actually open, and match the fixture policy
            doc = pdfium.PdfDocument(tmp_pdf)
            try:
                n_pages = len(doc)
            finally:
                doc.close()
            if expected_pages is not None and n_pages != expected_pages:
                print(f"  [render] attempt {attempt + 1}: {n_pages} pages, "
                      f"fixture policy requires {expected_pages} "
                      f"(pdf_to_png reads page 1 only)", file=sys.stderr)
                last = f"page count {n_pages} != {expected_pages}"
                continue
            shutil.move(tmp_pdf, out_pdf)          # only now is it ours
            return True, proc.returncode
        except subprocess.TimeoutExpired:
            last = "timeout"
            print(f"  [render] attempt {attempt + 1}: timeout", file=sys.stderr)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
        time.sleep(2)
    return False, last


def pdf_to_png(pdf, png, dpi=300):
    doc = pdfium.PdfDocument(pdf)
    try:
        bitmap = doc[0].render(scale=dpi / 72.0)
        bitmap.to_pil().save(png)
    finally:
        doc.close()


def jpeg_roundtrip(im, quality):
    """Encode/decode as JPEG so the artefacts are real, then hand back RGB."""
    buf = BytesIO()
    im.save(buf, format="JPEG", quality=quality)
    buf.seek(0)
    with Image.open(buf) as decoded:
        return decoded.convert("RGB")


def degrade(png, out_png, mode="scan", seed=7):
    if mode not in {"none", "light", "scan"}:
        raise ValueError(f"unknown degradation mode: {mode}")
    with Image.open(png) as src:
        im = src.convert("RGB")
    rng = random.Random(seed)
    meta = {"mode": mode, "seed": seed, "in_size": list(im.size)}
    w, h = im.size

    if mode == "none":
        im.save(out_png, format="PNG")
        meta.update(out_size=[w, h], noise_operations=0)
        return meta

    if mode == "scan":
        resample = 200 / 300.0
        im = im.resize((int(w * resample), int(h * resample)), Image.LANCZOS)
        im = im.resize((w, h), Image.BICUBIC)
        blur, contrast, brightness, jpeg_q = 0.6, 0.86, 1.04, 38
        angle = rng.uniform(-0.35, 0.35)
        im = im.filter(ImageFilter.GaussianBlur(radius=blur))
        im = ImageEnhance.Contrast(im).enhance(contrast)
        im = ImageEnhance.Brightness(im).enhance(brightness)
        im = im.rotate(angle, resample=Image.BICUBIC, fillcolor=(255, 255, 255))
        # speckle BEFORE the JPEG pass, so the artefacts also bite the noise
        px = im.load()
        dots = int(w * h * 0.0015)
        for _ in range(dots):
            x, y = rng.randrange(w), rng.randrange(h)
            r, g, b = px[x, y]
            n = rng.randint(-26, 26)
            px[x, y] = (max(0, min(255, r + n)), max(0, min(255, g + n)),
                        max(0, min(255, b + n)))
        im = jpeg_roundtrip(im, jpeg_q)
        im.save(out_png, format="PNG")
        meta.update(noise_operations=dots, rotation_deg=round(angle, 3),
                    blur_radius=blur, contrast=contrast, brightness=brightness,
                    resample_ratio=resample, jpeg_quality=jpeg_q)
    else:
        blur, contrast, jpeg_q = 0.25, 0.97, 88
        im = im.filter(ImageFilter.GaussianBlur(radius=blur))
        im = ImageEnhance.Contrast(im).enhance(contrast)
        im = jpeg_roundtrip(im, jpeg_q)
        im.save(out_png, format="PNG")
        meta.update(noise_operations=0, blur_radius=blur, contrast=contrast,
                    jpeg_quality=jpeg_q)

    meta["out_size"] = list(im.size)
    return meta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/fixtures")
    ap.add_argument("--name", default="page001")
    ap.add_argument("--measures", type=int, default=4)
    ap.add_argument("--degrade", default="scan", choices=["scan", "light", "none"])
    ap.add_argument("--musescore",
                    default=os.path.expanduser("~/.local/opt/musescore/bin/mscore4portable"))
    args = ap.parse_args()
    if args.measures < 1:
        ap.error("--measures must be positive")

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
    meta.update({"truth": truth, "pdf": pdf, "clean": clean,
                 "measures": args.measures, "render_dpi": 300,
                 "musescore": args.musescore})
    with open(os.path.join(outdir, f"{args.name}_fixture.json"), "w") as fh:
        json.dump(meta, fh, indent=2)
    print(json.dumps(meta, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
