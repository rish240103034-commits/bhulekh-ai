"""Region-aware OCR for the labelled areas of a form (above and below the table).

A single full-page OCR pass has to pick one segmentation for the whole sheet, which
reads a dense ruled table reasonably but makes a poor job of the sparse, widely
spaced label/value lines in a form's header and footer. This module re-reads just
those bands:

  * the page is split at the table's vertical extent into header / footer strips,
  * each strip is split again at its blank gutters, because these forms are laid out
    in columns (district/tehsil on the left, year/village/khata on the right) and
    reading across the columns interleaves unrelated labels onto one line,
  * each block is upscaled before OCR, which is what makes faint handwriting legible.

Tokens are returned in page coordinates so the verification UI can still highlight
where a field came from.
"""
from __future__ import annotations

import cv2
import numpy as np

from app.core.config import settings
from app.pipeline.ocr import OCRResult, Token, resolve_languages

REGION_SCALE = 2.0            # upscale factor for header/footer strips
MIN_GUTTER_FRAC = 0.035       # blank run (of block width) that counts as a column gutter
MIN_BLOCK_FRAC = 0.06         # ignore slivers narrower than this fraction of the strip


def _gutter_blocks(binary_strip: np.ndarray) -> list[tuple[int, int]]:
    """Split a strip into column blocks at its blank vertical gutters."""
    if binary_strip.size == 0 or binary_strip.shape[1] < 40:
        return []
    ink = cv2.bitwise_not(binary_strip)
    profile = ink.sum(axis=0) / 255.0
    if profile.max() <= 0:
        return []
    blank = profile <= max(1.0, profile.max() * 0.012)
    width = len(profile)
    min_gutter = max(8, int(width * MIN_GUTTER_FRAC))

    runs, start = [], None
    for i, v in enumerate(blank):
        if v and start is None:
            start = i
        elif not v and start is not None:
            if i - start >= min_gutter:
                runs.append((start, i))
            start = None
    if start is not None and width - start >= min_gutter:
        runs.append((start, width))

    cuts = [0] + [(a + b) // 2 for a, b in runs] + [width]
    blocks = [(a, b) for a, b in zip(cuts, cuts[1:]) if b - a > width * MIN_BLOCK_FRAC]
    return blocks or [(0, width)]


def _ocr_block(gray_block: np.ndarray, langs: str) -> tuple[list[Token], float]:
    """OCR one upscaled block; returns tokens in BLOCK coordinates (pre-scaling undone)."""
    import pytesseract
    from pytesseract import Output

    if gray_block.size == 0 or min(gray_block.shape) < 10:
        return [], 0.0
    up = cv2.resize(gray_block, None, fx=REGION_SCALE, fy=REGION_SCALE, interpolation=cv2.INTER_CUBIC)
    binar = cv2.threshold(cv2.GaussianBlur(up, (3, 3), 0), 0, 255,
                          cv2.THRESH_BINARY | cv2.THRESH_OTSU)[1]
    pad = 20
    padded = cv2.copyMakeBorder(binar, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=255)
    try:
        data = pytesseract.image_to_data(padded, lang=langs, config="--oem 1 --psm 6",
                                         output_type=Output.DICT, timeout=settings.ocr_timeout_s)
    except Exception:
        return [], 0.0

    tokens, confs, line_keys = [], [], {}
    for i, txt in enumerate(data["text"]):
        txt = (txt or "").strip()
        conf = float(data["conf"][i])
        if not txt or conf < 0:
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        line_keys.setdefault(key, len(line_keys))
        x = int((data["left"][i] - pad) / REGION_SCALE)
        y = int((data["top"][i] - pad) / REGION_SCALE)
        w = int(data["width"][i] / REGION_SCALE)
        h = int(data["height"][i] / REGION_SCALE)
        tokens.append(Token(txt, conf, x, y, w, h, line_keys[key]))
        confs.append(conf)
    return tokens, (sum(confs) / len(confs) if confs else 0.0)


def read_regions(pre: dict, languages: str, exclude_band: tuple[int, int] | None = None) -> OCRResult:
    """Re-read the form areas outside the table and return them as a line-preserving OCRResult."""
    gray, binary = pre["gray"], pre["binary"]
    h, w = gray.shape
    langs = resolve_languages(languages)

    if exclude_band:
        top, bottom = exclude_band
        strips = [(0, max(0, top)), (min(h, bottom), h)]
    else:
        strips = [(0, h)]

    all_tokens: list[Token] = []
    text_lines: list[str] = []
    confs: list[float] = []
    line_offset = 0

    for y0, y1 in strips:
        if y1 - y0 < 25:
            continue
        for x0, x1 in _gutter_blocks(binary[y0:y1, :]):
            tokens, mean = _ocr_block(gray[y0:y1, x0:x1], langs)
            if not tokens:
                continue
            by_line: dict[int, list[Token]] = {}
            for t in tokens:
                t.x += x0
                t.y += y0
                by_line.setdefault(t.line, []).append(t)
            for local_line in sorted(by_line):
                line_no = line_offset + local_line
                row = sorted(by_line[local_line], key=lambda t: t.x)
                for t in row:
                    t.line = line_no
                    all_tokens.append(t)
                    confs.append(t.conf)
                text_lines.append(" ".join(t.text for t in row))
            line_offset += len(by_line)

    return OCRResult(text="\n".join(text_lines), tokens=all_tokens,
                     mean_conf=round(sum(confs) / len(confs), 2) if confs else 0.0,
                     engine="tesseract-region", languages=langs)


# Fields whose value is pure digits (optionally with separators). Re-reading these with
# a digit-only model removes the '1'->'[', '4'->'+' class of handwriting errors that a
# language model introduces.
DIGIT_ONLY_FIELDS = {"khata_number", "khasra_number", "survey_number", "record_year",
                     "record_date", "mutation_date", "registration_date",
                     "total_area", "irrigated_area", "unirrigated_area", "plot_area"}
_DIGIT_WHITELIST = "-c tessedit_char_whitelist=0123456789./-"


def _digit_reading(gray: np.ndarray, box: dict) -> tuple[str, float]:
    from app.pipeline.table import _ocr_cell

    pad = 6
    y0 = max(0, box["y"] - pad); y1 = min(gray.shape[0], box["y"] + box["h"] + pad)
    x0 = max(0, box["x"] - pad); x1 = min(gray.shape[1], box["x"] + box["w"] + pad)
    crop = gray[y0:y1, x0:x1]
    best: tuple[str, float] = ("", 0.0)
    for psm in ("7", "8"):
        text, conf = _ocr_cell(crop, "eng", psm=psm, extra_config=_DIGIT_WHITELIST)
        text = text.strip()
        if text and conf > best[1]:
            best = (text, conf)
    return best


def refine_numeric_fields(pre: dict, fields: list, min_gain: float = 5.0) -> None:
    """Re-read digit-only fields from their own pixels, in place.

    Only replaces a reading when the digit-constrained pass is both more confident and
    still produces digits, so a failed re-read can never make a field worse.
    """
    gray = pre["gray"]
    for f in fields:
        if f.field_name not in DIGIT_ONLY_FIELDS or not f.value_bbox:
            continue
        if f.value_bbox["w"] < 8 or f.value_bbox["h"] < 8:
            continue
        text, conf = _digit_reading(gray, f.value_bbox)
        if not text or not any(ch.isdigit() for ch in text):
            continue
        from app.pipeline.extract import VALIDATORS, _trim_value

        refined = _trim_value(text, f.field_name)
        if not refined or not any(ch.isdigit() for ch in refined):
            continue
        validator = VALIDATORS.get(f.field_name)
        was_valid = bool(validator.match(f.value)) if validator else True
        now_valid = bool(validator.match(refined)) if validator else True
        # Take the digit reading when it is clearly more confident, or when it rescues a
        # value that does not even have the right shape for this field.
        if conf >= f.confidence + min_gain or (now_valid and not was_valid):
            f.value = refined
            f.confidence = round(min(99.0, max(conf, f.confidence if now_valid else conf)), 1)
            f.source = "rule+digits"
