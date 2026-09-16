"""Image preprocessing for degraded legacy land records.

Steps: load (PDF/image) -> grayscale -> denoise -> deskew -> adaptive threshold.
Also computes an image-quality score used in confidence weighting.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from app.core.config import settings

MAX_SIDE = 2400  # px; working resolution cap for OCR


def _poppler_path() -> str | None:
    """Windows: locate poppler's bin folder if pdftoppm is not on PATH."""
    import glob
    import shutil
    import sys

    if settings.poppler_path:
        return settings.poppler_path
    if sys.platform != "win32" or shutil.which("pdftoppm"):
        return None
    import os

    winget = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages")
    for pattern in (r"C:\poppler\Library\bin", r"C:\poppler*\Library\bin", r"C:\Program Files\poppler*\Library\bin",
                    r"C:\poppler*\bin", os.path.join(winget, "oschwartz10612.Poppler*", "*", "Library", "bin"),
                    os.path.join(winget, "oschwartz10612.Poppler*", "Library", "bin")):
        hits = sorted(glob.glob(pattern), reverse=True)
        if hits:
            return hits[0]
    return None


def load_pages(path: str | Path) -> list[np.ndarray]:
    """Return a list of BGR page images for an image or PDF path."""
    path = Path(path)
    if path.suffix.lower() == ".pdf":
        from pdf2image import convert_from_path

        pil_pages = convert_from_path(str(path), dpi=settings.ocr_dpi, poppler_path=_poppler_path())
        return [cv2.cvtColor(np.array(p.convert("RGB")), cv2.COLOR_RGB2BGR) for p in pil_pages]
    img = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"Could not decode image: {path}")
    return [img]


def deskew(gray: np.ndarray) -> tuple[np.ndarray, float]:
    """Estimate skew via minAreaRect of text pixels and rotate to correct it."""
    inv = cv2.bitwise_not(gray)
    thresh = cv2.threshold(inv, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)[1]
    coords = np.column_stack(np.where(thresh > 0))
    if len(coords) < 50:
        return gray, 0.0
    angle = cv2.minAreaRect(coords.astype(np.float32))[-1]
    if angle < -45:
        angle = 90 + angle
    elif angle > 45:
        angle = angle - 90
    if abs(angle) < 0.3 or abs(angle) > 15:  # ignore noise / implausible values
        return gray, 0.0
    h, w = gray.shape
    m = cv2.getRotationMatrix2D((w // 2, h // 2), angle, 1.0)
    rotated = cv2.warpAffine(gray, m, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    return rotated, float(angle)


def quality_score(gray: np.ndarray) -> float:
    """Heuristic 0-100 quality: sharpness (Laplacian variance) + contrast + resolution."""
    sharp = cv2.Laplacian(gray, cv2.CV_64F).var()
    sharp_s = min(sharp / 500.0, 1.0)
    contrast_s = min(gray.std() / 60.0, 1.0)
    res_s = min(min(gray.shape) / 1200.0, 1.0)
    return round(100 * (0.45 * sharp_s + 0.35 * contrast_s + 0.20 * res_s), 1)


def preprocess(img: np.ndarray) -> dict:
    """Return dict with 'binary' (for OCR), 'gray', 'quality', 'skew'."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img.copy()
    # Upscale small scans so Tesseract sees ~300dpi-equivalent glyphs
    if min(gray.shape) < 1000:
        scale = 1000 / min(gray.shape)
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    # ...and cap very large scans (300-dpi A4 PDFs) so denoising stays fast
    if max(gray.shape) > MAX_SIDE:
        scale = MAX_SIDE / max(gray.shape)
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    q = quality_score(gray)
    # Non-local-means handles scanner grain far better than bilateral/median (which leave
    # speckle that makes Tesseract extremely slow); MAX_SIDE keeps its cost bounded (~3-5 s).
    den = cv2.fastNlMeansDenoising(gray, None, h=10, templateWindowSize=7, searchWindowSize=21)
    den, angle = deskew(den)
    # CLAHE brings up faded ink
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(den)
    binary = cv2.adaptiveThreshold(enhanced, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                   cv2.THRESH_BINARY, 31, 15)
    return {"binary": binary, "gray": enhanced, "quality": q, "skew": angle}
