"""Extract the real homr symbol sequences from run logs and replay them.

homr prints ``Writing XML [[<symbols>]]`` before converting, and that line is
the actual model output for that crop -- the same list ``music_xml_generator``
receives. That means the bar-length accumulator can be tested against real data
without re-running inference, which takes minutes of CPU per crop.

This module exists because a synthetic repro of a suspected homr bug proved
worthless: with no clef in the input, ``_only_keep_lower_staff_if_there_is_a_
clef`` promoted the lower staff, so the "two staves" never existed and the
measurement described a different bug entirely. The lesson was to test against
what the recogniser actually produced, not against what I imagined it produced.
"""

from __future__ import annotations

import re
from typing import List

#: ``Writing XML [[clef_G2 _ _ _ _ upper, note_8 B4 b _ _ upper, ...]]``
_WRITING = re.compile(r"Writing XML\s*\[\[(.*)\]\]", re.DOTALL)

#: The four trailing fields are always present, whatever the symbol is.
_TRAILING = ("lift", "articulation", "slur", "position")


def parse_symbols(log_text: str) -> List[tuple]:
    """The symbol tuples in a homr log, in the order homr printed them.

    Returns ``(rhythm, pitch, lift, articulation, slur, position)`` tuples,
    with ``pitch`` set to ``"_"`` where the symbol has no pitch.

    The line is *not* a fixed six fields. ``note_8 B4 b _ _ upper`` has a pitch
    and ``clef_G2 _ _ _ _ upper`` does not, so taking the last six fields --
    which is what I did first -- silently shifted every non-note symbol by one
    and produced ``("clef_G2", "_", "_", "_", "_", "upper")``: a clef with a
    pitch of "_" and a lift of "_". It parsed without complaint and was wrong.

    So parse from the right: the last four fields are always
    lift/articulation/slur/position, and whatever remains is the rhythm, plus a
    pitch if there were two fields left rather than one.
    """
    match = _WRITING.search(log_text)
    if not match:
        return []
    out: List[tuple] = []
    for piece in match.group(1).split(","):
        fields = piece.split()
        if len(fields) < 5:
            continue
        position = fields[-1]
        slur = fields[-2]
        articulation = fields[-3]
        lift = fields[-4]
        rest = fields[:-4]
        if len(rest) >= 2:
            rhythm, pitch = rest[0], rest[1]
        else:
            rhythm, pitch = rest[0], "_"
        out.append((rhythm, pitch, lift, articulation, slur, position))
    return out


def read_log(path: str) -> List[tuple]:
    with open(path, encoding="utf-8", errors="replace") as handle:
        return parse_symbols(handle.read())