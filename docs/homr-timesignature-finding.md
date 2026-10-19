# homr time signature: what is measured, and what is NOT solved

Status: **the meter conflict is resolved in ScoreHalo; the homr bug is not
fixed upstream and no homr patch is proposed.** See "Resolution" at the end.

Two candidate *homr* fixes were tested against real data and both were
falsified. The problem was solved one level up instead, in `scorehalo.meter`.

## The measurement

From breakpoints in a byte-identical copy of the homr actually installed in the
venv (`scripts/instrument_homr.py`), on the p0020 system crops:

| crop | positions | two_position_chords | nominator | bars measured (quarters) | declared |
|---|---|---|---|---|---|
| sys1 | `.` `lower` `upper` | 16 | 3/4 | 4, 4, 5/2, 5/2, 3, 3, 3, 3 | 3/4 |
| sys2 | `.` `lower` `upper` / `.` `upper` | 9 | 1 | 4, 4, 4, 4, **6**, 4, 4 | 4/4 |
| sys3 | `.` `lower` `upper` / `.` `upper` | 8 | 1 | 4, 4, 4, **6**, 4 | none |

Bar lengths re-measured independently from the emitted MusicXML agree with these.

## Hypothesis 1 — the two staves are summed. FALSIFIED.

I claimed `find_division_and_time_signature_nominator()` kept one accumulator
for a whole voice, so a two-staff bar measured twice. I patched it to accumulate
per staff position.

`two_position_chords: 16` refutes it. homr's grouping merges simultaneous staves
into one `SymbolChord`, and `SymbolChord.get_duration()` takes `min()` over its
symbols — the correct reading of two staves sounding at once. The nominator is
already right about simultaneity. The patch was inert; **reverted**.

## Hypothesis 2 — the median is wrong; take the longest bar. FALSIFIED.

sys1's bars disagree (4, 4, 5/2, 5/2, 3, 3, 3, 3), `np.median` returns 3, and
`build_time_signature` publishes a 4/4 part as 3/4. Under-measurement is
one-directional, so I replaced the median with `max`.

Real run, real output:

```
sys1  declared time -> 4/4     (was 3/4)   fixed
sys2  declared time -> 6/4     (was 4/4)   BROKEN
sys3  declared time -> none    (unchanged)
```

I predicted "sys2 unchanged" in an earlier draft of this file. It was wrong.

## Why neither statistic works

Every candidate, scored against the page-wide truth (4 quarters, which
`scorehalo.pattern` reports for every staff position on this page):

| statistic | sys1 | sys2 | sys3 |
|---|---|---|---|
| max | 4 ✓ | **6 ✗** | **6 ✗** |
| median (current) | **3 ✗** | 4 ✓ | 4 ✓ |
| min | **5/2 ✗** | 4 ✓ | 4 ✓ |
| mode | **3 ✗** | 4 ✓ | 4 ✓ |
| upper median | **3 ✗** | 4 ✓ | 4 ✓ |
| last bar | **3 ✗** | 4 ✓ | 4 ✓ |
| first bar | 4 ✓ | 4 ✓ | 4 ✓ |

Only *first bar* is correct on all three, and it is correct for no reason I can
defend — sys1's opening bar happens to be a full bar. Winning 3/3 on three
samples is precisely the coincidence that does not survive another page. Tuning
until one statistic wins is how the earlier assumptions in this project were
invented in the first place.

The actual difficulty: sys1's error is **downward** (dropped notes shorten bars)
and sys2's is **upward** (one bar measures 6 quarters, probably a merged or
duplicated bar). A single statistic over a mixed-error distribution has no
correct choice.

## Why this is not patched

The conflict is real and detectable, and it is already detectable at the page
level, which is where the evidence actually lives:

```
staff position 0: G2 in 3 of 3 system(s)
staff position 1: G2 in 3 of 3 system(s); bar length 48 elsewhere, deviates: system 2
staff position 2: F4 in 2 of 2 system(s); bar length 48 elsewhere, deviates: system 2
```

`scorehalo.pattern` compares staff positions across the whole page instead of
guessing a statistic over one part's noisy internal measurements. On this page
it independently arrives at 48 ticks = 4 quarters, agreeing with the direction
the max fix got right and disagreeing with the direction the median got wrong.

So the fix is not a statistic. It is: **do not trust one part's internal meter
guess; check it against the page and report the conflict.** That is what
`pattern.py` does, and it is why no homr patch is proposed here.

## What is left genuinely open

* Whether the 6-quarter bar in sys2 is a merged bar, a pickup plus a full bar,
  or a duplication. Not diagnosed.
* sys3 declares no time signature at all — homr emitted no `timeSignature`
  token for that crop. Separate from the meter arithmetic.
* Whether the page-wide bar evidence should *override* homr's declared meter in
  the emitted score, or only be reported alongside it. This is a policy
  question, not a bug, and it changes what ScoreHalo writes.
## Resolution (later the same session)

The statistic hunt above was the wrong question. The page-wide answer was
already available — `scorehalo.pattern` reports 48 ticks = 4 quarters for every
staff position on this page — and so was the code that needed it.
`scripts/build_page_graph.py` *already computed the page-wide meter*, printed

    WARNING: systems disagree on meter; page takes 4/4 (11 of 15 measures)

and then **applied it only to the measures it invented** (padding for roles
missing from a system). Real measures were carried across with whatever their
own fragment declared. The decision was made, announced, and thrown away.

Two consequences, both measured:

* System 1's four measures kept 3/4, so their bars were 36 ticks, and every
  note at or past tick 36 was dropped by the emitter. **124 of 128 pitched
  notes reached the file.**
* The singer is absent from system 1, so its four silent bars were padded from
  a *template* whose meter was that same misread 3/4. The file therefore
  declared 3/4 and 4/4 at once.

`scorehalo.meter` fixes both, and only where the evidence is unambiguous:

    Override a measure only when its own content contradicts its own meter.

A measure whose notes overrun its declared bar, and which fit inside the
page-wide meter, is a misread meter — that is the one case where the
declaration is provably wrong rather than merely different. Content that fits
the meter it declares is left alone, so a genuine mid-piece meter change
survives. Padding now takes the page meter, because padding is invented
content and has no opinion of its own.

Before / after, same fragments, same command:

| | notes | pitched | meters declared |
|---|---|---|---|
| before | 139 | **124** | 3/4 and 4/4 |
| after | 143 | **128** | 4/4 |

All four notes recovered; the mixed meter gone; `validate_musicxml.py` reports
"clean: 2 parts, 20 measures, no violations"; `musicxml2ly` rc=0; `lilypond`
"Success".

### What this does NOT fix

* **homr still declares the wrong meter.** sys1 is still 3/4 inside its own
  MusicXML. ScoreHalo no longer obeys it; homr is unpatched.
* **Notes homr dropped are still gone.** The singer's system-2/3 fragments
  measure exactly 4 quarters, but system 1's Voice measures exactly 3 — which
  is what a *correctly-fitted* fragment of a 4/4 bar looks like only by
  coincidence. If homr truncated the voice to its own misread meter, those
  notes never reached us and no downstream fix can recover them.
* **A measure that fits no meter is left alone.** sys2's 6-quarter bar and
  sys3's 15/4 bar still clamp (2 notes). Guessing there would add noise, not
  accuracy.
* **18 further notes are trimmed in the LilyPond path, none of them at a
  barline.** All 18 are `overlap` — a note starting before the previous one
  ended in the same voice — and 6 are `past_bar`. This is homr's overlapping
  onsets, a separate defect from the meter, untouched here.
