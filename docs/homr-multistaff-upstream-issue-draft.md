# Issue #7 / #62 — Support multi-staff (two-piano, >2 staves per system)

HOMR currently collapses any system with four staff positions (e.g. Piano I
Treble/Bass + Piano II Treble/Bass) into a single part with `<staves>2</staves>`,
silently aliasing Piano II onto Piano I. This produces a schema-valid MusicXML
file that is musically wrong while reporting no error.

## Reproduction (minimal)

Synthetic 4-staff example with positions {upper, lower, upper2, lower2}:
- 16 notes in → 16 notes out, but `part` count = 1, `staves=2`, notes split
  `{'1': 8, '2': 8}` (Piano II interleaved into staff 1/2). No notes dropped.

2-staff positive control: `{'upper','lower'}` → 1 part, 2 staves, all correct.

The repro script: [proof_four_staves.py](https://raw.githubusercontent.com/willl376/homr-audit/main/tmp/opencode/homr_audit/proof_four_staves.py)
in the audit repo.

## Root cause

1. Vocabulary defines four positions: `upper`, `upper2`, `lower`, `lower2`
   (`homr/transformer/vocabulary.py`).
2. Brace pairing uses `_score_brace_with_staff_pair()` which scores each adjacent
   pair. A hard geometric veto (returns 0 if the brace crosses the midpoint
   toward a neighbour) means a single brace spanning 4 staves cannot form any
   pair. All 4 staves remain one `MultiStaff` and reach `generate_xml()` as one
   generator part.
3. Staff mapping is hard-coded to 2: `get_staff()` returns `2 if is_lower_position(s)
   else 1`, so `upper2 → 1`, `lower2 → 2`. Voice numbering already supports N staves
   correctly (`staff 3 → voices 9-12`, `staff 4 → 13-16`), only the staff map is
   limited to 2.

This explains the v0.5.0→v0.6.0 grand-staff regression (#103/#104/#105): the
pairing changed to be stricter and the aliasing went from detectable to silent.

## Suggested path (minimal, correct)

1. **Emit N staves per part, not 2.** In `homr/music_xml_generator.get_staff()`
   map `upper→1, upper2→3, lower→2, lower2→4` (or match the staff order in the
   system). Also emit `<staves>4</staves>` (and correct `<staff-details>`).
2. **Warn loudly, not silently.** Added warning when a part contains >2 staff
   positions: emitted in the current tree, so failures are visible instead of
   musically corrupt. (This is included in the branch used for verification.)
3. **Treat pairing as a scoring problem.** Prefer two-of-four over none when a
   brace spans all four (ambiguity noted by #62). The hard veto could be relaxed
   to prefer the highest-scoring pair set instead of rejecting all pairs outright.

## Notes on export

With 4 staves in one part, MuseScore needs `<staves>4</staves>` with
`<staff-details>`. If you prefer two independent grand-staff parts (Piano I/Piano
II) the grouping should be encoded as `<group>/<bar-group>`; the one-part
representation is also valid but needs correct staff counts.

## Verification

- 2-staff positive control: correct.
- 4-staff: notes preserved, but aliased until step (1) is applied. Warning
  emitted. Schema-valid both ways.

This matches the maintainer's own assessment (#7, #62) that grand-staff
detection is "just improvised" and needs a proper hierarchical treatment (Score →
GrandStaff → Staff).

## References

- homr-audit (private) contains full proof, scripts, and trace: `proof_four_staves.py`,
  `homr/music_xml_generator.py` (warning change)
- Upstream issues: #7, #60, #62, #103–#105, #142, #150

---

## A third path into the same silent collapse: an interrupted staff line (2026-10-17)

The failure above is about *how many* staves a system has. Here is the same
outcome — a system emitted as one part with the singer's staff gone — from how
*continuous* one staff line is.

Target: page 22 of the Carpenters songbook (300 dpi). The scan has a blank
patch straight through the page's third system: a white band ~120 px wide
across all three staves (a hole in the print, present in the source PDF). Every
staff line in that system therefore arrives in two halves, ~100 px apart, at the
same y. Nothing else about the page is unusual:

* our own analyser reads the band as **3 staves / 1 system** (staff spacing
  20.5 px, same as the other three systems on the page);
* homr's log for that crop:

```
Found 306 staff line fragments
Found 34 clefs
Found 6 staffs
Found 4 connected staffs (after merging grand staffs, multiple voices):  [1, 1, 1, 1]
```

i.e. **six staffs where three are printed**, four "connected" groups, and the
output is one part with **9 notes** and rests. The printed system has Voice
(treble, above) plus a braced two-staff Piano, ~48 notes; the other three
systems of the same page read correctly as 3 staffs → 2 parts (53, 44, 42
notes). One system of four loses the singer.

Reproduced without the scan, holding everything else fixed: take the same crop
and draw the staff-line rows shut across the break (fill only the detected line
rows, only between that line's own two pieces) and re-run the reader:

```
Found 3 staffs
Found 1 connected staffs (after merging grand staffs, multiple voices):  [2]
```

→ **2 parts, 48 notes**. Same pixels for the notation; one unbroken rule.

So the staff model is not only "how many staves", it is inferred from
*continuity* — and a scan defect that a human reader ignores (your eye bridges
a 100 px gap in a staff line without noticing) becomes a structural rewrite of
the system, silently and schema-validly. A reader that bridged small collinear
gaps in its own detected rules before counting staves would be immune to both
this and most of the scan defects in this book (the same healing also fires on
4 of the other 5 pages measured here, 60 px to 16 k px of ink restored).

Downstream workaround used in this repo (scorehalo, `layout.heal_line_gaps`):
heal detected rules on the page before cropping, so the reader receives an
unbroken staff, and record the amount healed per page.

---

## FILED as upstream issue #172 (2026-10-01)

`https://github.com/liebharc/homr/issues/172` — OPEN, authored `willl376`,
180 lines / 7252 chars, 5 code blocks. Verified via `gh issue view` after
posting (body length, block count, state, author all confirmed).

Referenced closed issues #7 and #62 in the body rather than commenting on them,
since both are CLOSED (`stateReason: COMPLETED`). A new issue gets a real
number and stays visible; a comment on a closed issue would be easy to miss.

Before posting, the issue's own code snippet was **extracted verbatim from the
posted body and executed** to confirm it reproduces as described — control
`8 notes, staves ['1','2']`, 4-staff `16 notes, <part> count 1, <staves> 2,
{'1': 8, '2': 8}`. Posting an unverified snippet to a public tracker would have
been the exact silent-success failure this project keeps hitting.

### Verified before filing
- `homr/model.py` is byte-identical to the pristine `homr-main.zip` — the
  bracket veto and its explanatory comment are **upstream code, not a local
  patch**. Only `music_xml_generator.py`, `main.py`, `pdf_utils.py`,
  `title_detection.py`, `download_utils.py` differ from pristine.
- Line numbers cited were re-checked against source: `vocabulary.py:97`
  positions, `vocabulary.py:101` `is_lower_position`, `music_xml_generator.py:505`
  `get_staff`, `model.py:430` scorer, `model.py:456-463` the two vetoes,
  `model.py:448-455` the upstream comment explaining the veto's intent.
- `get_xml_voice` N-capability re-verified: staff 3 → voices 9-12, staff 4 → 13-16.

### Deliberately NOT done
- No local commit to `/home/wburt59/Downloads/homr-main` (not a git repo; and
  the standing rule is not to commit there).
- No PR opened — offered in the issue instead, warning-only first.
- The audit repo `willl376/homr-audit` is PRIVATE, so it was **not** linked as
  evidence in a public issue; the proof is inline and self-contained instead.
