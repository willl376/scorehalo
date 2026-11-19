"""CLI hygiene: legacy verbs must say so on stderr, not silently succeed."""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pysrc"))

from scorehalo import cli  # noqa: E402
from scorehalo.cli import _deprecated  # noqa: E402


class DeprecationTests(unittest.TestCase):

    def test_warning_names_the_verb_and_the_replacement(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            _deprecated("convert", "use the per-staff pipeline")
        text = err.getvalue()
        self.assertIn("'convert' is DEPRECATED", text)
        self.assertIn("use the per-staff pipeline", text)


class ConvertControlTests(unittest.TestCase):

    def test_no_mask_graphics_flag_reaches_convert(self):
        """The A/B control must survive argparse (it changes the engine input)."""
        seen = {}
        with mock.patch.object(cli, "cmd_convert", side_effect=lambda a: seen.update(
                no_mask=a.no_mask_graphics, out=a.out, input=a.input)):
            cli.main(["convert", "x.png", "--no-mask-graphics", "-o", "ab-out"])
        self.assertTrue(seen["no_mask"])
        self.assertEqual(seen["input"], "x.png")
        self.assertEqual(seen["out"], "ab-out")

    def test_masking_is_the_default(self):
        seen = {}
        with mock.patch.object(cli, "cmd_convert", side_effect=lambda a: seen.update(
                no_mask=a.no_mask_graphics, gate=a.no_photo_gate)):
            cli.main(["convert", "x.png"])
        self.assertFalse(seen["no_mask"])
        self.assertFalse(seen["gate"])

    def test_no_photo_gate_flag_reaches_convert(self):
        seen = {}
        with mock.patch.object(cli, "cmd_convert", side_effect=lambda a: seen.update(
                gate=a.no_photo_gate)):
            cli.main(["convert", "x.png", "--no-photo-gate"])
        self.assertTrue(seen["gate"])

    def test_picture_is_masked_before_the_page_gate(self):
        """A picture must not make its own cleanup impossible.

        The gate counts smooth mid-tone pixels; an illustrated music page can
        exceed the 5% photo threshold. Mask first, then decide whether the
        remaining page is music.
        """
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "page.png")
            Image.new("RGB", (20, 20), "white").save(src)
            out = os.path.join(tmp, "out")
            gate_paths = []
            regions = [{"box": (2, 2, 8, 8), "area_frac": 0.1, "fill": 1.0}]

            def gate(path):
                gate_paths.append(path)
                return False, {"note": "skip"}, "skip"

            with mock.patch.object(cli.graphics, "find_graphics", return_value=regions), \
                 mock.patch.object(cli.cv2, "imread", return_value=np.zeros((20, 20), dtype=np.uint8)), \
                 mock.patch.object(cli.cv2, "imwrite", return_value=True), \
                 mock.patch.object(cli.preprocess, "is_music_page", side_effect=gate):
                cli.main(["convert", src, "-o", out])

            self.assertEqual(len(gate_paths), 1)
            self.assertTrue(gate_paths[0].endswith(os.path.join("masked", "p0001.png")))
            with open(os.path.join(out, "manifest.json")) as fh:
                entry = json.load(fh)["pages"][0]
            self.assertEqual(entry["status"], "skipped-not-music")
            self.assertEqual(entry["graphics_masked"], [[2, 2, 8, 8]])


if __name__ == "__main__":
    unittest.main()
