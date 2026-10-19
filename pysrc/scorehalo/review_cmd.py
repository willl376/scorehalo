"""``scorehalo review``: run both label-free checks over a set of pages.

Thin adapter over :mod:`scorehalo.review`, in the shape of ``toly_cmd`` and
``canon``: parse arguments, call the module, print the report.
"""

from __future__ import annotations

import argparse
import glob
import json
import os

from .review import discover_pages, format_review, review_page


def _assembled_path(pages_dir, page_id):
    """Where build_page_graph.py puts an assembled page, if it was asked for."""
    if not pages_dir:
        return None
    for candidate in (os.path.join(pages_dir, page_id + ".musicxml"),
                      os.path.join(pages_dir, "homr", page_id,
                                   page_id + ".musicxml")):
        if os.path.exists(candidate):
            return candidate
    return os.path.join(pages_dir, page_id + ".musicxml")


def cmd_review(args):
    system_root = args.system_dir
    perstaff_root = args.perstaff_dir
    pages = discover_pages(system_root, perstaff_root)
    if not pages:
        print("review: no pages found under both %s and %s"
              % (system_root, perstaff_root))
        return 1

    results = []
    for page_id in pages:
        results.append(review_page(
            page_id,
            os.path.join(system_root, "homr", page_id),
            os.path.join(perstaff_root, "homr", page_id),
            _assembled_path(args.pages, page_id),
            # Lets the coverage check read <page>.boxes.json, so a crop that
            # missed staves is not reported as a note-reading disagreement.
            system_root,
        ))

    print(format_review(results))
    if args.json:
        # DeviationReport and friends are dataclasses, not JSON-serialisable, so
        # the json gets the flags and a summary rather than the whole object.
        payload = [{
            "page": r["page"],
            "flags": r["flags"],
            "patterns": (None if r["patterns"] is None else {
                "systems": len(r["patterns"].observed),
                "repeating": len(r["patterns"].repeats()),
                "deviations": len(r["patterns"].deviations),
                "weak": r["patterns"].weak,
            }),
        } for r in results]
        with open(args.json, "w") as fh:
            json.dump(payload, fh, indent=2)
        print("\nwrote %s" % args.json)
    return 0


def add_parser(sub):
    p = sub.add_parser(
        "review",
        help="label-free per-page review: pattern breaks + two-count "
             "reconciliation",
    )
    p.add_argument("system_dir", help="root holding per-system fragments")
    p.add_argument("perstaff_dir", help="root holding per-staff fragments")
    p.add_argument("--pages", default=None,
                   help="assembled pages dir, from build_page_graph.py --outdir")
    p.add_argument("--json", default=None)
    p.set_defaults(fn=cmd_review)
    return p