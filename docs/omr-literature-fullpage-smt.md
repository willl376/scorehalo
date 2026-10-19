# Full-page end-to-end OMR — what the literature actually establishes

Source (read end to end, all 18 pp, extracted 2026-10-01):
`End-to-End Full-Page Optical Music Recognition for Pianoform Sheet Music`
Ríos-Vila, Calvo-Zaragoza, Rizo, Paquet — *Int. J. Computer Vision* (2026)
134:49. DOI 10.1007/s11263-025-02654-6. Published online 9 Jan 2026.
Accepted 10 Oct 2025, so it is peer-reviewed, not a preprint.
Local PDF + full text: `~/Downloads/End-to-End_Full-Page_...pdf` and
`~/Downloads/fullpage_omr_paper.txt` (80.5 KB, 1297 lines).

Code + weights are OPEN: `github.com/antoniorv6/SMT`,
HF `PRAIG/sheet-music-transformer`. Datasets on HF `PRAIG/pianoform`.

## The one-sentence result
The **first genuinely end-to-end full-page OMR** — a CNN encoder + autoregressive
Transformer decoder that takes a whole page and emits the transcription with no
layout analysis, no segmentation, no per-system slicing. It beats every
commercial and open-source OMR tool on fine-tuned real scans, and only ties the
best commercial tool when trained on pure synthetic data.

## Numbers that matter for us
Real scanned corpora, SER % (Symbol Error Rate, lower better). Fine-tuned on
<100 target samples, 5-fold CV at the *piece* level:

| Tool | Mozarteum SER | Polish SER |
|---|---|---|
| **SMT** (fine-tuned) | **14.1** | **25.9** |
| SoundSlice (best commercial) | 34.5 | 54.9 |
| PhotoScore | 70.3 | 83.3 |
| TrOMR | 25.7 | 53.2 |
| Oemer | 90.3 | 75.2 |
| Audiveris | 94.6 | 94.9 |

Audiveris — the tool in memory §18 as "does great on clean scans" — scores
**94.6 / 94.9** here, and its LER is a flat 100.0 on both. That is not a bug in
their setup; it is the field's measured baseline for pianoform. Note the
0-shot SMT column too: 38.9 / 65.4, i.e. without any target data it merely
*ties* SoundSlice. The win comes from fine-tuning.

## The four ideas worth stealing
1. **Humdrum Kern, not MusicXML, as the model's output.** MusicXML is "too
   verbose" and needs lossy intermediate conversion. `kern` has spines
   (tab-separated, simultaneous = polyphonic), so voice handling is *native* —
   and it converts onward to MusicXML with the `converter` library. Our whole
   stack is MusicXML-shaped; this is the strongest argument yet for a kern
   intermediate.
2. **bekern tokenization beats graphic-faithful ones.** Splitting `8cc-/L` into
   `8c · ·−· /· L` (duration, pitch, …) cut vocabulary from 20,578 → 184 and gave
   the best SER (6.9 vs 9.7 for raw kern) — *despite* lower correlation with the
   image. Semantic-aware tokenization wins over image-shaped tokens.
3. **Curriculum learning in three stages.** (a) pre-train on single *systems*,
   (b) incrementally grow pages from 2 systems to n while randomizing the count
   to avoid catastrophic forgetting, (c) fine-tune on a synthetic/real mixture
   with a linear probability ramp. Ablation: without (a) SER 42.6, without (b)
   12.4, without both 71.5, with both **6.9**. Stage (a) alone is worth 77%.
   Their (a) is exactly our homr pipeline's *only* supported mode — which is why
   we cannot get page-level global structure from it at all.
4. **2D positional encoding is mandatory, not a refinement.** No PE → SER 78.4.
   1D PE → 15.6. **2D PE → 6.9.** With polyphony you must preserve the vertical
   axis; flattening to 1D throws away staff order. 55.7% better SER than 1D.

Backbone: ConvNeXt (first 3 layers only, depths 64/128/256), 8-layer decoder,
4 heads, embed 256. Swin Transformer **failed to converge** even on the easiest
case — data-hungry and the corpora lack resolution. Decoder resamples h/16, w/8.

## Generalization behaviour (the honest caveat)
SER by (max systems trained) × (systems in test page): trained on 1 system, a
2-system page scores **48.7**. Trained on 5, it stays under 9 for 1–4 systems,
and degrades *sub-linearly* to 35.3 at 8 systems — trained on fewer systems than
the test page never collapses. For us: **systems-per-page is a capacity dial, and
over-provisioning degrades gracefully while under-provisioning fails hard.**

## What it does NOT solve — the part that matters for Mozart two-piano
- It evaluates on **pianoform = 2-staff grand staff** only. Every corpus is
  Beethoven/Chopin/Mozart solo piano. **No multi-instrument, no >2 staves, no
  two-piano, no manuscript.** So this paper does not answer our four-staff
  question; it sidesteps it by never testing it.
- Its own qualitative result: on a zero-shot transcription "all the voices are
  correctly placed, since its training is specific for pianoform scores" — but
  **all slurs are skipped and dots are completely skipped**, because those tokens
  are absent from GrandStaff. A known-vocabulary blind spot, same class as
  homr's missing dynamics/lyrics. Fine-tuning fixes it (0-shot → fine-tuned SER
  38.9 → 14.1).
- Recognition is non-semantic: CER/SER/LER on edit distance only. A page can
  score well while the *music* is wrong — precisely the failure mode in memory
  §34 (correct note count, Piano II folded into Piano I).

## Verdict for ScoreHalo
This **validates the project thesis and the homr diagnosis simultaneously.**
We were already building the "restore the page, then read it semantically"
concept; here is the strongest published evidence that (1) full-page end-to-end
reading beats system-slicing, and (2) the reason homr cannot is that it is
stuck at stage (a) of this curriculum and hard-aliased to 2 staves — not that
the approach is wrong.

Actionable, and worth doing in this order:
1. **Read the paper's kern output on the exact page we already have**, once. Our
   MusicXML→kern path does not exist yet, but reading `SMTNeXt`'s kern for
   `out-adele/p0003` would tell us whether a page-level model places voices
   correctly on OUR kind of scan. Highest information per unit of effort.
2. **Adopt the metric, not just the model.** SER/LER on a kern conversion is a
   better yardstick than our pitch/duration differ and would let us compare
   against published numbers. Needs a MusicXML→kern converter step.
3. **Adopt the bekern lesson in principle**: our measure/duration differ keeps
   being defeated by damaged tuplet spellings. Encoding *semantics* first and
   notation second sidesteps representation failures.
4. Consider 2 staves/system over 4 where the music allows — the curriculum
   matrix says over-provisioning is cheap.

Licence: article CC-BY-4.0 (Springer terms apply to the PDF as fetched).
Quote freely with attribution; do not republish the PDF itself.
