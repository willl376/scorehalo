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
        pid = p.get("id")
        for mi, m in enumerate(p.findall("measure"), start=1):
            where = "part %s measure %s" % (pid, m.get("number"))
            a = m.find("attributes")
            if a is not None and a.findtext("divisions"):
                divisions = int(a.findtext("divisions"))
            if divisions is None:
                problems.append("%s: no <divisions> in effect" % where)
                break
            beats = 4
            beat_type = 4
            if a is not None and a.find("time") is not None:
                beats = int(a.findtext("time/beats") or 4)
                beat_type = int(a.findtext("time/beat-type") or 4)
            expected = beats * divisions * 4 // beat_type

            # order matters: attributes must precede notes
            kids = [e.tag for e in m]
            if "attributes" in kids and "note" in kids:
                if kids.index("attributes") > kids.index("note"):
                    problems.append("%s: <attributes> after notes" % where)

            cursor = 0
            lowest = 0
            for el in m:
                if el.tag in ("note", "forward", "backup"):
                    dur = el.findtext("duration")
                    d = int(dur) if dur else 0
                    if el.tag == "backup":
                        cursor -= d
                        lowest = min(lowest, cursor)
                    elif el.tag == "forward":
                        cursor += d
                    else:
                        if el.find("chord") is None:
                            cursor += d
                elif el.tag in ("direction", "print", "barline", "harmony"):
                    pass
                elif el.tag not in ("attributes", "sound"):
                    pass
            if lowest < 0:
                problems.append("%s: <backup> overshoots to %d "
                                "(cursor before it was %d)"
                                % (where, lowest, lowest + 0))
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
        else:
            print("  clean: %d parts, %d measures, no violations"
                  % (len(parts), len(root.findall(".//measure"))))
    return problems


if __name__ == "__main__":
    for path in sys.argv[1:]:
        print(path)
        check(path)
