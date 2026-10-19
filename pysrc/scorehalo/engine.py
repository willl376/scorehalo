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
    the image, so we point it at a copy inside work_dir.

    Returns a 3-tuple ``(xml_path_or_None, log_path, returncode)``. The xml
    path is None on failure; the log always exists. Callers MUST unpack all
    three -- the previous version of this docstring claimed a bare path and
    that lie has already cost one caller a TypeError.

    A timeout is a *failure*, not an exception: returncode -1 with the timeout
    written to the log. The engine is a transformer asked to find notation, so
    a picture (or a page nothing like notation) can keep it searching until the
    budget runs out; letting ``TimeoutExpired`` escape would end a whole book's
    run at that page, which is the stall this return value exists to prevent.
    ``subprocess.run`` kills the child on timeout, so nothing keeps spinning.
    """
    os.makedirs(work_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(image_path))[0]
    staged = os.path.join(work_dir, base + "_in.png")
    shutil.copy(image_path, staged)
    log_path = os.path.join(work_dir, base + ".homr.log")
    try:
        proc = subprocess.run(
            [_homr_binary(), staged],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        with open(log_path, "w") as fh:
            fh.write("homr timed out after %ss\n" % timeout)
            for stream in (getattr(exc, "stdout", None),
                           getattr(exc, "stderr", None)):
                if stream:
                    fh.write(stream if isinstance(stream, str)
                             else stream.decode("utf-8", "replace"))
        return None, log_path, -1
    with open(log_path, "w") as fh:
        fh.write(proc.stdout)
        fh.write(proc.stderr)
    out_xml = staged[:-len(".png")] + RESULT_SUFFIX  # homr names <in-url-basename>.musicxml, drops .png
    if proc.returncode == 0 and os.path.exists(out_xml):
        return out_xml, log_path, proc.returncode
    return None, log_path, proc.returncode