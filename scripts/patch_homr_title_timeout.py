"""Patch homr so a cosmetic title-OCR timeout cannot discard real recognition.

homr's main.py does this, at the end of process_image():

        title = title_future.result(60)     # <- hardcoded 60s, no way to configure
        eprint("Found title:", title)
        xml = generate_xml(xml_generator_args, result_staffs, title)
        xml.write(xml_file)

The title is the page's name, read with RapidOCR, and it is used for exactly one
thing: `build_work(title)` -> `<work-title>` in the MusicXML. Pure metadata.

But the XML is written *after* that line, so when the OCR future times out,
homr raises TimeoutError and throws away a recognition it had already finished.
Observed on p0022 staff 1, whose log shows the model completing inference
("Removing tuplets from measure # 5") and then dying on the title. The music was
recognised and lost.

This wraps the wait so a timeout (or any OCR failure) degrades to an empty
title instead of aborting the run. It changes no musical output.

Idempotent: safe to run twice. Check-only unless --apply is passed.

    python scripts/patch_homr_title_timeout.py            # report
    python scripts/patch_homr_title_timeout.py --apply    # patch in place
"""

import argparse
import os
import sys

OLD = "        title = title_future.result(60)\n"
NEW = """        try:
            title = title_future.result(60)
        except Exception:  # title OCR is cosmetic; never discard recognition
            title = ""     # for it (see scripts/patch_homr_title_timeout.py)
"""


def find_main():
    try:
        import homr
    except ImportError:
        return None
    return os.path.join(os.path.dirname(homr.__file__), "main.py")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="write the patch (default is report-only)")
    args = ap.parse_args(argv)

    path = find_main()
    if not path or not os.path.exists(path):
        print("  homr is not importable from this interpreter.")
        print("  Run with the ScoreHalo venv: .venv/bin/python "
              "scripts/patch_homr_title_timeout.py")
        return 1

    src = open(path).read()
    if NEW in src:
        print(f"  already patched: {path}")
        return 0
    if OLD not in src:
        print(f"  !! could not find the expected line in {path}.")
        print("  homr has probably been upgraded; re-read main.py and adjust.")
        return 1

    print(f"  target: {path}")
    if not args.apply:
        print("  would wrap `title_future.result(60)` in try/except.")
        print("  re-run with --apply to do it.")
        return 0

    backup = path + ".orig"
    if not os.path.exists(backup):
        with open(backup, "w") as f:
            f.write(src)
    with open(path, "w") as f:
        f.write(src.replace(OLD, NEW, 1))
    print(f"  patched. original saved to {backup}")
    print("  restore with: mv " + backup + " " + path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
