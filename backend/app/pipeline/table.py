"""Tabular record extraction for ruled land-record forms (khasra / khatauni / 7-12).

Real khasra and khatauni sheets hold MANY parcels in a ruled grid — one row per
khasra/survey number with its own area, land type, crop and owner. A line-based
"label : value" parser cannot read those, so this module:

  1. finds the grid lines with morphological filtering (robust to handwriting,
     stains and skew already corrected upstream),
  2. reconstructs the cell matrix from the line coordinates,
  3. OCRs the header row and fuzzy-maps each column to a canonical field,
  4. OCRs only the cells of mapped columns and emits one ParcelRow per data row.

Hand-drawn map / diagram columns are detected and skipped, which keeps the
number of OCR calls (and therefore latency) proportional to useful data.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import cv2
import numpy as np
from rapidfuzz import fuzz, process

from app.core.config import settings

# Canonical table columns -> accepted header spellings (Devanagari + transliteration + English)
COLUMN_LEXICON: dict[str, list[str]] = {
    "parcel_number": ["खसरा संख्या", "खसरा सं", "खसरा नं", "खसरा क्रमांक", "khasra sankhya", "khasra no",
                      "सर्वे नंबर", "सर्वे क्रमांक", "गट नंबर", "survey no", "survey number", "गट क्र",
                      "भूखंड संख्या", "plot no", "खसरा"],
    "area": ["रकबा", "क्षेत्रफल", "क्षेत्र", "rakba", "area", "kshetrafal", "हे आ बीघा बिस्वा",
             "एकूण क्षेत्र", "क्षेत्रफळ"],
    "land_classification": ["भूमि का प्रकार", "भूमि प्रकार", "किस्म", "किस्म जमीन", "land type", "प्रकार",
                            "कृषि बंजर आदि", "जमिनीचा प्रकार", "land class", "वर्गीकरण"],
    "crop": ["फसल", "फ़सल", "पीक", "crop", "फसल वर्तमान", "वर्तमान फसल", "पैदावार"],
    "owner_name": ["मालिक का नाम", "मालिक", "खातेदार का नाम", "खातेदार", "भूमिधर", "स्वामी", "owner",
                   "owner name", "भूस्वामी", "मालकाचे नाव", "नाम खातेदार"],
    "possessor_name": ["कब्जेदार का नाम", "कब्जेदार", "काश्तकार", "occupant", "possessor", "कब्ज़ेदार",
                       "कब्जाधारक", "वहिवाटदार"],
    "father_name": ["पिता का नाम", "पिता", "वलद", "father name", "पति का नाम"],
    "share": ["हिस्सा", "अंश", "share", "हिस्सेदारी"],
    "remarks": ["टिप्पणी", "रिमार्क", "remarks", "note", "शेरा", "अभ्युक्ति"],
    "map": ["खसरा मानचित्र", "मानचित्र", "नक्शा", "नक़्शा", "map", "sketch", "नकाशा", "आकृति"],
}
# Columns we never OCR (hand-drawn content) and columns that carry no parcel data
SKIP_COLUMNS = {"map"}
_COL_PAIRS = [(k, col) for col, keys in COLUMN_LEXICON.items() for k in keys]
_COL_KEYS = [k for k, _ in _COL_PAIRS]

DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
_NUMERIC_RE = re.compile(r"[0-9०-९]")
# Cell borders bleed into crops as single bars/dots; drop those tokens outright.
_BORDER_NOISE_RE = re.compile(r"[|!\[\]{}()_~`'\"^*<>.,;:\\/-]+")
CELL_TARGET_HEIGHT = 120          # px; upscale target for a single cell line
HEADER_TARGET_HEIGHT = 260        # px; header cells hold two small wrapped lines
# Fraction of dark pixels below which a cell is treated as blank. Measured content sits
# around 5-15%; an empty cell is ~0%. Without this, Tesseract invents words from paper
# grain in the empty Remarks column of a khasra sheet.
BLANK_CELL_INK_RATIO = 0.015


@dataclass
class ParcelRow:
    values: dict[str, str] = field(default_factory=dict)     # canonical column -> text
    confidences: dict[str, float] = field(default_factory=dict)
    row_index: int = 0
    bbox: dict | None = None


@dataclass
class TableResult:
    columns: dict[int, str] = field(default_factory=dict)    # grid column index -> canonical name
    rows: list[ParcelRow] = field(default_factory=list)
    bbox: dict | None = None
    reason: str | None = None          # why no rows were produced, for diagnostics
    header_texts: list[str] = field(default_factory=list)
    area_unit_hint: str | None = None      # unit named in the area column header
    n_grid_rows: int = 0
    n_grid_cols: int = 0


def _line_positions(mask: np.ndarray, axis: int, min_gap: int, rel: float = 0.35,
                    absolute: float | None = None) -> list[int]:
    """Collapse a line mask into sorted centre positions along `axis`.

    `absolute` sets a minimum run length in pixels, used for horizontal rules: judging
    them relative to the strongest line lets a word's Devanagari headline stroke pass
    as a table rule, which invents rows that do not exist.
    """
    profile = mask.sum(axis=axis)
    if profile.max() == 0:
        return []
    threshold = absolute * 255 if absolute is not None else profile.max() * rel
    hits = np.where(profile > threshold)[0]
    if len(hits) == 0:
        return []
    groups, start, prev = [], hits[0], hits[0]
    for p in hits[1:]:
        if p - prev > 3:                       # new line
            groups.append((start + prev) // 2)
            start = p
        prev = p
    groups.append((start + prev) // 2)
    out = [groups[0]]
    for g in groups[1:]:
        if g - out[-1] >= min_gap:
            out.append(g)
    return [int(v) for v in out]


def _text_bands(binary: np.ndarray, y0: int, y1: int, x0: int, x1: int) -> list[tuple[int, int]]:
    """Rows of a faded/handwritten table are found from horizontal ink bands.

    Ruled separators on old forms are often lighter than the handwriting itself, so
    relying on them alone loses rows. We strip the vertical rules, project the
    remaining ink and treat each contiguous band as one table row.
    """
    if y1 - y0 < 10 or x1 - x0 < 10:
        return []
    body = binary[y0:y1, x0:x1]
    inv = cv2.bitwise_not(body)
    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(15, (y1 - y0) // 12)))
    inv = cv2.subtract(inv, cv2.morphologyEx(inv, cv2.MORPH_OPEN, v_kernel))
    profile = inv.sum(axis=1) / 255.0
    if profile.max() <= 0:
        return []
    on = profile > max(3.0, profile.max() * 0.06)

    bands, start = [], None
    for i, v in enumerate(on):
        if v and start is None:
            start = i
        elif not v and start is not None:
            if i - start >= 8:                 # ignore specks and residual rule lines
                bands.append((start, i))
            start = None
    if start is not None and len(on) - start >= 8:
        bands.append((start, len(on)))
    return [(y0 + a, y0 + b) for a, b in bands]


def _rule_rows(horiz_mask: np.ndarray, x0: int, x1: int, min_gap: int) -> list[int]:
    """Y positions of horizontal rules that actually cross the grid.

    Measured as connected components rather than a row-wise projection: a rule on a
    slightly skewed scan spreads over several rows, so no single row contains the whole
    run, while requiring a long component keeps a word's Devanagari headline stroke out.
    """
    strip = horiz_mask[:, x0:x1]
    width = x1 - x0
    count, _, stats, centroids = cv2.connectedComponentsWithStats((strip > 0).astype(np.uint8), 8)
    ys = []
    for i in range(1, count):
        _, _, w, h, _ = stats[i]
        # Height is bounded only enough to reject a component that has swallowed two
        # adjacent rules; thresholding a printed rule often leaves a band, not a hairline.
        if w >= width * 0.55 and h <= max(30, min_gap * 2):
            ys.append(int(round(centroids[i][1])))
    return _dedupe(sorted(ys), min_gap)


def _dedupe(values: list[int], min_gap: int) -> list[int]:
    """Collapse boundaries that sit on top of each other (a detected rule and the
    table edge derived from the column rules are usually the same line)."""
    out: list[int] = []
    for v in values:
        if not out or v - out[-1] >= min_gap:
            out.append(v)
        else:
            out[-1] = (out[-1] + v) // 2
    return out


def _bands_to_bounds(bands: list[tuple[int, int]], limit_lo: int, limit_hi: int) -> list[int]:
    """Convert ink bands into cell boundaries midway through the gaps between them."""
    if not bands:
        return []
    bounds = [max(limit_lo, bands[0][0] - 6)]
    for (_, end), (nxt_start, _) in zip(bands, bands[1:]):
        bounds.append((end + nxt_start) // 2)
    bounds.append(min(limit_hi, bands[-1][1] + 6))
    return bounds


def _vertical_extent(vert_mask: np.ndarray, cols: list[int]) -> tuple[int, int] | None:
    """Vertical extent of the table, taken from the column rules themselves.

    Deriving it from horizontal lines instead is unreliable: forms carry underlines,
    box borders and signature rules outside the grid, and the outermost of those is
    often not the table at all.

    Counted across the full width rather than in a narrow window per column, because
    any residual skew makes a "vertical" rule drift sideways over the table's height.
    """
    per_row = (vert_mask > 0).sum(axis=1).astype(float)
    if per_row.max() <= 0:
        return None
    inside = np.where(per_row >= per_row.max() * 0.4)[0]
    if len(inside) < 10:
        return None
    return int(inside[0]), int(inside[-1])


def detect_grid(binary: np.ndarray) -> tuple[list[int], list[int]]:
    """Return (row_y_boundaries, col_x_boundaries) of the dominant ruled table.

    Column separators run the full height of the grid and are reliably detected, so
    they define both the columns and the table's vertical extent. Row separators are
    often too faint on old forms, so they are recovered from ink bands whenever the
    ruled lines alone do not explain the table body.
    """
    h, w = binary.shape
    inv = cv2.bitwise_not(binary)              # lines become white

    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(20, w // 25), 1))
    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(20, h // 25)))
    horiz = cv2.morphologyEx(inv, cv2.MORPH_OPEN, h_kernel, iterations=1)
    vert = cv2.morphologyEx(inv, cv2.MORPH_OPEN, v_kernel, iterations=1)
    horiz = cv2.dilate(horiz, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 1)))
    vert = cv2.dilate(vert, cv2.getStructuringElement(cv2.MORPH_RECT, (1, 3)))

    cols = _line_positions(vert, axis=0, min_gap=max(18, w // 60))
    if len(cols) < 3:
        return [], cols

    extent = _vertical_extent(vert, cols)
    if extent is None:
        return [], cols
    top, bottom = extent
    if bottom - top < h * 0.08:
        return [], cols

    tol = max(6, (bottom - top) // 50)
    # A row rule has to run across the grid, not just under a word.
    row_gap = max(12, h // 60)
    h_lines = [y for y in _rule_rows(horiz, cols[0], cols[-1], row_gap)
               if top - tol <= y <= bottom + tol]
    h_lines = _dedupe(sorted(set([top] + h_lines + [bottom])), tol)
    inner = [y for y in h_lines if top + tol < y < bottom - tol]
    header_bottom = inner[0] if inner else None

    # Enough ruled separators inside the body to describe the rows on their own?
    if header_bottom is not None and len([y for y in inner if y >= header_bottom]) >= 4:
        return h_lines, cols            # the grid is fully ruled; use the rules as-is

    if header_bottom is None:
        return h_lines, cols

    bands = _text_bands(binary, header_bottom, bottom, cols[0], cols[-1])
    if len(bands) < 2:
        return h_lines, cols
    rows = [top, header_bottom] + _bands_to_bounds(bands, header_bottom, bottom)[1:]
    return _dedupe(sorted(set(int(r) for r in rows)), tol), cols


def _ocr_cell(gray_cell: np.ndarray, langs: str, psm: str = "7",
              extra_config: str = "", target_height: int | None = None) -> tuple[str, float]:
    """OCR one table cell.

    Cells cut out of a scan are small and low-contrast, which Tesseract reads poorly.
    Upscaling to a comfortable glyph height, lightly blurring away paper grain, then
    thresholding with Otsu and adding a wide white margin makes handwritten Devanagari
    cells legible where a full-page pass returns nothing.
    """
    import pytesseract
    from pytesseract import Output

    if gray_cell.size == 0 or min(gray_cell.shape) < 8:
        return "", 0.0
    scale = max(1.0, (target_height or CELL_TARGET_HEIGHT) / max(gray_cell.shape[0], 1))
    if scale > 1.0:
        gray_cell = cv2.resize(gray_cell, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    blurred = cv2.GaussianBlur(gray_cell, (3, 3), 0)
    binar = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)[1]
    padded = cv2.copyMakeBorder(binar, 25, 25, 25, 25, cv2.BORDER_CONSTANT, value=255)
    try:
        config = f"--oem 1 --psm {psm} {extra_config}".strip()
        data = pytesseract.image_to_data(padded, lang=langs, config=config,
                                         output_type=Output.DICT, timeout=settings.ocr_timeout_s)
    except Exception:
        return "", 0.0
    words, confs = [], []
    for i, t in enumerate(data["text"]):
        t = (t or "").strip()
        c = float(data["conf"][i])
        if t and c >= 0 and not _BORDER_NOISE_RE.fullmatch(t):
            words.append(t)
            confs.append(c)
    return " ".join(words), (sum(confs) / len(confs) if confs else 0.0)


NUMERIC_COLUMNS = {"parcel_number", "area", "share"}

# Closed vocabularies. Land type and crop can only take a handful of values, so a
# noisy reading is snapped to the nearest legal term instead of being left as garbage.
VOCABULARIES: dict[str, list[str]] = {
    "land_classification": [
        "कृषि", "बंजर", "आबादी", "गैर कृषि", "सिंचित", "असिंचित", "जिरायत", "बागायत",
        "चरागाह", "वन", "परती", "नवीन परती", "ऊसर", "पड़त", "तालाब", "रास्ता", "नदी",
        "आवासीय", "व्यावसायिक", "औद्योगिक", "बाग",
        "agricultural", "barren", "irrigated", "unirrigated", "residential", "commercial",
        "forest", "pasture", "fallow", "orchard", "waste",
    ],
    "crop": [
        "गेहूँ", "गेहूं", "धान", "अरहर", "चना", "मक्का", "बाजरा", "ज्वार", "सरसों", "गन्ना",
        "आलू", "मूंग", "उड़द", "तिल", "जौ", "मटर", "सोयाबीन", "कपास", "मूंगफली", "बरसीम",
        "प्याज", "लहसुन", "सब्जी", "दलहन", "तिलहन",
        "wheat", "paddy", "rice", "maize", "sugarcane", "mustard", "gram", "cotton",
    ],
}


def _script_lang(langs: str) -> str:
    """Prefer the Indic pack alone for vocabulary cells.

    With English in the mix Tesseract happily returns Latin nonsense ("pry", "prey")
    for short Devanagari words; restricting the model to the document's own script
    keeps the reading inside the right alphabet.
    """
    parts = [p for p in langs.split("+") if p and p != "eng"]
    return parts[0] if parts else langs


def snap_to_vocabulary(column: str, text: str) -> tuple[str | None, float]:
    """Snap a noisy reading to the nearest legal term for a closed-vocabulary column."""
    vocab = VOCABULARIES.get(column)
    if not vocab or not text:
        return None, 0.0
    cleaned = re.sub(r"[^\w\u0900-\u097F ]+", " ", text).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if len(cleaned) < 2:
        return None, 0.0
    hit = process.extractOne(cleaned, vocab, scorer=fuzz.ratio, score_cutoff=68)
    if not hit:
        return None, 0.0
    return hit[0], float(hit[1])


def _ocr_cell_vocab(gray_cell: np.ndarray, langs: str, column: str) -> tuple[str, float]:
    text, conf = _ocr_cell(gray_cell, _script_lang(langs), psm="7")
    if not text.strip():
        text, conf = _ocr_cell(gray_cell, langs, psm="7")
    snapped, score = snap_to_vocabulary(column, text)
    if snapped:
        # Agreement between the reading and a legal term is itself evidence.
        return snapped, min(99.0, max(conf, score))
    return text.strip(" .|_-\u2014\u2013"), conf

_DIGIT_CONFIG = "-c tessedit_char_whitelist=0123456789./"
_VALID_PARCEL = re.compile(r"^\d{1,5}(/\d{1,4})*$")
_VALID_AREA = re.compile(r"^\d+\.\d+$")


def _numeric_score(column: str, cleaned: str | None) -> float:
    """How well a candidate reading fits the shape this column must have."""
    if not cleaned:
        return 0.0
    if column == "parcel_number":
        return 100.0 if _VALID_PARCEL.match(cleaned) else 20.0
    if column == "area":
        if _VALID_AREA.match(cleaned):
            return 100.0
        return 55.0 if cleaned.replace(".", "").isdigit() else 20.0
    return 60.0 if any(ch.isdigit() for ch in cleaned) else 10.0


def _ocr_cell_numeric(gray_cell: np.ndarray, column: str) -> tuple[str, float]:
    """Read a numeric cell with a digit whitelist.

    Handwritten '1' and '4' are routinely misread as '[', '(', '/' or '+' by a
    general language model. Constraining Tesseract to digits removes that entire
    class of error; two page-segmentation modes are tried because short numbers and
    decimal figures favour different ones, and the reading that best fits the
    column's expected shape wins.
    """
    best_text, best_conf, best_score = "", 0.0, -1.0
    for psm in ("7", "8"):
        raw, conf = _ocr_cell(gray_cell, "eng", psm=psm, extra_config=_DIGIT_CONFIG)
        if not raw:
            continue
        cleaned = clean_parcel_number(raw) if column == "parcel_number" else (
            clean_area(raw) if column == "area" else raw.strip())
        score = _numeric_score(column, cleaned)
        if score > best_score or (score == best_score and conf > best_conf):
            best_text, best_conf, best_score = (cleaned or ""), conf, score
    return best_text, best_conf


def match_column(text: str) -> tuple[str | None, float]:
    """Fuzzy-map a header cell's text to a canonical column name."""
    t = re.sub(r"[()\[\]/|.,:;]+", " ", text).strip().lower()
    t = re.sub(r"\s+", " ", t)
    if len(t) < 2:
        return None, 0.0
    best = process.extractOne(t, _COL_KEYS, scorer=fuzz.token_set_ratio, score_cutoff=72)
    if not best:
        # header cells often wrap ("खसरा" / "संख्या") — try partial containment
        best = process.extractOne(t, _COL_KEYS, scorer=fuzz.partial_ratio, score_cutoff=85)
        if not best:
            return None, 0.0
    _, score, idx = best
    return _COL_PAIRS[idx][1], float(score)


