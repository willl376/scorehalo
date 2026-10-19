"""Tests for page-wide meter reconciliation.

Run from the scorehalo repo root::

    .venv/bin/python -m unittest tests.test_meter
"""

import unittest

from scorehalo.graph import Measure, Note, Page, Part, Pitch
from scorehalo.meter import (MeterOverride, declared_meters, format_overrides,
                             measure_extent, reconcile_meter,
                             resolve_page_meter)


def _note(staff=1, voice=1, onset=0, duration=4, step="C", octave=4,
          is_rest=False, measure=1):
    return Note(staff=staff, voice=voice, onset=onset, duration=duration,
                pitch=None if is_rest else Pitch(step, 0, octave),
                measure=measure, is_rest=is_rest)


def _measure(number=1, notes=(), beats=4, beat_type=4, divisions=12,
             is_empty=False):
    return Measure(number=number, divisions=divisions, beats=beats,
                   beat_type=beat_type, notes=list(notes), is_empty=is_empty)


def _page(meters, divisions=12):
    """One part whose measures carry the given (beats, beat_type) pairs."""
    part = Part(id="P1", name="Piano")
    for i, (beats, beat_type) in enumerate(meters, start=1):
        part.measures.append(_measure(number=i, beats=beats,
                                      beat_type=beat_type, divisions=divisions))
    page = Page(id="p", divisions=divisions)
    page.parts.append(part)
    return page


class TestMeasureExtent(unittest.TestCase):

    def test_empty_measure_has_no_extent(self):
        self.assertEqual(measure_extent(_measure()), 0)

    def test_extent_is_furthest_end_of_one_line(self):
        m = _measure(notes=[_note(onset=0, duration=4), _note(onset=4, duration=4)])
        self.assertEqual(measure_extent(m), 8)

    def test_two_staves_do_not_double_the_bar(self):
        """Simultaneity is equality of onsets, so a grand staff is one bar.

        Summing every note would report 96 ticks for a 48-tick piano bar and
        make every grand staff look overfull -- which is the mistake that would
        have made this module override every measure on the page.
        """
        m = _measure(notes=[
            _note(staff=1, onset=0, duration=24),
            _note(staff=1, onset=24, duration=24),
            _note(staff=2, onset=0, duration=24),
            _note(staff=2, onset=24, duration=24),
        ])
        self.assertEqual(measure_extent(m), 48)

    def test_two_voices_do_not_double_the_bar(self):
        m = _measure(notes=[
            _note(voice=1, onset=0, duration=24),
            _note(voice=1, onset=24, duration=24),
            _note(voice=2, onset=0, duration=24),
            _note(voice=2, onset=24, duration=24),
        ])
        self.assertEqual(measure_extent(m), 48)

    def test_the_longest_single_line_wins(self):
        m = _measure(notes=[
            _note(voice=1, onset=0, duration=12),
            _note(voice=2, onset=0, duration=48),
        ])
        self.assertEqual(measure_extent(m), 48)


class TestReconcileMeter(unittest.TestCase):

    def setUp(self):
        self.reconcile = reconcile_meter

    def test_overrunning_measure_is_fixed(self):
        page = _page([(3, 4), (3, 4)])
        page.beats, page.beat_type = 4, 4
        page.parts[0].measures[0].notes = [
            _note(onset=0, duration=24), _note(onset=24, duration=24)]
        out = self.reconcile(page)
        self.assertEqual(len(out), 1)
        self.assertEqual(page.parts[0].measures[0].beats, 4)
        self.assertEqual(out[0].was, (3, 4))
        self.assertEqual(out[0].now, (4, 4))

    def test_a_genuine_meter_change_is_left_alone(self):
        """Content that fits its own declared meter is not evidence of error."""
        page = _page([(4, 4), (3, 4)])
        page.beats, page.beat_type = 4, 4
        page.parts[0].measures[1].notes = [
            _note(onset=0, duration=12), _note(onset=12, duration=12),
            _note(onset=24, duration=12)]
        out = self.reconcile(page)
        self.assertEqual(out, [])
        self.assertEqual(page.parts[0].measures[1].beats, 3)

    def test_measure_that_fits_neither_is_left_alone(self):
        """No page meter explains it, so guessing would only add noise."""
        page = _page([(3, 4)])
        page.beats, page.beat_type = 4, 4
        page.parts[0].measures[0].notes = [_note(onset=0, duration=96)]
        self.assertEqual(self.reconcile(page), [])
        self.assertEqual(page.parts[0].measures[0].beats, 3)

    def test_empty_measures_are_untouched(self):
        page = _page([(4, 4)])
        page.beats, page.beat_type = 4, 4
        page.parts[0].measures[0] = _measure(number=1, beats=3, beat_type=4,
                                             is_empty=True)
        self.assertEqual(self.reconcile(page), [])
        self.assertEqual(page.parts[0].measures[0].beats, 3)

    def test_measure_already_at_the_page_meter_is_untouched(self):
        page = _page([(4, 4)])
        page.beats, page.beat_type = 4, 4
        page.parts[0].measures[0].notes = [_note(onset=0, duration=96)]
        self.assertEqual(self.reconcile(page), [])

    def test_rests_count_towards_the_extent(self):
        """A rest is content too: it fills a bar just as a note does."""
        page = _page([(3, 4)])
        page.beats, page.beat_type = 4, 4
        page.parts[0].measures[0].notes = [
            _note(onset=0, duration=24, is_rest=True),
            _note(onset=24, duration=12, is_rest=True),
            _note(onset=36, duration=12, is_rest=True)]
        self.assertEqual(len(self.reconcile(page)), 1)


