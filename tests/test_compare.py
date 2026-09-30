"""Tests for the compare subcommand's page PAIRING.

The defect these guard against was quiet and confident: `cmd_compare` used to
render the JOINED score, then zip its pages onto the source pages by list
index. Joining reflows, so 16 scanned pages rendered to 24, and every pairing
past the first few compared page N against an unrelated page -- reporting
plausible-looking ink ratios and "defect" verdicts for pages it never saw.

So the rule under test: a per-page verdict may only be produced from a render
of THAT page's own MusicXML, and the joined score is never page-paired.

Run: .venv/bin/python -m unittest discover -s tests -v
"""

import json
import os
import sys
import tempfile
import unittest

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pysrc"))

from scorehalo import compare as C  # noqa: E402


def _scan(path, bars, ink=40):
    """A synthetic 'scan': a page with `bars` staff-like bands."""
    img = Image.new("L", (600, 800), 255)
    px = img.load()
    for b in range(bars):
        y = 60 + b * 40
        for dy in range(3):
            for x in range(0, 600, 2):
                px[x, y + dy] = ink
    img.save(path)


class FakeMuseScore:
    """Stand-in for mscore4portable, as a REAL executable on disk.

    subprocess.run needs an executable path, so this writes a tiny script that
    logs the input path and writes a REAL multi-page PDF (via PIL) whose page
    count comes from joined_pages, so we can force the exact reflow the real
    bug needed: more render pages than source pages.
    """

    def __init__(self, joined_pages=1, bars_by_page=None):
        """bars_by_page: {page_number: bar_count}. The fake engraves exactly
        the bars the matching scan has, so a correct 1:1 pairing scores CLEAN
        and any shifted pairing scores dirty -- which is what makes this test
        able to detect the old mispairing bug."""
        self.dir = tempfile.mkdtemp(prefix="fakems-")
        self.log = os.path.join(self.dir, "calls.log")
        self.pages_txt = os.path.join(self.dir, "pages.txt")
        with open(self.pages_txt, "w") as fh:
            fh.write(str(joined_pages))
        self.bars = bars_by_page or {}
        self.default_bars = 8
        writer = os.path.join(self.dir, "mkpdf.py")
        with open(writer, "w") as fh:
            fh.write(
                "import re, sys\n"
                "from PIL import Image\n"
                "n = int(sys.argv[1]); out = sys.argv[2]; src = sys.argv[3]\n"
                "bars = int(sys.argv[4])\n"
                "imgs = []\n"
                "for k in range(n):\n"
                "    im = Image.new('L', (400, 600), 255); px = im.load()\n"
                "    for b in range(bars):\n"
                "        y = 60 + b * 40\n"
                "        for dy in range(3):\n"
                "            for x in range(0, 400, 2):\n"
                "                px[x, y + dy] = 40\n"
                "    imgs.append(im)\n"
                "imgs[0].save(out, 'PDF', save_all=True, append_images=imgs[1:])\n"
            )
        self.path = os.path.join(self.dir, "mscore")
        with open(self.path, "w") as fh:
            fh.write(
                "#!/bin/sh\n"
                f'echo "$3" >> "{self.log}"\n'
                # derive the bar count from the page number in the input name
                'P=$(echo "$3" | sed -n "s/.*p0*\\([0-9][0-9]*\\)\\..*/\\1/p")\n'
                'B=$(cat "' + os.path.join(self.dir, "bars.txt") + '" 2>/dev/null '
                f'| awk -F: -v p="$P" \'$1==p {{print $2}}\')\n'
                f'[ -z "$B" ] && B={self.default_bars}\n'
                f'exec "{sys.executable}" "{writer}" '
                f'"$(cat "{self.pages_txt}")" "$2" "$3" "$B"\n'
            )
        os.chmod(self.path, 0o755)
        with open(os.path.join(self.dir, "bars.txt"), "w") as fh:
            for p, b in self.bars.items():
                fh.write(f"{p}:{b}\n")

    def calls(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log) as fh:
            return [ln.strip() for ln in fh if ln.strip()]

    def set_joined_pages(self, n):
        with open(self.pages_txt, "w") as fh:
            fh.write(str(n))


