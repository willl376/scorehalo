"""Phantom barlines vs silence anyone played (scripts/per_staff_assemble.py).

The engraver looked at the render and said "remove the measure with the
rests present" -- a bar homr read with attributes but no notes in ANY
staff of its system. That bar is not silence the page said; it is two
barlines with nothing between them, and keeping it throws every following
bar out of step with the scan (the voice then enters a bar late against
the book). A bar with music in even one staff of the system stays --
that silence is real music and is emitted as a whole-measure rest.

Note.measure is re-stamped on every survivor because to_ly buckets notes
by matching ``note.measure == measure.number``; a dropped bar leaves the
later bars renumbered, and unstamped notes would fall out of the buckets
silently -- music missing from a render that otherwise looks fine.
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pysrc"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from scorehalo.graph import Measure, Note, Part, Pitch  # noqa: E402
from per_staff_assemble import drop_phantom_bars  # noqa: E402


def _frag(staff_no, empty_at):
    """A 3-bar fragment whose local bar ``empty_at`` has no notes."""
    p = Part(id="s%d" % staff_no, name="Fragment", staff_index=staff_no)
    for j in (1, 2, 3):
        m = Measure(number=j, divisions=12, beats=4, beat_type=4)
        if j != empty_at:
            m.notes = [Note(staff=staff_no, voice=1, onset=0, duration=12,
                            pitch=Pitch("C", 0, 4), measure=j)]
        p.measures.append(m)
    return p


class PhantomBarTests(unittest.TestCase):

    def test_a_bar_empty_in_every_staff_is_dropped_and_survivors_renumbered(self):
        staves = [_frag(1, empty_at=2), _frag(2, empty_at=2)]
        sys_of = [1, 1]
        empty = [(1, 1, 2, "Frag"), (2, 1, 2, "Frag")]
        dropped = drop_phantom_bars(staves, sys_of, empty)
        self.assertEqual(dropped, [(1, 2)])
        for p in staves:
            self.assertEqual([m.number for m in p.measures], [1, 2],
                             "the old bar 3 is now bar 2")
            self.assertTrue(all(m.notes for m in p.measures),
                            "no bar left behind empty")
        for p in staves:
            self.assertEqual([n.measure for m in p.measures for n in m.notes],
                             [1, 2],
                             "notes must follow their bar: to_ly buckets by "
                             "note.measure and would drop the restamped ones")

    def test_a_bar_with_music_in_one_staff_stays(self):
        """Silence one instrument keeps is music, not a miscount."""
        staves = [_frag(1, empty_at=2), _frag(2, empty_at=99)]
        sys_of = [1, 1]
        empty = [(1, 1, 2, "Frag")]
        self.assertEqual(drop_phantom_bars(staves, sys_of, empty), [])
        self.assertEqual([len(p.measures) for p in staves], [3, 3])
        self.assertEqual(len(staves[0].measures[1].notes), 0,
                         "the silent bar is kept (the emitter pads it)")

    def test_systems_are_counted_separately(self):
        """Each system's own staves decide: one of two silent in sys1 keeps
        the bar, while the lone staff of sys2 being silent drops it."""
        staves = [_frag(1, empty_at=2), _frag(2, empty_at=99),
                  _frag(3, empty_at=1)]
        sys_of = [1, 1, 2]
        empty = [(1, 1, 2, "Frag"), (3, 2, 1, "Frag")]
        self.assertEqual(drop_phantom_bars(staves, sys_of, empty), [(2, 1)])
        self.assertEqual(len(staves[0].measures), 3, "sys1 bar 2 stays")
        self.assertEqual([m.number for m in staves[2].measures], [1, 2])


if __name__ == "__main__":
    unittest.main()
