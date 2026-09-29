"""Build a page graph from system-level recognitions and emit MusicXML.

A page is a *sequence* of systems, read top to bottom. The parts inside one
system are simultaneous; the systems themselves are consecutive in time. So a
page is NOT one part per fragment: ``Piano`` appearing in system 1 and again in
system 3 is one continuous part, nine bars long, and must be emitted as one
part -- otherwise the file claims a 4-bar page made of five instruments
sounding together, which is a different piece of music.

Assembly therefore happens in two stages:

1. group the fragments of every system by ROLE (the ``<part-name>``), keeping
   the reading order of the systems;
2. concatenate each role's measures into one continuous part, renumbering
   bars 1..N across the whole page.

Two things then need saying out loud rather than being smoothed over:

- A role that is absent from a system (``Voice`` does not appear in system 1)
  still needs bars there, because parts sound simultaneously. Those bars are
  silence, and silence is *music*, not padding: a singer genuinely is not
  singing while the piano plays the intro.
- If two roles disagree about how many bars a system contains, homr has
  under-detected one of them. The shorter role is padded to the longer, and
  the disagreement is REPORTED, because that gap is recognition error and
  pretending otherwise would hide it.
"""

import argparse
import glob
import json
import os
import sys
from collections import Counter
from dataclasses import replace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "pysrc"))

from scorehalo.emit import GLOBAL_DIVISIONS, emit_page, parse_part  # noqa: E402
from scorehalo.graph import Measure, Page, Part  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("system_dir", help="data/out-system")
    ap.add_argument("--stem", default="p0020",
                    help="page stem, or 'all' for every assembled page")
    ap.add_argument("--out", default=None)
    ap.add_argument("--outdir", default=None,
                    help="destination directory when --stem all")
    ap.add_argument("--report", action="store_true",
                    help="also run the fast invariant checks on each page")
    args = ap.parse_args()

    if args.stem == "all":
        return _build_all(args)

    print("%s:" % args.stem)
    build_one(args.system_dir, args.stem,
              args.out or os.path.join(args.system_dir,
                                       "PAGE-%s.musicxml" % args.stem))
    return 0


def build_one(system_dir, stem, out):
    """Assemble one page from its system fragments. Returns the Page."""
    files = sorted(glob.glob(os.path.join(system_dir, "homr", stem,
                                          "%s.sys*_in.musicxml" % stem)))
    if not files:
        raise SystemExit("no system fragments for %s in %s" % (stem, system_dir))

    page = Page(id=stem, divisions=GLOBAL_DIVISIONS)
    meters = Counter()
    systems = []
    for sysno, f in enumerate(files, start=1):
        parts = parse_part(f, staff_index=1, system=sysno)
        for p in parts:
            for m in p.measures:
                meters[(m.beats, m.beat_type)] += 1
        systems.append((sysno, parts))
        print("  system %d: %d part(s) [%s], %d measures, %d notes"
              % (sysno, len(parts),
                 ", ".join("%s=%d bars" % (p.name, len(p.measures))
                           for p in parts),
                 sum(len(p.measures) for p in parts),
                 sum(len(p.notes) for p in parts)))

    print("  meters seen: %s" % dict(meters))
    if len(meters) > 1:
        (b, bt), n = meters.most_common(1)[0]
        print("  WARNING: systems disagree on meter; page takes %d/%d "
              "(%d of %d measures). Change Page.beats/beat_type to override."
              % (b, bt, n, sum(meters.values())))
        page.beats, page.beat_type = b, bt
    else:
        (page.beats, page.beat_type), _ = meters.most_common(1)[0]

    roles, order = _group_by_role(systems)
    _warn_if_role_sets_disagree(systems, order)
    page.parts = _concat_roles(order, roles, systems, page)
    _renumber(page.parts)

    xml = emit_page(page)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(xml)
    clamped = getattr(emit_page, "last_clamped", 0)
    if clamped:
        print("  NOTE: %d note(s) clamped at a barline -- homr overflowed "
              "the meter. The graph still holds all %d."
              % (clamped, len(page.notes)))

    print("  parts=%d notes=%d measures=%d"
          % (len(page.parts), len(page.notes),
             sum(len(p.measures) for p in page.parts)))
    print("  wrote %s (%d bytes)" % (out, os.path.getsize(out)))
    return page


