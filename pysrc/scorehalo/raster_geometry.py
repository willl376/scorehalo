"""
Geometric analysis for raster/scanned PDFs using calculus and analytic geometry.
"""
from __future__ import annotations

import math
from typing import List, Tuple, Optional

import numpy as np
from PIL import Image

try:
    import cv2
except Exception:
    cv2 = None

try:
    import pymupdf as fitz
except Exception:
    fitz = None


def extract_page_image(pdf_path: str, page_num: int = 0, dpi: int = 300) -> np.ndarray:
    """Extract raster image from PDF page."""
    if fitz is None:
        raise RuntimeError("pymupdf not available")
    doc = fitz.open(pdf_path)
    try:
        page = doc[page_num]
        pix = page.get_pixmap(dpi=dpi)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
        if pix.n == 4:
            img = img[:, :, :3]
        elif pix.n == 1:
            img = np.stack([img[:, :, 0]] * 3, axis=2)
        return img
    finally:
        doc.close()


def estimate_deskew_angle(img_gray: np.ndarray) -> float:
    """Estimate deskew angle using projection variance optimization."""
    if cv2 is None:
        return 0.0
    
    h, w = img_gray.shape
    # Downsample for speed
    small = cv2.resize(img_gray, (w // 4, h // 4))
    
    best_angle = 0.0
    best_var = -1.0
    
    for angle in np.arange(-2.0, 2.1, 0.1):
        # Rotate
        M = cv2.getRotationMatrix2D((small.shape[1]/2, small.shape[0]/2), angle, 1.0)
        rotated = cv2.warpAffine(small, M, (small.shape[1], small.shape[0]), 
                                 flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        # Compute horizontal projection variance
        proj = rotated.sum(axis=1)
        var = np.var(proj)
        if var > best_var:
            best_var = var
            best_angle = angle
    
    return best_angle


def find_staff_systems_geometric(img_gray: np.ndarray, 
                                  min_staff_height: int = 50,
                                  max_staff_height: int = 200) -> List[Tuple[int, int, int, int]]:
    """Find staff systems using geometric analysis."""
    if cv2 is None:
        return []
    
    # Binarize
    _, binary = cv2.threshold(img_gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    
    # Find horizontal lines
    horizontal_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (40, 1))
    horizontal_lines = cv2.morphologyEx(binary, cv2.MORPH_OPEN, horizontal_kernel)
    
    # Find connected components of staff line groups
    contours, _ = cv2.findContours(horizontal_lines, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    
    systems = []
    for cnt in contours:
        x, y, w, h = cv2.boundingRect(cnt)
        if h > min_staff_height and h < max_staff_height and w > 100:
            systems.append((x, y, w, h))
    
    # Sort by y position
    systems.sort(key=lambda s: s[1])
    return systems


def analyze_page_geometry(pdf_path: str, page_num: int = 0) -> dict:
    """Analyze page geometry using calculus-based methods."""
    img = extract_page_image(pdf_path, page_num, dpi=300)
    img_gray = np.mean(img, axis=2).astype(np.uint8)
    
    angle = estimate_deskew_angle(img_gray)
    systems = find_staff_systems_geometric(img_gray)
    
    return {
        'angle': angle,
        'systems': systems,
        'shape': img_gray.shape
    }
