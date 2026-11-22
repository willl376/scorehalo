"""Paired A/B harness: stale output, bad rc and bad archives must not score.

These tests use fake subprocess runners, so they never start Audiveris or
MuseScore. The real one-page smoke run is recorded in reports/ab-engines.md.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

try:
    import ab_engines  # noqa: E402
except ModuleNotFoundError as exc:  # optional PDF dependency on a bare clone
    if exc.name != "pypdfium2":
        raise
    ab_engines = None

skip_missing_pdf = unittest.skipIf(
    ab_engines is None, "pypdfium2 not installed")


def score_xml(pitch="C", octave=4, duration=4):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<score-partwise version="4.0">'
        '<part-list><score-part id="P1"><part-name>P</part-name>'
        '</score-part></part-list>'
        '<part id="P1"><measure number="1">'
        f'<note><pitch><step>{pitch}</step><octave>{octave}</octave></pitch>'
        f'<duration>{duration}</duration><type>whole</type></note>'
        '</measure></part></score-partwise>')


def make_mxl(path, member="score.xml", payload=None, container=None):
    payload = payload or score_xml()
    if container is None:
        container = (
            '<?xml version="1.0"?><container>'
            '<rootfiles><rootfile full-path="score.xml"/></rootfiles>'
            '</container>')
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("META-INF/container.xml", container)
        zf.writestr(member, payload)
    return path


class FakeProc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@skip_missing_pdf
class HelperTests(unittest.TestCase):

    def test_sha256_changes_with_content(self):
        with tempfile.TemporaryDirectory() as td:
            a, b = os.path.join(td, "a"), os.path.join(td, "b")
            with open(a, "wb") as fh:
                fh.write(b"abc")
            with open(b, "wb") as fh:
                fh.write(b"abd")
            self.assertEqual(ab_engines.sha256(a), ab_engines.sha256(a))
            self.assertNotEqual(ab_engines.sha256(a), ab_engines.sha256(b))

    def test_extract_follows_container_pointer(self):
        with tempfile.TemporaryDirectory() as td:
            mxl = make_mxl(os.path.join(td, "x.mxl"), member="inner.xml",
                           container='<?xml version="1.0"?><container><rootfiles>'
                           '<rootfile full-path="inner.xml"/></rootfiles>'
                           '</container>')
            out = os.path.join(td, "out.musicxml")
            self.assertEqual(ab_engines.extract_mxl(mxl, out), out)
            with open(out) as fh:
                self.assertIn("score-partwise", fh.read())

    def test_extract_refuses_unparseable_payload(self):
        with tempfile.TemporaryDirectory() as td:
            mxl = make_mxl(os.path.join(td, "x.mxl"), payload="<broken")
            with self.assertRaises(Exception):
                ab_engines.extract_mxl(mxl, os.path.join(td, "out.musicxml"))
            self.assertFalse(os.path.exists(os.path.join(td, "out.musicxml")))

    def test_markdown_report_shows_errors_and_deltas(self):
        rows = [{
            "case": "scan",
            "arms": {
                "scorehalo": {"rc": 0, "exact": 5, "truth_notes": 5,
                              "exact_precision": 100.0, "exact_recall": 100.0,
                              "pitch_wrong": 0, "duration_wrong": 0,
                              "missed": 0, "spurious": 0, "seconds": 1.0},
                "audiveris": {"rc": 1, "error": "no usable prediction"},
            },
            "delta_exact": None,
            "delta_precision": None,
        }]
        text = ab_engines.markdown_report(rows, "test-run")
        self.assertIn("test-run", text)
        self.assertIn("scan | scorehalo", text)
        self.assertIn("ERROR", text)


@skip_missing_pdf
class RunnerTests(unittest.TestCase):

    def test_scorehalo_runner_uses_fresh_dir_and_checks_rc(self):
        with tempfile.TemporaryDirectory() as td:
            parent = os.path.join(td, "arm")
            image = os.path.join(td, "page.png")
            with open(image, "wb") as fh:
                fh.write(b"png")
            seen = {}

            def fake(argv, **kwargs):
                seen["argv"] = argv
                seen["env"] = kwargs["env"]
                with open(os.path.join(parent, "p0001.musicxml"), "w") as fh:
                    fh.write(score_xml())
                return FakeProc()

            arm = ab_engines.run_scorehalo(image, parent, runner=fake)
            self.assertEqual(seen["argv"][3:6], ["convert", image, "-o"])
            self.assertIn(os.path.join(ROOT, "pysrc"),
                          seen["env"]["PYTHONPATH"])
            self.assertTrue(os.path.isfile(arm["pred"]))
            self.assertTrue(os.path.isfile(arm["log"]))

            parent2 = os.path.join(td, "arm-fail")
            with self.assertRaises(FileExistsError):
                ab_engines.run_scorehalo(image, parent, runner=fake)
            self.assertIsNotNone(parent2)

    def test_scorehalo_rc_nonzero_has_no_prediction(self):
        with tempfile.TemporaryDirectory() as td:
            image = os.path.join(td, "page.png")
            with open(image, "wb") as fh:
                fh.write(b"png")
            arm = ab_engines.run_scorehalo(
                image, os.path.join(td, "arm"),
                runner=lambda *a, **k: FakeProc(returncode=2, stderr="boom"))
            self.assertIsNone(arm["pred"])
            self.assertEqual(arm["rc"], 2)

    def test_audiveris_uses_java_opts_and_extracts_mxl(self):
        with tempfile.TemporaryDirectory() as td:
            image = os.path.join(td, "page.png")
            with open(image, "wb") as fh:
                fh.write(b"png")
            seen = {}

            def fake(argv, **kwargs):
                seen["argv"] = argv
                seen["env"] = kwargs["env"]
                make_mxl(os.path.join(kwargs["cwd"], "page.mxl"))
                return FakeProc()

            arm = ab_engines.run_audiveris(
                image, os.path.join(td, "arm"), runner=fake,
                binary="/bin/true")
            self.assertEqual(seen["argv"][1:4],
                             ["-batch", "-export", "-output"])
            self.assertIn("-Djava.awt.headless=true", seen["env"]["JAVA_OPTS"])
            self.assertNotIn("JAVA_TOOL_OPTIONS", seen["env"])
            self.assertTrue(os.path.isfile(arm["pred"]))


@skip_missing_pdf
class ScoringTests(unittest.TestCase):

    def test_score_prediction_counts_exact_and_rejects_bad_arm(self):
        with tempfile.TemporaryDirectory() as td:
            truth = os.path.join(td, "truth.musicxml")
            pred = os.path.join(td, "pred.musicxml")
            with open(truth, "w") as fh:
                fh.write(score_xml())
            with open(pred, "w") as fh:
                fh.write(score_xml(duration=2))
            row = ab_engines.score_prediction(
                truth, pred, {"pred": pred, "rc": 0}, 0.0, 1.0)
            self.assertEqual(row["exact"], 0)
            self.assertEqual(row["duration_wrong"], 1)
            self.assertEqual(row["truth_notes"], 1)
            self.assertEqual(row["pred_notes"], 1)
            self.assertEqual(len(row["pred_sha256"]), 64)

            bad = ab_engines.score_prediction(
                truth, pred, {"pred": None, "rc": 3}, 0.0, 1.0)
            self.assertIn("error", bad)
            self.assertNotIn("exact", bad)


if __name__ == "__main__":
    unittest.main()
