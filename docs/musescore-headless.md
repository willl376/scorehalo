# MuseScore 3.6 as a headless oracle and renderer

Date: 2026-10-17
Binary tested: `MuseScore-3.6.1.515740129-x86_64.AppImage` (x86_64, "MuseScore
3.6-portable")

**Verification status: EVERYTHING BELOW WAS RUN on this machine.** The numbers
are timings and outputs measured here, not read from the source. Where a fact
depends on this binary's version, the version is named.

---

## Why it is here

We cannot look at a page. MuseScore can. It is an independent MusicXML
implementation, and two of its modes give us something our own tests cannot:

| Our own check | `validate_musicxml.py` | `tests/test_emit_invariants.py` | **MuseScore** |
|---|---|---|---|
| runs in the test suite | yes | yes | no |
| sees `<duration>` arithmetic | yes | yes | yes |
| sees pitch/voice loss on import | no | no | **yes** |
| fails on *nominal* over-rhythm (`<type>`) | no | partly | **yes** |
| renders a PDF | no | no | **yes** |

The class of bug it caught that nothing else caught: a bar whose notes each
carry a `<duration>` the barlines agree with, but whose `<type>` values sum
longer than the meter. Every cursor-based check passes; MuseScore flags it.
That was 8 of 16 pages of this songbook.

---

## Getting it to run

The AppImage runs directly (this machine has `libfuse2`); nothing is installed
and no page size is written:

```
DISPLAY=:0 ~/Downloads/MuseScore-3.6.1.x86_64.AppImage -o out.pdf in.musicxml
```

`DISPLAY` must be a real X. MuseScore 3's Qt build ships **only xcb**, so:

* `DISPLAY=:0` — works (X11 here).
* `QT_QPA_PLATFORM=offscreen` → **fails**, no engine for it.

Two lines of stderr are always there and are always benign:

```
/lib/x86_64-linux-gnu/libjack.so.0
/lib/x86_64-linux-gnu/libnss3.so
```

## Export formats (`-o`)

The format is chosen by the extension. Measured file sizes, one 16-bar
single-system page:

| `-o …` | produced | note |
|---|---|---|
| `.musicxml` | MuseScore's own MusicXML | the round-trip artefact |
| `.mid` | Standard MIDI, format 1, 3 tracks, 480 tpqn | |
| `.pdf` | PDF 1.4, 1 page | `-P` also exports every part |
| `.png` | **`out-1.png`** | page-suffixed; `-r DPI` sets resolution, `-T` trims |
| `.mlog` | `{"result":0}` | the sanity check, see below |

## The `.mlog` oracle

`-o file.mlog` writes a JSON verdict, and it is the only output we read for
correctness:

* clean → `{"result":0}`
* otherwise → `{"result":1,"error":"Measure 6, staff 3 incomplete.
  Expected: 4/4; Found: 12/8"}`

Two facts make it usable:

1. **Staff numbers are score-global** (`parts` concatenated), not per-part.
2. **It only flags overfull bars.** A short bar passes silently, so an
   experiment whose file is short "passes" vacuously. Two earlier conclusions
   in this repo were invalidated by exactly that; both were re-run here.

Reading it: the nominal value of a note is its written value (`<type>`+dots)
scaled by `<time-modification>`; `<duration>` is not what is summed. That is why
a file can be barline-perfect and still be flagged.

**The process exit code is not the verdict.** An import that fails still writes
the log and still exits 0. Always parse the log.

## The conversion job (`-j`)

`-j` takes **one JSON file holding an ARRAY** of `{"in":…,"out":…}`:

```json
[{"in":"p0020.musicxml","out":"p0020.mlog"},
 {"in":"p0021.musicxml","out":"p0021.pdf"}]
```

* a bare object (not an array) fails: `json file <…> is not an array`
* every pair runs in ONE process — 16 pages in ~8 s here, against ~1 min each
* the runner **stops at the first job it calls failed**:

```
convert <p0020.musicxml>...
	to <c000.mlog>
... success!
convert <p0021.musicxml>...
	to <c001.mlog>
... failed!job     ← and p0022, p0024 … are never attempted
```

So a run is a prefix, not a sweep: the failed file's own log IS written, and
everything after it was never read. Treating the missing logs as "clean" — or
as "unreadable" — is wrong in both directions. `scripts/musescore_check.py`
walks the ladder: offer the whole list, keep the logs returned, re-offer the
tail, until every file has been attempted. A book with 10 bad files takes ~15 s.

## What the checker is not

* It does not verify pitches against the page (nothing can but eyes).
* It does not speak for MuseScore 4 (`-o .musicxml` from 4.x imports
  differently; this is 3.6.1).
* Its bar-length rule is MuseScore's, not the standard's. When our file is
  genuinely right — self-consistent in `<duration>` and `<type>` — and MuseScore
  still complains, the honest reading is "MuseScore's importer disagrees",
  and that is a fact worth having, not a verdict worth obeying.

## Usage in this repo

```
scripts/musescore_check.py <file.musicxml>…      # or --all DIR
scripts/musescore_check.py --json …              # for a caller
```

Exit 0 iff every file is clean. `--all DIR` is directly below DIR. Env:
`MUSESCORE_APPIMAGE`, `MUSESCORE_DISPLAY`.

The round-trip evidence used here was produced the same way:

```
timeout 180 MuseScore-…AppImage -o rt.xml grid-p0020.musicxml   # then compare
```

which is how we found that MuseScore *dropped notes* (2 pitches of 128) when a
part asked for more than four voices on a staff.

---

## MuseScore 4 (Flatpak) — the second engraver

`org.musescore.MuseScore` **4.7.5** (flathub, system install) is also here:

```
flatpak run org.musescore.MuseScore -o out.pdf in.musicxml
```

Three differences from the MS3 AppImage, all measured on this machine:

* **No `DISPLAY` needed.** MS4 selects a Qt platform itself; MS3's AppImage is
  xcb-only and fails without a real X. (MS4 still prints one harmless line
  about the session manager socket.)
* **The sandbox has a private `/tmp`.** `-o /tmp/x.pdf` writes **nothing** and
  exits 0 — the same silent-success failure this project keeps meeting. Write
  the output beside the input, inside the home directory.
* Two inspection flags MS3 3.6 does not have. `--score-meta FILE` prints the
  model MuseScore built, as JSON:

  ```json
  {"metadata": {"measures": 9, "keysig": -3, "pages": 1,
                "parts": [{"id": "1", "name": "Voice", "instrumentId": "voice",
                           "hasPitchedStaff": "true"},
                          {"id": "2", "name": "Piano", "instrumentId": "piano"}]}}
  ```

  so "did the Voice survive the import, and is it still a pitched part" is
  answerable without reading pixels. `--score-elements FILE` prints a JSON
  array of every element with its staff index, measure index, beat and voice
  (`{"beat":0,"measureIdx":0,"name":"Treble clef","staffIdx":0,...}`) — the
  per-staff view of what was actually imported.

Round-trip through MS4 (`-o rt.musicxml in.musicxml`, then parse both): the
structure survives — the same two parts, Piano still `<staves>2</staves>`,
Voice still one staff and 12 measures. Note counts rise a little
(p0022: Voice 39 → 42 notes, Piano 138 → 145) because MS4 *fills the
incomplete bars* our recogniser left behind; the sounding-note count is
unchanged (33 and 135). So a round-trip difference in note totals is expected
and is not note loss.

## The render oracle: count what the engraver *prints*

`.mlog` judges the file (overfull bars). The render judges the page. The method
that survived scrutiny here:

```
MS3/MS4 -o page.pdf grid-pNNNN.musicxml
pdftoppm -gray -r 150 page.pdf page
layout.analyze_page(gray)      # the SAME analyser that reads the scans
```

Using the project's own `analyze_page` is the point: the count is produced by
the same geometry that reads the printed book, so "MuseScore printed 3 staves
per system" and "the scan shows 3 staves per system" are directly comparable.
Result for the songbook pages of "We've Only Just Begun" (2026-10-17), staff
count per system, every page of every flow:

| page | MS3 3.6.1 | MS4 4.7.5 |
|---|---|---|
| p0020 | 6 staves `[3,3]` | 6 staves `[3,3]` |
| p0021 | 12 staves `[3,3,3,3]` | 9 staves `[3,3,3]` |
| p0022 | 9 staves `[3,3,3]` | 9 staves `[3,3,3]` |
| p0033 | 9 staves `[3,3,3]` | 9 staves `[3,3,3]` |
| p0035 | 12 + 3 staves `[3,3,3,3]`,`[3]` | same |

`{"result":0}` from `.mlog` for all five files. The number of *systems* is the
engraver's business (it re-flows bars onto its own page); what must hold is
that **every system it prints carries the Voice staff and the two piano
staves**, and both engravers agree that it does.

Three traps, each hit once before the method above was settled:

* **MS3's PNG export is black.** Export PDF and rasterise it; do not count the
  PNG.
* **A hand-rolled counter lies on the last system.** A counter that counts rows
  whose ink covers >50% of the page width reported p0020 as **6** staves where
  MS3 printed **9**: the last system on a page is often *unjustified* (it stops
  at x≈678 of 2550, row coverage 0.495), so every staff in it fell under the
  threshold. `analyze_page` finds staff *lines*, not covered rows, and counts 9.
* **The engraver re-paginates the flow**, so one input page can print as two
  (p0035 above). Measure every page, not the first one.

What this oracle is **not**: it is structural (staves per system, parts
present), not a pitch oracle. It cannot say the notes are right — only that
the layout a reader sees carries every part on every system.
