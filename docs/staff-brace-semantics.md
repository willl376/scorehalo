# Staff/brace semantics: what two independent implementations actually do

Date: 2026-10-02
Question: **how should a 4-staff (two-piano) system be encoded in MusicXML?**

This matters because it settles the shape of `liebharc/homr#172` fix 3, which is
the half still blocked on having model weights.

**Verification status: SOURCE-READ ONLY for both projects. Nothing was executed.**
MuseScore cannot be built here (Qt 6.8+, CMake 3.31+, no sudo). Rosegarden cannot be
built here either. Every claim is a quoted reading with exact line numbers.

---

## The finding

**The `<part>` is the unit of bracing.** Both mature, independent implementations
agree, unprompted, and neither is derived from the other.

### Rosegarden 26.06 — `MusicXMLImportHelper.cpp:97-107`, inside `setStaff()`

```cpp
// When a multi staff system is created, place CurlyOn and CurlyOff on the
// first and last track of the part.
if (m_tracks.size() > 1) {
    TrackMap::iterator it = m_tracks.begin();
    ((*it).second)->setStaffBracket(Brackets::CurlyOn);
    while (++it != m_tracks.end()) {
        ((*it).second)->setStaffBracket(Brackets::None);
    }
    --it;
    ((*it).second)->setStaffBracket(Brackets::CurlyOff);
}
```

Unconditional, with no way to intervene: a multi-staff part gets one curly brace
from its first staff to its last. `m_tracks` is `std::map<QString, Track*>`
(`MusicXMLImportHelper.h:48`), so `begin()` is staff "1" and `end()-1` is staff "N".

### MuseScore 4 — `importmusicxmlpass1.cpp:1492-1500`

```cpp
    // handle the implicit brackets:
    // multi-staff parts w/o explicit brackets get a brace
    for (const Part* p : il) {
        if (p->nstaves() > 1 && !muse::contains(partSet, p)) {
            Staff* staff = p->staff(0);
            const size_t column = m_score->bracketLevels(staff->idx()) + 1;
            EditStaffBrackets::setBracketType(m_score, staff->idx(), column, BracketType::BRACE);
            EditStaffBrackets::setBracketSpan(m_score, staff->idx(), column, p->nstaves());
```

Same rule, same consequence, stated in the source's own words: **span = `nstaves()`**.

Two independent codebases, one rule. That is what makes this a finding rather than
an inference.

---

## The consequence for a two-piano score

| Encoding | Braces produced |
|---|---|
| **one `<part>`, `<staves>4</staves>`** | **one** brace over staves 1–4 — asserts a single instrument |
| **two `<score-part>`s, each `<staves>2</staves>`** | **two** braces, 1–2 and 3–4 — correct |

And note *how many notes are affected either way*: all of them. This is not a
bracket-cosmetics issue; it is an instrument-identity issue, because a brace is the
only thing in MusicXML that says "these staves are played by one performer."

---

## The escape hatch: braces are explicit objects, not derived properties

This is the part that corrects a claim I made earlier. I said MuseScore "hangs
staves off an `Instrument` that owns a staff list, with the brace living on that
parent." **That is wrong in the load-bearing detail.**

`Instrument` (`src/engraving/dom/instrument.h`, 432 lines) has essentially no staff
API. `Staff` (`staff.h`, 299 lines) holds neither an instrument pointer nor a
bracket. The brace is an **independent first-class element with an explicit span**
— `src/engraving/dom/bracketitem.h:48-56`:

```cpp
    size_t bracketSpan() const { return m_bracketSpan; }
    BracketType bracketType() const { return m_bracketType; }
    void setBracketSpan(size_t v) { m_bracketSpan = v; }
    void setBracketType(BracketType v) { m_bracketType = v; }
    staff_idx_t startStaffIdx() const { return m_startStaffIdx; }
    void setStartStaffIdx(staff_idx_t staffIdx) { m_startStaffIdx = staffIdx; }
```

