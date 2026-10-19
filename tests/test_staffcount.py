"""Tests for staffcount's input handling (scripts/staffcount.py).

Run from the scorehalo repo root::

    .venv/bin/python -m unittest tests.test_staffcount

``_as_array`` normalizes whatever shape the pipeline already holds -- a path,
an open file, a PIL image, an ndarray -- into a grayscale array. Its else-branch
called ``.convert("L")`` on everything that was not path or file, so the
ndarray shape (the one a numpy pipeline hands over naturally) died with
AttributeError: the function crashed on the input it was written to accept.

Input handling is what is under test; the detection heuristic itself is still
unwired (no caller outside this module) and has no contract to pin yet.
"""

import os
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from staffcount import _as_array  # noqa: E402


class TestAsArray(unittest.TestCase):
    def _assert_gray(self, a, shape):
        self.assertEqual(a.ndim, 2, "expected a single-channel array")
        self.assertEqual(a.shape, shape)
        self.assertEqual(a.dtype, np.uint8)

    def test_grayscale_ndarray_passes_through(self):
        src = np.full((7, 9), 128, dtype=np.uint8)
        a = _as_array(src)  # used to crash: ndarray has no .convert
        self._assert_gray(a, (7, 9))
        np.testing.assert_array_equal(a, src)

    def test_colour_ndarray_becomes_gray(self):
        src = np.zeros((7, 9, 3), dtype=np.uint8)
        src[..., 1] = 255  # green field
        a = _as_array(src)
        self._assert_gray(a, (7, 9))
        self.assertGreater(a.mean(), 0, "the colour channels were dropped "
                                        "instead of converted")

    def test_pil_image_is_converted(self):
        a = _as_array(Image.new("RGB", (9, 7), (255, 0, 0)))
        self._assert_gray(a, (7, 9))

    def test_path_is_opened(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "page.png")
            Image.new("L", (9, 7), 128).save(p)
            a = _as_array(p)
        self._assert_gray(a, (7, 9))

    def test_open_file_object(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "page.png")
            Image.new("L", (9, 7), 128).save(p)
            with open(p, "rb") as f:
                a = _as_array(f)
        self._assert_gray(a, (7, 9))


if __name__ == "__main__":
    unittest.main()
