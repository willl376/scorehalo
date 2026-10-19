"""Tests for the pictured-graphics bypass.

The behaviour under test is a promise from the README: a songbook page that
carries a photograph should still be read, and a page that IS a photograph
should be bypassed without the run stalling on it.

Synthetic images are used so the truth is known exactly:

* a "music" page: near-white paper with thin near-black staff lines;
* a "picture": a smooth mid-tone field (what a photograph looks like after a
  photocopy, and what notation never looks like);
* a "scan edge": a smooth mid-tone strip along the page border, which the real
  book has on every page and which must NOT be mistaken for a picture.

The measured thresholds this pins are in ``graphics.py``: music pages of the
1972 Carpenters book measure 0.00-0.47% photo-like, its photo plates 15.7-62.8%.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pysrc"))

from scorehalo import engine, graphics  # noqa: E402
from scorehalo.preprocess import is_music_page  # noqa: E402

H, W = 900, 700


def music_page():
    """Near-white paper, thin near-black staff lines: no smooth mid-tones."""
    img = np.full((H, W), 245, np.uint8)
    for sys_top in (80, 360):
        for st in range(2):
            top = sys_top + st * 120
            for ln in range(5):
                y = top + ln * 10
                img[y:y + 2, 60:W - 60] = 20
            # a few noteheads/stems so it is not just ruled lines
            for x in range(90, W - 90, 30):
                img[top - 4:top + 16, x:x + 9] = 20
    return img


def picture(w=260, h=200, x=200, y=300, tone=140):
    """A smooth mid-tone blob: `graphics` must call this a pictured graphic."""
    blob = np.full((h, w), tone, np.uint8)
    # smooth, low-contrast variation: a photograph, not a flat colour block
    yy, xx = np.mgrid[0:h, 0:w]
    blob = np.clip(blob + (20 * np.sin(xx / 40.0) * np.cos(yy / 40.0)), 0, 255)
    return blob.astype(np.uint8)


def with_blob(base, blob, x, y):
    img = base.copy()
    img[y:y + blob.shape[0], x:x + blob.shape[1]] = blob
    return img


class TestPhotoSignature(unittest.TestCase):
    def test_notation_has_no_photo_signature(self):
        self.assertLess(graphics.photo_fraction(music_page()),
                        graphics.PAGE_PHOTO_PCT)
        self.assertFalse(graphics.is_graphic_page(music_page()))

    def test_a_smooth_mid_tone_page_is_a_graphic(self):
        img = with_blob(music_page(), picture(600, 700, 50, 80), 50, 80)
        self.assertGreaterEqual(graphics.photo_fraction(img),
                                graphics.PAGE_PHOTO_PCT)
        self.assertTrue(graphics.is_graphic_page(img))

    def test_a_scan_edge_is_not_a_picture(self):
        """The book's own scan border is smooth and mid-tone on every page.

        A whole-page measure would call every page of the book a photograph;
        the measured border rule (centre-only for the page, off-border for a
        region) is what stops that.
        """
        img = music_page()
        img[:, :60] = picture(W, H, tone=150)[:, :60]  # full-height left strip
        self.assertLess(graphics.photo_fraction(img),
                        graphics.PAGE_PHOTO_PCT)
        self.assertEqual(graphics.find_graphics(img), [])


class TestFindingRegions(unittest.TestCase):
    def test_a_picture_inside_a_music_page_is_found(self):
        img = with_blob(music_page(), picture(), 220, 320)
        regs = graphics.find_graphics(img)
        self.assertEqual(len(regs), 1, regs)
        x, y, bw, bh = regs[0]["box"]
        # the box must cover the pasted blob, allowing for the 15px window and
        # the 9px opening kernel
        self.assertLessEqual(x, 220 + 20)
        self.assertLessEqual(y, 320 + 20)
        self.assertGreaterEqual(x + bw, 220 + 260 - 20)
        self.assertGreaterEqual(y + bh, 320 + 200 - 20)

    def test_a_region_overlapping_a_staff_is_not_erased(self):
        img = with_blob(music_page(), picture(), 200, 70)  # lies on a staff
        regs = graphics.find_graphics(img)
        self.assertTrue(regs, "a smooth blob on a staff is still detected")
        staves = [{"x0": 0, "y_top": 60, "x1": W, "y_bot": 200}]
        self.assertEqual(graphics.keep_off_staves(regs, staves), [])
        # ... but with no staff knowledge it would be kept
        self.assertTrue(graphics.keep_off_staves(regs, []))

    def test_blanking_erases_the_picture_and_keeps_the_music(self):
        img = with_blob(music_page(), picture(), 400, 620)
        regs = graphics.find_graphics(img)
        self.assertEqual(len(regs), 1, regs)
        out = graphics.blank(img, regs)
        # the picture is gone, to its own edge (the box must not be smaller
        # than the blob it locates)
        self.assertTrue((out[620:820, 400:660] == 255).all())
        # ...and the page is byte-for-byte unchanged everywhere else, so every
        # staff line, stem and notehead is left alone
        x, y, bw, bh = regs[0]["box"]
        keep = np.ones(img.shape, bool)
        keep[y:y + bh, x:x + bw] = False
        self.assertTrue((out[keep] == img[keep]).all())
        self.assertTrue((img[80:82, 60:W - 60] == 20).all())

    def test_crop_verdict(self):
        self.assertTrue(graphics.is_graphic_crop(picture(600, 700)))
        self.assertFalse(graphics.is_graphic_crop(music_page()))


class TestGateNamesTheFinding(unittest.TestCase):
    """`is_music_page` must say *why* a page was skipped."""

    def _write(self, img):
        fd, path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        import cv2
        cv2.imwrite(path, img)
        self.addCleanup(os.unlink, path)
        return path

    def test_a_picture_page_is_named_a_graphic(self):
        ok, stats, verdict = is_music_page(self._write(picture(650, 850)))
        self.assertFalse(ok)
        self.assertTrue(verdict.startswith("skip: pictured graphic"), verdict)
        self.assertIn("photo%", stats)

    def test_a_music_page_still_passes(self):
        ok, _stats, verdict = is_music_page(self._write(music_page()))
        self.assertTrue(ok, verdict)

    def test_a_page_that_is_merely_blank_keeps_its_old_wording(self):
        blank = np.full((H, W), 250, np.uint8)
        ok, _stats, verdict = is_music_page(self._write(blank))
        self.assertFalse(ok)
        self.assertEqual(verdict, "skip: not music-like")


class TestHomrTimeoutIsRecordedNotRaised(unittest.TestCase):
    """A wedged engine must not end a book run (the stall this exists for)."""

    def test_timeout_returns_failure(self):
        import cv2
        tmp = tempfile.mkdtemp(prefix="scorehalo-timeout-")
        self.addCleanup(shutil.rmtree, tmp, True)
        img = os.path.join(tmp, "page.png")
        cv2.imwrite(img, music_page())
        sleeper = os.path.join(tmp, "homr")
        with open(sleeper, "w") as fh:
            fh.write("#!%s\nimport time\ntime.sleep(30)\n" % sys.executable)
        os.chmod(sleeper, 0o755)

        old = engine.HOMR_BIN
        engine.HOMR_BIN = sleeper
        self.addCleanup(setattr, engine, "HOMR_BIN", old)

        t0 = time.time()
        xml, log, rc = engine.run_homr(img, os.path.join(tmp, "work"),
                                       timeout=1)
        elapsed = time.time() - t0
        self.assertIsNone(xml)
        self.assertEqual(rc, -1)
        self.assertLess(elapsed, 20, "must return, not hang")
        self.assertIn("timed out", open(log).read())


FAKE_HOMR = """\
#!{python}
import os, sys
src = sys.argv[1]
xml = src[:-len('.png')] + '.musicxml'
open(xml, 'w').write(
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<score-partwise version="4.0">'
    '<part-list><score-part id="P1"><part-name>Voice</part-name></score-part>'
    '</part-list><part id="P1"><measure number="1"/></part></score-partwise>')
