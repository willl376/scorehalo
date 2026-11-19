# Slur honesty: paired A/B and decision — 2026-11-19

**Verdict:** install the conservative pair-and-drop repair; **never renumber slurs** as part of it.

The validator flagged unbalanced slurs on all six delivered Carpenters band
scores (the only remaining validation finding after the tuplet repairs). Verovio
reported 39 dangling-slur warnings across the same files. This A/B measured a
repair before touching the live tree, then installed only the policy that every
engraver accepted.

## Policy installed

Within each `(part, staff, voice, slur number)` stream, pair each `stop` with
the most recent unmatched `start` (LIFO). Drop both kinds of orphan and record
them in a JSON report. Preserve the source `number` attribute. No pitch,
duration, voice, staff, backup or forward value is changed; a SHA-256
fingerprint of every note with slurs removed is compared before and after.

## Controls that isolated the real problem

| Control | Result |
|---|---|
| Parse + rewrite XML, no slur change (`for-all-we-know`) | validator still BAD, 13 Verovio warnings, MuseScore rc=0 — serialization is innocent |
| Strip all 233 slurs (`for-all-we-know`) | validator OK, 0 warnings, MuseScore rc=0 — slur presence is not the trigger |
| Stack pair + **global renumber** | `close-to-you` MuseScore **segfault (rc=139)** |
| Stack pair + **per-stream renumber** | validator/Verovio clean, but `for-all-we-know` MuseScore **rc=-11**; 5/6 songs OK |
| Flat pair + per-stream renumber | `for-all-we-know` still rc=-11; left 1 warning on `i-need-to-be-in-love` |
| Stack pair + **preserve source number** | **6/6 clean on every check** |

So the MuseScore crash was caused by assigning new slur numbers, not by pairing
or dropping orphans. The repair deliberately has no renumbering option.

## Full-band result (preserve policy)

| Song | Before → after validator | Verovio warnings | MuseScore rc | Pairs / orphans | Notes unchanged |
|---|---|---:|---:|---:|---|
| close-to-you | BAD → OK | 12 → 0 | 0 | 75 / 16 | yes |
| for-all-we-know | BAD → OK | 13 → 0 | 0 | 99 / 35 | yes |
| goodbye-to-love | BAD → OK | 2 → 0 | 0 | 65 / 9 | yes |
| i-just-fall-in-love-again | BAD → OK | 4 → 0 | 0 | 32 / 6 | yes |
| i-need-to-be-in-love | BAD → OK | 2 → 0 | 0 | 22 / 5 | yes |
| i-won-t-last-a-day-without-you | BAD → OK | 6 → 0 | 0 | 97 / 7 | yes |
| **Total** | **0/6 → 6/6 OK** | **39 → 0** | **6/6 rc=0** | **390 / 78** | **6/6 yes** |

The installed module reproduced the isolated candidate output byte-for-byte on
all six songs. Full upstream suite after installation: **592 tests OK, 7
skipped** (was 582 / 7).

## What this does not claim

Dropping an orphan is an explicit decision that the printed slur mark is
unreliable, not proof of what the composer intended. Clean validation, zero
Verovio warnings and a successful MuseScore import prove structural
well-formedness and engraver acceptance only. They do **not** compare the
transcription to the scanned source. `source_review` remains `not_performed`.

## Files

- Module: `pysrc/scorehalo/slurs.py`
- CLI: `scripts/repair_slurs.py`
- Tests: `tests/test_slurs.py` (10 tests)
- Machine-readable evidence: `reports/slur-honesty.json`
