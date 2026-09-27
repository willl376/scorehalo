"""Patch homr so cosmetic title detection can never destroy real recognition.

homr reads the page's title with RapidOCR in order to fill in MusicXML
`<work-title>`. That is metadata. But the title path has three ways to abort a
run that had already recognised the music, all observed in the wild:

1. A hardcoded 60s timeout on the title future, awaited AFTER recognition and
   BEFORE the XML is written (main.py). Observed: p0022 s1 raised TimeoutError;
   p0030 s8, p0031 s9/s12, p0032 s6, p0036 s2 hung and were SIGKILLed by our
   own 900s timeout -- ~75 minutes of wall time lost to one cosmetic feature.

2. An empty region above the staff. title_detection.py computes
   `height = min(height, int(top_staff.min_y) - y)`; when homr's detected top
   staff has min_y == 0 that is zero, so `above_staff` is an empty array and
   `cv2.imwrite` dies on `!_img.empty()`. Observed: p0031 s3/s6, p0032 s12,
   p0033 s3/s6/s12.

3. (guarded) any other OCR exception.

Neither changes a single note: the title is the only thing these paths touch.
Both patches are idempotent, keep a .orig backup, and are check-only unless
--apply is passed.

    python scripts/patch_homr_title_timeout.py            # report
    python scripts/patch_homr_title_timeout.py --apply    # patch in place
"""

import argparse
import os
import sys

HUNKS = [
    (
        "main.py",
        "        title = title_future.result(60)\n",
        """        try:
            title = title_future.result(60)
        except Exception:  # title OCR is cosmetic; never discard recognition
            title = ""     # for it (see scripts/patch_homr_title_timeout.py)
""",
    ),
    (
        "title_detection.py",
        "    above_staff = image[y : y + height, x : x + width]\n",
        """    if height <= 0 or width <= 0:
        return ""  # no room above the staff; title detection is optional
    above_staff = image[y : y + height, x : x + width]
""",
    ),
    (
        "title_detection.py",
        "    return _executor.submit(_detect_title_task, debug, top_staff)\n",
        """    # Disabled by ScoreHalo: the title feeds only MusicXML <work-title>, and
    # this OCR path could hang in native code, time out, or crash on an empty
    # region -- each time destroying a recognition that had already finished.
    # The hang was NOT catchable by the .result(60) guard, because native
    # onnxruntime code held the GIL, so Python could not even run its timeout.
    f: Future[str] = Future()
    f.set_result("")
    return f
""",
    ),
]


def homr_dir():
    try:
        import homr
    except ImportError:
        return None
    return os.path.dirname(homr.__file__)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="write the patches (default is report-only)")
    args = ap.parse_args(argv)

    d = homr_dir()
    if not d:
        print("  homr is not importable from this interpreter.")
        print("  Run with the ScoreHalo venv: .venv/bin/python "
              "scripts/patch_homr_title_timeout.py")
        return 1

    todo = []
    for fname, old, new in HUNKS:
        path = os.path.join(d, fname)
        if not os.path.exists(path):
            print(f"  !! missing {path} -- skipping that hunk")
            continue
        src = open(path).read()
        if new in src:
            print(f"  already patched: {fname}")
            continue
        if old not in src:
            print(f"  !! {fname} does not contain the expected line.")
            print("     homr has probably been upgraded; re-read it and adjust.")
            return 1
        todo.append((path, old, new))

    if not todo:
        return 0

    if not args.apply:
        for path, _, _ in todo:
            print(f"  would patch: {path}")
        print("  re-run with --apply to do it.")
        return 0

    for path, old, new in todo:
        src = open(path).read()
        backup = path + ".orig"
        if not os.path.exists(backup):
            with open(backup, "w") as f:
                f.write(src)
        with open(path, "w") as f:
            f.write(src.replace(old, new, 1))
        print(f"  patched {path}  (original at {backup})")
    print("  restore all with: for f in " +
          " ".join(p + ".orig" for p, _, _ in todo) + "; do mv \"$f.orig\" \"$f\"; done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
