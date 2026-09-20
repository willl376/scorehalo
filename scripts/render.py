"""Render PDF pages to PNG. KISS: one page in, one PNG out."""

import pypdfium2 as pdfium
import sys
import os


def render(pdf_path, page_index, out_path, scale=1.5):
    pdf = pdfium.PdfDocument(pdf_path)
    page = pdf[page_index]
    bitmap = page.render(scale=scale)
    pil = bitmap.to_pil()
    pil.save(out_path)
    print(f"rendered p{page_index + 1} -> {out_path} ({pil.size[0]}x{pil.size[1]})")


if __name__ == "__main__":
    pdf, pageno, out, scale = sys.argv[1], int(sys.argv[2]), sys.argv[3], float(os.environ.get("RENDER_SCALE", "1.5"))
    render(pdf, pageno - 1, out, scale)