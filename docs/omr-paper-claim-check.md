# Auditing a paper summary against the paper — the IJCV full-page OMR case

An analysis of *End-to-End Full-Page Optical Music Recognition for Pianoform
Sheet Music* (Ríos-Vila, Calvo-Zaragoza, Rizo, Paquet, IJCV 2026 134:49)
characterised the work as treating **multi-staff piano notation as a first-class
concept**, and cited it as support for a `Score → GrandStaff → Staff` hierarchy
to fix HOMR's four-staff (two-piano) ceiling.

Checked against the full text, **that framing does not hold.** This file records
the check, because the failure mode is more useful than the verdict.

## The check that settled it

Rather than reading for whether the summary "sounded right," I counted terms
across all 18 pages (`~/Downloads/fullpage_omr_paper.txt`, 1297 lines):

| Term | Occurrences |
|---|---|
| `pianoform` | 22 |
| `kern` | 56 |
| `curriculum` | 20 |
| `ekern` / `bekern` / `GrandStaff` | 21 / 13 / 15 |
| `layout analysis` | 13 |
| `reading order`, `polyphonic`, `spines`, `positional encoding` | 8 / 8 / 3 / 4 |
| **`multi-staff`, `two-staff`, `three staff`, `four staff`** | **0 / 0 / 0 / 0** |
| **`organ`, `two-piano`, `duet`, `choral`, `ensemble`, `multi-instrument`** | **0 each** |
| **`hierarchy` / `hierarchical`** | **3** — all about *reading order*, not staff structure |

The paper never mentions a staff count other than two, never mentions a
two-piano, organ, ensemble or duet work, and never states how many spines a kern
file contains. "Pianoform" is used 22 times as a fixed, unexamined label —
"one of the most complex sources in the OMR literature" — never once defined.

**Reading a benchmark name as an architectural guarantee is the whole error.**

## The specific overreach: hierarchy is claimed, not shown

The summary presented as *the paper's architecture*:

```
Image → Full-page model → Hierarchical representation → Music encoding
```

The paper's actual model is **CNN encoder + autoregressive Transformer decoder
→ kern, directly**. There is no hierarchy stage. The word "hierarchical"
appears three times: once about the general complexity of notation, once about
the *ablation* finding that system-level pre-training teaches "the hierarchical
reading order of pianoform documents," and once in a Swin Transformer
bibliography entry. All three concern **page reading order** — top-to-bottom
across systems — not staff or instrument structure.

So the summary took an *implicit* property of a learned representation and
rendered it as an *explicit* component of a pipeline. That is a much stronger
claim, and it is the paper's own argument turned against it:

> "the system-level decomposition used in the alternative variant may introduce
> segmentation boundaries that hinder the capture of full-page context"

The paper's thesis is that layout decomposition is what you should **eliminate**.
The proposed architecture adds one.

## What the summary got right (checked, not conceded)

An audit that lists only errors is its own kind of distortion. Grepping these
too — they hold up:

| Claim | Verified at |
|---|---|
| Trained on GrandStaff, then on the FP-GrandStaff corpus | line 463 (GrandStaff), 560 (FP-GrandStaff) |
| GrandStaff treated as primary training data, not a special case | 15 mentions; the whole test protocol |
| Curriculum learning over pages with multiple systems, complexity increasing | lines 326-337, 20 mentions |
| **"Up to seven systems per page"** | line 531, `Max. systems per page 7` — **exact** |
| "Better when learning full-page hierarchy rather than decomposing" | supported, but as the *direction*, not the architecture |

The seven-systems figure is worth singling out: it is a precise, checkable
number, and it is correct. The summary was not uniformly sloppy — it was
*precise about what it had counted and fluent about what it had not.* That is
a more dangerous profile, because it makes the wrong parts feel as trustworthy
as the right ones.

## What the paper does legitimately support

One claim in the summary is correct and does matter — verified against Table 6:

> "their full-page approach outperformed system-decomposition approaches after
> fine-tuning"

SMT fine-tuned **14.1** SER vs **TrOMR 25.7** on Mozarteum, and **25.8** vs
**53.2** on the Polish corpus. TrOMR *is* the system-level decomposition
baseline. So the paper does support the *direction* of the fix — page-level
reading beats system-slicing — just not by the route claimed.

Every figure in this file was re-verified against `fullpage_omr_paper.txt`
after the first draft, which caught two of my own errors: I had written the
Polish SMT figure as 25.9 (it is **25.8**; 25.9 is the system-level SMT
row) and the text length as 1297 lines (it is **1296**). Both are the kind of
number that survives into an issue unchallenged if nobody checks.

Also correct and load-bearing: the paper's own qualitative analysis says voices
are placed right *"since its training is specific for pianoform scores."*
Training specificity is doing the work. That is an admission that staff
configuration was baked in, not a claim of generality.

