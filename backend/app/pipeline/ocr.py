"""OCR engine abstraction: Tesseract (default, offline) or EasyOCR (better handwriting/Indic).

Returns word-level tokens with confidence and bounding boxes, plus full text.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from app.core.config import settings  # noqa: F401  (import configures Tesseract paths)

# Map ISO-ish language codes to Tesseract packs (install via tessdata / apt: tesseract-ocr-<lang>)
TESS_LANG_MAP = {
    "en": "eng", "hi": "hin", "mr": "mar", "gu": "guj", "bn": "ben", "ta": "tam", "te": "tel",
    "kn": "kan", "ml": "mal", "pa": "pan", "or": "ori", "ur": "urd", "as": "asm",
}
EASYOCR_LANG_MAP = {"eng": "en", "hin": "hi", "mar": "mr", "guj": None, "bn": "bn", "ta": "ta",
                    "te": "te", "kn": "kn", "ur": "ur", "ben": "bn", "tam": "ta", "tel": "te", "kan": "kn"}


@dataclass
class Token:
    text: str
    conf: float          # 0-100
    x: int
    y: int
    w: int
    h: int
    line: int = 0


@dataclass
class OCRResult:
    text: str
    tokens: list[Token] = field(default_factory=list)
    mean_conf: float = 0.0
    engine: str = "tesseract"
    languages: str = ""


def _available_tess_langs() -> set[str]:
    try:
        import pytesseract
        return set(pytesseract.get_languages(config=""))
    except Exception:
        return {"eng"}


def missing_languages(requested: str | None) -> list[str]:
    """Requested Tesseract packs that are not installed.

    resolve_languages() silently falls back to English so a job never dies, but that
    hides the most common deployment fault by far: Tesseract present, the Indic pack
    absent. Callers record this so the failure is visible instead of looking like a
    model that cannot read the page.
    """
    req = (requested or settings.ocr_languages).replace(",", "+").split("+")
    avail = _available_tess_langs()
    return [l for l in (TESS_LANG_MAP.get(x, x) for x in req) if l and l not in avail]


def resolve_languages(requested: str | None) -> str:
    """Keep only installed tesseract packs; always fall back to eng."""
    req = (requested or settings.ocr_languages).replace(",", "+").split("+")
    avail = _available_tess_langs()
    langs = [TESS_LANG_MAP.get(l, l) for l in req]
    langs = [l for l in langs if l in avail]
    if "eng" not in langs:
        langs.append("eng")
    return "+".join(dict.fromkeys(langs))


def ocr_tesseract(binary: np.ndarray, languages: str) -> OCRResult:
    import pytesseract
    from pytesseract import Output

    langs = resolve_languages(languages)
    config = "--oem 1 --psm 6"  # LSTM engine, assume a uniform block of text
    data = pytesseract.image_to_data(binary, lang=langs, config=config, output_type=Output.DICT,
                                     timeout=settings.ocr_timeout_s)
    tokens: list[Token] = []
    lines: dict[tuple, list[Token]] = {}
    for i, txt in enumerate(data["text"]):
        txt = (txt or "").strip()
        conf = float(data["conf"][i])
        if not txt or conf < 0:
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        t = Token(txt, conf, data["left"][i], data["top"][i], data["width"][i], data["height"][i])
        lines.setdefault(key, []).append(t)
        tokens.append(t)
    # assemble line-preserving text (important for key: value parsing)
    text_lines = []
    for idx, key in enumerate(sorted(lines)):
        for t in lines[key]:
            t.line = idx
        text_lines.append(" ".join(t.text for t in lines[key]))
    text = "\n".join(text_lines)
    mean = float(np.mean([t.conf for t in tokens])) if tokens else 0.0
    return OCRResult(text=text, tokens=tokens, mean_conf=round(mean, 2), engine="tesseract", languages=langs)


def ocr_easyocr(gray: np.ndarray, languages: str) -> OCRResult:
    """Optional: pip install easyocr (downloads models on first use)."""
    import easyocr  # noqa: F401  (imported lazily; heavy dependency)

    langs = [EASYOCR_LANG_MAP.get(l, l) for l in languages.split("+")]
    langs = [l for l in langs if l]
    reader = easyocr.Reader(langs or ["en"], gpu=False)
    res = reader.readtext(gray, detail=1, paragraph=False)
    tokens = []
    for i, (box, txt, conf) in enumerate(res):
        xs = [p[0] for p in box]; ys = [p[1] for p in box]
        tokens.append(Token(txt, conf * 100, int(min(xs)), int(min(ys)),
                            int(max(xs) - min(xs)), int(max(ys) - min(ys)), i))
    tokens.sort(key=lambda t: (t.y // 20, t.x))
    text = "\n".join(t.text for t in tokens)
    mean = float(np.mean([t.conf for t in tokens])) if tokens else 0.0
    return OCRResult(text=text, tokens=tokens, mean_conf=round(mean, 2), engine="easyocr", languages="+".join(langs))


def run_ocr(pre: dict, languages: str | None = None) -> OCRResult:
    """Multi-pass OCR: for multilingual documents Tesseract's result depends on which
    language pack is primary, so we run each ordering and keep the most confident pass."""
    languages = languages or settings.ocr_languages
    if settings.ocr_engine == "easyocr":
        try:
            return ocr_easyocr(pre["gray"], languages)
        except Exception:  # fall back gracefully
            pass
    langs = resolve_languages(languages).split("+")
    orderings = [langs] if len(langs) == 1 else [langs, list(reversed(langs))]
    best: OCRResult | None = None
    for order in orderings:
        res = ocr_tesseract(pre["binary"], "+".join(order))
        if best is None or res.mean_conf > best.mean_conf:
            best = res
    return best
