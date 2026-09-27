# ScoreHalo

Photo/graphics-tolerant Optical Music Recognition (OMR): turn real-world sheet
music — degraded scans, camera photos, songbook pages with embedded pictures —
into MusicXML that MuseScore can open, with a review UI and placeholder-aware
output.

The "different concept" vs Audiveris: instead of decomposing the page into
staff/symbol geometry first (the step that breaks when a page carries
photographs or decorative graphics), ScoreHalo (1) restores and classifies the
page, (2) skips or masks non-music content, and (3) hands the page to a
warp-tolerant transformer engine (homr) that reads notation semantically.

## Pipeline

```
PDF/image ──> render (≥300 dpi, pypdfium2)
           ──> triage gate (photo/cover/blank pages skipped, staff-likeness scored)
           ──> [--enhance] adaptive contrast + sharpen
           ──> homr (transformer OMR, per page, CPU/ONNX)
           ──> MusicXML 4.0 validation (well-formed, note counts, namespace bug checks)
           ──> join pages -> score.musicxml / score.mxl (for MuseScore)
           ──> scorehalo serve: review UI (per-page status, downloads, logs)
```

## Usage

```
scorehalo convert <file.pdf|image.png> --pages 20-22 --out dir [--enhance] [--dpi 300]
scorehalo validate dir          # re-check outputs
scorehalo join dir              # (re)assemble score.musicxml + score.mxl
scorehalo score --truth ref.musicxml --pred dir/p0020.musicxml
                                 # note-level accuracy diff (needs labels)
scorehalo audit dir [--top N] [--json out.json]
                                 # label-free structural audit (no labels needed)
scorehalo serve dir --port 8001 # review UI at http://127.0.0.1:8001
```

Crash-safe: convert resumes by skipping pages already present in manifest.json.

## Auditing without labels

`scorehalo audit` checks a converted directory for structural defects that need
no ground truth: per-voice measure arithmetic, declared vs. inferred meter, voice
collisions, clef/content mismatch, outlier pitches, and adjacent duplicate
notes. It is control-validated against a known-good MuseScore-authored fixture,
which must come out clean.

On the 17-page Carpenters band it reports 283 measures whose per-voice note totals
don't add up, 9 pages with no declared time signature, 13 voice collisions and 8
clef/content mismatches. Two things worth knowing when reading the output:

- `duplicate_adjacent` is a weak signal (severity 1) — real music repeats notes.
- Raw single pages import into MuseScore fine while the *joined* score fails
  (`rc=40`), so per-page measure damage is not by itself fatal; the failure lives
  in the joiner's merge. Any fix belongs there.

## Measuring accuracy

`validate` only proves the MusicXML is well-formed, not that the notes are
right. For that you need labels, and the only trustworthy labels are ones you
control, so the repo can manufacture them:

```
python scripts/make_fixture.py --out data/fixtures --name page001 \
    --measures 6 --degrade scan     # known music -> MuseScore PDF -> scan-like PNG
python scripts/bench_fixtures.py --cases none,light,scan --measures 6
```

`make_fixture.py` engraves a piece whose notes are known exactly, renders it
at 300 dpi with MuseScore, then degrades it (200-ppi resample, blur, JPEG
artifacts, skew, speckle) to imitate the 1972 photocopy scans.
`bench_fixtures.py` pushes each fixture through the real `convert` path and
reports the substitution classes: exact, right-pitch-wrong-duration,
wrong-pitch, missed, spurious.

Current result (6- and 8-measure fixtures, several skew seeds): pitch 100%,
duration ~89-90%, no notes invented or dropped, and unchanged when the image
loses 35% of its edge energy. The one error class is beamed eighth-note pairs
read as dotted eighths. Caveat: fixtures are clean engravings at moderate
degradation, so this is the easy end of the range, not real-book accuracy.

## Layout

- `pysrc/scorehalo/`  — the package
  - `cli.py` convert/validate/join/score/serve
  - `pdf.py`  pypdfium2 rendering
  - `preprocess.py` restorer + photo/not-music gate
  - `triage.py`  page statistics (colorfulness, ink, staff-line density)
  - `engine.py`  homr wrapper
  - `validate.py` MusicXML safety checks
  - `join.py`    per-page -> one score: canonical 2-staff single part (vocal
                 staff 1 + piano staff 2, extra voice layers merged, nothing
                 dropped), + minimal valid .mxl
  - `ui.py`      stdlib review web UI
  - `score.py`   note-level accuracy diff (pitch/duration, chord-order blind)
  - `compare.py` MuseScore faithful-twin render + structural diff vs source pages
- `scripts/make_fixture.py`  labeled fixture: known music -> degraded scan PNG
- `scripts/bench_fixtures.py` run fixtures through convert and score them
- `data/out-test/` example output on "Carpenters Gold Songbook 1972" pages 20-22

## Notes

- Backend: homr (AGPL-3.0), chosen because it is camera-photo/degredation
  tolerant and runs on CPU. Models download on first run (`homr --init`).
- homr neglects dynamics/articulation/double-sharps and some lyrics; treat
  output as a strong draft to proofread in MuseScore — same caveat as every OMR.
- The joiner canonicalizes each page to one part (staff 1 vocal + staff 2
  piano); homr's sporadic extra part is merged back as extra voice layers so no
  notes are lost and scores never carry a near-empty second part.
- Page 1 (color cover) is correctly rejected by the triage gate
  ("skip: color-photo page"); homr itself also refuses non-notation pages.