`Part` does own its staves (`Part::staves()`, used at
`importmusicxmlpass1.cpp:1455`, `:1461`, `:1498`) — that part of my claim was
roughly right. But the brace is **not** derived from that ownership; it is
authored data. `BracketItem` even provides `intersects()` / `intersects(first, last)`
(`bracketitem.h:62-63`) for braces that overlap, and MuseScore has an
`isRedundantBracket()` suppression test (`importmusicxmlpass1.cpp:1473`).

**That is the design lesson for `homr`.** homr's brace veto in `model.py` is
reactive: it rejects a brace that might span the wrong staves, because its model has
no way to *express* "brace from staff 3 to staff 4." Give the model an explicit
spanning brace and the a-cappella-trio case stops being a veto and becomes a value.
That is very likely why `homr#62` never closed.

---

## `MAX_STAVES = 4` — homr's case sits exactly on the boundary

`src/engraving/dom/mscore.h:75`:

```cpp
static constexpr int MAX_STAVES = 4;
```

Import (`importmusicxmlpass1.cpp:2960`) reads `<staves>` and calls
`part->setStaves()`. Then at `:2969-2972`:

```cpp
    if (staves - static_cast<int>(hiddenStaves.size()) > MAX_STAVES) {
        m_logger->logError(u"staves exceed MAX_STAVES, even when discarding hidden staves", &m_e);
        return;
```

So **`<staves>4</staves>` imports; `<staves>5</staves>` errors loudly.** Export is the
mirror (`exportmusicxml.cpp:8669-8671`), writing `<staves>` when `staves > 1`.

**This upgrades fix 2 from "acceptable" to "exactly the native representation."**
A lossless remap to four staves plus `<staves>4</staves>` is precisely what the
reference implementation wants, at its hard limit. It will not scale to six staves
without a limit change — worth stating plainly rather than discovering later.

MuseScore also handles real-world mess here that is worth copying: hidden staves
(`<staff-details print-object="no">`) are collected and discarded
(`importmusicxmlpass1.cpp:2956-2962`, `:2973-2985`), and a `<staves>` value that
disagrees with the running count sets `setHideStavesWhenIndividuallyEmpty(true)`
(`:2662-2677`).

---

## Ruled out: MusicXML 4.0 `<instruments>` / `<instrument-staff>`

MusicXML 4.0 allows multiple instruments inside one `<part>`, which would have been
the elegant answer: one part, two instruments, two staves each. **MuseScore does not
import it.** `grep -rn 'instrument-staff' src/importexport/musicxml/` returns
nothing. `<instruments>` is read at `importmusicxmlpass1.cpp:2953` and skipped; the
only real use is percussion metadata (`hasDrumset()`,
`importmusicxmlpass2.cpp:426`, `initDrumset()` `:469`). The exporter *writes*
`<instruments>` (`:8672-8674`) — so MuseScore can emit something it cannot read back
for staff mapping.

Checked and discarded rather than recommended. Two `<score-part>`s it is.

---

## What this settles for `homr#172`

- **Fix 2 (exporter, no weights needed) — strengthened.** Lossless position→staff
  remap + `<staves>4</staves>` is the native encoding, at `MAX_STAVES`. Two
  independent importers round-trip it. It yields right notes on right staves under
  one wide brace.
- **Fix 3 (instrument identity) — now specified.** Emit two `<score-part>`s of two
  staves each. The *decision* of where to cut requires the image (weights). The
  *encoding* no longer does — it is settled by two reference implementations.
- **The model change that unblocks it** — represent the brace as an explicit
  `(startStaffIdx, span)` object rather than vetoing ambiguous pairings. homr's four
  position tokens already carry everything needed to build such an object; they are
  discarded at `get_staff()`.

## What this does not establish

- Neither project was executed. Both readings are source-only.
- It does not show that `homr`'s *model* emits `upper2`/`lower2` on a real two-piano
  scan. That still needs weights. What is settled is the encoder, not the detector.
- `MAX_STAVES = 4` means neither reference implementation can express, say, a
  two-piano plus string-bass layout in one part. That limit is inherited, not solved,
  and should be stated as such in any fix.
