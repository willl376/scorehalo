# Rosegarden as a staff-count reference — and the fix-2 specification

Date: 2026-10-02
Source read: `~/Downloads/rosegarden-official-26.06` (Rosegarden 26.06 source tarball,
GPL-2.0+, © Rosegarden development team, unaffiliated with this project).
Files read in full or by targeted inspection:

| File | Lines | Role |
|---|---|---|
| `src/document/io/MusicXMLXMLHandler.cpp` | 1636 | SAX handler: MusicXML → per-part model |
| `src/document/io/MusicXMLImportHelper.h` | 127 | per-part model definition |
| `src/document/io/MusicXMLImportHelper.cpp` | 354 | staff→track mapping, brace placement |
| `src/document/io/MusicXmlExportHelper.cpp` | 1564 | model → MusicXML (export direction) |
| `src/document/io/MusicXmlExportHelper.h` | — | `getStaffCount()` at :135 |

**Verification status: SOURCE-READ ONLY. Nothing was executed.** Rosegarden cannot be
built on this machine (Qt5/Qt6 + KDE + liblo/lrdf/fftw3/samplerate/LADSPA/DSSI dev
packages, no sudo; `data/devices/` ships empty). Every claim below is a reading of
code, quoted with exact line numbers so it can be checked independently. No claim here
rests on running the program.

---

## 1. The headline: Rosegarden round-trips `<staves>` — it is a valid referee

`homr`'s `<staves>2</staves>` hardcoding (`music_xml_generator.py:466`) was reported
in `liebharc/homr#172`. The proposed fix is to emit `<staves>N</staves>` with N staves.
Rosegarden — a 25-year-old shipping application with a MusicXML 4.0 importer — proves
consumers expect exactly that.

**Import** — `MusicXMLXMLHandler.cpp:1425-1434`:

```cpp
} else if (m_currentElement == "staves") {
    int staves = 0;
    if (!checkInteger(m_currentElement, staves))
        return false;
    for (int s = 2; s <= staves; s++) {
        QString staff;
        staff.setNum(s);
        m_parts[m_partId]->setStaff(staff);
    }
    m_parts[m_partId]->setStaff("1");
```

`<staves>` is read as an integer and each staff `2..N` is materialised. **There is no
upper bound.** `<staves>4</staves>` creates staves "2", "3", "4".

**Export** — `MusicXmlExportHelper.cpp:229-233`, guarded by `isMultiStave()`
(`MusicXmlExportHelper.h:130`, `return m_staves.size() > 1;`):

```cpp
if (isMultiStave()) {
    tmp.str("");
    tmp << "        <staves>" << getStaffCount() << "</staves>\n";
    m_strStaves = tmp.str();
```

`getStaffCount()` is `m_staves.size()` (`MusicXmlExportHelper.h:135`), i.e. the number
of staves in the part. **It reads what it writes.** A `<staves>N</staves>` fix is
demonstrably interoperable, not merely principled.

**Per-note `<staff>` is unvalidated and unclamped** — `MusicXMLXMLHandler.cpp:951-955`:

```cpp
} else if (m_currentElement == "staff") {
    int dummy;
    if (!checkInteger(m_currentElement, dummy))
        return false;
    m_staff = m_characters;
```

Default is `"1"` (`:479`). The value is validated as an integer and then stored
verbatim. `<staff>4</staff>` is accepted silently.

---

## 2. One track per staff — and no 16-track cap

`MusicXMLImportHelper.h:48` declares `typedef std::map<QString, Track*> TrackMap;`
(held as the member `TrackMap m_tracks;` at `:112`), and `MusicXMLImportHelper.cpp:60-90`
creates a `Track` for each new staff key:

```cpp
if (m_tracks.find(staff) == m_tracks.end()) {
    // No such track, create a new one.
    TrackId id = composition.getNewTrackId();
    ...
    m_tracks[staff] = track;
}
```

**So a track per staff — which is the multi-track audition surface. But it is keyed by
the staff string as it arrives in the file.** For homr's current 4-staff output (all
notes aliased onto `<staff>1</staff>`/`<staff>2</staff>`), Rosegarden creates exactly
**two** tracks, each holding an interleaved mixture of both pianos. Track splitting
happens downstream of the aliasing and therefore cannot recover it.