def _make_outdir(td, pages, bars_by_page):
    out = os.path.join(td, "out")
    os.makedirs(os.path.join(out, "render"))
    for p in pages:
        _scan(os.path.join(out, "render", f"p{p:04d}.png"), bars_by_page[p])
        with open(os.path.join(out, f"p{p:04d}.musicxml"), "w") as fh:
            fh.write(f"<score><!-- page {p} --></score>")
    with open(os.path.join(out, "score.mxl"), "wb") as fh:
        fh.write(b"PK\x03\x04not-a-real-mxl-but-present")
    with open(os.path.join(out, "manifest.json"), "w") as fh:
        json.dump({"dpi": 150,
                   "pages": [{"page": p, "status": "ok"} for p in pages]
                   + [{"page": 1, "status": "skipped-not-music"}]}, fh)
    return out


class Args:
    def __init__(self, out_dir, render_dir, musescore):
        self.out_dir = out_dir
        self.render_dir = render_dir
        self.musescore = musescore


class TestOutlierFlaggingIsCorpusRelative(unittest.TestCase):
    """The scan-vs-render ratios carry a large systematic offset on a
    degraded photocopy (render staff count is ~2x the scan's on every page).
    So nothing is judged against a constant; a page is flagged only when it
    departs from the REST OF THE SAME BOOK."""

    @staticmethod
    def _rep(ink, staff_s, staff_r, quad=0.02):
        return {"ink_ratio": ink, "staff_scan": staff_s, "staff_render": staff_r,
                "quadrant_deviance": quad, "issues": []}

    def test_uniform_offset_is_not_flagged(self):
        """Every page carrying the same photocopy offset must read clean --
        this is the regression: absolute thresholds flagged all 16 of them."""
        reps = [self._rep(0.60, 20, 40 + (i % 2)) for i in range(8)]
        self.assertEqual(C.flag_outliers(reps), {})

    def test_genuine_outlier_is_flagged(self):
        reps = [self._rep(0.60, 20, 40) for _ in range(7)]
        reps.append(self._rep(0.15, 20, 40))       # one page lost most of its ink
        flagged = C.flag_outliers(reps)
        self.assertEqual(list(flagged), [7], "only the odd page should flag")
        self.assertIn("ink ratio", flagged[7][0])

    def test_too_few_pages_to_establish_a_baseline(self):
        self.assertEqual(C.flag_outliers([self._rep(0.1, 5, 90)]), {})

    def test_quadrant_deviance_still_flags_individually(self):
        reps = [self._rep(0.60, 20, 40) for _ in range(6)]
        reps[2]["quadrant_deviance"] = 0.30
        flagged = C.flag_outliers(reps)
        self.assertEqual(list(flagged), [2])
        self.assertIn("quadrant", flagged[2][0])

    def test_median_helper_is_correct(self):
        self.assertEqual(C._median([3, 1, 2]), 2)
        self.assertEqual(C._median([4, 1, 2, 3]), 2.5)
        self.assertEqual(C._median([]), 0.0)


class TestReportStatesWhatItDoesNotProve(unittest.TestCase):
    def test_report_carries_an_explicit_honesty_clause(self):
        pages = [20, 21, 22, 23, 24]
        bars = {p: 8 for p in pages}
        with tempfile.TemporaryDirectory() as td:
            out = _make_outdir(td, pages, bars)
            fake = FakeMuseScore(joined_pages=2, bars_by_page=bars)
            C.cmd_compare(Args(out, os.path.join(td, "cmp"), fake.path))
            rep = json.load(open(os.path.join(td, "cmp", "compare.json")))
        self.assertIn("does NOT mean", rep["what_this_does_not_prove"])
        self.assertIn("BLIND", rep["measured_blind_spots"])
        # The blindness is a property of raster measurement, not a tunable
        # threshold. Pinning the measured numbers stops a future reader from
        # "fixing" it by loosening limits that are not the problem.
        self.assertIn("19/30", rep["measured_blind_spots"])
        self.assertIn("score --truth", rep["measured_blind_spots"])


