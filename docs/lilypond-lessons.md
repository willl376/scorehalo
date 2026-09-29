# LilyPond / Frescobaldi tutorial series — reference index

**Source:** `https://www.youtube.com/watch?v=tclwyZr08bE&list=PLHi8BvxILUV6x9FqEmZiYrEj6VMGmTKjt`
(URL as supplied by Wilbur in conversation; not verified offline.)

**Source (VERIFIED 2026-09-28 by fetch, not inferred):** the playlist is
**"Learn LilyPond: Music Notation & Engraving"** by *Sounds From Sound* —
<https://www.youtube.com/playlist?list=PLHi8BvxILUV6x9FqEmZiYrEj6VMGmTKjt>

- **It contains 28 videos.** The YouTube sidebar reads "Playlist (28)", and
  `LilyPond Tutorial 28 — How to MIDI Input, Playback Audio, MIDI Export
  (Frescobaldi)` exists. Wilbur's count of 28 is correct.
- **CORRECTION OF A PREVIOUS ENTRY OF MINE:** I earlier claimed the series was
  25 lessons, reasoning that "LilyPond Tutorial 25" describes itself as the
  final video of the playlist. That was **wrong**. Lesson 25 closed the
  *beginner* segment; tutorials 26–28 were added afterwards (T1 dated 12y ago,
  T28 9y ago). I over-read a single closing line and repeated it as fact —
  the same failure mode as the two invalid probes in lesson 9's notes.
- Naming convention is **`LilyPond Tutorial N - <title>`**, which makes lessons
  identifiable by title, not only by end-card number.

### Known lesson titles (from the fetched playlist metadata)

| N | Title | Held? |
|---|-------|-------|
| 1 | Introduction to LilyPond (Your First Score) | **no** — chapters: Download LilyPond / Text Editor / Main Section / Pdf Score |
| 2 | Need some help? Here's where you can get it! | **no** |
| 3 | Introduction to Frescobaldi and LilyPond | **no** |
| 4 | **Learn the Basic Fundamentals (How to Input Music)** | **no — highest value, see below** |
| 9 | variables + tuplets | yes (confirmed by end-card) |
| 10 | voices | yes (confirmed by end-card) |
| 16 | percussion | yes (confirmed by end-card) |
| 15 | Snippets, folding, line numbers, magnification | yes — hand-transcribed 2026-09-28; **no `.vtt` exists to cross-check it** (see archive-completeness note) |
| 25 | solo piano template / closing the beginner series | yes |
| 28 | MIDI input, playback audio, MIDI export (Frescobaldi) | yes — matches the held "MIDI configuration" transcript |

**Tutorial 4 is almost certainly the lesson covering pitches and durations** —
"the basic fundamentals / how to input music". It is the single most useful
missing item, because it is the source of the two facts (note spelling,
`c1`/`r1` duration semantics) that the held lessons got wrong or omitted.

### Two traps in this archive — read before trusting either source

**1. Lesson numbers are unreliable; cross-reference by CONTENT.** The series'
end-cards do not follow one consistent convention. Wilbur's label for a
transcript and the number spoken on an end-card have both proven to name the
same video (the "New Score dialog" transcript ends *"i'll see you in the next
video lesson 8"* **and** is labelled lesson 8; the chords transcript ends
*"thanks for watching lesson 7"*, where the number may be the current lesson
rather than the next). Do not renumber anything to make two files agree — note
the conflict and move on.

**2. The `.vtt` folder is INCOMPLETE — 26 of 28 videos.** It is missing exactly
**L08 and L15**, and those are precisely the two lessons Wilbur had to
transcribe by hand. So the transcript archive is the authority for those two and
the captions are not available to check them against; **lesson 15 is the
weakest-held entry in the file** and is labelled as such. Any claim about lesson
15's exact wording is single-sourced. Where coverage is claimed below, the VTT
folder is the better source — except for those two.

## TRIAGE — which lessons actually earn their place

Verdicts are `LOAD-BEARING` (changed or validated emitter code), `LATER` (a
real gap it exposed, not yet exploited), `OUT OF SCOPE` (no bearing on this
project), or see QUARANTINE below.

