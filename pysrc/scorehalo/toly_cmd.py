"""``scorehalo toly``: MusicXML / page graph -> LilyPond, for Frescobaldi.

Writes ``.ly`` rather than shelling out to ``musicxml2ly``. See
:mod:`scorehalo.to_ly` for why that is a different tool and not a worse one.
"""

from __future__ import annotations

import argparse
import os
import sys

from .to_ly import to_ly


def _page_from_musicxml(path: str):
    """Read a MusicXML file into a :class:`~scorehalo.graph.Page`.

    ``emit.parse_part`` is the same reader the rest of ScoreHalo uses, so a
    page round-trips through exactly one parser -- a second parser would be a
    second set of bugs.
    """
    from .emit import GLOBAL_DIVISIONS, parse_part
    from .graph import Measure, Page

    parts = parse_part(path, staff_index=1, system=1)
    page = Page(id=os.path.splitext(os.path.basename(path))[0],
                divisions=GLOBAL_DIVISIONS)
    if parts:
        first = parts[0].measures[0] if parts[0].measures else None
        if first is not None:
            page.beats, page.beat_type = first.beats, first.beat_type
            page.key_fifths = first.key_fifths
    for i, part in enumerate(parts, start=1):
        part.id = "P%d" % i
        part.staff_index = i
        _renumber_notes(part)
        page.parts.append(part)
    return page


def _renumber_notes(part):
    """Keep ``Note.measure`` in step with ``Measure.number``.

    A single MusicXML file numbers its measures 1..N already, but a file
    assembled by the page-graph builder can carry notes whose ``measure``
    disagrees with the bar they sit in; grouping by ``note.measure`` then
    silently sweeps two bars into one.
    """
    from dataclasses import replace
    for m in part.measures:
        if m.notes and any(n.measure != m.number for n in m.notes):
            m.notes = [replace(n, measure=m.number) for n in m.notes]


def cmd_toly(args) -> int:
    page = _page_from_musicxml(args.musicxml)

    out_path = args.out or os.path.splitext(args.musicxml)[0] + ".ly"
    stats: dict = {}
    text = to_ly(
        page,
        version=args.version,
        title=args.title,
        point_and_click=not args.no_point_and_click,
        provenance=not args.no_provenance,
        midi=args.midi,
        snippet=args.snippet,
        stats=stats,
    )
    with open(out_path, "w") as fh:
        fh.write(text)

    print("%s: %d role(s), %d staff(s), %d note(s) (%d pitched), %d tuplet(s)"
          % (os.path.basename(out_path), stats["roles"], stats["staves"],
             stats["notes"], stats["pitched"], stats["tuplets"]))
    # Report the damage rather than hiding it in the file.
    if stats["clamped_notes"]:
        print("  NOTE %d note(s) shortened or dropped to fit their bar; "
              "the rest of that bar is padded with rests." % stats["clamped_notes"])
    if stats["clefs_inferred"]:
        print("  NOTE homr supplied no clef, so %d staff clef(s) were inferred "
              "from the pitch range, not read from the page."
              % stats["clefs_inferred"])
    print("  wrote %s" % out_path)
    return 0


def add_parser(sub):
    p = sub.add_parser(
        "toly",
        help="convert a page of MusicXML to LilyPond for Frescobaldi",
    )
    p.add_argument("musicxml",
                   help="a .musicxml page (assemble pages first with "
                        "scripts/build_page_graph.py --stem all)")
    p.add_argument("--out", help="output .ly path")
    p.add_argument("--title", help="score title (default: the file stem)")
    p.add_argument("--version", default="2.24.3",
                   help="LilyPond \\version to declare (default 2.24.3)")
    p.add_argument("--no-point-and-click", action="store_true",
                   help="omit PDF links back into the .ly")
    p.add_argument("--no-provenance", action="store_true",
                   help="do not annotate notes with their scan pixel")
    p.add_argument("--midi", action="store_true", help="also emit a \\midi block")
    p.add_argument("--snippet", action="store_true",
                   help="wrap in Frescobaldi's %%<< / %%.>> snippet markers")
    p.set_defaults(fn=cmd_toly)
    return p