def _build_all(args):
    """Assemble every page that has system fragments, and report honestly.

    The report is the point. A page that assembles cleanly still hides
    recognition damage -- homr under-detecting a bar, a role that vanished
    from a system, a part that had to be padded -- and those are the numbers
    that decide whether the output is worth proofreading by hand.
    """
    import io
    import contextlib
    import xml.etree.ElementTree as ET

    homr = os.path.join(args.system_dir, "homr")
    stems = sorted(d for d in os.listdir(homr)
                   if glob.glob(os.path.join(homr, d, "%s.sys*_in.musicxml" % d)))
    outdir = args.outdir or os.path.join(args.system_dir, "pages")
    os.makedirs(outdir, exist_ok=True)

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tests"))
    try:
        import test_emit_invariants as inv
    except ImportError:
        inv = None

    rows = []
    for stem in stems:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            try:
                page = build_one(args.system_dir, stem,
                                 os.path.join(outdir, "%s.musicxml" % stem))
            except SystemExit as exc:
                rows.append((stem, "FAIL", str(exc), "", "", ""))
                continue
        log = buf.getvalue()
        warn = log.count("WARNING")
        clamped = 0
        for line in log.splitlines():
            if "clamped at a barline" in line:
                clamped = int(line.split("NOTE: ")[1].split()[0])
        roles = sorted({p.name for p in page.parts})
        bad = []
        if inv is not None:
            root = inv._root(open(os.path.join(outdir,
                                              "%s.musicxml" % stem)).read())
            for check in (inv.check_parts_same_width, inv.check_every_note_typed,
                          inv.check_no_chord_on_rest, inv.check_measures_fill,
                          inv.check_measures_numbered_sequentially):
                bad.extend(check(root))
        rows.append((stem, "ok" if not bad else "INVARIANT",
                     ",".join(roles), warn, clamped, "; ".join(bad[:2])))

    hdr = ("page", "status", "roles", "warnings", "clamped", "invariant")
    print("  %-7s %-10s %-18s %4s %5s  %s" % hdr)
    print("  " + "-" * 78)
    for r in rows:
        print("  %-7s %-10s %-18s %4d %5d  %s" % r)
    print("\n  %d page(s). %d with invariant failures, %d homr warnings, "
          "%d clamped notes." % (len(rows),
                                 sum(1 for r in rows if r[1] != "ok"),
                                 sum(r[3] for r in rows),
                                 sum(r[4] for r in rows)))
    return 0


def _warn_if_role_sets_disagree(systems, order):
    """Report when a page's systems disagree about which roles exist.

    A role that is genuinely absent from a system (no vocal staff on the intro
    system) is fine, and the page is built correctly. A role that vanishes and
    comes back, or two roles trading places from system to system, is a
    different thing: it usually means the recogniser is naming the same staff
    differently on different systems, and then padding the "missing" role with
    silence puts rests where there is actually music.

    This distinction cannot be settled from the MusicXML alone, so the page is
    flagged rather than silently believed. Cross-check it against the staff
    geometry in ``<stem>.boxes.json``: if staves-per-system is uniform and the
    role sets are not, the labels are the unreliable part.
    """
    seen = []
    for sysno, parts in systems:
        names = frozenset(p.name for p in parts)
        seen.append((sysno, names))
    distinct = {n for _s, n in seen}
    if len(distinct) <= 1:
        return
    full = frozenset(order)
    desc = ", ".join("sys%d=%s" % (s, "{" + ",".join(sorted(n)) + "}")
                     for s, n in seen)
    if all(n <= full for _s, n in seen):
        # Every system is a SUBSET of the page's roles: a role may simply not be
        # printed there. Legitimate, but worth seeing.
        print("  NOTE: role sets vary by system (%s). Each is a subset of the "
              "page's roles {%s}, so absent roles are padded with silence. If "
              "this page's staves-per-system is uniform, that silence is "
              "probably a recognition miss, not a rest." % (desc, ",".join(order)))
    else:
        print("  WARNING: a system names a role no other system does (%s) -- "
              "treating it as a separate part. Review this page by hand."
              % desc)