| Lesson | Verdict | What it did |
|---|---|---|
| Chords | **LOAD-BEARING** | Supplied the *rule* behind the 538-barcheck fix: **duration binds to the pitch, post-events come after it** (`<c' e'>4`; `c'4^\markup{…}`, never `c'^\markup{…}4`). Also confirmed `^` is the default position for text above a staff — where the emitter puts source-pixel provenance markup. |
| Voices (10) | **LOAD-BEARING** | `<< { \new Voice } \\ { } >>` confirmed. More valuable: gave the *reason* for the existing invariant that no onset bucket may hold mixed durations — a chord requires equal durations; unequal means voices, not a chord. |
| Staves + instrument names | **LOAD-BEARING** | `\new Staff` + `\with { instrumentName = }`, and `<< >>` for simultaneity — the emitter's exact shape. |
| Score block | **LOAD-BEARING** | One outer expression in `{}`; `\score` with `<< >>`. Validates the emitter's block structure. |
| Variables + tuplets (9) | **LOAD-BEARING** | Confirms `\tuplet 3/2 { … }`; warns tuplet syntax *changed across versions*, which is why every claim is verified against 2.24.3 rather than trusted. Also **partly wrong — see QUARANTINE.** |
| Relative mode | **LATER** | Absolute mode uses identical `'`/`,` marks. This is the *justification* for the emitter staying absolute: the graph is cursor-free, so relative semantics have nothing to be relative to. |
| New Score dialog (7) | **LATER** | Maps GUI fields to emitted code: version number → `\version`, key/time → `\key`/`\time`. **Exposed a real gap:** the emitter writes no `\tempo` (homr transcribes no tempo text, so nothing to emit) and has no pickup-measure concept. |
| Solo piano / `PianoStaff` (25) | **LATER** | The idiomatic alternative to the emitter's explicit-staff approach. A/B candidate, not a rewrite. |
| Percussion (16) | **LATER** | "normal notation of pitches in drum mode will cause an error" — a second, independent proof that **note names are resolved by context, not globally**. Reinforces the rule that the emitter must pin `\language` explicitly. |
| Paper block | **LATER** | Emitter sets no `\paper`; relevant when matching source page size. |
| MIDI input/export (28) | **LATER** | Emitter has a bare `\midi { }` behind an option but no MIDI content. |
| Extra Frescobaldi features | **LATER** | "Quick remove" offers a bulk strip of articulations/slurs/dynamics — a plausible tool if a bad join ever needs hand-un-messing. |
| Lyrics | OUT OF SCOPE | homr does not transcribe words. |
| Snippets, folding, line numbers, magnification (15) | OUT OF SCOPE | Editor ergonomics; no bearing on generated output. Its one technical claim was checked at source: snippet bodies expand `$LILYPOND_VERSION` via `lilypondinfo.preferred()`, which on this box is **2.26.0** — so a snippet dropped into emitter output will not match the file's pinned `2.24.3`. Not a bug; know it before misreading it as one. |
| Frescobaldi 2.19 features, customisation (solarized, log config) | OUT OF SCOPE | Editor UI. |
| Harmonics & snap pizzicato | OUT OF SCOPE | No such content in this corpus. |
| `\global` block, tracking releases | OUT OF SCOPE | Emitter emits neither; release-tracking is administrative. |

## QUARANTINE — claims that failed verification

**Do not carry these forward.** Each is recorded with the correction and the
evidence, so the right item can be cherry-picked later without re-testing.

### Q1 — "Variable names are letters only: no numbers, underscores or dashes" (lesson 9)

**Verdict: half right, half wrong.** Tested against LilyPond 2.24.3.

| Name | Result |
|---|---|
| `ab2` (digit) | **REJECTED** — `syntax error, unexpected UNSIGNED` + `unknown escaped string: \ab` |
| `a_b` (underscore) | **ACCEPTED** — 0 errors, matches control |
| `abc-def` (dash) | **ACCEPTED** — 0 errors, matches control |
| `abcd` (control) | 0 errors — validates the error-signal method |

