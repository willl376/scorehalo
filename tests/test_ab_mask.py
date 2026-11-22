"""The illustrated fixture used by the masking A/B must really be illustrated."""

import os
import sys
import tempfile
import unittest

import cv2
import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pysrc"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

try:
    import ab_mask  # noqa: E402
    import make_fixture  # noqa: E402
    from scorehalo import graphics  # noqa: E402
except ModuleNotFoundError as exc:  # optional PDF dependency on a bare clone
    if exc.name != "pypdfium2":
        raise
    ab_mask = make_fixture = graphics = None

skip_missing_pdf = unittest.skipIf(
    ab_mask is None, "pypdfium2 not installed")


@skip_missing_pdf
class IllustratedFixtureTests(unittest.TestCase):

    def test_added_picture_is_detected_as_a_graphic(self):
        im = Image.new("RGB", (1000, 1400), "white")
        im, box = make_fixture.add_illustration(im)
        path = os.path.join(tempfile.mkdtemp(), "page.png")
        im.save(path)
        gray = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        regions = graphics.find_graphics(gray)
        self.assertEqual(len(regions), 1)
        self.assertEqual(tuple(box[:2]), (280, 518))
        self.assertGreater(regions[0]["area_frac"], 0.02)

    def test_degrade_illustration_records_the_box_and_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "clean.png")
            out = os.path.join(tmp, "illustrated.png")
            Image.new("RGB", (1000, 1400), "white").save(src)
            meta = make_fixture.degrade(src, out, "illustration", seed=7)
            self.assertEqual(meta["mode"], "illustration")
            self.assertEqual(meta["resample_ratio"], 200 / 300)
            self.assertEqual(len(meta["illustration_box"]), 4)
            regions = graphics.find_graphics(cv2.imread(out, cv2.IMREAD_GRAYSCALE))
            self.assertEqual(len(regions), 1)

    def test_report_without_scores_does_not_crash(self):
        row = {"arms": {"masked": {"error": "rc=9"},
                        "unmasked": {"error": "rc=8"}},
               "detected_regions": [], "sha256": {}}
        text = ab_mask.markdown_report(row, "probe")
        self.assertIn("No score comparison", text)
        self.assertIn("rc=9", text)


if __name__ == "__main__":
    unittest.main()
