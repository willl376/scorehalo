"""PDF page rendering with pypdfium2."""

import os
import pypdfium2 as pdfium

DEFAULT_DPI = 300
# pypdfium2 scale: pixels per point (72dpi == 1.0)
DPI_TO_SCALE = 72.0


def page_count(pdf_path):
    return len(pdfium.PdfDocument(pdf_path))


def render_page(pdf_path, page_index, out_path, dpi=DEFAULT_DPI):
    """Render one page (0-based) to a PNG. Returns size tuple."""
    pdf = pdfium.PdfDocument(pdf_path)
    page = pdf[page_index]
    bitmap = page.render(scale=dpi / DPI_TO_SCALE)
    pil = bitmap.to_pil()
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    pil.save(out_path)
    return pil.size


def render_range(pdf_path, page_nums_1based, out_dir, dpi=DEFAULT_DPI):
    """Render 1-based page numbers to out_dir/pXXX.png. Returns list of (page, path, size)."""
    os.makedirs(out_dir, exist_ok=True)
    results = []
    pdf = pdfium.PdfDocument(pdf_path)
    for n in page_nums_1based:
        page = pdf[n - 1]
        bitmap = page.render(scale=dpi / DPI_TO_SCALE)
        pil = bitmap.to_pil()
        path = os.path.join(out_dir, f"p{n:04d}.png")
        pil.save(path)
        results.append((n, path, pil.size))
    return results