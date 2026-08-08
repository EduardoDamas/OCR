"""
Image preprocessing: PDF loading, deskew, perspective correction, normalization.
"""

import cv2
import numpy as np
from pathlib import Path
from typing import List


def load_images_from_pdf(pdf_path: str, dpi: int = 300) -> List[np.ndarray]:
    """Convert each PDF page to a BGR numpy array. Uses PyMuPDF if available."""
    try:
        import fitz  # PyMuPDF — preferred, no external binary needed
        doc = fitz.open(pdf_path)
        zoom = dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)
        pages = []
        for page in doc:
            pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
            pages.append(cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
        doc.close()
        return pages
    except ImportError:
        pass

    # Fallback: pdf2image + poppler
    try:
        from pdf2image import convert_from_path
        pil_images = convert_from_path(pdf_path, dpi=dpi)
        return [cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR) for img in pil_images]
    except ImportError:
        raise RuntimeError(
            "No PDF renderer found. Install PyMuPDF:  pip install pymupdf"
        )


def load_image(path: str) -> np.ndarray:
    """Load a single image file (JPG, PNG, etc.)."""
    img = cv2.imread(path)
    if img is None:
        raise ValueError(f"Could not load image: {path}")
    return img


def load_source(path: str, dpi: int = 300) -> List[np.ndarray]:
    """Auto-detect PDF vs image and return list of BGR frames."""
    p = Path(path)
    if p.suffix.lower() == ".pdf":
        return load_images_from_pdf(path, dpi=dpi)
    else:
        return [load_image(path)]


def to_gray(img: np.ndarray) -> np.ndarray:
    if len(img.shape) == 2:
        return img
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


# Calibration reference width: the detector's line/edge thresholds are tuned for
# pages about this wide (≈ a sheet rendered at 200 DPI).
CANONICAL_WIDTH = 1654


def normalize_width(img: np.ndarray, target_width: int = CANONICAL_WIDTH) -> np.ndarray:
    """
    Resize a page so its width matches the calibration width.

    Critical for accuracy: many detection steps use ABSOLUTE thresholds (Canny,
    adaptive-threshold constants, Hough line lengths) tuned for ~1654px-wide
    pages.  Feeding a high-resolution input — a phone photo (~3000-4000px) or a
    high-DPI render — makes those thresholds wrong and detection collapses
    (measured: 47% at 3308px vs 73% at 1654px).  Normalizing first keeps
    accuracy stable across any input resolution.
    """
    w = img.shape[1]
    if w == 0 or abs(w - target_width) <= target_width * 0.05:
        return img
    scale = target_width / w
    new_h = max(1, int(round(img.shape[0] * scale)))
    interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC
    return cv2.resize(img, (target_width, new_h), interpolation=interp)


def _horizontal_line_mask(gray: np.ndarray) -> np.ndarray:
    """Isolate long horizontal printed table lines via morphology."""
    w = gray.shape[1]
    bw = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                               cv2.THRESH_BINARY_INV, 15, 8)
    hk = cv2.getStructuringElement(cv2.MORPH_RECT, (max(20, w // 40), 1))
    return cv2.morphologyEx(bw, cv2.MORPH_OPEN, hk)


def deskew(img: np.ndarray) -> np.ndarray:
    """
    Correct small rotation from the photographed sheet.

    Uses the printed table's long horizontal lines (very reliable on these
    forms) to estimate the skew angle, instead of generic Canny edges which
    pick up text and background clutter.
    """
    gray = to_gray(img)
    horiz = _horizontal_line_mask(gray)

    lines = cv2.HoughLinesP(horiz, 1, np.pi / 360, threshold=120,
                            minLineLength=img.shape[1] // 6, maxLineGap=30)
    if lines is None:
        return img

    angles = []
    for line in lines:
        x1, y1, x2, y2 = line[0]
        if x2 != x1:
            angle = np.degrees(np.arctan2(y2 - y1, x2 - x1))
            if abs(angle) < 20:        # table lines are near-horizontal
                angles.append(angle)

    if not angles:
        return img

    median_angle = np.median(angles)
    if abs(median_angle) < 0.2:
        return img

    h, w = img.shape[:2]
    center = (w // 2, h // 2)
    M = cv2.getRotationMatrix2D(center, median_angle, 1.0)
    rotated = cv2.warpAffine(img, M, (w, h),
                              flags=cv2.INTER_CUBIC,
                              borderMode=cv2.BORDER_REPLICATE)
    return rotated


def correct_perspective(img: np.ndarray) -> np.ndarray:
    """
    Detect the largest quadrilateral (the sheet border) and apply
    a perspective warp to produce a flat, top-down view.
    Returns the original image if no clear quad is found.
    """
    gray = to_gray(img)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    thresh = cv2.adaptiveThreshold(blur, 255,
                                   cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                   cv2.THRESH_BINARY_INV, 11, 2)

    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return img

    contours = sorted(contours, key=cv2.contourArea, reverse=True)
    sheet_contour = None
    for c in contours[:5]:
        peri = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, 0.02 * peri, True)
        if len(approx) == 4:
            area = cv2.contourArea(approx)
            img_area = img.shape[0] * img.shape[1]
            if area > 0.3 * img_area:
                sheet_contour = approx
                break

    if sheet_contour is None:
        return img

    pts = sheet_contour.reshape(4, 2).astype(np.float32)
    pts = _order_points(pts)

    (tl, tr, br, bl) = pts
    width = int(max(np.linalg.norm(br - bl), np.linalg.norm(tr - tl)))
    height = int(max(np.linalg.norm(tr - br), np.linalg.norm(tl - bl)))

    dst = np.array([[0, 0], [width - 1, 0],
                    [width - 1, height - 1], [0, height - 1]], dtype=np.float32)
    M = cv2.getPerspectiveTransform(pts, dst)
    warped = cv2.warpPerspective(img, M, (width, height))
    return warped


def _order_points(pts: np.ndarray) -> np.ndarray:
    """Order points as: top-left, top-right, bottom-right, bottom-left."""
    rect = np.zeros((4, 2), dtype=np.float32)
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]
    rect[2] = pts[np.argmax(s)]
    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)]
    rect[3] = pts[np.argmax(diff)]
    return rect


def enhance(img: np.ndarray) -> np.ndarray:
    """Normalize contrast and denoise for cleaner thresholding."""
    gray = to_gray(img)
    denoised = cv2.fastNlMeansDenoising(gray, h=10)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(denoised)
    return enhanced


def preprocess(img: np.ndarray, correct_persp: bool = True) -> np.ndarray:
    """Full preprocessing pipeline: perspective → deskew → enhance."""
    if correct_persp:
        img = correct_perspective(img)
    img = deskew(img)
    enhanced = enhance(img)
    return enhanced