**Correct version to use:** digits are rejected in *music variable names*;
underscores and dashes are legal. A separate and *also true* fact: **quoted
staff identifiers** accept digits (`\new Staff = "role0"`, used by the
emitter). Two different namespaces — conflating them is the error I originally
made.

**Do not** restate this as "identifiers may contain digits" without saying
which kind.

### Q2 — English note names use `s`/`f` spellings (no transcript held)

**Verdict: the claim is WRONG**, but flagged for honesty: **no transcript of the
lesson making it survives**, so this rests on the installed LilyPond alone.

**Correct version to use:** English = `c cs cf css cx`; `cis/ces/bes` are
**Dutch**. Source: `/usr/share/lilypond/2.24.3/scm/lily/define-note-names.scm`
(`english.ly` is a legacy stub). The emitter therefore declares
`\language "nederlands"` explicitly rather than inheriting the default, and
`musicxml2ly` has the identical latent fragility.

### Q3 — "A PDF exists, therefore it engraved"

**Verdict: not a lesson claim, but a probe I used and it is WRONG.** LilyPond
writes a PDF **even for a score that errored** (`ab2` produced 27,083 bytes
alongside four errors). Separately, a MIDI probe on scores without a `\midi`
block returns 0 bytes for *everything*, which proves nothing — and a known-good
control (`abcd`) is what caught that one.

**Correct version to use:** the only trustworthy signal is *does LilyPond emit
an error line*, and only alongside a positive control. Same rule as memory §26.

**What this file is:** an index of a beginner LilyPond + Frescobaldi video
playlist, recording which lessons actually affected
`scorehalo`'s native LilyPond emitter.

**What this file is not:** the transcripts. They are not stored. If a lesson
here matters, re-watch it; do not trust a paraphrase to settle a syntax
question. Everything marked **CORRECTED** below is a place where the video's
claim did *not* survive contact with the installed LilyPond.

## Why the full text is deliberately not kept

The series is generic beginner material (staff creation, MIDI device setup,
paper blocks, colour schemes). It is reference, not machine state. The
actionable conclusions live in `.serena/memories/project_state.md`, which is
read on demand; this file makes the source recoverable without loading
thousands of words into every session.

## Recovered, verbatim

`lylessons-transcripts.md` holds the raw speech-to-text. Start there when a
question needs the source rather than a paraphrase. It currently holds
**lesson 7** (New Score dialog: Input / Parts / Score Settings, plus
File → Save As Template). The video's end-card names the *next* lesson number,
so entries are self-identifying — paste the rest as they come.

Still not recovered: the opening fundamentals (lessons 1–6 and anything after
25, if the series runs that far), which are the ones that bear on the emitter:
pitch spelling, duration arithmetic, chords, barlines/barchecks, `\version` /
`\header` / `\paper`, and tuplets beyond the single quick mention.

## Lessons with full text available

