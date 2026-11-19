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

**New here?** Start with **[USER_GUIDE.md](USER_GUIDE.md)** — step-by-step
install, single pages, whole books, and an honest account of what the output
is and is not. This README is the design and measurement record.

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

Per-staff cropping on p0020 finds **9 staves** (three systems of three) and every
crop recognizes at `rc=0`, where one whole-page recognition pass returns 2 parts.
Measured 2026-11-02; the earlier "8 of 8 staves" here was wrong twice over — the
page has nine, and the number it counted was the `<staves>` declaration inside
each emitted part, not a count of staff regions. Do not read `<staves>` as a
recovery score: it is a per-part declaration defaulting to 1, so a crop with one
declared staff satisfies it by construction.

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

Result over the first 16-page run: **167 staves, 3 outliers, zero phantoms**
(the layout-healing fix that followed lifted detection to 187 staves across the
same pages). All three outliers are benign — real staves carrying a ledger line
or a faded line, not bracket edges or photo borders misread as staves.

Recognition of every detected staff is driven by `scripts/per_staff_all.py`; it
appends to `per_staff.jsonl` as it goes and resumes where it left off. Current
result over the 16 converted pages: **186 of 187 staves recognised, 3,937
notes** — the detected count rose from 167 staves when the layout-healing fix
landed. p0020 contributes 183 notes across its **9** staves (re-measured
2026-11-02; the earlier "118 notes across its 8 staves" came from the first
detector pass, which missed the page's voice staff), against 194 from a
whole-page pass.

Note counts alone do not rank the two passes: the whole-page result is 2 parts
with the singer folded into the piano, while the per-staff result keeps the voice
as its own line. Compare *structure*, not totals.

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

## From staves to songs

Recognition is per staff; a song is a page range. Three steps join the two ends
— the path that produced the six-song Carpenters deliverable (pp. 20–36):

1. **`scripts/per_staff_assemble.py`** (`build_page_graph.py` is the sibling
   entry point with the same wiring) — one page: folds the homr fragments of
   every staff into a two-part page graph (singer above piano, in the printed
   systems' brace groups), reconciles meter across fragments, then emits
   `PAGE-*.musicxml`.
2. **`scripts/stitch_songs.py`** — a song: concatenates its pages into one
   score. A part that sits a page out is padded with whole-measure rests that
   inherit that page's *per-position* meters, so a bar lengthened for one part
   stays lengthened for the tacet part too.
3. **`scripts/validate_musicxml.py`** — the gate: per-measure cursor
   arithmetic, `<staves>` vs `<staff>` references, meter and divisions
   inheritance, attributes ordering. All six delivered songs: **zero
   violations**, and all six import headlessly in MuseScore (`rc=0`).

### The barline gives before the notes do

homr occasionally writes a bar longer than its printed meter holds. The old
emitter had only one recourse — drop every note whose onset lay past the
barline and truncate the note crossing it — measured at **229 notes fully
dropped (4.3%) + 89 truncated** across the 16 pages, the worst pages losing
12–13% of their content.

`scorehalo.meter.extend_overfull_bars` now lengthens the *barline* instead: the
smallest meter that holds the bar's content (capped at 2× the page meter),
applied to every part at that bar so the parts stay aligned — nothing moves,
nothing is dropped. A bar past the cap is a misreading rather than music: it
keeps the old drop and is *reported* at assembly time. Result: **192 → 19
dropped, 88 → 11 truncated** (both confined to the two cap-exceeded bars),
**+153 sounding notes** in the delivered songs, with every part count and bar
count unchanged.

One trap worth recording: lengthen a bar and a full-bar rest can inherit a
nonstandard length (e.g. 120 ticks in a 5/4 bar). Writing that rest as an
invented tuplet put `<time-modification>` on a rest, which MuseScore rejects
outright (`rc=40`, with no message at all). Such rests now use the first-class
`<rest measure="yes"/>` form, which carries no note value for an importer to
second-guess.

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

## Clef/staff audit

`scorehalo validate --clefs dir` runs a read-only clef/staff semantic audit on
each MusicXML file, separate from structural validation. It tracks clef
declarations per measure, assigns an active clef to every pitched note by
musical onset (measure inheritance, mid-measure change, voice/`<backup>` order,
grace notes, `additional="yes"`, `print-object="no"`, octave change), and reports
findings instead of guessing:

| Finding code | Meaning |
|---|---|
| `clef_staff_invalid` | Clef references an undeclared or out-of-range staff |
| `clef_sign_invalid` | Missing or unsupported clef sign |
| `clef_line_invalid` | Clef line is not a positive integer |
| `clef_line_unspecified` | Clef sign present but line omitted (info only) |
| `clef_conflict` | Different primary clefs at the same score time — **one finding per conflicting time, not one per note** |
| `clef_unresolved` | No unambiguous active clef for a pitched note; inspect source and timing |
| `clef_note_staff_invalid` | Note references an undeclared or invalid staff |

Conflict dedupe: if measure 1 has both a G2 and an F4 clef at onset 0, that is
**one** `clef_conflict` finding, not one per note. Unresolved clefs are
deduplicated per (staff, measure).

Example output:
```
scorehalo validate --clefs dir
  p0020.musicxml clef[warning] clef_conflict: Different primary clefs at the same score time (part=P1, measure=1, staff=1)
  p0020.musicxml clef[warning] clef_unresolved: No unambiguous active clef; inspect source and timing (part=P2, measure=3, staff=1)
```

## verification.json schema

The review build at `~/scorehalo` writes a `verification.json` per run with this
schema (verified against the saved Carpenters p12 run):

```json
{
  "source_review": "not_performed",
  "joined": "score.musicxml",
  "join_error": null,
  "pages": [
    {
      "index": 1,
      "source": "/path/to/page-0001.png",
      "clefs": {
        "declarations": [
          {"part": "P1", "measure": "1", "staff": 1, "sign": "G", "line": "2", ...},
          {"part": "P1", "measure": "1", "staff": 2, "sign": "F", "line": "4", ...},
          {"part": "P2", "measure": "1", "staff": 1, "sign": "G", "line": "2", ...}
        ],
        "note_assignments": [
          {"part": "P1", "measure": "1", "onset": "0", "staff": "1", "voice": "1", "pitched": true, "clef": {...}, "clef_origin": "declared_this_measure"},
          ...
        ],
        "findings": [...]
      },
      "events": [...],
      "findings": [...]
    }
  ]
}
```

Key guarantee: **no automated check compares a transcription to the scan.**
`source_review: "not_performed"` means the accuracy claim is structural only —
note counts, clef declarations, and timing consistency are verified, but whether
the pitches match the printed page requires human review against the source
image.

`clefs.note_assignments` carries full per-note detail (clef resolution,
origin, pitch). `summary()` (used in manifests) keeps only findings and
counts — a one-page manifest is ~4 KB with summary vs ~160 KB without.

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

Current result (6-measure fixture, skew seed 7, `none`/`light`/`scan`, MuseScore
3.6.2 engraving → image): **37 of 37 notes exact in every degradation case —
100% precision, 100% recall, zero pitch errors, zero duration errors, no notes
invented or dropped.** Re-measured 2026-11-02 (run `20261101-040432-yeq95h0d`,
`data/bench/runs/`).

Superseded claim, kept so it is not rediscovered: this section used to report
"pitch 100%, duration ~89-90%, the one error class beamed eighth-note pairs read
as dotted eighths." That was **the benchmark measuring its own fixture bug**.
`make_fixture.py` decided dotting with `dotted = (beats * 2) % 2 == 1`, which
marked a half-beat eighth as dotted (notation 3/8, three ticks) while writing
`<duration>2</duration>`, and never marked a real dotted eighth. 4 of 27 fixture
notes were self-contradictory, so MuseScore engraved bars of 4.25 beats in 4/4
and the "truth" XML stopped describing the picture. Repairing the fixture took
the same recognizer from 33/38 exact (86.8% / 89.2%) to 37/37 (100% / 100%) with
the degradation untouched. Every duration figure recorded before that repair is
void; the recognizer never had that error class.

Caveat that survives: fixtures are clean engravings at moderate degradation, so
this is the easy end of the range, not real-book accuracy.

### Paired engine A/B and the picture-mask A/B

Engine agreement is not truth, but two engines disagreeing is a useful review
signal. `scripts/ab_engines.py` runs ScoreHalo and the locally patched Audiveris
on the **same** labelled fixture image, in fresh directories, checks the return
code before scoring, and records truth/image/prediction SHA-256:

```
python scripts/ab_engines.py --cases scan --measures 6 --seed 7 \
    --musescore ~/.local/opt/musescore-3.6.2/bin/mscore-362-local \
    --json reports/ab-engines.json
```

Smoke run (`20261119-074316-99pe3d2m`): both arms scored **37/37 exact**
(100% precision/recall, zero pitch or duration errors); ScoreHalo 31.1 s,
Audiveris 24.9 s. Reports: `reports/ab-engines.{json,md}`.

`scripts/ab_mask.py` is the narrower control for the advertised picture
tolerance: one labelled scan with a smooth mid-tone picture pasted below the
staves, recognized twice by the same ScoreHalo CLI, once with the default mask
and once with `--no-mask-graphics --no-photo-gate`. The gate flag is necessary
because the page gate counts picture-like pixels and would otherwise skip the
control before it could be scored. The detector's box and every SHA are in the
report.

Measurement (`20261119-080858-67wha267`): the picture covered 7.7% of the page
and was detected; both arms scored **37/37 exact**, delta **+0**
(`reports/mask-ab.{json,md}`). The A/B exposed a real ordering bug on the way:
the old `convert` gate ran *before* masking and skipped the illustrated page
outright, so masking now runs first. Covers whose picture touches the page edge
are not found by the region detector and still reach the gate unchanged.

Equal note scores do **not** prove masking harmless on real photographs, so the
production per-staff pipeline keeps `--mask-graphics` **opt-in**; only the
legacy whole-page `convert` path masks by default. No source-accuracy claim is
made by either A/B.

## Layout

- `pysrc/scorehalo/`  — the package
  - `cli.py` convert/validate/join/score/serve
  - `clefs.py` read-only clef/staff audit: declarations, per-note assignments, conflict dedupe
  - `pdf.py`  pypdfium2 rendering
  - `preprocess.py` restorer + photo/not-music gate
  - `storage.py` atomic UTF-8/JSON writes
  - `triage.py`  page statistics (colorfulness, ink, staff-line density)
  - `verification.py` timing diagnostics + clef integration + compact summary()
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
- `scripts/make_fixture.py`  labeled fixture: known music -> degraded scan PNG (also `illustration` mode with a detectable picture)
- `scripts/bench_fixtures.py` run fixtures through convert and score them
- `scripts/ab_engines.py`   paired ScoreHalo vs Audiveris note-level A/B
- `scripts/ab_mask.py`      paired picture-mask on/off note-level A/B
- `scripts/validate_layout.py`  arithmetic audit of detected staves (5 lines, even spacing)
- `scripts/layout_audit.py`   detector vs homr staff counts across a run
- `scripts/crop_probe.py`     system vs per-staff crop experiment (measures note yield; its old "recovered N of N staves" verdict was withdrawn — see above)
- `scripts/per_staff_all.py`  parallel per-staff recognition over a whole run
- `scripts/annotate_layout.py` overlay staves/systems on a page for eyeballing
- `scripts/per_staff_assemble.py` + `scripts/build_page_graph.py`  fragments -> one assembled two-part PAGE
- `scripts/stitch_songs.py`   a page range -> one song (cross-page rests inherit per-position meters)
- `scripts/validate_musicxml.py`  strict gate: cursor arithmetic, `<staves>` vs `<staff>`, meter inheritance
- `tests/`  **491 unit tests** — `.venv/bin/python -m pytest tests/ -q`
- `data/out-test/` example output on "Carpenters Gold Songbook 1972" pages 20-22

### Experimental: direct PDF geometry (not wired)

Six files from 2026-10-20 sketch a different road: read MusicXML straight out of
a PDF's vector geometry instead of running homr over rendered pixels. They are
prototypes kept for the record, and **no pipeline uses them** — nothing imports
them, no test covers them, and each one is known to fall short of its own
description:

- `pysrc/scorehalo/pdf_geometry.py` — staff lines, barlines and noteheads from
  PyMuPDF drawing commands. Requires `pymupdf`, which this repo does not
  install; the shipping renderer is pypdfium2, so this module raises
  `RuntimeError("pymupdf not available")` in the project venv.
- `pysrc/scorehalo/pdf_to_musicxml.py` — geometry to MusicXML, and the reason
  this stays experimental: it emits one `<part>` per staff rather than per
  brace group (so the part list and the parts disagree on any page with more
  than one system), caps a bar at four notes, reads page 0 only, and writes
  every note as a quarter. It is a shape, not a transcription.
- `pysrc/scorehalo/raster_geometry.py`, `pysrc/scorehalo/scanned_constraints.py`
  — raster-side helpers toward the same goal (system/staff geometry from a
  scanned page, snapping recognition output to printed system breaks).
- `scripts/pdf_exact.py` — the CLI over the above; today it exits 1 with
  "pymupdf not available".
- `scripts/omr_grade_loop.py` — the render → OMR → audit → tune loop sketched in
  earlier work: it walks a DPI ladder (150/300/450/600), records Audiveris
  acceptance per step, and writes the corrected page at the target interline
  (2026-10-23: page 19 at 300 DPI → `ACCEPTED by Audiveris (interline=20px)`,
  image written). Its earlier `--out`/`--target-interline` flags did not exist —
  the real ones are `-o/--output` and `--target` — so the write half never ran;
  that is fixed. **The audit and tune halves are still stubs:** truth pitches are
  counted but never compared, nothing is ever adjusted, and `parse_omr_grades`
  has no input, because `omr-sheet write` only verifies at `-step SCALE` and
  never exports a `.omr` for grades to be read from.

Treat them as notes toward a future exact-PDF path. They are listed here so the
next reader does not mistake them for working features.

## Notes

- Backend: homr (AGPL-3.0), chosen because it is camera-photo/degredation
  tolerant and runs on CPU. In homr 0.7.0 the ~150 MB of ONNX weights ship
  inside the PyPI wheel itself, so there is no separate `homr --init` download
  step. ScoreHalo *invokes* homr as a separate process and does not vendor or
  link it, so this repo is MIT-licensed. Keep it that way: adding homr's code
  or weights here would pull the whole project into AGPL-3.0.
- homr neglects dynamics/articulation/double-sharps and some lyrics; treat
  output as a strong draft to proofread in MuseScore — same caveat as every OMR.
- The joiner canonicalizes each page to one part (staff 1 vocal + staff 2
  piano); homr's sporadic extra part is merged back as extra voice layers so no
  notes are lost and scores never carry a near-empty second part.
- Page 1 (color cover) is correctly rejected by the triage gate
  ("skip: color-photo page"); homr itself also refuses non-notation pages.