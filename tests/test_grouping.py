"""Tests for grouping per-staff fragments into the parts of a page.

homr returns one single-staff part per staff, each labelled with a role name
that carries no information. The page assembler used to hand those fragments to
the engraver as they arrived, which produced a score whose "instruments" were
really just staves: a piano's two staves became two instruments, and the same
piano appearing in two systems became two more.

The rules under test, in order of how quietly they can go wrong:

* a treble staff directly above a bass staff is one instrument (two staves);
* a lone staff above a grand staff is the solo line, not another instrument;
* a part seen in more than one system is the same part continuing, so its bars
  concatenate and renumber across systems;
* no note is ever lost or invented by grouping.

The last one is the assertion that matters most: grouping is a *decision about
ownership*, and the test that would catch a wrong decision is comparing total
note counts against the fragments, not comparing part names.

Run: .venv/bin/python -m unittest discover -s tests -v
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pysrc"))

from scorehalo.graph import Measure, Note, Part, Pitch
from scorehalo.grouping import (SOLO_ROLE, TWO_STAFF_ROLE, assemble_page,
                                role_staves, system_offsets)

G = ("G", 2)
F = ("F", 4)


def staff(count, notes_per_measure=2, step="c", octave=4):
    part = Part(id="S", name="Voice")
    for number in range(1, count + 1):
        notes = [Note(staff=1, voice=1, onset=i, duration=1,
                      pitch=Pitch(step=step, alter=0, octave=octave),
                      measure=number) for i in range(notes_per_measure)]
        part.measures.append(Measure(number=number, notes=notes))
    return part


def pitched(holder):
    measures = holder.measures if hasattr(holder, "measures") else [holder]
    return [n for m in measures for n in m.notes if not n.is_rest]


class RoleStavesTests(unittest.TestCase):
    """Pairing inside one system."""

    def test_treble_over_bass_is_one_part(self):
        self.assertEqual(role_staves([G, F]), [[0, 1]])

    def test_two_treble_staves_stay_separate(self):
        self.assertEqual(role_staves([G, G]), [[0], [1]])

    def test_bass_over_treble_is_not_a_grand_staff(self):
        self.assertEqual(role_staves([F, G]), [[0], [1]])

    def test_solo_above_grand_staff_is_three_roles(self):
        self.assertEqual(role_staves([G, G, F]), [[0], [1, 2]])

    def test_unknown_clef_does_not_pair(self):
        self.assertEqual(role_staves([G, None]), [[0], [1]])

    def test_orchestra_four_single_staves(self):
        self.assertEqual(role_staves([G, G, G, G]), [[0], [1], [2], [3]])

    def test_two_grand_staves_plus_solo(self):
        self.assertEqual(role_staves([G, G, F, G, F]), [[0], [1, 2], [3, 4]])


class BraceDrivenTests(unittest.TestCase):
    """Brace geometry decides the pairing; clefs only fill in for a missing brace.

    The layouts below are the reason the clef rule was demoted. ``(G, G, F)``
    reads identically for a singer on top and a singer in the middle, so no
    amount of clef work can separate them -- but the braces are different and
    the answer is not.
    """

    def test_singer_above_a_braced_pair(self):
        self.assertEqual(role_staves([G, G, F], [None, "p", "p"]),
                         [[0], [1, 2]])

    def test_singer_below_a_braced_pair(self):
        """A layout the fallback also gets right -- recorded as such.

        Clefs ``(G, F, G)``: the fallback's rule pairs staves 1-2 on treble over
        bass and leaves staff 3 alone, which is the correct answer here. It is
        right by coincidence of position, not by evidence, so this test pins
        the *agreement* rather than claiming the braces were needed. Mutation
        testing found this test passing with brace geometry switched off
        entirely, which is why it is labelled this way.
        """
        self.assertEqual(role_staves([G, F, G], ["p", "p", None]),
                         [[0, 1], [2]])

    def test_singer_between_two_braced_groups(self):
        """Clefs here match singer-on-top exactly; the braces do not."""
        self.assertEqual(role_staves([G, G, F], ["a", None, "b"]),
                         [[0], [1], [2]])

    def test_piano_only_system_has_no_singer(self):
        """Every staff braced: nothing unbraced, so no solo part is invented."""
        self.assertEqual(role_staves([G, F], ["p", "p"]), [[0, 1]])

    def test_three_staff_brace_is_one_part(self):
        self.assertEqual(role_staves([G, F, G], ["p", "p", "p"]),
                         [[0, 1, 2]])

    def test_two_braced_groups_stay_separate(self):
        """A harp beside a piano is two parts, not one four-staff part."""
        self.assertEqual(role_staves([G, F, G, F], ["a", "a", "b", "b"]),
                         [[0, 1], [2, 3]])

    def test_brace_over_a_noncontiguous_run_raises(self):
        """A brace spans a continuous run; a gap means the geometry is misread."""
        with self.assertRaises(ValueError):
            role_staves([G, G, F, F], ["p", None, "p", "p"])

    def test_brace_ids_length_must_match_staves(self):
        with self.assertRaises(ValueError):
            role_staves([G, F], ["p"])

    def test_missing_geometry_warns_rather_than_guessing_silently(self):
        with self.assertWarns(RuntimeWarning):
            role_staves([G, G, F])

    def test_supplied_geometry_does_not_warn(self):
        import warnings
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            role_staves([G, G, F], [None, "p", "p"])
        self.assertEqual([w for w in caught
                          if issubclass(w.category, RuntimeWarning)], [])


class BraceAssembleTests(unittest.TestCase):
    """``assemble_page`` with real brace geometry: the p0020 page as printed.

    Brace ids measured from ``data/out-band/render/p0020.png`` and confirmed by
    Wilbur reading the scan -- staves 1-2 braced in system 1; staves 4-5 braced
    under the singer in system 2 ("Slowly" above staff 3); staves 7-8 braced
    under the singer in system 3. One singer throughout, piano under her.
    """

    def setUp(self):
        self.staves = [
            staff(4, step="e", octave=5),   # s1  sys1 piano RH
            staff(4, step="g", octave=3),   # s2  sys1 piano LH
            staff(3, step="c", octave=5),   # s3  sys2 voice
            staff(3, step="a", octave=4),   # s4  sys2 piano RH
            staff(3, step="b", octave=2),   # s5  sys2 piano LH
            staff(3, step="d", octave=5),   # s6  sys3 voice
            staff(3, step="f", octave=4),   # s7  sys3 piano RH
            staff(3, step="e", octave=2),   # s8  sys3 piano LH
        ]
        self.systems = [1, 1, 2, 2, 2, 3, 3, 3]
        self.clefs = [G, F, G, G, F, G, G, F]
        self.braces = ["p", "p", None, "p", "p", None, "p", "p"]
        self.page = assemble_page(self.staves, self.systems, self.clefs,
                                 brace_ids=self.braces, id="p0020")

    def test_the_page_is_one_piano_and_one_voice(self):
        # Singer first: systems 2 and 3 both print her above the piano,
        # and that vote outranks system 1's accident of being seen first.
        self.assertEqual([p.name for p in self.page.parts],
                         [SOLO_ROLE, TWO_STAFF_ROLE])

    def test_the_singer_prints_above_the_piano_despite_the_intro(self):
        """Vertical order is a vote across systems, not first appearance.

        System 1 shows the piano alone -- nothing to compare -- and the
        piano is what the page is seen doing first, so first-appearance
        order would engrave the singer UNDER the accompaniment while the
        book itself says "piano under her". Systems 2 and 3 both print
        her first, and the vote is what puts her on top.
        """
        self.assertEqual([p.name for p in self.page.parts],
                         [SOLO_ROLE, TWO_STAFF_ROLE])
        # The piano still owns the intro bars; ordering never moves music.
        self.assertTrue(all(pitched(self.page.parts[1].measures[0])))

    def test_piano_owns_two_staves_and_spans_every_bar(self):
        piano = self.page.parts[1]
        self.assertEqual(len(piano.clef_by_staff), 2)
        self.assertEqual([m.number for m in piano.measures], list(range(1, 11)))

    def test_voice_is_tildes_that_staff_the_piano_is_not(self):
        """The singer is staves 3 and 6 -- the two unbraced ones."""
        voice = self.page.parts[0]
        self.assertEqual(len(voice.clef_by_staff), 1)
        singing = {n.pitch.octave for m in voice.measures
                   for n in pitched(m)}
        self.assertEqual(singing, {5}, "staves 3 and 6, both octave 5")

    def test_intro_bars_are_silent_for_the_voice_only(self):
        voice = self.page.parts[0]
        silent = [m.number for m in voice.measures if not pitched(m)]
        self.assertEqual(silent, [1, 2, 3, 4])
        self.assertTrue(all(pitched(self.page.parts[1].measures[0])))

    def test_no_note_is_lost_or_invented(self):
        self.assertEqual(sum(len(pitched(s)) for s in self.staves), 52)
        self.assertEqual(sum(len(pitched(p)) for p in self.page.parts), 52)

    def test_the_clef_fallback_agrees_on_this_page(self):
        """Coincidence, not a licence: p0020 happens to be singer-on-top.

        Recorded so that a future change to the fallback cannot silently alter
        this page without anyone noticing it was the *only* reason to care.
        """
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            fallback = assemble_page(self.staves, self.systems, self.clefs,
                                     id="p0020")
        self.assertEqual([p.name for p in fallback.parts],
                         [p.name for p in self.page.parts])
        self.assertEqual(sum(len(p.clef_by_staff) for p in fallback.parts),
                         sum(len(p.clef_by_staff) for p in self.page.parts))


class SingerBelowTests(unittest.TestCase):
    """``assemble_page`` must not need the singer on top."""

    def _page(self, braces, clefs):
        staves = [staff(2, step="e", octave=5), staff(2, step="g", octave=3),
                  staff(2, step="a", octave=4)]
        return assemble_page(staves, [1, 1, 1], clefs, brace_ids=braces,
                             id="below")

    def test_singer_below_yields_piano_over_voice(self):
        page = self._page(["p", "p", None], [G, F, G])
        self.assertEqual([p.name for p in page.parts],
                         [TWO_STAFF_ROLE, SOLO_ROLE])
        self.assertEqual([len(p.clef_by_staff) for p in page.parts], [2, 1])

    def test_singer_between_two_braced_groups_splits_three_parts(self):
        """Three one-staff parts, not a two-staff piano plus a singer.

        The layout the clef fallback cannot see: clefs ``(G, G, F)`` here are
        byte-identical to singer-on-top, so the fallback reports
        ``[singer, piano]`` while the braces report ``[piano, singer, piano]``.
        Same page, opposite readings -- only the braces tell them apart.
        """
        page = self._page(["a", None, "b"], [G, G, F])
        self.assertEqual([p.name for p in page.parts],
                         [TWO_STAFF_ROLE, SOLO_ROLE, "%s 2" % TWO_STAFF_ROLE])
        self.assertEqual([len(p.clef_by_staff) for p in page.parts], [1, 1, 1])
        self.assertEqual([p.id for p in page.parts], ["R1", "R2", "R3"])

    def test_two_braced_groups_do_not_fuse_into_one_part(self):
        """Four staves, two braces: two instruments, not one."""
        staves = [staff(2) for _ in range(4)]
        page = assemble_page(staves, [1, 1, 1, 1], [G, F, G, F],
                             brace_ids=["a", "a", "b", "b"], id="two")
        self.assertEqual([p.name for p in page.parts],
                         [TWO_STAFF_ROLE, "%s 2" % TWO_STAFF_ROLE])
        self.assertEqual(sum(len(pitched(p)) for p in page.parts), 16)


class VerticalOrderVoteTests(unittest.TestCase):
    """When no system prints two parts together, the order abstains.

    The vertical vote is only as good as its comparisons: a page whose
    systems each show a single part has never said which instrument is on
    top, and inventing an order there would claim an observation the scan
    never made. First appearance stands in as the neutral answer.
    """

    def test_systems_that_never_compare_keep_first_appearance(self):
        staves = [staff(4, step="e", octave=5),   # sys1: piano alone
                  staff(3, step="c", octave=5)]   # sys2: singer alone
        page = assemble_page(staves, [1, 2], [G, G],
                             brace_ids=["p", None], id="apart")
        self.assertEqual([p.id for p in page.parts], ["R1", "R2"],
                         "no system printed both parts, so no vote: "
                         "first appearance is the abstention, not a claim")


class SystemOffsetTests(unittest.TestCase):
    """Bars are numbered locally by each system and must be offset."""

    def test_offsets_accumulate(self):
        self.assertEqual(system_offsets([4, 3, 3]), {0: 1, 1: 5, 2: 8})

    def test_single_system_starts_at_one(self):
        self.assertEqual(system_offsets([7]), {0: 1})


class AssemblePageTests(unittest.TestCase):
    """The p0020 shape: 2+3+3 systems, read off the scan by the user."""

    def setUp(self):
        # system 1: one grand staff (2 staves, 4 bars)
        # system 2: solo + grand staff (3 staves, 3 bars)
        # system 3: solo + grand staff (3 staves, 3 bars)
        self.staves = [
            staff(4, step="e", octave=5),   # s1  system 1, treble
            staff(4, step="g", octave=3),   # s2  system 1, bass
            staff(3, step="c", octave=5),   # s3  system 2, solo
            staff(3, step="a", octave=4),   # s4  system 2, treble
            staff(3, step="b", octave=2),   # s5  system 2, bass
            staff(3, step="d", octave=5),   # s6  system 3, solo
            staff(3, step="f", octave=4),   # s7  system 3, treble
            staff(3, step="e", octave=2),   # s8  system 3, bass
        ]
        self.systems = [1, 1, 2, 2, 2, 3, 3, 3]
        self.clefs = [G, F, G, G, F, G, G, F]
        self.page = assemble_page(self.staves, self.systems, self.clefs,
                                  id="p0020")

    def test_no_note_is_lost(self):
        before = sum(len(pitched(s)) for s in self.staves)
        after = sum(len(pitched(p)) for p in self.page.parts)
        self.assertEqual(before, 52)
        self.assertEqual(before, after)

    def test_piano_is_one_part_of_two_staves(self):
        pianos = [p for p in self.page.parts
                  if p.name == TWO_STAFF_ROLE]
        self.assertEqual(len(pianos), 1, "the same piano in 3 systems "
                                         "must not become 3 parts")
        self.assertEqual(len(pianos[0].clef_by_staff), 2)

    def test_piano_spans_every_system(self):
        piano = [p for p in self.page.parts if p.name == TWO_STAFF_ROLE][0]
        self.assertEqual([m.number for m in piano.measures],
                         list(range(1, 11)))

    def test_solo_lines_share_one_part_and_are_padded(self):
        solos = [p for p in self.page.parts if p.name == SOLO_ROLE]
        self.assertEqual(len(solos), 1)
        self.assertEqual([m.number for m in solos[0].measures],
                         list(range(1, 11)))

    def test_padding_measures_are_rests(self):
        solo = [p for p in self.page.parts if p.name == SOLO_ROLE][0]
        silent = [m for m in solo.measures if not pitched(m)]
        self.assertEqual([m.number for m in silent], [1, 2, 3, 4])

    def test_clefs_survive_onto_the_part(self):
        piano = [p for p in self.page.parts if p.name == TWO_STAFF_ROLE][0]
        self.assertEqual(piano.clef_by_staff[1], G)
        self.assertEqual(piano.clef_by_staff[2], F)

    def test_two_parts_totalling_three_staves(self):
        """Two parts holding three staves -- not "three staves" each.

        The old name of this test said "two parts of three staves", which is
        both false (the split is 2+1) and the wrong claim to leave standing:
        nothing about a page gives every part three staves. Pinned because the
        number that matters is that eight fragments became two instruments
        rather than five.
        """
        self.assertEqual(len(self.page.parts), 2)
        # Singer first (1) then piano (2): systems 2 and 3 print her above
        # the grand staff, and the vote puts her on top here exactly as it
        # does for the brace-read page -- the split itself is 2+1 either way.
        self.assertEqual([len(p.clef_by_staff)
                          for p in self.page.parts], [1, 2])
        self.assertEqual(sum(len(p.clef_by_staff)
                             for p in self.page.parts), 3)

    def test_measures_are_bar_aligned(self):
        counts = {len(p.measures) for p in self.page.parts}
        self.assertEqual(counts, {10})


class NoLossTests(unittest.TestCase):
    """Grouping must never be a filter."""

    def test_all_treble_page_makes_one_part_per_staff(self):
        staves = [staff(2), staff(2), staff(2)]
        page = assemble_page(staves, [1, 1, 1], [G, G, G])
        self.assertEqual(len(page.parts), 3)
        self.assertEqual(sum(len(pitched(p)) for p in page.parts), 12)

    def test_two_grand_staves_are_the_same_continuing_instrument(self):
        staves = [staff(2), staff(2), staff(2), staff(2)]
        page = assemble_page(staves, [1, 1, 2, 2], [G, F, G, F])
        self.assertEqual([p.name for p in page.parts], [TWO_STAFF_ROLE])
        self.assertEqual([m.number for m in page.parts[0].measures],
                         [1, 2, 3, 4])
        self.assertEqual(sum(len(pitched(p)) for p in page.parts), 16)


if __name__ == "__main__":
    unittest.main()
