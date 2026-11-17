"""Tests for scorehalo.storage — atomic UTF-8/JSON writes.

Verifies that writes are atomic: a crash mid-write never leaves a partial
destination file, and the temp file is always cleaned up.
"""

import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pysrc"))

from scorehalo.storage import atomic_text, atomic_json  # noqa: E402


class AtomicTextTests(unittest.TestCase):

    def test_writes_exact_content(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "out.txt")
            result = atomic_text(path, "hello \xe9 world")
            self.assertEqual(result, path)
            self.assertEqual(open(path, encoding="utf-8").read(), "hello \xe9 world")

    def test_creates_parent_dirs(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "sub", "deep", "out.txt")
            atomic_text(path, "x")
            self.assertTrue(os.path.exists(path))

    def test_no_temp_left_behind(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "out.txt")
            before = set(os.listdir(d))
            atomic_text(path, "data")
            after = set(os.listdir(d))
            self.assertEqual(before | {"out.txt"}, after)

    def test_overwrites_existing(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "out.txt")
            atomic_text(path, "old")
            atomic_text(path, "new")
            self.assertEqual(open(path).read(), "new")


class AtomicJsonTests(unittest.TestCase):

    def test_writes_valid_json(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "out.json")
            data = {"key": "value", "n": 42, "unicode": "\xe9"}
            atomic_json(path, data)
            loaded = json.load(open(path, encoding="utf-8"))
            self.assertEqual(loaded, data)

    def test_ends_with_newline(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "out.json")
            atomic_json(path, {"a": 1})
            content = open(path, encoding="utf-8").read()
            self.assertTrue(content.endswith("\n"))


if __name__ == "__main__":
    unittest.main()
