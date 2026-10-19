"""The validator must read the fields MuseScore actually enforces.

The blind spot this file exists for: an 81-error page came back "clean"
because ``<staves>`` was never checked against the ``<staff>`` tags notes
land on. MuseScore reads both and refuses the file; a checker that reads
one of two fields is a rubber stamp, and "clean" from a rubber stamp is
worse than no check -- it is an agreement that measured nothing.
"""

import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from validate_musicxml import check  # noqa: E402


def _note(staff=None, dur=4):
    ref = "<staff>%d</staff>" % staff if staff else ""
    return ("<note><pitch><step>C</step><octave>4</octave></pitch>"
            "<duration>%d</duration>%s</note>" % (dur, ref))


def _score(body, staves=None):
    attrs = "<divisions>1</divisions><time><beats>4</beats>" \
            "<beat-type>4</beat-type></time>"
    if staves is not None:
        attrs += "<staves>%d</staves>" % staves
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<score-partwise version="4.0">\n'
        '  <part-list><score-part id="P1"><part-name>P</part-name>'
        '</score-part></part-list>\n'
        '  <part id="P1"><measure number="1">\n'
        '    <attributes>%s</attributes>\n'
        '    %s\n'
        '  </measure></part>\n'
        '</score-partwise>\n' % (attrs, body))


class StaffDeclarationTests(unittest.TestCase):

    def _check(self, xml):
        with tempfile.NamedTemporaryFile(
                "w", suffix=".musicxml", delete=False) as fh:
            fh.write(xml)
            path = fh.name
        try:
            return check(path, verbose=False)
        finally:
            os.unlink(path)

    def test_a_declared_grand_staff_is_clean(self):
        body = (_note(staff=1) +
                "<backup><duration>4</duration></backup>" +
                _note(staff=2))
        self.assertEqual(self._check(_score(body, staves=2)), [])

    def test_a_plain_single_staff_file_is_clean(self):
        # No <staff> tags anywhere and no <staves>: the ordinary page.
        # The check must not invent a problem from absence alone.
        self.assertEqual(self._check(_score(_note())), [])

    def test_staff_one_without_a_declaration_is_clean(self):
        """Absent <staves> defaults to 1, so <staff>1</staff> is redundancy,
        not a violation -- the fresh per-staff page emits exactly this on
        its single-staff Voice part and MuseScore accepts it."""
        self.assertEqual(self._check(_score(_note(staff=1))), [])

    def test_staff_two_of_an_undeclared_part_is_flagged(self):
        """The 81-error blind spot, verbatim.

        Notes landing on staff 2 of a part that never declared <staves>
        made MuseScore print "illegal staff '2'" down the page while this
        validator reported clean -- the two fields were never joined.
        """
        problems = self._check(_score(_note(staff=2)))
        self.assertTrue(
            any("defaults to 1" in p for p in problems),
            "a staff-2 reference with no declaration must be named, got %r"
            % problems)

    def test_staff_beyond_the_declared_count_is_flagged(self):
        problems = self._check(_score(_note(staff=3), staves=2))
        self.assertTrue(
            any("declares 2" in p for p in problems),
            "staff 3 of a 2-staff part must be named, got %r" % problems)


def _score_of(measures, time="<time><beats>6</beats><beat-type>8</beat-type>"
                                   "</time>", divisions=2):
    """A score whose measures are given verbatim, meter/divisions on bar 1."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<score-partwise version="4.0">\n'
        '  <part-list><score-part id="P1"><part-name>P</part-name>'
        '</score-part></part-list>\n'
        '  <part id="P1">%s</part>\n'
        '</score-partwise>\n' % "".join(measures))


def _bar(number, body, attrs=None):
    a = "<attributes>%s</attributes>" % attrs if attrs else ""
    return '<measure number="%d">%s%s</measure>' % (number, a, body)


class MeterInheritanceTests(unittest.TestCase):

    def _check(self, xml):
        with tempfile.NamedTemporaryFile(
                "w", suffix=".musicxml", delete=False) as fh:
            fh.write(xml)
            path = fh.name
        try:
            return check(path, verbose=False)
        finally:
            os.unlink(path)

    # Six eighth notes at divisions=2 is exactly one 6/8 bar (6 ticks).
    _six_eighths = "".join(_note(dur=1) for _ in range(6))

    def test_meter_carries_into_later_measures(self):
        """<time> is an attribute, so it is declared once and inherited.

        A 6/8 piece that names its meter only in bar 1 must not be measured
        against a default 4/4 from bar 2 on -- else every bar after the first
        reads "short" and "clean" becomes a lie.
        """
        xml = _score_of([
            _bar(1, self._six_eighths,
                 attrs="<divisions>2</divisions>"
                       "<time><beats>6</beats><beat-type>8</beat-type></time>"),
            _bar(2, self._six_eighths),
        ])
        self.assertEqual(self._check(xml), [])

    def test_a_short_bar_in_an_inherited_meter_is_still_caught(self):
        # The fix must not swallow real short bars, only the inherited meter.
        xml = _score_of([
            _bar(1, self._six_eighths,
                 attrs="<divisions>2</divisions>"
                       "<time><beats>6</beats><beat-type>8</beat-type></time>"),
            _bar(2, "".join(_note(dur=1) for _ in range(4))),
        ])
        problems = self._check(xml)
        self.assertTrue(
            any("bar short" in p and "4 of 6" in p for p in problems),
            "a genuinely short 6/8 bar must still be flagged, got %r"
            % problems)

    def test_a_meter_change_re_anchors_the_expectation(self):
        xml = _score_of([
            _bar(1, self._six_eighths,
                 attrs="<divisions>2</divisions>"
                       "<time><beats>6</beats><beat-type>8</beat-type></time>"),
            _bar(2, "".join(_note(dur=2) for _ in range(4)),
                 attrs="<time><beats>4</beats><beat-type>4</beat-type></time>"),
        ])
        self.assertEqual(self._check(xml), [])


if __name__ == "__main__":
    unittest.main()
