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
scorehalo serve dir --port 8001 # review UI at http://127.0.0.1:8001
```

Crash-safe: convert resumes by skipping pages already present in manifest.json.

## Layout

- `pysrc/scorehalo/`  — the package
  - `cli.py` convert/validate/join/serve
  - `pdf.py`  pypdfium2 rendering
  - `preprocess.py` restorer + photo/not-music gate
  - `triage.py`  page statistics (colorfulness, ink, staff-line density)
  - `engine.py`  homr wrapper
  - `validate.py` MusicXML safety checks
  - `join.py`    per-page -> one score: canonical 2-staff single part (vocal
                 staff 1 + piano staff 2, extra voice layers merged, nothing
                 dropped), + minimal valid .mxl
  - `ui.py`      stdlib review web UI
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