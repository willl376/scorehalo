"""Draw the detected staves and systems onto a page image, for human checking.

The detector's numbers are only as good as the human who checks them, and the
agent cannot see images. This renders exactly what the detector believes --
green box per staff, red box per system, labelled -- so Wilbur can confirm or
refute it in one look instead of trusting a number.

Writes <out>/<name>_boxes.png and prints the geometry it drew.

Usage:
  python scripts/annotate_layout.py data/out-band/render/p0020.png --out /tmp/opencode/anno
"""

import argparse
import os
import sys

import cv2
import numpy as np
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "pysrc"))
from scorehalo.layout import analyze_page  # noqa: E402

GREEN = (0, 190, 0)
RED = (0, 0, 235)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("image")
    ap.add_argument("--out", default="/tmp/opencode/anno")
    ap.add_argument("--dpi", type=int, default=300)
    args = ap.parse_args(argv)

    os.makedirs(args.out, exist_ok=True)
    name = os.path.splitext(os.path.basename(args.image))[0]
    gray = np.array(Image.open(args.image).convert("L"))
    r = analyze_page(gray)
    img = r["deskewed"]                      # boxes live in deskewed coords

    vis = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    sc = img.shape[1] / 2550.0                # scale text/edges with resolution

    def box(color, s, thick):
        cv2.rectangle(vis, (int(s["x0"]), int(s["y_top"])),
                      (int(s["x1"]), int(s["y_bot"])), color, thick)

    for s in r["staves"]:
        box(GREEN, s, max(1, int(2 * sc)))
    for i, s in enumerate(r["systems"]):
        box(RED, s, max(1, int(5 * sc)))
        txt = f"S{i + 1}: {s['n_staves']} staff"
        cv2.putText(vis, txt, (int(s["x0"] + 15 * sc), int(s["y_top"] + 70 * sc)),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.8 * sc, RED, max(1, int(5 * sc)))

    # label each staff with its global index, at the right edge
    for i, s in enumerate(r["staves"]):
        cv2.putText(vis, f"staff {i + 1}", (int(s["x1"] - 210 * sc), int(s["y_center"] + 12 * sc)),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.3 * sc, GREEN, max(1, int(3 * sc)))

    dst = f"{args.out}/{name}_boxes.png"
    Image.fromarray(cv2.cvtColor(vis, cv2.COLOR_BGR2RGB)).save(dst)

    print(f"  wrote {dst}  ({vis.shape[1]}x{vis.shape[0]})")
    print(f"  staves={r['n_staves']}  systems={r['n_systems']}  "
          f"grouping={'+'.join(str(s['n_staves']) for s in r['systems'])}")
    print(f"  staff_space={r['staff_space']}px  skew={r['skew_deg']}deg  "
          f"ink-lines={r['n_lines']}  vertical-spans={r['n_vspans']}")
    print()
    for i, s in enumerate(r["staves"]):
        print(f"    staff {i + 1}: y {s['y_top']:.0f}-{s['y_bot']:.0f} "
              f"(centre {s['y_center']:.0f})  x {s['x0']}-{s['x1']}  lines={s['n_lines']}")
    print()
    for i, s in enumerate(r["systems"]):
        print(f"    system {i + 1}: {s['n_staves']} staff(s)  "
              f"y {s['y_top']:.0f}-{s['y_bot']:.0f}  x {s['x0']}-{s['x1']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
