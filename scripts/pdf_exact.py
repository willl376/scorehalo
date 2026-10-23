#!/usr/bin/env python3
"""
pdf-exact: Convert PDF directly to MusicXML using geometric extraction.
"""
import argparse
import sys
from pathlib import Path

from scorehalo import pdf_to_musicxml


def main():
    p = argparse.ArgumentParser(description="Convert PDF to MusicXML via geometric extraction")
    p.add_argument("input", help="Input PDF file")
    p.add_argument("-o", "--out", help="Output MusicXML file")
    p.add_argument("--title", help="Work title")
    args = p.parse_args()
    
    if not Path(args.input).exists():
        print(f"Error: {args.input} not found", file=sys.stderr)
        return 1
    
    if args.out is None:
        args.out = args.input.replace('.pdf', '.musicxml')
    
    try:
        pdf_to_musicxml.pdf_to_musicxml(args.input, args.out, args.title)
        print(f"Wrote {args.out}")
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