class TestResolvePageMeter(unittest.TestCase):

    def setUp(self):
        self.resolve = resolve_page_meter

    def test_modal_meter_wins(self):
        page = _page([(4, 4), (4, 4), (3, 4)])
        self.assertEqual(self.resolve(page), (4, 4))
        self.assertEqual((page.beats, page.beat_type), (4, 4))

    def test_a_long_system_outvotes_a_short_one(self):
        """Counts are over measures, not fragments, so length carries weight."""
        page = _page([(4, 4)] * 9 + [(3, 4)])
        self.assertEqual(self.resolve(page), (4, 4))

    def test_minority_meter_can_win_if_it_has_more_measures(self):
        page = _page([(3, 4)] * 6 + [(4, 4)])
        self.assertEqual(self.resolve(page), (3, 4))

    def test_tie_breaks_toward_four_four(self):
        page = _page([(4, 4), (3, 4)])
        self.assertEqual(self.resolve(page), (4, 4))

    def test_page_with_no_measures_keeps_its_meter(self):
        page = Page(id="p", divisions=12, beats=4, beat_type=4)
        self.assertEqual(self.resolve(page), (4, 4))


class TestFormatOverrides(unittest.TestCase):

    def test_empty_is_empty_string(self):
        self.assertEqual(format_overrides([]), "")

    def test_states_the_loss_it_prevents(self):
        text = format_overrides([MeterOverride(
            part="Piano", measure=1, staff=1, was=(3, 4), now=(4, 4),
            needed=48, declared_held=36, now_holds=48)])
        self.assertIn("3/4 -> 4/4", text)
        self.assertIn("would have been dropped", text)

    def test_reports_the_declared_length_not_the_replacement(self):
        """A 3/4 bar at divisions=12 holds 36 ticks, and must say so.

        Measure.length is derived from beats/beat_type, so reading it after the
        override reports the new length and the message claims the short bar
        always held the long one's worth.
        """
        from scorehalo.meter import MeterOverride
        page = _page([(3, 4)])
        page.beats, page.beat_type = 4, 4
        page.parts[0].measures[0].notes = [
            _note(onset=0, duration=24), _note(onset=24, duration=24)]
        out = reconcile_meter(page)
        self.assertEqual(out[0].declared_held, 36)
        self.assertEqual(out[0].now_holds, 48)
        self.assertIn("the declared bar held 36", format_overrides(out))


class TestRealPageP0020(unittest.TestCase):
    """The failure this module was written for, on the real page.

    Skipped unless the assembled page and its fragments are on disk, so this
    file stays green in a checkout with no data.
    """

    def setUp(self):
        import os
        import sys
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from tests.test_emit_invariants import _assemble_real_page
        # reconcile=False on purpose: these tests need the UNFIXED page so they
        # can show the loss and then show it gone. The shipping path reconciles.
        self.page, self.order, self.nsystems = _assemble_real_page(
            reconcile=False)

    def test_p0020_has_a_meter_disagreement_to_reconcile(self):
        """Guard: if homr ever stops disagreeing, this test must be revisited
        rather than silently passing for the wrong reason."""
        self.assertGreater(len(declared_meters(self.page)), 1)

    def test_p0020_recovers_the_four_dropped_notes(self):
        """128 pitched notes in the graph; 124 reached the file.

        The four are the notes at or past the 3/4 barline in system 1. Fixing
        the misread meter must get all four back.
        """
        import xml.etree.ElementTree as ET
        from scorehalo.emit import emit_page

        graph = sum(1 for n in self.page.notes if not n.is_rest)
        before = ET.fromstring(emit_page(self.page))
        before_count = sum(1 for p in before.findall("part")
                           for m in p.findall("measure")
                           for n in m.findall("note") if n.find("rest") is None)

        overrides = reconcile_meter(self.page)
        after = ET.fromstring(emit_page(self.page))
        after_count = sum(1 for p in after.findall("part")
                          for m in p.findall("measure")
                          for n in m.findall("note") if n.find("rest") is None)

        self.assertEqual(graph, 128)
        self.assertEqual(before_count, 124)          # the loss, reproduced
        self.assertTrue(overrides, "expected at least one meter override")
        self.assertEqual(after_count, graph)         # nothing lost now

    def test_the_shipped_page_already_loses_nothing(self):
        """The production path reconciles, so it must never show the loss.

        The test above proves the fix works. This one proves it is actually
        wired up: _assemble_real_page grew its own copy of the assembly logic
        instead of calling the production path, so for a while the production
        bug was fixed while these tests still measured the broken behaviour --
        and both were green.
        """
        import os
        import sys
        import xml.etree.ElementTree as ET
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from scorehalo.emit import emit_page
        from tests.test_emit_invariants import _assemble_real_page

        page, _order, _n = _assemble_real_page()      # the shipping default
        graph = sum(1 for n in page.notes if not n.is_rest)
        shipped = sum(1 for p in ET.fromstring(emit_page(page)).findall("part")
                      for m in p.findall("measure")
                      for n in m.findall("note") if n.find("rest") is None)
        self.assertEqual(shipped, graph)


if __name__ == "__main__":
    unittest.main()