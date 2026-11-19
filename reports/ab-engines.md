# Paired engine A/B — run `20261119-074316-99pe3d2m`

Both engines saw the **same** labelled image. `exact` requires pitch and duration; streams are (part, staff, voice), so a different part layout counts as misses/spurious. This is a comparison of these fixtures, not a claim of source accuracy.

| case | arm | rc | notes | exact | exact P | exact R | pitch | dur | missed | spurious | sec |
|---|---|---|---|---|---|---|---|---|---|---|---|
| scan | scorehalo | 0 | 37/37 | 37 | 100.0% | 100.0% | 0 | 0 | 0 | 0 | 31.1 |
| scan | audiveris | 0 | 37/37 | 37 | 100.0% | 100.0% | 0 | 0 | 0 | 0 | 24.9 |

Positive `delta_exact` favours ScoreHalo; negative favours Audiveris. Errors are rows too — an engine that cannot run does not silently score zero.

- scan: delta_exact = +0, delta_precision = +0.0 pp