## Why the drift is dangerous, not merely wrong

The summary used "multi-staff is first-class" to validate a four-staff
hierarchy for Mozart K.448. The paper offers **zero evidence** about any staff
count but two — its GrandStaff corpus is pianoform throughout. Importing its
authority into a four-staff claim would have made the ScoreHalo fix look
research-backed when the only evidence for it is our own `proof_four_staves.py`.

This is the same error class as the "GrandStaff" *name* being read as
multi-staff support. A dataset's title is not a specification.

It is also precisely the failure this project keeps paying for in its own code:
**a system confidently, structurally correct about what it knows, silent about
what it doesn't.** The paper reports voices correctly placed *and* slurs and
dots silently skipped, because those tokens were absent from its training
vocabulary. The summary reproduced the behaviour in prose: confident on
multi-staff, silent on the fact that nothing in the paper addresses it.

## The honest version

> The paper proves page-level reading beats system-slicing. Its model has no
> staff-count ceiling because it never had one — it does not decompose the page,
> so the question never arises. It says **nothing** about repairing a system
> whose ceiling is an explicit two-staff assumption in its exporter. Its silence
> is why the audit matters more, not less.

That scoping is what makes upstream issue #172 stronger: two named bugs,
reproduced with a positive control, and the literature pointer confined to the
one claim it actually makes.

## Method note — the habit worth keeping

When a summary of a paper arrives with confident citations, **grep the source
for the load-bearing nouns before believing the argument.** A term with **zero**
occurrences cannot support a claim about that term's absence-of-limitation. It
cost one pass over 1296 lines to settle this, and it prevented a
research-backed framing of our fix that the research does not provide.

Related standing lessons: lesson 18/26 (a probe that cannot fail proves
nothing — here, a summary that could not be wrong about a term the paper never
uses), and lesson 31 (absence in a lossy extraction is not absence in the
document — the mirror image: presence in a summary is not presence in the
source).

---

## Outcome — scoped citation added to issue #172 (2026-10-01)

`https://github.com/liebharc/homr/issues/172#issuecomment-5931431461`
(2853 chars, author `willl376`, verified via the GitHub API after posting).

I had earlier argued the issue was stronger **without** the citation. That was
the wrong call, and the reason it was wrong is instructive.

**Why posting it was correct after all.** The risk of citing literature on a
bug report is that the citation smuggled in an *unsupported* claim. But that risk
is eliminated by **stating the limits in the same comment** — a three-part
structure that keeps positive and negative evidence in separate, labelled
sections:

- **What it supports** — Table 6, re-verified numbers (TrOMR 25.7/53.2 vs
  fine-tuned SMT 14.1/25.8), the authors' own decomposition argument, and the
  seven-systems-per-page corpus fact.
- **What it does not support** — the zero-hit grep list, the undefined
  `pianoform`, the absence of any intermediate hierarchical object model, and
  the paper's own admission that voice placement works *"since its training is
  specific for pianoform scores"*.
- **Where the evidence actually comes from** — the reproduction and the code
  analysis, explicitly labelled as such.

So the comment cannot be misread as research backing for the fix. It hands
upstream a pointer **and** the boundary, which is strictly more informative than
silence.

**The one line that carries the argument** — and it is a *category* distinction
rather than a ranking:

> It is an explicit two-staff assumption in code (`get_staff()`), not a
> limitation of page context. Those are different problems, and citing the SMT
> paper as a remedy for this one would be a category error.

This is sharper than "the issue is stronger without it." The issue's value is
that it names the bug's actual location. A paper about context cannot help
repair an exporter that assumes two — but a maintainer deciding whether to care
about page context at all *should* know that the paper exists.

### New verification detail worth keeping
The paper's code and weights are **public and live**:
`github.com/antoniorv6/SMT` and
`huggingface.co/collections/PRAIG/sheet-music-transformer`. That materially
changes the blocked item on SMTNeXt — it is no longer "research-only", it is
runnable on this box if the weights are small enough for 15 GiB RAM and an AMD
Oland. Worth an actual attempt rather than a speculative plan.

---

## Corollary to §36 — the exporter fix is *provable now*, and the veto fix is not

The "live vs dead token" correction in §36 changes the shape of the fix, so it
belongs here rather than only in the code audit:

| | Fix 2 — exporter | Fix 3 — brace pairing |
|---|---|---|
| Needs model weights? | **No** | **Yes** |
| Why | input symbols already carry a 5-value position; the loss happens in the *consumer* | the veto must not regress a real case (whole-system bracket over an a-cappella trio) and geometry alone cannot disambiguate |
| Testable today? | **Yes** — synthetic 4-staff input | No |

Two independent bugs, two different levels of evidence. The paper is relevant to
neither directly: it says nothing about staff counts, and its claim is about page
context, which is a third, separate axis.