class TestStaffRowUnitsAreAFractionOfSamples(unittest.TestCase):
    """compare.py sampled every 3rd pixel but compared the count against
    w*0.10, making the real cut 30% of samples -- 3x stricter than the name
    and docstring claim. Clean vector renders survived that; the degraded
    200-pii photocopy did not, so the metric went silent on the input that
    most needed it and those pages were reported 'ok' unmeasured."""

    @staticmethod
    def _page(bands, frac=1.0, grey=0, w=1200, h=800, line=3, pitch=40, thick=2):
        """`frac` = the fraction of each row's width that is dark, which is
        exactly what the threshold is supposed to be measuring."""
        img = Image.new("L", (w, h), 255)
        px = img.load()
        span = int(w * frac)
        for b in range(bands):
            y = 60 + b * pitch
            for dy in range(thick):
                for x in range(span):
                    px[x, y + dy] = grey
        return img

    def test_row_just_above_ten_percent_is_counted(self):
        """A bar covering 20% of the row: 80 of 400 samples. The corrected
        threshold (10% of samples = 40) counts it; the old one (30% of
        samples = 120) missed it. This is the discriminating case."""
        img = self._page(bands=6, frac=0.20)
        self.assertEqual(C._staff_like_rows(img), 6)

    def test_faint_photocopy_bars_are_still_counted(self):
        """Grey mush, not black -- the Carpenters case."""
        img = self._page(bands=8, frac=0.20, grey=90)
        self.assertEqual(C._staff_like_rows(img), 8)

    def test_genuinely_blank_page_is_still_zero(self):
        """NEGATIVE CONTROL: the fix must not turn the metric into
        'everything is a staff page'. A blank page stays at zero."""
        blank = Image.new("L", (1200, 800), 255)
        self.assertEqual(C._staff_like_rows(blank), 0)

    def test_sparse_noise_does_not_become_a_staff(self):
        """NEGATIVE CONTROL: 5% of samples dark in a row stays below the
        threshold, so random speckle is not mistaken for a staff line."""
        img = self._page(bands=8, frac=0.05)
        self.assertEqual(C._staff_like_rows(img), 0)

    def test_full_width_bars_still_count(self):
        """Positive control: the unambiguous case keeps working."""
        img = self._page(bands=6, frac=1.0)
        self.assertEqual(C._staff_like_rows(img), 6)


class TestComparePairsEachPageToItself(unittest.TestCase):
    def test_reflow_does_not_mispair_pages(self):
        """4 source pages with DISTINCT staff-band counts; the joined score
        renders 9. The old code scored page N against render N, which after
        the first page belongs to a different source page -- so a correct
        pairing must come out all-ok and a shifted one must not."""
        pages = [20, 21, 22, 23]
        bars = {20: 4, 21: 6, 22: 8, 23: 10}
        with tempfile.TemporaryDirectory() as td:
            out = _make_outdir(td, pages, bars)
            fake = FakeMuseScore(joined_pages=9, bars_by_page=bars)
            rc = C.cmd_compare(Args(out, os.path.join(td, "cmp"), fake.path))
            rep = json.load(open(os.path.join(td, "cmp", "compare.json")))
            calls = fake.calls()

        self.assertEqual([r["page"] for r in rep["results"]], pages)
        # THE ASSERTION THAT MATTERS, and first, so it fails on the old code
        # for the RIGHT reason: a correct 1:1 pairing of distinct bar counts
        # reads clean. The old index-pairing compared each page against a
        # render belonging to a different page and produced a warn/defect.
        self.assertEqual([r["verdict"] for r in rep["results"]], ["clean"] * 4,
                         "a correct 1:1 pairing of distinct bar counts must "
                         "compare clean; a dirty verdict here means pages were "
                         "paired against the wrong render")
        self.assertFalse(rep["joined_pages_are_paired"],
                         "joined render must be declared unpaired")
        self.assertEqual(rep["source_pages"], 4)
        self.assertEqual(rep["joined_render_pages"], 9)
        # every per-page verdict came from that page's OWN musicxml
        rendered = [c for c in calls if not c.endswith("score.mxl")]
        self.assertEqual(len(rendered), 4)
        for p in pages:
            self.assertIn(os.path.join(out, f"p{p:04d}.musicxml"), rendered)
        self.assertEqual(rc, 0)

    def test_report_never_pairs_index_when_counts_differ(self):
        """Direct guard on the invariant, independent of rendering."""
        with tempfile.TemporaryDirectory() as td:
            out = _make_outdir(td, [20, 21], {20: 8, 21: 8})
            fake = FakeMuseScore(joined_pages=7)
            C.cmd_compare(Args(out, os.path.join(td, "cmp"), fake.path))
            rep = json.load(open(os.path.join(td, "cmp", "compare.json")))
        self.assertNotEqual(rep["source_pages"], rep["joined_render_pages"])
        self.assertIn("per-page render", rep["paired_by"])


