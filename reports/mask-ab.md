# Masking A/B — run `20261119-080858-67wha267`

Both arms recognized the **same** labelled scan with the same ScoreHalo CLI. The control adds `--no-mask-graphics` and `--no-photo-gate` (the gate otherwise skips any page with 5%+ picture-like pixels, leaving no prediction to score). `exact` requires pitch and duration. This is a fixture measurement, not a claim of source accuracy.

- picture boxes detected: `[{'box': (691, 1295, 1048, 673), 'area_frac': 0.0774, 'fill': 1.0}]`
- image SHA-256: `7ab02feb93a7a3d0ae4e65bf5d42ba2e32515fefeeb61747a9c33a9a575c9918`
- truth SHA-256: `37e277b0dd91e7344f0ac6387147ce119c036663aa2903422ab3c6846343ed12`

| arm | rc | exact | precision | recall | sec |
|---|---|---|---|---|---|
| masked (default) | 0 | 37/37 | 100.0% | 100.0% | 30.8 |
| unmasked (control) | 0 | 37/37 | 100.0% | 100.0% | 32.2 |

Delta exact (masked - unmasked): **+0**.

A positive delta supports making masking the default; a negative delta means the picture's context helped the engine, so masking must stay opt-in. Equal scores are not evidence that masking is harmless on real photographs.
