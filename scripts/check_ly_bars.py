"""Check that every bar in a ``.ly`` file adds up to its own time signature.

Written as an INDEPENDENT reader of LilyPond durations rather than a wrapper
around the emitter's arithmetic. That independence is the point: when the
emitter and the checker share a formula, both can be wrong in the same
direction and the suite stays green over a systematically broken file.

LilyPond's duration grammar, from the manual:

* a duration number ``k`` means **1/k of a whole note**, so ``k = 4/k`` quarter
  notes -- ``1`` is a whole, ``2`` a half, ``4`` a quarter, ``8`` an eighth.
  (It is a denominator, not an exponent. Getting that backwards is exactly how
  the emitter once wrote every note four times too long.)
* ``d`` dots multiply the length by ``2 - 2**-d``.
* ``*n/d`` multiplies the length by ``n/d``.
* inside ``\\tuplet a/b { }`` the written lengths are divided by ``a/b``, so the
  sounding length is ``written * b/a``.

The reader's unit conversions are checked against hand-computed values before it
is trusted with a corpus.
"""

from __future__ import annotations

import re
import sys
from fractions import Fraction
from typing import List, Tuple

#: ``\markup { \typewriter "x=12 y=34" }`` -- digits inside are NOT durations.
MARKUP = re.compile(r'\\markup\s*\{[^}]*\}\s*')
#: A duration token, with optional dots and an optional scale factor.
DUR = re.compile(r"(\d+)(\.*)(\*\d+/\d+)?")
#: Structural constructs that must never contribute length. Each is matched
#: whole, WITH its arguments: stripping only the keyword leaves `4/4` from
#: `\time 4/4` and `c` from `\key c \major`, and those digits then read as
#: quarter notes -- which is how 4 of every 6 bars looked "wrong" once.
NOISE = re.compile(
    r"\\(clef\s+\w+)"          # \clef treble
    r"|\\(key\s+\w+\s+\\(?:major|minor))"  # \key c \major
    r"|\\(time\s+\d+/\d+)"      # \time 4/4
    r"|\\(numericTimeSignature|voiceOne|voiceTwo|voiceThree|language)"
    r"|\\(?:new\s+)?(?:Staff|Voice|ChordVoice|Score|new)\b"
    r"|\\major|\\minor|\\pp|\\p\b|\\ff|\\f\b|\\mp|\\mf|\\fff"
    r"|\\\\"           # the \\ that separates voices in <<>>
    r"|[{}]"
)
TUPLET = re.compile(r"\\tuplet (\d+)/(\d+) \{(.*?)\}", re.S)


def written_quarters(token: str) -> Fraction:
    """Length of one duration token, in quarter notes."""
    match = DUR.fullmatch(token)
    if match is None:
        raise ValueError(f"not a duration: {token!r}")
    number, dots, factor = match.groups()
    quarters = Fraction(4, int(number)) * (Fraction(2) - Fraction(1, 2 ** len(dots)))
    if factor:
        num, den = factor[1:].split("/")
        quarters *= Fraction(int(num), int(den))
    return quarters


def bar_quarters(bar: str, beats: int = 4, beat_type: int = 4) -> Fraction:
    """Sounding length of one bar, honouring ``\\tuplet`` brackets."""
    bar = MARKUP.sub(" ", bar)
    outer = NOISE.sub(" ", TUPLET.sub(" ", bar))
    total = Fraction(0)
    for token in DUR.findall(outer):
        total += written_quarters("".join(token))
    for actual, normal, inner in TUPLET.findall(bar):
        written = Fraction(0)
        for token in DUR.findall(inner):
            written += written_quarters("".join(token))
        # \tuplet a/b divides the written length by a/b.
        total += written * Fraction(int(normal), int(actual))
    return total


def bars(path: str) -> List[Tuple[str, Fraction, Fraction]]:
    """Every bar of every voice, with its measured and expected length."""
    out: List[Tuple[str, Fraction, Fraction]] = []
    for line in open(path):
        if "\\new Voice" not in line:
            continue
        line = MARKUP.sub(" ", line)
        expect = Fraction(4)
        meter = re.search(r"\\time (\d+)/(\d+)", line)
        if meter:
            expect = Fraction(4 * int(meter.group(1)), int(meter.group(2)))
        for text in line.split("|"):
            if not NOISE.sub("", MARKUP.sub(" ", text)).strip():
                continue  # a bar that is only `}` or a stray separator
            out.append((text, bar_quarters(text, expect), expect))
    return out


def self_test() -> None:
    """The reader must be right about the grammar before it judges a file."""
    cases = [
        ("1", Fraction(4), "whole"),
        ("2", Fraction(2), "half"),
        ("4", Fraction(1), "quarter"),
        ("8", Fraction(1, 2), "eighth"),
        ("16", Fraction(1, 4), "sixteenth"),
        ("1.", Fraction(6), "dotted whole"),
        ("4.", Fraction(3, 2), "dotted quarter"),
        ("8..", Fraction(7, 8), "double-dotted eighth"),
        ("4*5/6", Fraction(5, 6), "scaled quarter"),
        ("1*6/1", Fraction(24), "scaled whole"),
    ]
    for token, want, why in cases:
        got = written_quarters(token)
        assert got == want, f"{token} ({why}): {got} != {want}"
    assert bar_quarters("c4 d4 e4 f4") == 4
    assert bar_quarters("c4 d8 e8 f8") == Fraction(5, 2)
    # Three written eighths inside \tuplet 3/2 sound as one quarter note;
    # three written quarters sound as two. This is the whole reason a tuplet
    # has to be bracketed rather than written out as plain notes.
    assert bar_quarters("\\tuplet 3/2 { c8 d8 e8 }") == 1
    assert bar_quarters("\\tuplet 3/2 { c4 c4 c4 }") == 2
    assert bar_quarters("c4 \\tuplet 3/2 { c8 c8 c8 } c2") == 4
    # The digits inside a \typewriter provenance string must not be read as
    # durations: c4 + c2 + c4 = 4 quarters once the markup is removed.
    assert bar_quarters('c4^ \\markup { \\typewriter "x=12 y=34" } c2 c4') == 4
    print("self-test OK: %d token cases, tuplets, markup, and a whole bar" % len(cases))


def main(argv: List[str]) -> int:
    if "--self-test" in argv:
        self_test()
        return 0
    self_test()
    total = wrong = 0
    for path in argv:
        if not path.endswith(".ly"):
            continue
        bad = [(t, got, want) for t, got, want in bars(path) if got != want]
        total += len(bars(path))
        wrong += len(bad)
        if bad:
            print("%s: %d/%d bars wrong" % (path, len(bad), len(bad) and total))
            for text, got, want in bad[:3]:
                body = NOISE.sub(" ", text).strip()
                print("   got %-8s want %-5s  %s" % (got, want, body[:100]))
    print("TOTAL: %d bars, %d not equal to their meter" % (total, wrong))
    return 1 if wrong else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
