"""Graphics masking must be opt-in, auditable, and subordinate to staff geometry.

These tests drive ``prep_pages`` with a known synthetic page: a music page with
one smooth mid-tone photograph in a staff-free band. The real detector runs;
only staff *analysis* is stubbed, because this test is about the wiring.
"""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "pysrc"))
sys.path.insert(0, os.path.join(ROOT, "tests"))

import per_staff_all as psa  # noqa: E402
from test_graphics import W, music_page, picture, with_blob  # noqa: E402


STAVES = [
    {"x0": 60, "y_top": 80, "x1": W - 60, "y_bot": 210},
    {"x0": 60, "y_top": 360, "x1": W - 60, "y_bot": 490},
]


def fake_analysis(gray, min_staff_lines=5):
    return {
        "deskewed": gray,
        "staves": [dict(s) for s in STAVES],
        "n_systems": 2,
        "systems": [{"n_staves": 1}, {"n_staves": 1}],
        "staff_space": 10,
        "n_slivers_removed": 0,
    }


class PrepPagesMaskTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.render = os.path.join(self.root, "render")
        os.makedirs(self.render)
        self.manifest = os.path.join(self.root, "manifest.json")
        with open(self.manifest, "w") as fh:
            json.dump({"pages": [{"status": "ok", "image": "render/p0001.png"}]},
                      fh)
        self.image = with_blob(music_page(), picture(), 400, 620)
        Image.fromarray(self.image).save(os.path.join(self.render, "p0001.png"))

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, name, mask):
        out = os.path.join(self.root, name)
        os.makedirs(out)
        with mock.patch.object(psa, "analyze_page", fake_analysis):
            pages, skipped = psa.prep_pages(
                self.render, self.manifest, out, mask_graphics=mask)
        self.assertEqual(skipped, [])
        self.assertEqual(len(pages), 1)
        meta = pages[0]
        with open(os.path.join(out, "p0001.boxes.json")) as fh:
            saved = json.load(fh)
        # JSON turns the detector's tuple boxes into lists; compare like for like.
        self.assertEqual(saved, json.loads(json.dumps(meta)))
        return out, meta

    def test_default_keeps_the_raw_deskew(self):
        out, meta = self._run("plain", False)
        self.assertEqual(meta["deskew"], os.path.join(out, "p0001.deskew.png"))
        self.assertEqual(meta["graphics_masked"], [])
        self.assertFalse(meta["mask_graphics"])
        self.assertFalse(os.path.exists(
            os.path.join(out, "p0001.deskew.masked.png")))

    def test_opt_in_masks_only_off_staff_regions_and_keeps_both_images(self):
        out, meta = self._run("masked", True)
        self.assertEqual(len(meta["graphics_masked"]), 1)
        self.assertTrue(meta["mask_graphics"])
        masked_path = os.path.join(out, "p0001.deskew.masked.png")
        self.assertEqual(meta["deskew"], masked_path)
        raw_path = os.path.join(out, "p0001.deskew.png")
        self.assertEqual(meta["deskew_raw"], raw_path)
        self.assertTrue(os.path.isfile(raw_path))
        with Image.open(raw_path) as im:
            raw = np.array(im.convert("L"))
        with Image.open(masked_path) as im:
            masked = np.array(im.convert("L"))
        self.assertTrue((raw[620:820, 400:660] != 255).any())
        self.assertTrue((masked[620:820, 400:660] == 255).all())
        # Nothing outside the reported box may change.
        x, y, bw, bh = meta["graphics_masked"][0]["box"]
        keep = np.ones(raw.shape, bool)
        keep[y:y + bh, x:x + bw] = False
        self.assertTrue((masked[keep] == raw[keep]).all())
        self.assertEqual(meta["staves"], STAVES)

    def test_a_picture_on_a_staff_is_left_alone(self):
        image = with_blob(music_page(), picture(), 200, 70)
        Image.fromarray(image).save(os.path.join(self.render, "p0001.png"))
        out, meta = self._run("onstaff", True)
        self.assertEqual(meta["graphics_masked"], [])
        self.assertEqual(meta["deskew"], os.path.join(out, "p0001.deskew.png"))


if __name__ == "__main__":
    unittest.main()