sys.exit(0)
"""


class TestSweepBypassesAndContinues(unittest.TestCase):
    """End-to-end: a picture page is bypassed and the music page after it is
    still recognised, in the same run."""

    def test_graphic_page_then_music_page(self):
        import cv2
        try:
            import cv2  # noqa: F401
        except ImportError:  # pragma: no cover
            self.skipTest("cv2 unavailable")
        tmp = tempfile.mkdtemp(prefix="scorehalo-sweep-")
        self.addCleanup(shutil.rmtree, tmp, True)
        data, out = os.path.join(tmp, "data"), os.path.join(tmp, "out")
        os.makedirs(os.path.join(data, "render"))
        with open(os.path.join(data, "manifest.json"), "w") as fh:
            import json
            json.dump({"pages": [{"page": 1, "status": "ok"},
                                 {"page": 2, "status": "ok"}]}, fh)
        cv2.imwrite(os.path.join(data, "render", "p0001.png"), picture(650, 850))
        cv2.imwrite(os.path.join(data, "render", "p0002.png"), music_page())

        fake = os.path.join(tmp, "homr")
        with open(fake, "w") as fh:
            fh.write(FAKE_HOMR.format(python=sys.executable))
        os.chmod(fake, 0o755)

        env = dict(os.environ, HOMR_BIN=fake, PYTHONPATH=os.path.join(ROOT, "pysrc"))
        proc = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "per_system_all.py"),
             data, "--out", out],
            capture_output=True, text=True, timeout=300, env=env)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

        import json
        recs = [json.loads(l) for l in
                open(os.path.join(out, "per_system.jsonl")) if l.strip()]
        pictured = [r for r in recs if r.get("skipped") == "graphic"]
        recognised = [r for r in recs if r.get("ok")]
        self.assertEqual([r["stem"] for r in pictured], ["p0001"], recs)
        self.assertTrue(recognised, "the music page after the picture was read")
        self.assertEqual({r["stem"] for r in recognised}, {"p0002"})
        self.assertIn("bypassed", proc.stdout)


if __name__ == "__main__":
    unittest.main()
