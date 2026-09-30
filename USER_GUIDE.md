# ScoreHalo — User Guide

Step-by-step instructions for turning scanned or photographed sheet music into
MusicXML you can open, edit and print in MuseScore.

**Read section 8 before you rely on the output.** ScoreHalo produces a strong
first draft, not a publishable edition. It is honest about its mistakes, and
so is this guide.

---

## 1. What ScoreHalo actually does

Most optical music recognition (OMR) software decomposes a page into staff
lines and symbols first. That step is what fails on a real songbook page with a
photograph, a coloured border, or a decorative title — the "geometry" of the
page stops being music, and the recogniser silently throws music away.

ScoreHalo takes a different route:

1. **Render** the page cleanly (at least 300 dpi) from your PDF or image.
2. **Triage** the page — detect and skip photographs, cover pages and blank
   pages instead of trying to read music that isn't there.
3. **Find the staves with classical computer vision**, not with the AI — so a
   page with a picture in it is not misread as having extra staves.
4. **Hand one staff at a time to `homr`**, a warp-tolerant OMR model that reads
   notation semantically.
5. **Validate** the MusicXML and join the pages into one score.

The practical consequence: pages with photos and graphics survive instead of
destroying the music around them.

---

## 2. What you get

| File | What it is |
|---|---|
| `p0001.musicxml` … | one MusicXML file per source page |
| `score.musicxml` | all selected pages joined into one score |
| `score.mxl` | the same thing, compressed — **this is what you open in MuseScore** |
| `manifest.json` | per-page record: status, note counts, measures, warnings |
| `render/` | the cleaned page images that were actually read |
| `enhanced/` | only with `--enhance`; the contrast-sharpened images |

---

## 3. Requirements

- **Python 3.12 or newer.** Check with `python3 --version`.
- **About 1 GB of free disk** — the virtual environment is roughly 650 MB once
  installed, plus room for page images.
- **MuseScore 4** — only if you want to render results to PDF, or play them
  back. Not needed to produce MusicXML.
- **No GPU.** It runs on CPU. Expect roughly **1–3 minutes per page** on a
  modern laptop; more on older or slower machines.

The OMR model does **not** need a separate download. About 150 MB of ONNX
weights (a transformer encoder and decoder plus a staff-segmentation network)
are bundled inside the `homr` package and arrive with `pip install`.

Everything installs into a Python virtual environment, so nothing touches your
system Python.

---

## 4. Install

Get the code, then install it into its own environment:

```bash
git clone https://github.com/willl376/scorehalo.git
cd scorehalo
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e ./pysrc
```

That installs ScoreHalo and the bundled OMR model — allow a few minutes while
it downloads Python packages. Nothing else is needed; there is no separate
model download step.

```bash
cd ~/scorehalo
source .venv/bin/activate
```

You can now run `scorehalo`. To confirm:

```bash
scorehalo --version
```

<details>
<summary>Prefer not to activate the environment every time?</summary>

Run it without activating, using the full path:

```bash
~/scorehalo/.venv/bin/scorehalo --help
```
</details>

<details>
<summary><code>python3 -m venv</code> fails on Debian/Ubuntu</summary>

That means the `venv` module is not installed. Install it with
`sudo apt install python3-venv`, then repeat step 4.
</details>

---

## 5. First run — a single page

Pick any page from your book and convert it:

```bash
scorehalo convert "path/to/your/book.pdf" --pages 20 --out ./out-test
```

Check what came out:

```bash
scorehalo validate ./out-test
```

Then open it in MuseScore:

```bash
xdg-open ./out-test/score.mxl      # Linux
open ./out-test/score.mxl          # macOS
```

Add `--enhance` if your pages are faint, greasy, or low-contrast:

```bash
scorehalo convert "book.pdf" --pages 20 --out ./out-test --enhance
```

> **Note:** `--enhance` sharpens and raises contrast. It helps genuinely poor
> input. On already-clean input it can invent harsh edges, so compare both ways
> before applying it to a whole book.

---

## 6. Whole books

### Select the pages you want

```bash
scorehalo convert "book.pdf" --pages 20-36 --out ./out-book
```

Page specifications accept ranges and lists together: `--pages 3-8,16,20-22`.

> **Scan a few pages first.** Two or three consecutive pages, checked by eye,
> costs a few minutes. Discovering a systematic problem on page 20 of 200 costs
> an evening. Do the small run first.

### Join them

```bash
scorehalo join ./out-book
```

This writes `score.musicxml` and `score.mxl`.

### If the book is an anthology (many different songs)

**Give each song its own output directory and join only that song's pages.**

```bash
scorehalo convert "book.pdf" --pages 20-22 --out ./out-song1
scorehalo convert "book.pdf" --pages 40-41 --out ./out-song2
scorehalo join ./out-song1
scorehalo join ./out-song2
```

Joining pages from different songs treats unrelated music as one continuous
composition and produces nonsense. A printed page may even belong to two
different pieces, so check the page boundaries against the contents page.

### Resume after an interruption

`convert` is crash-safe. It skips pages already recorded in `manifest.json`, so
if it dies, run the identical command again and it picks up where it stopped.
Delete a page's entry from `manifest.json` to redo that page.

---

## 7. All commands