There is **no 16-track limit anywhere**: `grep -rnE 'MAX_TRACKS|maxTracks|trackLimit' src/`
returns nothing. (Track count is bounded only by `Composition::getNewTrackId()`.)

---

## 3. THE DECISIVE FINDING: one brace per part

`MusicXMLImportHelper.cpp:97-107`, inside `setStaff()`:

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

**Within a single part, a multi-staff system is unconditionally braced from its first
staff to its last, with no way to intervene.** `m_tracks` is a `std::map<QString,…>`,
so `begin()` is staff "1" and `end()-1` is staff "N".

### Consequence for two-piano MusicXML

| Encoding | Result in Rosegarden |
|---|---|
| **one `<part>`, `<staves>4</staves>`** | 4 tracks, **one curly brace spanning staves 1→4** — visually asserts a single instrument |
| **two `<score-part>`s, each `<staves>2</staves>`** | 4 tracks, **two braces, 1→2 and 3→4** — correct |

**The correct MusicXML for a two-piano score is two parts of two staves each.** The
one-part form cannot express it, because the brace is a per-part scalar
(`setBracketType(int)` at `MusicXMLImportHelper.cpp:310-351` applies to the part's first
or last track only).

Rosegarden's own code admits the difficulty — `MusicXMLXMLHandler.cpp:340-345`:

```
// A simple algorithme is used. Overlapping braces or overlapping brackets
// are not supported. However braces an brackets may overlap.
// A complication arises in case of multi staff systems because these
// will imply a brace which can cause implicit overlapping brace. Since
// these overlapping braces are merges a new, extended multi staff system
// can the result.
```

