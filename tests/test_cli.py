"""CLI hygiene: legacy verbs must say so on stderr, not silently succeed."""

import contextlib
import io
import os
import sys
import unittest
from unittest import mock

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
                no_mask=a.no_mask_graphics)):
            cli.main(["convert", "x.png"])
        self.assertFalse(seen["no_mask"])


if __name__ == "__main__":
    unittest.main()