class _FailsAlways:
    path = None

    def __init__(self):
        d = tempfile.mkdtemp(prefix="failms-")
        self.path = os.path.join(d, "mscore")
        with open(self.path, "w") as fh:
            # writes NOTHING, exits 40 -- MuseScore's silent headless failure
            fh.write('#!/bin/sh\nexit 40\n')
        os.chmod(self.path, 0o755)


class TestRenderRejectsStaleOutput(unittest.TestCase):
    def test_stale_pdf_is_not_reported_as_success(self):
        """MuseScore can fail headlessly and write nothing. If a PDF from a
        previous run is still there, the old code returned it as a fresh
        success -- silently 'verifying' a stale render."""
        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "p0001.musicxml")
            with open(src, "w") as fh:
                fh.write("<score/>")
            out_pdf = os.path.join(td, "p0001.pdf")
            with open(out_pdf, "wb") as fh:      # stale, 5000 bytes of junk
                fh.write(b"y" * 5000)

            with self.assertRaises(SystemExit):
                C.render_score_musescore(src, out_pdf, _FailsAlways().path)
            # the stale file must not survive as a usable "render"
            self.assertFalse(
                os.path.exists(out_pdf) and os.path.getsize(out_pdf) > 1024,
                "stale output left in place after a failed render")

    def test_fresh_render_is_returned(self):
        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "p0001.musicxml")
            with open(src, "w") as fh:
                fh.write("<score/>")
            out_pdf = os.path.join(td, "p0001.pdf")
            got = C.render_score_musescore(src, out_pdf,
                                           FakeMuseScore().path)
            self.assertEqual(got, out_pdf)
            self.assertGreater(os.path.getsize(out_pdf), 1024)


class TestMissingInputsAreNamedNotScored(unittest.TestCase):
    def test_page_without_musicxml_is_named_not_guessed(self):
        with tempfile.TemporaryDirectory() as td:
            out = _make_outdir(td, [20], {20: 8})
            os.remove(os.path.join(out, "p0020.musicxml"))
            fake = FakeMuseScore()
            C.cmd_compare(Args(out, os.path.join(td, "cmp"), fake.path))
            rep = json.load(open(os.path.join(td, "cmp", "compare.json")))
        self.assertEqual(rep["results"][0]["verdict"], "no-musicxml")

    def test_page_without_scan_is_named_not_guessed(self):
        with tempfile.TemporaryDirectory() as td:
            out = _make_outdir(td, [20], {20: 8})
            os.remove(os.path.join(out, "render", "p0020.png"))
            fake = FakeMuseScore()
            C.cmd_compare(Args(out, os.path.join(td, "cmp"), fake.path))
            rep = json.load(open(os.path.join(td, "cmp", "compare.json")))
        self.assertEqual(rep["results"][0]["verdict"], "no-scan")


if __name__ == "__main__":
    unittest.main(verbosity=2)
