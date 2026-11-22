"""Opt-in rotation-tolerant gate: fallback-only, auditable, negbable.

These are wiring/statistics tests, not note-accuracy claims. The note-level A/B
evidence lives in ~/Documents/omr_workspace/deskew-experiment-2026-11-22/.
"""

import os
import shutil
import sys
import tempfile
import unittest

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pysrc"))

from scorehalo import gate_rotation, preprocess  # noqa: E402

try:
    import cv2  # noqa: E402
except ImportError:  # pragma: no cover - optional dep path
    cv2 = None


def music_page(path, rotate=0.0):
    im = Image.new("RGB", (2400, 3200), "white")
    d = ImageDraw.Draw(im)
    for y in range(300, 2900, 300):
        for k in range(5):
            d.line([(100, y + k * 12), (2300, y + k * 12)],
                   fill="black", width=3)
    if rotate:
        im = im.rotate(rotate, resample=Image.BICUBIC,
                       expand=False, fillcolor="white")
    im.save(path)


@unittest.skipIf(cv2 is None, "cv2 not installed")
class GateRotationTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.src = os.path.join(self.root, "page.png")
        self.dst = os.path.join(self.root, "prepared.png")
        music_page(self.src)

    def tearDown(self):
        self.tmp.cleanup()

    def test_accepted_page_is_copied_byte_for_byte_and_search_skipped(self):
        ok, _, _ = preprocess.is_music_page(self.src)
        self.assertTrue(ok)
        decision = gate_rotation.prepare_for_gate(
            self.src, self.dst, gate_ok=ok)
        self.assertFalse(decision["activated"])
        self.assertIsNone(decision["chosen_angle"])
        self.assertEqual(decision["search_angles"], [])
        with open(self.src, "rb") as a, open(self.dst, "rb") as b:
            self.assertEqual(a.read(), b.read())

    def test_rotated_page_is_levelled_and_accepted_by_the_unchanged_gate(self):
        rot = os.path.join(self.root, "rot.png")
        music_page(rot, rotate=-3.0)
        ok, _, _ = preprocess.is_music_page(rot)
        self.assertFalse(ok)
        decision = gate_rotation.prepare_for_gate(
            rot, self.dst, gate_ok=ok)
        self.assertTrue(decision["activated"])
        self.assertEqual(decision["chosen_angle"], 3.0)
        self.assertEqual(decision["before"]["staff-like rows"], 0)
        self.assertGreaterEqual(decision["after"]["staff-like rows"], 4)
        retry_ok, _, _ = preprocess.is_music_page(self.dst)
        self.assertTrue(retry_ok)

    def test_no_music_like_angle_preserves_the_original_bytes(self):
        blank = os.path.join(self.root, "blank.png")
        Image.new("RGB", (1600, 2200), "white").save(blank)
        ok, _, _ = preprocess.is_music_page(blank)
        self.assertFalse(ok)
        decision = gate_rotation.prepare_for_gate(
            blank, self.dst, gate_ok=ok)
        self.assertTrue(decision["activated"])
        self.assertIsNone(decision["chosen_angle"])
        with open(blank, "rb") as a, open(self.dst, "rb") as b:
            self.assertEqual(a.read(), b.read())
        retry_ok, _, _ = preprocess.is_music_page(self.dst)
        self.assertFalse(retry_ok)

    def test_narrow_angle_window_can_be_supplied_for_a_fast_probe(self):
        rot = os.path.join(self.root, "rot.png")
        music_page(rot, rotate=-3.0)
        ok, _, _ = preprocess.is_music_page(rot)
        decision = gate_rotation.prepare_for_gate(
            rot, self.dst, gate_ok=ok, angles=(3.0,))
        self.assertEqual(decision["chosen_angle"], 3.0)
        self.assertEqual(decision["search_angles"], [3.0])


if __name__ == "__main__":
    unittest.main()
