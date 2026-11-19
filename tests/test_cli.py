"""CLI hygiene: legacy verbs must say so on stderr, not silently succeed."""

import contextlib
import io
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pysrc"))

from scorehalo.cli import _deprecated  # noqa: E402


class DeprecationTests(unittest.TestCase):

    def test_warning_names_the_verb_and_the_replacement(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            _deprecated("convert", "use the per-staff pipeline")
        text = err.getvalue()
        self.assertIn("'convert' is DEPRECATED", text)
        self.assertIn("use the per-staff pipeline", text)


if __name__ == "__main__":
    unittest.main()