AREA_HEADER_UNITS = [
    ("हेक्टेयर", "ha"), ("हे", "ha"), ("hectare", "ha"), ("ha", "ha"),
    ("एकड़", "acre"), ("acre", "acre"),
    ("बीघा", "bigha"), ("bigha", "bigha"),
    ("बिस्वा", "biswa"), ("biswa", "biswa"),
    ("गुंठा", "guntha"), ("guntha", "guntha"),
    ("वर्ग मीटर", "sqm"), ("sqm", "sqm"),
]


def _unit_from_header(header_text: str) -> str | None:
    """Khasra forms print the area unit in the column header ("रकबा (हे./बीघा/बिस्वा)").

    The cells themselves hold a bare figure, so without this the area is unitless and
    cannot be converted or totalled. The first unit named wins, which matches the
    convention of listing the primary unit first.
    """
    if not header_text:
        return None
    low = header_text.lower()
    best: tuple[int, str] | None = None
    for token, unit in AREA_HEADER_UNITS:
        idx = low.find(token.lower())
        if idx >= 0 and (best is None or idx < best[0]):
            best = (idx, unit)
    return best[1] if best else None


def extract_tables(pre: dict, langs: str) -> TableResult:
    """Detect the dominant ruled table on a preprocessed page and read its rows.

    Always returns a result; `rows` is empty when nothing could be read and `reason`
    says why, so a silent miss can be told apart from a page that simply has no table.
    """
    binary, gray = pre["binary"], pre["gray"]
    rows, cols = detect_grid(binary)
    if len(rows) < 3 or len(cols) < 3:
        return TableResult(reason=f"no ruled grid found ({max(0, len(rows) - 1)} rows x "
                                  f"{max(0, len(cols) - 1)} columns detected)")

    n_rows, n_cols = len(rows) - 1, len(cols) - 1
    if n_rows < 2 or n_cols < 3 or n_rows * n_cols > 400:
        return TableResult(reason=f"grid shape implausible for a parcel table ({n_rows}x{n_cols})")

    result = TableResult(n_grid_rows=n_rows, n_grid_cols=n_cols,
                         bbox={"x": cols[0], "y": rows[0], "w": cols[-1] - cols[0], "h": rows[-1] - rows[0]})

    def is_blank(r: int, c: int) -> bool:
        pad_y, pad_x = 7, 4
        y0, y1 = rows[r] + pad_y, rows[r + 1] - pad_y
        x0, x1 = cols[c] + pad_x, cols[c + 1] - pad_x
        if y1 <= y0 or x1 <= x0:
            return True
        cell = binary[y0:y1, x0:x1]
        return (cell < 128).sum() / cell.size < BLANK_CELL_INK_RATIO

    def crop(r: int, c: int) -> np.ndarray:
        """Inset the crop so the ruled borders do not leak in as phantom characters."""
        # Inset past the ruled border without clipping glyphs. The vertical inset is the
        # larger of the two because row rules are thicker than column rules once dilated.
        pad_y, pad_x = 7, 4
        y0, y1 = rows[r] + pad_y, rows[r + 1] - pad_y
        x0, x1 = cols[c] + pad_x, cols[c + 1] - pad_x
        if y1 <= y0 or x1 <= x0:
            return np.empty((0, 0), dtype=np.uint8)
        return gray[y0:y1, x0:x1]

    # ---- header row: map each column ----
    candidates: list[tuple[float, int, str]] = []
    for c in range(n_cols):
        # Header cells are tall (they hold two wrapped lines) but their glyphs are small,
        # so scale to the cell rather than trusting its height as a proxy for text size.
        text, _ = _ocr_cell(crop(0, c), langs, psm="6", target_height=HEADER_TARGET_HEIGHT)
        result.header_texts.append(text)
        name, score = match_column(text)
        if name:
            candidates.append((score, c, name))
    # Highest-scoring header wins its column name; each name is claimed once.
    for score, c, name in sorted(candidates, key=lambda t: -t[0]):
        if name not in result.columns.values() and c not in result.columns:
            result.columns[c] = name

    for c, name in result.columns.items():
        if name == "area":
            result.area_unit_hint = _unit_from_header(result.header_texts[c])

    data_cols = {c: n for c, n in result.columns.items() if n not in SKIP_COLUMNS}
    # A real parcel table must at least identify parcels or their owners.
    if not data_cols or not ({"parcel_number", "owner_name"} & set(data_cols.values())):
        result.reason = (f"grid found ({n_rows}x{n_cols}) but its column headers could not be "
                         f"matched to known fields — headers read as {result.header_texts}. "
                         f"This is what a missing Devanagari language pack looks like.")
        return result

    # ---- data rows ----
    for r in range(1, n_rows):
        row = ParcelRow(row_index=r,
                        bbox={"x": cols[0], "y": rows[r], "w": cols[-1] - cols[0], "h": rows[r + 1] - rows[r]})
        for c, name in data_cols.items():
            if is_blank(r, c):        # nothing written here; do not let OCR invent something
                continue
            if name in NUMERIC_COLUMNS:
                text, conf = _ocr_cell_numeric(crop(r, c), name)
            elif name in VOCABULARIES:
                text, conf = _ocr_cell_vocab(crop(r, c), langs, name)
            else:
                text, conf = _ocr_cell(crop(r, c), langs, psm="7")
                text = text.strip(" .|_-—–")
            if text:
                row.values[name] = text
                row.confidences[name] = conf
        if _row_has_content(row):
            result.rows.append(row)
    if not result.rows:
        result.reason = f"columns mapped {sorted(set(data_cols.values()))} but every row read blank"
    return result