def _group_by_role(systems):
    """Collect each role's fragment across systems, in reading order.

    Returns ``(roles, order)`` where ``roles[name]`` is the list of Parts that
    carry that ``<part-name>``, one per system in which it appears.
    """
    roles, order = {}, []
    for _sysno, parts in systems:
        for p in parts:
            if p.name not in roles:
                roles[p.name] = []
                order.append(p.name)
            roles[p.name].append(p)
    return roles, order


def _concat_roles(order, roles, systems, page):
    """Flatten every role into one continuous part spanning the whole page.

    A role missing from a system contributes that system's worth of silent
    bars, because a part that stops is malformed and because the silence is
    real: the instrument is not playing there.
    """
    # A system's length is the LONGEST role in it. A shorter role in the same
    # system is homr under-detection, so pad and say so.
    sys_width, disagreements = {}, []
    for sysno, parts in systems:
        width = max((len(p.measures) for p in parts), default=0)
        sys_width[sysno] = width
        for p in parts:
            if len(p.measures) < width:
                disagreements.append(
                    (sysno, p.name, len(p.measures), width))
    for sysno, name, got, want in disagreements:
        print("  WARNING: system %d -- %s recognised %d bar(s) but the system "
              "holds %d; padded %d silent bar(s). homr likely missed notes."
              % (sysno, name, got, want, want - got))

    template = None
    for _sysno, parts in systems:
        for p in parts:
            for m in p.measures:
                if template is None or m.key_fifths:
                    template = m
                    break
            if template is not None:
                break
        if template is not None:
            break
    if template is None:
        template = Measure(number=1, divisions=GLOBAL_DIVISIONS)

    out = []
    for i, name in enumerate(order, start=1):
        part = Part(id="P%d" % i, name=name, system=1, staff_index=i)
        by_system = {p.system: p for p in roles[name]}
        for sysno, _parts in systems:
            src = by_system.get(sysno)
            if src is not None:
                part.measures.extend(src.measures)
            else:
                for _ in range(sys_width[sysno]):
                    part.measures.append(Measure(
                        number=0, divisions=GLOBAL_DIVISIONS,
                        beats=template.beats, beat_type=template.beat_type,
                        key_fifths=template.key_fifths, clef=template.clef,
                        is_empty=True))
        out.append(part)
    return out


def _renumber(parts):
    """Renumber measures so each part reads 1..N across the whole page.

    The role NAME is left alone: it came from the recogniser and is the only
    record of which instrument a part is. Renaming it to "Staff 1" would throw
    away the one fact the page assembly depended on.
    """
    for i, p in enumerate(parts, start=1):
        p.id = "P%d" % i
        n = 0
        for m in p.measures:
            n += 1
            m.number = n
            # The NOTES must be renumbered too. A role's measures are stitched
            # from several systems, so system 2's bar 1 becomes page bar 5 --
            # but its notes still carried measure=1. Anything that groups notes
            # by note.measure then swept bars 1, 5 and 9 into page bar 1 and
            # produced duplicate pitches; the MusicXML emitter never noticed
            # because it walks m.notes directly instead of filtering.
            if m.notes:
                m.notes = [replace(note, measure=n) for note in m.notes]
    return parts


if __name__ == "__main__":
    main()