("multi staff systems … imply a brace which can cause implicit overlapping brace. Since
these overlapping braces are merges a new, extended multi staff system can the result.")

---

## 4. REFINEMENT of `homr#172` fix 2 — necessary but NOT sufficient

Previously stated: *"emit `<staves>N</staves>` with N staves; the dead `upper2`/`lower2`
tokens show the intent was never finished."* That is right about the **note-routing**
corruption and wrong to imply it also fixes the instrument identity.

Corrected statement:

- **Fix 2** (lossless position→staff remap `upper→1, lower→2, upper2→3, lower2→4`,
  plus `<staves>N</staves>`, plus `<staff-details>` for staff-line counts) converts
  **silent musical corruption** into **correct notes on correct staves with an incorrect
  visual grouping**. Today piano II's notes are folded into piano I's bars; after fix 2
  they are on their own staves, under one wide brace.
- **That is still a large, unblocked win** — no model weights required, because the
  decoder already emits four distinguishable position tokens (§36: `decoder_inference.py:124`
  is a 5-way argmax over `{'.', 'upper', 'upper2', 'lower', 'lower2'}` and all four are
  reachable). "Right music, wrong bracket" instead of "wrong music, silently."
- **Fix 3** (brace pairing → part splitting) is what recovers piano I / piano II
  identity, and it remains blocked on weights, because geometry alone cannot distinguish
  brace-over-two-grand-staves from bracket-over-unrelated-staves — which is precisely
  why `homr#62` never closed.
- Also required for fix 2 to be meaningful: `parse_part` currently hardcodes
  `name="Staff %d"` and discards homr's `<part-name>` (§29). Part-name plumbing must
  land with it, or every part is called "Staff 1".

---

## 5. Two genuine bugs found in Rosegarden (reportable upstream)

### 5.1 HIGH — assignment instead of comparison in `endIndication`

`MusicXMLImportHelper.cpp:270`:

```cpp
            ((*i).m_endName == name) &&
            ((*i).m_number = number)) {          // <-- '=' not '=='
```

**Verified unique in the entire source tree**: a grep for assignment-shaped terms
inside a parenthesised condition returns exactly this one line.

Three distinct consequences:

1. The term is an assignment whose value is `number`, which is **non-zero unless the file
   explicitly sets `number="0"`** — the default is 1 (`getAttributeInteger(atts, "number",
   number, false, 1)` at `:568` for `<slur>` and `:1180`/`:1203` for `<wedge>`/`<octave-shift>`).
   MusicXML numbering starts at 1, so for any well-formed file the condition is **always
   true**: the indication **name and number are never actually compared**.
2. `endIndication` therefore matches the **first** pending indication on the same
   staff+voice **of any kind**.
3. It **mutates** `m_number` on every candidate walked past, corrupting stored state
   for later lookups.

**Why this matters here.** `homr` emits unbalanced slurs — 7 starts vs 10 stops on
`scorehalo/data/out-nineties/p0003`, with orphan stops in mm. 10, 11, 12, 17. Every
surplus slur stop will silently attach to whatever pending indication happens to be
first on that staff and voice — a wedge or a tuplet — producing wrong markings with
**no warning at all**. This is exactly the silent-success failure class that motivates
this project.

Fix: `==` in place of `=`.

### 5.2 MEDIUM — `<staff-details>` is warned about but its contents are then dropped

`MusicXMLXMLHandler.cpp:1365-1366`:

```cpp
} else if (m_currentElement == "staff-details") {
    cerrElementNotSupported(m_currentElement);
```

`cerrElementNotSupported` (`:1560-1565`) only logs. It does **not** set `m_ignored`,
unlike `ignoreElement()` (`:1510-1516`) which sets `m_ignored = m_currentElement` and
causes `startElement`/`characters` to skip the whole subtree (`:130`, `:228-232`).

So the children of `<staff-details>` leak into the normal handlers, and the nested
`<staff-details>N</staff-details>` (the staff-line count) matches nothing in either
`startAttributesData` or `endAttributesData` (`:1465-1466` — "no action required here").
**Result: warned once, then silently ignored.**

Rosegarden's export is self-consistent — `MusicXmlExportHelper.cpp:406-418` writes
`<staff-details>` only for a one-line percussion staff and emits nothing for normal
staves. But the practical consequence stands: **a 3-line or 4-line staff (organ,
tablature, some harp music) is silently lost on import.** For piano repertoire this is
harmless; it is recorded because the class of loss is silent, not because it affects us.

---

## 6. What this does NOT establish

- **Nothing was executed.** No claim is backed by a run. Rosegarden cannot be built here.
- §33's framing needs its correction restated precisely: Rosegarden is **not** a
  MusicXML→LilyPond dictionary. It is a **two-hop translator** — `MusicXMLXMLHandler.cpp`
  (MusicXML → per-part track/segment model in `MusicXMLImportHelper`) and
  `LilyPondExporter.cpp` (that model → LilyPond). MusicXML and LilyPond never meet
  directly. That remains the more valuable architectural lesson: ScoreHalo currently
  goes MusicXML → (`musicxml2ly` | our own emitter) → LilyPond, so every hop re-learns
  every element mapping. One intermediate model would have *listed* the required
  pitch/duration/post-event ordering rather than leaving it to be discovered by a
  538-barcheck failure.
- Per-staff **clefs** work (`insertClef` with `number > 0` switches staff,
  `MusicXMLImportHelper.cpp:193-197`). Per-staff **key signatures** do not
  (`insertKey` warns "Different keys on multistaff systems not supported yet", `:169`).
- Rosegarden's MIDI parser was already found to have a signed-`division` bug giving an
  11.24× error in real-time conversion. That is a separate defect in `src/midi/`, and it
  makes Rosegarden a **poor referee for MIDI timing** while leaving it a **good referee
  for MusicXML staff structure** — which is the only thing it was used for here.

---

## 7. Proposed next actions

1. Post §3 + §4 to `liebharc/homr#172` — refines the fix-2 proposal with an
   interoperability proof and an honest statement of what fix 2 does *not* achieve.
2. Ask before filing §5.1 upstream to the Rosegarden tracker: it is a different project
   and a different author, so it is not ours to file unasked.
3. Implement fix 2 in `homr` with the positive-control discipline already used in
   `homr_audit/proof_four_staves.py`: feed four position values through the writer and
   assert the emitted XML routes notes to staves 1/2/3/4 with `<staves>4</staves>`.
   Provable **without** weights — the writer can be exercised directly — but note the
   honest limit: that proves the writer, not that the model emits `upper2`/`lower2` on
   a real two-piano scan. The decoder half stays unverified until weights are available.
