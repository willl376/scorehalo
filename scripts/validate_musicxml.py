"""Strict structural validation of a MusicXML file.

MuseScore's headless importer reports a silent rc=40 for a dozen different
causes, which makes it useless for diagnosis. This walks every measure the way
a strict importer does and names the actual violation, so a malformed file can
be fixed instead of guessed at.
"""

import sys
import xml.etree.ElementTree as ET


def check(path, verbose=True):
    root = ET.parse(path).getroot()
    problems = []
    parts = root.findall("part")
    declared = {sp.get("id") for sp in root.findall("part-list/score-part")}
    for p in parts:
        if p.get("id") not in declared:
            problems.append("part %s missing from <part-list>" % p.get("id"))

    for p in parts:
        divisions = None
        staves = None
        # MusicXML attributes are inherited: <time> appears only when the
        # meter changes, exactly as <divisions> and <staves> do. Keeping the
        # meter in the loop reset it to 4/4 on every measure that did not
        # restate it, so a 6/8 piece was read as 6/8 only in its first bar
        # and "bar short" against 4/4 everywhere after -- a false alarm that
        # said nothing about the file and everything about this checker.
        beats = None
        beat_type = None
        pid = p.get("id")
        for mi, m in enumerate(p.findall("measure"), start=1):
            where = "part %s measure %s" % (pid, m.get("number"))
            a = m.find("attributes")
            if a is not None and a.findtext("divisions"):
                divisions = int(a.findtext("divisions"))
            if a is not None and a.findtext("staves"):
                staves = int(a.findtext("staves"))
            if a is not None and a.find("time") is not None:
                beats = int(a.findtext("time/beats") or 4)
                beat_type = int(a.findtext("time/beat-type") or 4)
            if divisions is None:
                problems.append("%s: no <divisions> in effect" % where)
                break
            if beats is None or beat_type is None:
                problems.append("%s: no <time> in effect" % where)
                break
            expected = beats * divisions * 4 // beat_type

            # order matters: attributes must precede notes
            kids = [e.tag for e in m]
            if "attributes" in kids and "note" in kids:
                if kids.index("attributes") > kids.index("note"):
                    problems.append("%s: <attributes> after notes" % where)

            cursor = 0
            for el in m:
                if el.tag in ("note", "forward", "backup"):
                    # Which staff does this land on, and did the part ever
                    # declare it has that many? MuseScore's "illegal staff '2'"
                    # came from notes on staff 2 of a part whose <staves> was
                    # never emitted -- a whole page of errors this validator
                    # read as clean, because it never joined the two fields.
                    ref = el.findtext("staff")
                    if ref:
                        n = int(ref)
                        # Absent <staves> defaults to 1, so a lone <staff>1</staff>
                        # is legal redundancy; what cannot happen is a reference
                        # to staff 2 of a part that never said it has two.
                        limit = 1 if staves is None else staves
                        if n > limit:
                            declared = ("no <staves> declared (defaults to 1)"
                                        if staves is None
                                        else "<staves> declares %d" % staves)
                            problems.append(
                                "%s: lands on staff %d but %s"
                                % (where, n, declared))
                    dur = el.findtext("duration")
                    d = int(dur) if dur else 0
                    if el.tag == "backup":
                        # Remember where the cursor stood before this backup
                        # so an overshoot can report the real "before" value
                        # instead of echoing the same number twice.
                        prev_cursor = cursor
                        cursor -= d
                        if cursor < 0:
                            problems.append(
                                "%s: <backup> overshoots to %d "
                                "(cursor before backup was %d)"
                                % (where, cursor, prev_cursor))
                    elif el.tag == "forward":
                        # <forward> is only valid in multi-voice measures --
                        # it fills a later voice's gap up to the cursor while
                        # <backup> rewinds for the layer above it. Well-formed
                        # backup/forward pairs keep the end-of-bar cursor
                        # honest, so no separate allowance is needed here.
                        cursor += d
                    else:
                        if el.find("chord") is None:
                            cursor += d
                elif el.tag in ("direction", "print", "barline", "harmony"):
                    pass
                elif el.tag not in ("attributes", "sound"):
                    pass
            if cursor > expected:
                problems.append("%s: bar overflows: %d > %d ticks"
                                % (where, cursor, expected))
            elif cursor < expected:
                problems.append("%s: bar short: %d of %d ticks"
                                % (where, cursor, expected))
    if verbose:
        if problems:
            print("  %d problem(s):" % len(problems))
            for x in problems[:25]:
                print("    - %s" % x)
            if len(problems) > 25:
                print("    ... and %d more" % (len(problems) - 25))
        else:
            print("  clean: %d parts, %d measures, no violations"
                  % (len(parts), len(root.findall(".//measure"))))
    return problems


if __name__ == "__main__":
    for path in sys.argv[1:]:
        print(path)
        check(path)