| Command | What it does |
|---|---|
| `scorehalo convert FILE --pages N-M --out DIR` | Read pages into MusicXML |
| `scorehalo validate DIR` | Check the MusicXML for structural faults |
| `scorehalo join DIR` | Join pages into `score.mxl` |
| `scorehalo audit DIR` | Deep structural audit — **no labels needed** |
| `scorehalo serve DIR --port 8001` | Review UI at `http://127.0.0.1:8001` |
| `scorehalo hear FILE` | Play the result back through your speakers |
| `scorehalo score --truth REF --pred PRED` | Note-by-note accuracy against known-good |
| `scorehalo compare DIR` | Compare page images against a MuseScore render |

Useful `convert` options: `--dpi` (default 300), `--enhance`, `--timeout`
(seconds per staff, default 900).

---

## 8. Honest limitations

**Please read this section. It is the difference between a useful tool and a
trap.**

### Output is a draft

The output is a **strong first draft for proofreading in MuseScore**, not a
finished edition. Do not print from it as though it were a clean copy.

### Measured defects in real output

Running the auditor over a 16-page run of a 1972 photocopied songbook reported:

| Defect | Extent |
|---|---|
| Measures whose notes don't add up to the time signature | 544 flags across 17 pages |
| Unbalanced slurs (more starts than stops) | **every page** |
| Two voices colliding on the same beat | 26 flags |
| `<time-modification>` with no `<type>` | 11 measures |

The last one matters: **those 11 measures will crash `musicxml2ly`**, the
MusicXML-to-LilyPond converter. If you plan to export via LilyPond, run
`scorehalo validate` first and fix the reported measures.

Unbalanced slurs are the most widespread fault. They are usually survivable in
MuseScore, which is forgiving — another reason to proofread rather than trust.

Manifest note: a page's `status` is `ok` when its MusicXML validates, or
`validate-warn` when it was produced but carries warnings — the file is still
there and still worth reviewing.

### What accuracy is actually known to be

On **clean, digitally engraved** input, measured note-by-note against known
ground truth:

- **Pitch: 100% correct**
- **Duration: ~89–90% correct** — the errors are beamed eighth pairs read as
  dotted eighths
- No notes invented or dropped

**That is the easy end of the range.** Those pages were engraved cleanly and
then deliberately degraded. A real 200-pii photocopy with bleed-through,
skew and fading is a harder problem and **has not been fully measured yet**.
Treat the figures above as an optimistic bound, not an expectation.

### `scorehalo compare` cannot judge correctness

The `compare` command measures gross image properties — ink coverage,
staff-band counts, how evenly ink is spread across quadrants. It is a
**triage pointer**, useful for finding the pages that look unlike the rest of
the book.

It is **nearly blind to two very common faults**, measured on deliberately
broken pages:

| Fault | Detected |
|---|---|
| Notes lost | 5 of 6 |
| Notes duplicated | 6 of 6 |
| Far too much music invented | 6 of 6 |
| **Pitches read at the wrong interval** | **1 of 6** |
| **Page transcribed as empty** | **1 of 6** |

A note read one semitone too low leaves the note count, its staff position and
the ink coverage essentially unchanged. No ink-based measurement can see it.
This is a property of measuring images, not a bug awaiting a fix.

**So a "clean" verdict from `compare` does not mean your music is correct.**
It means the page is not visually unusual. A human reading the score is still
the last line of defence.

### When to trust what

| Question | Ask |
|---|---|
| Is the file structurally valid? | `scorehalo validate` |
| Is anything obviously broken? | `scorehalo audit` |
| Is this page visually unusual? | `scorehalo compare` |
| **Are the notes actually right?** | **Your eyes, in MuseScore** |

---

## 9. Troubleshooting

**`scorehalo: command not found`**
The environment is not active. Run `cd ~/scorehalo && source .venv/bin/activate`,
or call `~/scorehalo/.venv/bin/scorehalo` by full path.

**The very first run takes noticeably longer**
It is compiling Python bytecode and loading the ONNX model into memory. This
happens once; later runs start immediately.

**A page converts but produces no music**
Check `manifest.json` for that page. If it was classified as a photograph or
cover page, that is the triage gate working as intended — a page with a large
photo is skipped rather than mangled. Force it through by removing its
`manifest.json` entry and re-running.

**MusicXML imports into MuseScore but sounds wrong**
Almost always the `<time-modification>`/`<type>` fault, or measures that
overflow. Run `scorehalo audit DIR` and read the top suspects.

**The joined score will not open but individual pages do**
A known interaction: pages can each be valid while the join produces a
sequence MuseScore rejects. Work with the individual `p00NN.musicxml` files, or
join a smaller range to isolate the offending page.

**Recognition is very slow**
Per-staff recognition on a 6-system page is the expensive step. Raising
`--timeout` will not speed it up; it only prevents the watchdog from giving up.
Run fewer pages at a time, and leave the machine otherwise idle.

---

## 10. For developers

```bash
# tests
.venv/bin/python -m unittest discover -s tests

# accuracy against known-good labels
.venv/bin/python scripts/bench_fixtures.py --cases none,light,scan --measures 6

# measure what the comparison tool can and cannot detect
.venv/bin/python scripts/bench_compare.py
```

Layout notes are in `README.md`; that file also records the design decisions
and the measurements behind them.

---

## License

See `LICENSE`.
