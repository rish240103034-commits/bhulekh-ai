"""Detect the document boundary in a photograph and warp it to an orthogonal image.

Real-world uploads are often phone photos of paper records lying on a desk or held in
hand, not clean scans. Those photos carry perspective distortion, a background around
the paper, and dog-eared corners. Downstream OCR and ruled-grid table detection both
assume an orthogonal, borderless page — on a raw photo they simply fail. This module
finds the paper region and warps it to a straight rectangle before preprocessing.

The detector is conservative: it only warps when it is confident the boundary is real
(a low-saturation paper blob against a higher-saturation background, occupying between
20% and 92% of the frame). Clean scans, where the paper fills the frame, are passed
through unchanged. Everything runs locally with OpenCV — no external services, so the
document content never leaves the machine.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


# ---------------------------------------------------------------------- rotation

def detect_rotation(img: np.ndarray, langs: str = "eng") -> tuple[int, float]:
    """Guess if a photo was uploaded sideways or upside down.

    Tries 0 / 90 / 180 / 270 degrees, reads a downsized version at each rotation
    with Tesseract's fast page-segmentation mode, and picks the orientation with
    the highest mean word confidence. The score gap has to be meaningful (5+ pts)
    to accept a non-zero rotation, so a scan whose OCR quality is roughly equal
    across rotations (a rare but real case with abstract diagrams) is left alone.

    Returns (best_angle, best_mean_confidence). angle ∈ {0, 90, 180, 270}.
    """
    import pytesseract
    try:
        # Downscale so the whole probe stays under ~1 second on a laptop.
        h, w = img.shape[:2]
        scale = 900.0 / max(h, w)
        small = cv2.resize(img, (int(w * scale), int(h * scale)))
    except cv2.error:
        return 0, 0.0

    def _mean_conf(candidate: np.ndarray) -> float:
        try:
            data = pytesseract.image_to_data(
                candidate, lang=langs, config="--oem 3 --psm 6",
                output_type=pytesseract.Output.DICT, timeout=6.0)
        except Exception:
            return 0.0
        confs = [int(c) for c in data["conf"] if str(c).isdigit() and int(c) >= 0]
        words = [w for w in data["text"] if w and w.strip()]
        if not confs or len(words) < 4:                # too little content to trust
            return 0.0
        return sum(confs) / len(confs)

    rotations = {
        0: small,
        90: cv2.rotate(small, cv2.ROTATE_90_CLOCKWISE),
        180: cv2.rotate(small, cv2.ROTATE_180),
        270: cv2.rotate(small, cv2.ROTATE_90_COUNTERCLOCKWISE),
    }
    scores = {angle: _mean_conf(rot) for angle, rot in rotations.items()}
    best_angle = max(scores, key=lambda a: scores[a])
    # Only accept a rotation when it's meaningfully more confident than 0°.
    if best_angle != 0 and scores[best_angle] - scores[0] < 5.0:
        best_angle = 0
    return best_angle, scores[best_angle]


def apply_rotation(img: np.ndarray, angle: int) -> np.ndarray:
    if angle == 90:
        return cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
    if angle == 180:
        return cv2.rotate(img, cv2.ROTATE_180)
    if angle == 270:
        return cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return img


# ---------------------------------------------------------------- content check

def looks_like_document(img: np.ndarray) -> tuple[bool, str]:
    """A cheap sanity check that the upload actually contains a printed page.

    Rejects near-blank photos (all-white walls, all-dark photos of a floor) and
    photos with no strong horizontal/vertical structure. Returns (is_document,
    reason). Called before the expensive OCR/table passes so a wrong upload
    fails fast with an explanation the reviewer can act on.
    """
    if img is None or img.size == 0:
        return False, "Image is empty."
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    # A flat photo (blank wall, sky) has almost no variance.
    if float(gray.std()) < 8.0:
        return False, "The uploaded image has almost no contrast — it looks like a blank photo, not a scanned page."
    # A page has SOME straight-line structure (edges, rules, margins). A random
    # photo of a person or an object does not. Canny + Hough gives a rough proxy.
    edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 60, 180)
    if edges.sum() < 5000:
        return False, "No text-like edges found — please upload a photo or scan of a written land record."
    return True, ""


@dataclass
class WarpInfo:
    """What the boundary detector decided, for diagnostics and UI feedback."""
    warped: bool
    detector: str                # "hsv-mask", "canny-fallback", "identity", "none"
    boundary_area_ratio: float   # 0..1, share of the source frame the paper covered
    quad: list[list[int]] | None # 4 corners in source-image pixel coords, TL/TR/BR/BL
    reason: str                  # human-readable note for the UI

    def as_dict(self) -> dict:
        return {"warped": self.warped, "detector": self.detector,
                "boundary_area_ratio": round(self.boundary_area_ratio, 3),
                "quad": self.quad, "reason": self.reason}


def _order_quad(pts: np.ndarray) -> np.ndarray:
    """Return quad corners in TL, TR, BR, BL order."""
    pts = pts.reshape(4, 2).astype(np.float32)
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).reshape(-1)
    tl = pts[np.argmin(s)]
    br = pts[np.argmax(s)]
    tr = pts[np.argmin(d)]
    bl = pts[np.argmax(d)]
    return np.array([tl, tr, br, bl], dtype=np.float32)


def _largest_quad(binary: np.ndarray, min_area_ratio: float) -> tuple[np.ndarray | None, float]:
    """Find the largest quadrilateral in a binary mask.

    Returns (approximated_quad, absolute_area) in the mask's coordinate frame, or
    (None, area_of_biggest_contour) if the biggest blob is smaller than the threshold or
    can't be reduced to a plausible 4-sided approximation.
    """
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None, 0.0
    c = max(contours, key=cv2.contourArea)
    area = cv2.contourArea(c)
    img_area = binary.shape[0] * binary.shape[1]
    if area < min_area_ratio * img_area:
        return None, area
    peri = cv2.arcLength(c, True)
    for eps in (0.02, 0.03, 0.04, 0.05):
        approx = cv2.approxPolyDP(c, eps * peri, True)
        if len(approx) == 4 and cv2.isContourConvex(approx):
            return approx.astype(np.float32), area
    return None, area


def _paper_mask(small_bgr: np.ndarray) -> np.ndarray:
    """A binary mask where the paper is white and the background is black.

    Uses HSV to separate low-saturation, high-value paper from more-saturated background
    (wood, cloth, etc). Morphological close-then-open bridges small ink gaps and drops
    isolated noise. Thresholds are fixed rather than adaptive: a synthetic scan has
    near-zero saturation everywhere, so any percentile-based tightening would erase
    the whole page and force the fallback path.
    """
    hsv = cv2.cvtColor(small_bgr, cv2.COLOR_BGR2HSV)
    _, s, v = cv2.split(hsv)
    mask = ((s < 90) & (v > 90)).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8), iterations=2)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8), iterations=1)
    return mask


def _canny_edges(small_bgr: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(small_bgr, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 50, 150)
    return cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)


def detect_document(img: np.ndarray) -> tuple[np.ndarray | None, str, float]:
    """Return (quad_in_source_pixels, detector_name, boundary_area_ratio).

    quad is None when nothing plausible is found.
    """
    h, w = img.shape[:2]
    scale = 800.0 / max(h, w)
    small = cv2.resize(img, (int(w * scale), int(h * scale)))
    img_area = small.shape[0] * small.shape[1]

    quad, area = _largest_quad(_paper_mask(small), min_area_ratio=0.15)
    detector = "hsv-mask"
    if quad is None:
        quad, area = _largest_quad(_canny_edges(small), min_area_ratio=0.15)
        detector = "canny-fallback"
    if quad is None:
        return None, "none", area / img_area if img_area else 0.0
    return (_order_quad(quad) / scale).astype(np.float32), detector, area / img_area


def warp_to_rect(img: np.ndarray, quad: np.ndarray, target_w: int = 2000) -> np.ndarray:
    """Perspective-warp the source image so the quad becomes an axis-aligned rectangle."""
    tl, tr, br, bl = quad
    src_w = max(np.linalg.norm(tr - tl), np.linalg.norm(br - bl))
    src_h = max(np.linalg.norm(bl - tl), np.linalg.norm(br - tr))
    w = target_w
    h = int(round(w * src_h / src_w))
    dst = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], dtype=np.float32)
    m = cv2.getPerspectiveTransform(quad, dst)
    return cv2.warpPerspective(img, m, (w, h), flags=cv2.INTER_CUBIC,
                               borderMode=cv2.BORDER_REPLICATE)


def _content_crop(warped: np.ndarray) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    """After warping, trim edges that carry no structured content.

    A phone photo that captured some desk beside the paper leaves a strip of wood
    grain at the top or side of the warped image. Wood grain has strong edges but
    they aren't horizontal or vertical lines, whereas the document body carries a
    tall column of rules and dense long horizontal ink runs. Cropping to the region
    where **horizontal or vertical line strokes** actually exist reliably strips the
    residual desk, without touching a clean scan (whose content already fills the
    warp).
    """
    gray = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY) if warped.ndim == 3 else warped
    edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 60, 180)
    h, w = edges.shape
    hk = cv2.getStructuringElement(cv2.MORPH_RECT, (max(30, w // 30), 1))
    vk = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(30, h // 30)))
    long_lines = cv2.bitwise_or(cv2.morphologyEx(edges, cv2.MORPH_OPEN, hk),
                                 cv2.morphologyEx(edges, cv2.MORPH_OPEN, vk))
    if long_lines.max() == 0:
        return warped, (0, 0, w, h)
    ys = np.where(long_lines.sum(axis=1) > long_lines.sum(axis=1).max() * 0.05)[0]
    xs = np.where(long_lines.sum(axis=0) > long_lines.sum(axis=0).max() * 0.05)[0]
    if len(ys) < 20 or len(xs) < 20:
        return warped, (0, 0, w, h)
    y0, y1 = int(ys[0]), int(ys[-1]) + 1
    x0, x1 = int(xs[0]), int(xs[-1]) + 1
    # Only accept the crop when it meaningfully shrinks the frame; otherwise leave
    # the warp alone. This keeps clean scans untouched.
    if (y1 - y0) > h * 0.94 and (x1 - x0) > w * 0.94:
        return warped, (0, 0, w, h)
    pad = 10
    y0 = max(0, y0 - pad); y1 = min(h, y1 + pad)
    x0 = max(0, x0 - pad); x1 = min(w, x1 + pad)
    return warped[y0:y1, x0:x1].copy(), (x0, y0, x1 - x0, y1 - y0)


def rectify(img: np.ndarray) -> tuple[np.ndarray, WarpInfo]:
    """If a document boundary is detectable and the paper doesn't already fill the frame,
    warp the image to a straight rectangle and return the warp. Otherwise pass through.

    Runs the detector twice: after the first warp, a second pass often finds a tighter
    quad within the rectified image because background bleed (a tan desk under the
    paper) that fooled the first mask has been reduced by the coarse crop. Only takes
    the refined warp when it meaningfully shrinks the working region — otherwise the
    first rectified image is kept unchanged.

    The 92% ceiling on boundary_area_ratio is deliberately conservative: on a genuine
    scan the paper covers essentially the whole frame, so any mask thresholded above 92%
    is treated as "already orthogonal — do not warp" to avoid spurious cropping.
    """
    quad, detector, ratio = detect_document(img)
    if quad is None:
        return img, WarpInfo(warped=False, detector=detector, boundary_area_ratio=ratio,
                             quad=None,
                             reason="Document boundary not detected. If this is a phone "
                                    "photo, please retake with the paper filling the "
                                    "frame against a plain, contrasting background.")
    if ratio >= 0.92:
        return img, WarpInfo(warped=False, detector="identity", boundary_area_ratio=ratio,
                             quad=quad.astype(int).tolist(),
                             reason="Paper already fills the frame — no perspective "
                                    "correction needed.")
    warped = warp_to_rect(img, quad, target_w=2000)
    final_ratio = ratio
    passes = 1
    # Second pass — a coarsely detected paper region often still carries a strip of
    # desk at one or two edges. Re-detecting inside the warp trims that residue.
    # It only fires when the first detection was itself loose (ratio < 0.80): a first
    # pass at 0.85+ almost certainly already had the whole paper, and re-warping a
    # near-orthogonal image can drop the edges of a genuine document.
    if ratio < 0.80:
        inner, inner_det, inner_ratio = detect_document(warped)
        if inner is not None and inner_ratio < 0.80:
            warped = warp_to_rect(warped, inner, target_w=2000)
            detector = f"{detector}+{inner_det}"
            final_ratio = ratio * inner_ratio
            passes = 2
    cropped, crop_box = _content_crop(warped)
    cropped_frac = (cropped.shape[0] * cropped.shape[1]) / (warped.shape[0] * warped.shape[1])
    if cropped_frac < 1.0:
        reason = (f"Detected paper occupying {ratio * 100:.0f}% of the photo, warped "
                  f"it to a straight rectangle ({passes} pass{'es' if passes > 1 else ''}), "
                  f"then trimmed edges without structured content to isolate the form "
                  f"({cropped_frac * 100:.0f}% of the warp kept).")
        return cropped, WarpInfo(warped=True, detector=f"{detector}+content-crop",
                                 boundary_area_ratio=final_ratio,
                                 quad=quad.astype(int).tolist(), reason=reason)
    return warped, WarpInfo(warped=True, detector=detector, boundary_area_ratio=final_ratio,
                            quad=quad.astype(int).tolist(),
                            reason=(f"Detected paper occupying {ratio * 100:.0f}% of the "
                                    f"photo and warped it to a straight rectangle "
                                    f"({passes} pass{'es' if passes > 1 else ''})."))
