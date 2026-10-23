"""
PDF geometry extraction for vector PDFs.

Uses PyMuPDF to extract drawing commands, staff lines, barlines, and symbols.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

try:
    import pymupdf as fitz
except Exception:
    fitz = None


@dataclass
class LineSegment:
    x0: float
    y0: float
    x1: float
    y1: float
    thickness: float = 1.0
    color: Tuple[float, ...] = (0, 0, 0)


@dataclass
class Staff:
    lines: List[LineSegment] = field(default_factory=list)
    y_values: List[float] = field(default_factory=list)
    interline: float = 0.0
    bbox: Tuple[float, float, float, float] = (0, 0, 0, 0)


@dataclass
class System:
    staves: List[Staff] = field(default_factory=list)
    barlines: List[LineSegment] = field(default_factory=list)
    bbox: Tuple[float, float, float, float] = (0, 0, 0, 0)


@dataclass
class Symbol:
    type: str  # notehead, stem, beam, rest, accidental, clef, time, key, dot
    bbox: Tuple[float, float, float, float]
    center: Tuple[float, float]
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PDFGeometry:
    page_width: float
    page_height: float
    lines: List[LineSegment] = field(default_factory=list)
    rects: List[Any] = field(default_factory=list)
    curves: List[Any] = field(default_factory=list)
    ellipses: List[Any] = field(default_factory=list)
    symbols: List[Symbol] = field(default_factory=list)
    systems: List[System] = field(default_factory=list)
    is_vector: bool = True


def is_vector_pdf(doc) -> bool:
    if fitz is None or doc is None:
        return False
    for i in range(min(2, len(doc))):
        page = doc[i]
        paths = page.get_drawings()
        if paths and len(paths) > 10:
            return True
    return False


def extract_drawings(page) -> PDFGeometry:
    if fitz is None:
        raise RuntimeError("pymupdf not available")
    
    geom = PDFGeometry(page_width=page.rect.width, page_height=page.rect.height)
    paths = page.get_drawings()
    
    for p in paths:
        color = p.get('color')
        if color and isinstance(color, (list, tuple)):
            color = tuple(color)
        width = p.get('width', 1.0)
        items = p.get('items', [])
        for item in items:
            if not item:
                continue
            if item[0] == 'l':
                try:
                    p1 = item[1]
                    p2 = item[2]
                    geom.lines.append(LineSegment(float(p1.x), float(p1.y), float(p2.x), float(p2.y), float(width) if width else 1.0, color or (0,0,0)))
                except Exception:
                    pass
            elif item[0] == 're':
                geom.rects.append(item)
            elif item[0] == 'c':
                geom.curves.append(item)
            elif item[0] == 'qu':
                # quadratic curves - often used for noteheads/beams
                geom.curves.append(item)
    
    return geom


def find_horizontal_lines(geom: PDFGeometry, tolerance: float = 2.0, min_len: float = 30.0) -> List[LineSegment]:
    hlines = []
    for seg in geom.lines:
        dx = abs(seg.x1 - seg.x0)
        dy = abs(seg.y1 - seg.y0)
        length = math.hypot(dx, dy)
        if length < min_len:
            continue
        if dy < tolerance:
            hlines.append(seg)
    return hlines


def cluster_staff_lines(hlines: List[LineSegment], tolerance: float = 5.0) -> List[Staff]:
    if not hlines:
        return []
    
    ys = []
    for seg in hlines:
        y = (seg.y0 + seg.y1) / 2.0
        ys.append(y)
    
    ys_rounded = [round(y / tolerance) * tolerance for y in ys]
    ys_sorted = sorted(set(ys_rounded))
    
    staffs = []
    i = 0
    while i < len(ys_sorted) - 4:
        group = ys_sorted[i:i+5]
        gaps = [group[j+1] - group[j] for j in range(4)]
        if gaps:
            avg_gap = sum(gaps) / len(gaps)
            if all(abs(g - avg_gap) < avg_gap * 0.3 for g in gaps):
                staff_lines = []
                for gy in group:
                    for seg in hlines:
                        sy = (seg.y0 + seg.y1) / 2.0
                        if abs(sy - gy) < tolerance * 2:
                            staff_lines.append(seg)
                            break
                if len(staff_lines) >= 3:
                    staff = Staff(lines=staff_lines[:5], y_values=group, interline=avg_gap)
                    xs = [s.x0 for s in staff_lines[:5]] + [s.x1 for s in staff_lines[:5]]
                    ys_box = [s.y0 for s in staff_lines[:5]] + [s.y1 for s in staff_lines[:5]]
                    staff.bbox = (min(xs), min(ys_box), max(xs), max(ys_box))
                    staffs.append(staff)
                i += 5
                continue
        i += 1
    
    return staffs


def group_into_systems(staffs: List[Staff], geom: PDFGeometry, vgap_tol: float = 60.0) -> List[System]:
    if not staffs:
        return []
    
    staffs_sorted = sorted(staffs, key=lambda s: s.y_values[0] if s.y_values else 0)
    
    systems = []
    current = [staffs_sorted[0]]
    for s in staffs_sorted[1:]:
        last = current[-1]
        gap = s.y_values[0] - last.y_values[-1]
        if gap < vgap_tol:
            current.append(s)
        else:
            systems.append(System(staves=current, bbox=(
                min(st.bbox[0] for st in current),
                min(st.bbox[1] for st in current),
                max(st.bbox[2] for st in current),
                max(st.bbox[3] for st in current),
            )))
            current = [s]
    if current:
        systems.append(System(staves=current, bbox=(
            min(st.bbox[0] for st in current),
            min(st.bbox[1] for st in current),
            max(st.bbox[2] for st in current),
            max(st.bbox[3] for st in current),
        )))
    
    return systems


def find_vertical_lines(geom: PDFGeometry, tolerance: float = 2.0, min_len: float = 20.0) -> List[LineSegment]:
    vlines = []
    for seg in geom.lines:
        dx = abs(seg.x1 - seg.x0)
        dy = abs(seg.y1 - seg.y0)
        length = math.hypot(dx, dy)
        if length < min_len:
            continue
        if dx < tolerance:
            vlines.append(seg)
    return vlines


def detect_barlines_in_system(system: System, vlines: List[LineSegment], tolerance: float = 15.0) -> List[LineSegment]:
    if not system.staves:
        return []
    
    system_y0 = min(st.bbox[1] for st in system.staves)
    system_y1 = max(st.bbox[3] for st in system.staves)
    system_x0 = min(st.bbox[0] for st in system.staves)
    system_x1 = max(st.bbox[2] for st in system.staves)
    
    barlines = []
    for seg in vlines:
        seg_y0 = min(seg.y0, seg.y1)
        seg_y1 = max(seg.y0, seg.y1)
        sx = (seg.x0 + seg.x1) / 2.0
        if seg_y1 >= system_y1 - tolerance and seg_y0 <= system_y0 + tolerance:
            if system_x0 - tolerance < sx < system_x1 + tolerance:
                barlines.append(seg)
    
    barlines_sorted = sorted(barlines, key=lambda s: min(s.x0, s.x1))
    unique = []
    for b in barlines_sorted:
        if not unique or abs(min(b.x0,b.x1) - min(unique[-1].x0,unique[-1].x1)) > 3.0:
            unique.append(b)
    return unique


def extract_symbols(geom: PDFGeometry, tolerance: float = 1.0) -> List[Symbol]:
    """Extract musical symbols from geometry (noteheads, stems, beams, etc.)."""
    symbols = []
    
    # Look for ellipses/circles (noteheads) - often drawn as curves or small ovals
    for i, curve in enumerate(geom.curves):
        try:
            # Try to analyze curve for elliptical shape
            if len(curve) >= 4:
                # Quadratic/cubic curves - rough detection
                pass
        except Exception:
            pass
    
    # Look for noteheads as filled ellipses - often in rects or as paths
    for rect in geom.rects:
        try:
            if len(rect) >= 5 and rect[0] == 're':
                r = rect[1]  # rect object
                x0, y0, x1, y1 = float(r.x0), float(r.y0), float(r.x1), float(r.y1)
                w, h = abs(x1-x0), abs(y1-y0)
                # Noteheads are roughly oval, similar width/height
                if 3 < w < 25 and 4 < h < 30 and 0.5 < w/h < 2.0:
                    cx, cy = (x0+x1)/2, (y0+y1)/2
                    symbols.append(Symbol(type='notehead', bbox=(x0,y0,x1,y1), 
                                         center=(cx,cy), data={'filled': True}))
        except Exception:
            pass
    
    # Stems are thin vertical lines
    for seg in geom.lines:
        dx = abs(seg.x1 - seg.x0)
        dy = abs(seg.y1 - seg.y0)
        if dx < 1.0 and dy > 10 and dy < 80:  # thin vertical
            symbols.append(Symbol(type='stem', 
                                 bbox=(min(seg.x0,seg.x1), min(seg.y0,seg.y1), 
                                       max(seg.x0,seg.x1), max(seg.y0,seg.y1)),
                                 center=((seg.x0+seg.x1)/2, (seg.y0+seg.y1)/2)))
    
    # Beams are thick horizontal/angled rectangles
    for seg in geom.lines:
        dx = abs(seg.x1 - seg.x0)
        dy = abs(seg.y1 - seg.y0)
        if dx > 5 and dy < 3 and seg.thickness > 2:  # thick horizontal
            symbols.append(Symbol(type='beam',
                                 bbox=(min(seg.x0,seg.x1), min(seg.y0,seg.y1),
                                       max(seg.x0,seg.x1), max(seg.y0,seg.y1)),
                                 center=((seg.x0+seg.x1)/2, (seg.y0+seg.y1)/2)))
    
    geom.symbols = symbols
    return symbols


def extract_geometry(pdf_path: str, page_num: int = 0) -> PDFGeometry:
    if fitz is None:
        raise RuntimeError("pymupdf not available. Install with: pip install pymupdf")
    
    doc = fitz.open(pdf_path)
    try:
        if page_num >= len(doc):
            raise ValueError(f"Page {page_num} out of range")
        page = doc[page_num]
        geom = extract_drawings(page)
        hlines = find_horizontal_lines(geom)
        staffs = cluster_staff_lines(hlines)
        systems = group_into_systems(staffs, geom)
        geom.systems = systems
        extract_symbols(geom)
        # Also try text-based symbol extraction for music fonts
        try:
            text_syms = extract_symbols_from_text(geom, page)
            if text_syms:
                geom.symbols.extend(text_syms)
        except Exception:
            pass
        return geom
    finally:
        doc.close()


def extract_symbols_from_curves(geom: PDFGeometry) -> List[Symbol]:
    """Better symbol extraction from curves for vector PDFs."""
    symbols = []
    for curve in geom.curves:
        try:
            # Check if it's a filled curve (notehead)
            if len(curve) >= 2:
                # Try to compute bounding box
                points = []
                for item in curve:
                    if isinstance(item, tuple):
                        for p in item:
                            if hasattr(p, 'x') and hasattr(p, 'y'):
                                points.append((p.x, p.y))
                if len(points) >= 3:
                    xs = [p[0] for p in points]
                    ys = [p[1] for p in points]
                    w = max(xs) - min(xs)
                    h = max(ys) - min(ys)
                    if 4 < w < 30 and 5 < h < 35 and 0.4 < w/h < 2.5:
                        cx, cy = sum(xs)/len(xs), sum(ys)/len(ys)
                        symbols.append(Symbol(type='notehead', bbox=(min(xs),min(ys),max(xs),max(ys)),
                                             center=(cx,cy), data={'filled': True}))
        except Exception:
            pass
    return symbols


def extract_symbols_from_text(geom: PDFGeometry, page) -> List[Symbol]:
    """Extract musical symbols from text spans (music fonts like Bravura/MScore)."""
    symbols = []
    try:
        blocks = page.get_text("dict")["blocks"]
        for block in blocks:
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    font = span.get('font', '').lower()
                    text = span.get('text', '')
                    if 'mscore' in font or 'bravura' in font or 'gonville' in font or 'leland' in font:
                        bbox = span.get('bbox', (0,0,0,0))
                        x0,y0,x1,y1 = bbox
                        cx, cy = (x0+x1)/2, (y0+y1)/2
                        # \ue050 is often a notehead in SMuFL
                        if text and ord(text[0]) > 0xE000:
                            symbols.append(Symbol(type='notehead', bbox=bbox, center=(cx,cy),
                                                 data={'code': hex(ord(text[0])), 'font': font}))
    except Exception:
        pass
    return symbols
