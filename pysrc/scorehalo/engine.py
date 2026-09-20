"""homr engine wrapper: run the homr CLI on one image, capture its MusicXML."""

import os
import shutil
import subprocess
import sys

HOMR_BIN = os.environ.get("HOMR_BIN", None)
RESULT_SUFFIX = ".musicxml"


def _homr_binary():
    if HOMR_BIN:
        return HOMR_BIN
    # homr ships in the same venv as this package
    sibling = os.path.join(os.path.dirname(sys.executable), "homr")
    if os.path.exists(sibling):
        return sibling
    found = shutil.which("homr")
    if not found:
        raise RuntimeError("homr not found; set HOMR_BIN=/path/to/homr")
    return found


def run_homr(image_path, work_dir, timeout=600):
    """Run homr on image_path. homr writes <image_basename>.musicxml next to
    the image, so we point it at a copy inside work_dir and return the xml path
    (or None on genuine failure)."""
    os.makedirs(work_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(image_path))[0]
    staged = os.path.join(work_dir, base + "_in.png")
    shutil.copy(image_path, staged)
    proc = subprocess.run(
        [_homr_binary(), staged],
        capture_output=True, text=True, timeout=timeout,
    )
    log_path = os.path.join(work_dir, base + ".homr.log")
    with open(log_path, "w") as fh:
        fh.write(proc.stdout)
        fh.write(proc.stderr)
    out_xml = staged[:-len(".png")] + RESULT_SUFFIX  # homr names <in-url-basename>.musicxml, drops .png
    if proc.returncode == 0 and os.path.exists(out_xml):
        return out_xml, log_path, proc.returncode
    return None, log_path, proc.returncode