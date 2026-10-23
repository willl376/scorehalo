"""
Scanned PDF constraint path: use original PDF geometry to snap
scorehalo's per-staff homr output to original system breaks.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Tuple, Optional

try:
    import numpy as np
    import cv2
except Exception:
    np = None
    cv2 = None

try:
    from PIL import Image
except Exception:
    Image = None

from . import pdf, raster_geometry


def extract_system_geometry_from_image(img_path: str) -> Dict:
    """Extract system/staff geometry from a rendered/scanned page image."""
    if cv2 is None:
        return {}
    
    img = cv2.imread(str(img_path))
    if img is None:
        return {}
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    
    # Binarize
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    
    # Find horizontal staff lines
    horizontal_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (50, 1))
    horizontal_lines = cv2.morphologyEx(binary, cv2.MORPH_OPEN, horizontal_kernel)
    
    contours, _ = cv2.findContours(horizontal_lines, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    
    staffs = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        if h >= 1 and h <= 20 and w > 150:
            staffs.append((y, x, w, h))
    
    staffs.sort()
    
    # Group into systems of 5
    systems = []
    i = 0
    while i < len(staffs):
        group = []
        for j in range(i, min(i + 40, len(staffs))):
            if not group or staffs[j][0] - group[-1][0] < 30:
                group.append(staffs[j])
            else:
                break
        if len(group) >= 5:
            # Take first 5 lines as a staff system
            staff_lines = group[:5]
            ys = [s[0] for s in staff_lines]
            xs = [s[1] for s in staff_lines]
            ws = [s[2] for s in staff_lines]
            gaps = [ys[k+1]-ys[k] for k in range(min(4, len(ys)-1))]
            interline = sum(gaps) / max(1, len(gaps))
            systems.append({
                'y_top': min(ys),
                'y_bottom': max(ys) + max(staff_lines, key=lambda x: x[3])[3],
                'x_left': min(xs),
                'x_right': max([xs[k] + ws[k] for k in range(len(xs))]),
                'line_y': ys,
                'interline': interline
            })
            i += len(group)
        else:
            i += 1
    
    return {
        'systems': systems,
        'image_size': gray.shape
    }


def snap_to_original_layout(page_xml_path: str, constraints: Dict) -> str:
    """Snap generated MusicXML to match original PDF layout."""
    try:
        with open(page_xml_path) as f:
            content = f.read()
        
        systems = constraints.get('systems', [])
        if not systems:
            return content
        
        # Add system-layout information
        # This is a simplified version - in practice would need more complex XML manipulation
        modified = content
        
        # Add print elements with system layout hints
        for i, sys in enumerate(systems):
            layout = f'''      <print new-system="yes">
        <system-layout>
          <system-margins>
            <left-margin>50</left-margin>
            <right-margin>50</right-margin>
          </system-margins>
          <system-distance>{int(sys.get('interline', 10) * 5)}</system-distance>
        </system-layout>
      </print>'''
            # Try to insert after first measure's attributes
            if '<measure number="1">' in modified and '<print' not in modified.split('<measure number="1">')[1].split('</measure>')[0]:
                modified = modified.replace('<measure number="1">', '<measure number="1">\n' + layout, 1)
                break  # Only first for now
        
        return modified
    except Exception as e:
        return content


def process_scanned_pdf(pdf_path: str, page_num: int, out_dir: str) -> Dict:
    """Process a scanned PDF using geometric constraints."""
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    
    # Render page
    img_path = out_path / f'page_{page_num}.png'
    try:
        pdf.render_page(pdf_path, page_num, out_path=str(img_path))
    except Exception:
        pass
    
    # Extract constraints
    constraints = extract_system_geometry_from_image(str(img_path)) if img_path.exists() else {}
    
    return {
        'image': str(img_path) if img_path.exists() else None,
        'constraints': constraints
    }