| # | Topic | Bearing on scorehalo |
|---|-------|---------------------|
| 1 | Variables, and tuplet entry syntax | **USED** — confirms `\tuplet 3/2 { }`; notes syntax "changed over recent versions", so the 2.24.3 spelling is the one that matters |
| 2 | Voices | **CONFIRMED** — `<< { \new Voice } \\ { \new Voice } >>` is exactly the emitter's shape |
| 3 | Lyrics | **UNUSED** — homr does not transcribe words |
| 4 | Relative mode | **CONFIRMED** — `'` up, `,` down, repeatable; absolute mode uses identical marks, which is why the emitter can stay in absolute |
| 5 | Staves and instrument names | **CONFIRMED** — `\new Staff` + `\with { instrumentName = ... }`; `<< >>` needed for simultaneity |
| 6 | Snippets, folding, line numbers, magnification | **UNUSED** — editor ergonomics. Hand-transcribed 2026-09-28 (no `.vtt` exists). Checked at source: snippet bodies expand `$LILYPOND_VERSION` → `preferred()` = **2.26.0** on this box, while the emitter pins `2.24.3`. |
| 7 | Frescobaldi features (snapshot, edit in place, doc browser, find/replace, relative↔absolute, double durations, transpose, quick remove, preview options) | **PARTLY** — *quick remove* offers a bulk "remove articulations / slurs / dynamics" pass, which matters if a bad join ever needs un-messing by hand |
| 8 | Percussion note names, drum mode, sticking | **UNUSED** — no percussion in this corpus |
| 9 | Natural/artificial harmonics, snap pizzicato | **UNUSED** |
| 10 | Paper block, `\set paper-size` | **FUTURE** — the emitter sets no `\paper` block; relevant when matching source page size |
| 11 | Tracking changes between releases | **PARTLY** — reinforces pinning an exact `\version` |
| 12 | `\global` block | **UNUSED** — emitter has no global block |
| 13 | The score block | **CONFIRMED** — one outer expression in `{}`; `\score` present, `<<>>` for simultaneity |
| 14 | Solo piano template, `\new PianoStaff` | **FUTURE** — the emitter uses `\new Staff` with explicit staves; `\new PianoStaff` is the idiomatic alternative worth an A/B |
| 15 | MIDI configuration and export | **FUTURE** — `\midi` block is absent from emitter output |
| 16 | Frescobaldi 2.19 features (mode shift, MIDI import, comment snippets, 800% zoom) | **UNUSED** |
| 17 | Frescobaldi customisation (solarized, bracket highlighting, log config) | **UNUSED** |

Lessons 1–7 of the series were supplied only as conversation *summaries*, not
full transcripts: absolute mode, note duration, note spelling, barcheck,
barlines, chords, tuplets, artifacts/dynamics, paper, version + header. Those
are second-hand — verify against LilyPond before relying on them.

## The four facts that changed the code

1. **Duration `k` means `1/k` of a whole note** (`4` = quarter, not four
   quarters). Internal units are quarter notes; `WHOLE = 4`. Getting this
   backwards quadruples every duration and still parses.
2. **Pitch, then duration, then post-events.** `c'^\markup{...}4` makes the
   note untimed; it silently becomes a whole note. Caught 538 barchecks.
3. **A barcheck only tests anything if the bar is full and carries a `|`.**
   A probe summing to 3.5/4 with no barline is vacuous.
4. **`\language` must be declared, not inherited.** See CORRECTED below.

## CORRECTED — video claim that did not survive

The videos state that English note names use `s`/`f` spellings. Verified
against the installed LilyPond 2.24.3
(`/usr/share/lilypond/2.24.3/scm/lily/define-note-names.scm`):

- **English** = `c`, `cs`, `cf`, `css`, `cx`
- **Dutch (`nederlands`)** = `c`, `cis`, `ces`, `cisis`

The emitter's names are Dutch. They compiled only because `nederlands` is the
default, so the emitter now declares `\language "nederlands"` explicitly.
`musicxml2ly` has the same latent fragility (emits `es`/`bes`, no `\language`).

Also **CORRECTED — and then corrected back**: I once recorded the "variable
names are letters only" rule as a video error, on the grounds that
`\new Staff = "role0"` compiles. That was **my** mistake — I tested a *quoted
staff identifier* and generalised to *music variable names*, which are a
different namespace. Tested properly, `ab2 = { ... }` is **rejected** by
LilyPond 2.24.3. The video is right about digits. (It is still wrong about
underscores and dashes: `a_b` and `abc-def` both compile with 0 errors.) Both
statements are true and describe different things. See the lesson 9 entry in
`lylessons-transcripts.md` for the table.

## Unimplemented features these lessons point at

When any of these become relevant, the playlist is the quick reference:

- articulations, dynamics, fingerings, slurs (not emitted; slur *balance* is a
  known homr defect and needs its own fix, not a syntax fix)
- `\paper` block / page size
- `\new PianoStaff` and `<staves>` for grand staffs
- `\midi` block and MIDI export
- tweaks, `\context`, engraver/object-collision work (the "advanced" playlist)

## Verification rule for this file

Do not resolve a syntax question from a line in this table. Re-watch, or run
LilyPond. This index exists to tell you *which lesson to re-watch*, and to
record where the video was already found wanting.