def _row_has_content(row: ParcelRow) -> bool:
    """Ignore blank ruled rows at the foot of the table."""
    meaningful = [v for k, v in row.values.items() if len(v.strip(" .-—–_")) >= 1]
    if not meaningful:
        return False
    # a row of pure punctuation noise is not a parcel
    return any(len(re.sub(r"[^\wऀ-ॿ]", "", v)) >= 1 for v in meaningful)


def clean_parcel_number(s: str | None) -> str | None:
    """'/43' -> '143', 'I45' -> '145'; khasra numbers are numeric with optional /sub-division."""
    if not s:
        return None
    s = s.translate(DEVANAGARI_DIGITS)
    s = s.replace("l", "1").replace("I", "1").replace("|", "1").replace("O", "0").replace("o", "0")
    s = re.sub(r"[^0-9/]", "", s)
    s = re.sub(r"/{2,}", "/", s).strip("/")
    return s or None


def clean_area(s: str | None) -> str | None:
    """'0.I500' -> '0.1500', '0.4f00' -> '0.4100'; keeps a trailing unit word if present."""
    if not s:
        return None
    s = s.translate(DEVANAGARI_DIGITS)
    unit = ""
    m = re.search(r"(हे|हेक्टेयर|एकड़|बीघा|बिस्वा|गुंठा|ha|acre|bigha|biswa|guntha)\.?\s*$", s, re.I)
    if m:
        unit = m.group(1)
        s = s[:m.start()]
    s = s.replace("l", "1").replace("I", "1").replace("|", "1").replace("f", "1")
    s = s.replace("O", "0").replace("o", "0").replace("S", "5").replace(",", ".")
    s = re.sub(r"[^0-9.]", "", s)
    s = re.sub(r"\.{2,}", ".", s).strip(".")
    if not s:
        return None
    if s.count(".") > 1:                       # keep the first decimal point only
        head, _, tail = s.partition(".")
        s = head + "." + tail.replace(".", "")
    return f"{s} {unit}".strip() if unit else s
