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
           ──> layout: deskew, find staff lines, group staves, group systems
           ──> [--enhance] adaptive contrast + sharpen
           ──> homr PER STAFF (transformer OMR, CPU/ONNX)   <- not per page
           ──> merge the per-staff parts back into one part
           ──> MusicXML 4.0 validation (well-formed, note counts, namespace bug checks)
           ──> join pages -> score.musicxml / score.mxl (for MuseScore)
           ──> scorehalo serve: review UI (per-page status, downloads, logs)
```

## Finding the staves

homr reads a whole page badly. On page 20 of the 1972 Carpenters book it
reports **2 staves on a page that has 8** — it merges the systems and loses
most of the music. The cause is not image quality: that page is a clean,
deskewed, 300-dpi render. The cause is that we handed a 6-system page to a
model that expects to find the systems itself.

So we find the staves ourselves, with classical CV, and hand homr **one staff
at a time** (`layout.py`):

1. deskew by dominant line angle
2. find long horizontal ink runs, merge collinear fragments
3. estimate staff spacing from the median gap between lines
4. group lines into staves
5. group staves into systems using the **vertical rules / brackets** that
   span them — gaps between staves are *not* a reliable signal, brackets are
6. crop each staff and run homr on the crop, then merge the results

Per-staff cropping on p0020 recovers **8 of 8 staves** where the whole page
yielded 2, every run `rc=0`.

### Two bugs the measurement caught

**Near-duplicate lines.** `merge_collinear` only merged candidates that
overlapped horizontally, so a beam or ledger fragment sitting *beside* a staff
line survived as a second "line". One 2px sliver was enough to corrupt the
median-gap spacing estimate for the entire page — p0022 measured **11.7px
against a true 20px**, and real 5-line staves reported 6 lines. `dedupe_slivers()`
fixes it: clean pages went from 5 of 16 to 13 of 16, and p0022 collapsed from 8
phantom staves to 1 honest staff.

**"Staff spacing is a book constant" was wrong.** It is tempting to calibrate
spacing once on a page you have verified and hard-code it. Measured across 16
pages, spacing is **16.0 to 20.5px**, clustering by *adjacent* pages
(p0024/25/26 all 16.0; p0027/28/29 all 20.0). Hard-coding p0020's 19.5px would
have broken 9 of 16 pages. Estimate per page.

### Checking the detector's own arithmetic

A real staff is 5 lines at near-equal spacing. That is a checkable constraint,
so `validate_layout.py` tests it without needing labels:

```
python scripts/validate_layout.py data/out-band --json /tmp/val.json
```

Current result over the 16 converted pages: **167 staves, 3 outliers, zero
phantoms.** All three outliers are benign — real staves carrying a ledger line
or a faded line, not bracket edges or photo borders misread as staves.

Recognising all 167 staves is driven by `scripts/per_staff_all.py`; it appends to
`per_staff.jsonl` as it goes and resumes where it left off. Current result over
the 16 converted pages: **166 of 167 staves recognised, 3,630 notes.** p0020
contributes 118 notes across its 8 staves, against 120 from the whole page.

### homr needs a patch before this works at scale

`scripts/patch_homr_title_timeout.py` (check-only by default, `--apply` to
write) fixes three ways homr's cosmetic title detection destroyed recognition
that had already completed:

- a hardcoded 60s timeout on the title future, awaited *after* recognition and
  *before* the MusicXML is written;
- a `cv2.imwrite` assertion on an empty "above the staff" region when homr's
  detected top staff has `min_y == 0`;
- a hang in native onnxruntime code inside RapidOCR that **held the GIL**, so
  Python could not even run its own `.result(60)` timeout. Five staves hung
  until our 900s watchdog killed them — ~75 minutes of wall time for metadata
  we never use.

The last one is why the third hunk simply **disables title detection** rather
than trying to bound it. `<work-title>` is the only thing it feeds, and we
join pages, so the feature is switched off instead of hardened. With it off, the
five staves that had been hanging completed in 0.4 minutes total.

The one remaining non-recognition is p0024 staff 3, where homr reports
`Found 79 staff line fragments / Found 0 noteheads`. Its geometry matches the
staves either side of it, so this is either a notehead-detection miss or a
genuinely sparse staff — `data/out-perstaff/crops/p0024.s{2,3,4}.png` settles it
by eye.

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

## Joining pages

`scorehalo join` concatenates pages into one part. homr emits each detected
staff region as its own part, and those regions run *in parallel* (same time),
so the extra ones are merged back into the matching measures.

Each merged region gets **its own staff** (3, 4, ...), not staff 2. Staff 2
already carries the base part's bass voices, and stacking a sixth voice there
makes MuseScore's importer reject the entire file (`rc=40`, no PDF at all).
Giving each region a separate staff imports cleanly with no notes lost.

The cost is a denser layout: 16 source pages render as 24, so
`scorehalo compare`'s index-based page-to-page mapping no longer lines up and
its "defect" verdicts are misalignment rather than transcription errors.

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
  - `layout.py`  CV layout: deskew, staff-line detection, sliver removal,
                 staff grouping, bracket-based system grouping, per-staff crops
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
- `scripts/validate_layout.py`  arithmetic audit of detected staves (5 lines, even spacing)
- `scripts/layout_audit.py`   detector vs homr staff counts across a run
- `scripts/crop_probe.py`     system vs per-staff crop experiment (the 8-of-8 proof)
- `scripts/per_staff_all.py`  parallel per-staff recognition over a whole run
- `scripts/annotate_layout.py` overlay staves/systems on a page for eyeballing
- `data/out-test/` example output on "Carpenters Gold Songbook 1972" pages 20-22

## Notes

- Backend: homr (AGPL-3.0), chosen because it is camera-photo/degredation
  tolerant and runs on CPU. Models download on first run (`homr --init`).
  ScoreHalo *invokes* homr as a separate process and does not vendor or link
  it, so this repo is MIT-licensed. Keep it that way: adding homr's code or
  weights here would pull the whole project into AGPL-3.0.
- homr neglects dynamics/articulation/double-sharps and some lyrics; treat
  output as a strong draft to proofread in MuseScore — same caveat as every OMR.
- The joiner canonicalizes each page to one part (staff 1 vocal + staff 2
  piano); homr's sporadic extra part is merged back as extra voice layers so no
  notes are lost and scores never carry a near-empty second part.
- Page 1 (color cover) is correctly rejected by the triage gate
  ("skip: color-photo page"); homr itself also refuses non-notation pages.