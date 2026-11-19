"""The review UI must not serve files outside its output directory."""

import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pysrc"))

from scorehalo.ui import _safe_path  # noqa: E402


class SafePathTests(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="scorehalo-ui-")
        self.inside = os.path.join(self.root, "page.png")
        with open(self.inside, "wb") as fh:
            fh.write(b"png")
        self.outside = os.path.join(os.path.dirname(self.root),
                                    os.path.basename(self.root) + "-extra")
        os.makedirs(self.outside, exist_ok=True)

    def test_relative_inside_is_allowed(self):
        self.assertEqual(_safe_path("page.png", self.root), self.inside)

    def test_absolute_inside_is_allowed(self):
        self.assertEqual(_safe_path(self.inside, self.root), self.inside)

    def test_parent_traversal_is_rejected(self):
        self.assertIsNone(_safe_path("../secret.png", self.root))

    def test_absolute_outside_is_rejected(self):
        self.assertIsNone(_safe_path("/etc/passwd", self.root))

    def test_sibling_with_prefix_name_is_rejected(self):
        # The old startswith() check let /tmp/out-extra through for root /tmp/out.
        target = os.path.join(self.outside, "secret.png")
        self.assertIsNone(_safe_path(target, self.root))

    def test_empty_path_is_rejected(self):
        self.assertIsNone(_safe_path("", self.root))


if __name__ == "__main__":
    unittest.main()
