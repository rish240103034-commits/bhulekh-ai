"""NLP field extraction from OCR text.

Strategy (layered, each layer raises confidence when they agree):
  1. Multilingual keyword lexicon (English + Hindi/Marathi transliterations & Devanagari)
     -> "key : value" pattern matching on each OCR line.
  2. Regex validators per field (survey/khasra/khata numbers, area with unit, dates).
  3. Fuzzy matching of OCR-noisy keys (rapidfuzz) to tolerate faded/handwritten labels.
  4. Learned corrections lexicon (from human reviews) applied as post-processing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz, process

from app.pipeline.ocr import OCRResult, Token

# ---- Predefined land-record fields (spec: PS 26018) ----
FIELD_LEXICON: dict[str, list[str]] = {
    "owner_name": ["owner name", "owner", "landowner", "name of owner", "khatedar", "bhumidhar",
                   "malik", "khatedar ka naam", "भूमिधर", "खातेदार", "खातेदार का नाम", "मालिक", "स्वामी",
                   "भूस्वामी", "नाम", "name"],
    "father_or_husband_name": ["father's name", "father name", "s/o", "d/o", "w/o", "pita ka naam",
                               "husband name", "पिता", "पिता का नाम", "पति", "वडिलांचे नाव"],
    "survey_number": ["survey no", "survey number", "survey", "sy no", "s.no", "सर्वे नं", "सर्वे क्रमांक",
                      "सर्वे", "गट नंबर", "gat no", "gat number", "hissa"],
    "khasra_number": ["khasra no", "khasra number", "khasra", "खसरा", "खसरा नं", "खसरा संख्या", "khasra sankhya"],
    "khata_number": ["khata no", "khata number", "khata", "khatauni", "account no", "खाता", "खाता संख्या",
                     "खाता नं", "खतौनी", "खाता क्रमांक", "8-अ"],
    "plot_area": ["area", "plot area", "rakba", "kshetrafal", "रकबा", "क्षेत्रफल", "क्षेत्र"],
    "village": ["village", "gram", "gaon", "mauza", "ग्राम", "गांव", "मौजा", "गाव",
                "ग्राम का नाम", "गांव का नाम", "village name", "मौजा का नाम", "ग्राम नाम"],
    "tehsil": ["tehsil", "tahsil", "taluka", "taluk", "mandal", "तहसील", "तालुका", "मंडल"],
    "district": ["district", "zila", "jila", "जिला", "जिल्हा", "ज़िला"],
    "state": ["state", "rajya", "राज्य"],
    "land_classification": ["land type", "land classification", "classification", "kism", "bhumi prakar",
                            "land class", "किस्म", "भूमि प्रकार", "भूमि का प्रकार", "जमिनीचा प्रकार", "class"],
    "ownership_type": ["ownership type", "ownership", "tenure", "swamitva", "स्वामित्व", "स्वामित्व प्रकार",
                       "भूमि स्वामित्व"],
    "mutation_number": ["mutation no", "mutation number", "mutation", "namantaran", "dakhil kharij",
                        "नामांतरण", "नामांतरण संख्या", "दाखिल खारिज", "फेरफार", "ferfar"],
    "mutation_date": ["mutation date", "date of mutation", "नामांतरण दिनांक", "फेरफार दिनांक"],
    "registration_number": ["registration no", "registration number", "regn no", "reg no", "document no",
                            "deed no", "पंजीयन संख्या", "पंजीकरण संख्या", "रजिस्ट्री नं", "दस्तावेज क्रमांक"],
    "registration_date": ["registration date", "date of registration", "regn date", "पंजीयन दिनांक",
                          "पंजीकरण दिनांक"],
    "record_year": ["year", "fasli", "record year", "वर्ष", "फसली", "सन", "वित्तीय वर्ष"],
    "pargana": ["pargana", "परगना", "पargana", "circle"],
    "total_area": ["कुल रकबा", "कुल क्षेत्रफल", "कुल रकबबा", "total area", "kul rakba", "योग रकबा",
                   "एकूण क्षेत्र"],
    "irrigated_area": ["सिंचित", "सिंचित क्षेत्र", "irrigated", "sinchit", "बागायत क्षेत्र"],
    "unirrigated_area": ["असिंचित", "असिंचित क्षेत्र", "unirrigated", "asinchit", "जिरायत क्षेत्र"],
    "record_date": ["तारीख", "दिनांक", "date", "tarikh", "दिनाँक"],
    "encumbrance": ["भू-स्वामी / साहूकार", "साहूकार", "ऋण", "बंधक", "encumbrance", "mortgage",
                    "भू-स्वामी साहूकार"],
}

# Value validators: (regex, weight). A match multiplies confidence up; mismatch scales it down.
VALIDATORS: dict[str, re.Pattern] = {
    "survey_number": re.compile(r"^[0-9०-९]{1,5}(\s*/\s*[0-9०-९A-Za-z]{1,4})*[A-Za-z]?$"),
    "khasra_number": re.compile(r"^[0-9०-९]{1,5}(\s*/\s*[0-9०-९]{1,4})*$"),
    "khata_number": re.compile(r"^[0-9०-९]{1,6}$"),
    "mutation_number": re.compile(r"^[A-Za-z0-9०-९/\-]{1,20}$"),
    "registration_number": re.compile(r"^[A-Za-z0-9०-९/\-]{1,25}$"),
    "record_year": re.compile(r"^(19|20)[0-9]{2}(\s*[-/]\s*(19|20)?[0-9]{2})?$"),
    "plot_area": re.compile(r"^[0-9०-९]+([.,][0-9०-९]+)?\s*(ha|hectare|hect|acre|acres|bigha|biswa|guntha|sq\.?\s*m|sqm|sq\s*ft|sqft|kanal|marla|cent|are|हेक्टेयर|हे|एकड़|बीघा|बिस्वा|गुंठा|वर्ग\s*मीटर)?\.?$", re.I),
    "mutation_date": re.compile(r"^[0-9०-९]{1,2}[-/.][0-9०-९]{1,2}[-/.][0-9०-९]{2,4}$"),
    "record_date": re.compile(r"^[0-9०-९]{1,2}\s*[-/.]\s*[0-9०-९]{1,2}\s*[-/.]\s*[0-9०-९]{2,4}$"),
    "total_area": re.compile(r"^[0-9०-९]+([.,][0-9०-९]+)?.*$"),
    "irrigated_area": re.compile(r"^[0-9०-९]+([.,][0-9०-९]+)?.*$"),
    "unirrigated_area": re.compile(r"^[0-9०-९]+([.,][0-9०-९]+)?.*$"),
    "registration_date": re.compile(r"^[0-9०-९]{1,2}[-/.][0-9०-९]{1,2}[-/.][0-9०-९]{2,4}$"),
}

TITLE_NOISE = [
    "उत्तर प्रदेश शासन", "मध्य प्रदेश शासन", "राजस्थान सरकार", "महाराष्ट्र शासन", "बिहार सरकार",
    "हरियाणा सरकार", "पंजाब सरकार", "भारत सरकार", "राजस्व विभाग", "भूमि अभिलेख", "अधिकार अभिलेख",
    "खसरा", "खतौनी", "जमाबंदी", "नकल", "प्रपत्र", "फार्म", "लेखपाल", "कानूनगो", "तहसीलदार",
    "ग्राम पंचायत", "government of", "record of rights", "revenue department", "form no",
]

LAND_CLASSES = ["agricultural", "irrigated", "unirrigated", "residential", "commercial", "industrial",
                "barren", "forest", "pasture", "waste land", "orchard", "सिंचित", "असिंचित", "कृषि",
                "आवासीय", "व्यावसायिक", "बंजर", "वन", "बागायत", "जिरायत"]
OWNERSHIP_TYPES = ["individual", "joint", "trust", "company", "bhumidhar", "sirdar", "asami",
                   "एकल", "संयुक्त", "सरकारी", "भूमिधर", "सीरदार", "आसामी"]

SEP = re.compile(r"\s*[:|]\s*|\s+[-–—=]+\s*|\s{3,}")
MAX_KEY_WORDS = 4
DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")
_LATIN_WORD_RE = re.compile(r"^[A-Za-z]{1,}$")
# Fields whose value is a figure: everything after the number is commentary.
NUMERIC_VALUE_FIELDS = {"total_area", "irrigated_area", "unirrigated_area", "plot_area"}
DATE_VALUE_FIELDS = {"record_date", "mutation_date", "registration_date"}
# A handwritten '1' is routinely read as one of these strokes.
_STROKE_TO_ONE = str.maketrans(dict.fromkeys("|[]()!lI/\\।ाl", "1"))


@dataclass
class Extracted:
    field_name: str
    value: str
    confidence: float
    source: str = "rule"
    page: int = 1
    line: int | None = None
    bbox: dict | None = None
    value_bbox: dict | None = None
    evidence: str = ""


@dataclass
class ExtractionOutput:
    fields: list[Extracted] = field(default_factory=list)
    doc_type: str = "unknown"


def _flat_keys() -> list[tuple[str, str]]:
    return [(k, fname) for fname, keys in FIELD_LEXICON.items() for k in keys]


_KEY_LIST = _flat_keys()
_KEY_STRINGS = [k for k, _ in _KEY_LIST]


def _match_key(candidate: str) -> tuple[str | None, float]:
    """Return (field_name, score 0-100) for a noisy OCR key fragment (whole string)."""
    c = candidate.strip().lower().strip(".:()")
    if not c or len(c) < 2:
        return None, 0.0
    best = process.extractOne(c, _KEY_STRINGS, scorer=fuzz.ratio, score_cutoff=80)
    if not best:
        return None, 0.0
    key, score, idx = best
    return _KEY_LIST[idx][1], float(score)


def _match_key_suffix(segment: str) -> tuple[str | None, float, int]:
    """The label usually sits at the END of the text before ':' (junk/prev value before it).
    Try the last 1..MAX_KEY_WORDS words; return (field, score, n_words_used)."""
    words = segment.split()
    best: tuple[str | None, float, int] = (None, 0.0, 0)
    for n in range(1, min(MAX_KEY_WORDS, len(words)) + 1):
        f, s = _match_key(" ".join(words[-n:]))
        if f and s + 4.0 * n > best[1] + 4.0 * best[2]:   # prefer longer, more specific labels
            best = (f, s, n)
    return best


def _trim_value(value: str, field_name: str | None = None) -> str:
    """Cut form furniture out of a value.

    Header lines span the full sheet, so a single OCR line often reads
    "जिला - कानपुर देहात   उत्तर प्रदेश शासन   वर्ष - 1987-88". Splitting on the
    separators leaves the document title sitting inside the district's value; the
    title is not data, so the value ends where such a phrase begins.
    """
    v = value.strip()
    low = v.lower()
    cut = len(v)
    for phrase in TITLE_NOISE:
        idx = low.find(phrase.lower())
        if idx > 0:
            cut = min(cut, idx)
    v = v[:cut]
    v = re.sub(r"[_.\-–—~`'\"]{2,}", " ", v)          # dotted rules and fill-in underscores
    v = re.sub(r"\s{2,}", " ", v).strip(" .,;:_-–—|\"'()")

    # A value written in Devanagari picks up stray Latin tokens from OCR noise; drop them.
    if _DEVANAGARI_RE.search(v):
        kept = [w for w in v.split() if not _LATIN_WORD_RE.match(w)]
        if kept:
            v = " ".join(kept)

    if field_name in NUMERIC_VALUE_FIELDS:
        v = v.split("(")[0]                            # "0.62 (शून्य दशमलव...)" -> "0.62"
        m = re.search(r"[0-9]+(?:[.,][0-9]+)?", v.translate(DEVANAGARI_DIGITS))
        if m:
            unit = re.search(r"(हेक्टेयर|हे|एकड़|बीघा|बिस्वा|गुंठा|ha|acre|bigha|biswa)\.?", v, re.I)
            v = m.group(0).replace(",", ".") + (f" {unit.group(1)}" if unit else "")
    elif field_name in DATE_VALUE_FIELDS:
        d = v.translate(DEVANAGARI_DIGITS).translate(_STROKE_TO_ONE)
        d = re.sub(r"\s+", "", d)
        m = re.search(r"([0-9]{1,2})[-/.]([0-9]{1,2})[-/.]([0-9]{2,4})", d)
        if m:
            v = f"{m.group(1)}.{m.group(2)}.{m.group(3)}"
    elif field_name == "record_year":
        y = v.translate(DEVANAGARI_DIGITS).translate(_STROKE_TO_ONE)
        m = re.search(r"([0-9]{4})\s*[-/]?\s*([0-9]{2,4})?", y)
        if m:
            v = m.group(0).strip()
    return v


def _span_bbox(tokens: list[Token], line: int, line_text: str, fragment: str) -> dict | None:
    """Bounding box of just `fragment` within a line, in page coordinates.

    Lines are built by space-joining tokens, so character offsets map back to tokens
    exactly. This lets a numeric field be re-read from the pixels of its own value
    rather than the whole line.
    """
    start = line_text.find(fragment)
    if start < 0:
        return None
    end = start + len(fragment)
    row = sorted([t for t in tokens if t.line == line], key=lambda t: t.x)
    if not row:
        return None
    hit, cursor = [], 0
    for t in row:
        t_start, t_end = cursor, cursor + len(t.text)
        if t_start < end and t_end > start:
            hit.append(t)
        cursor = t_end + 1                     # the joining space
    if not hit:
        return None
    x0 = min(t.x for t in hit); y0 = min(t.y for t in hit)
    x1 = max(t.x + t.w for t in hit); y1 = max(t.y + t.h for t in hit)
    return {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0}


def _line_bbox(tokens: list[Token], line: int) -> dict | None:
    ts = [t for t in tokens if t.line == line]
    if not ts:
        return None
    x0 = min(t.x for t in ts); y0 = min(t.y for t in ts)
    x1 = max(t.x + t.w for t in ts); y1 = max(t.y + t.h for t in ts)
    return {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0}


def _line_conf(tokens: list[Token], line: int) -> float:
    ts = [t.conf for t in tokens if t.line == line]
    return sum(ts) / len(ts) if ts else 50.0


def detect_doc_type(text: str) -> str:
    t = text.lower()
    if any(k in t for k in ["sale deed", "conveyance", "vendor", "vendee", "विक्रय", "बैनामा", "sale"]):
        return "sale_deed"
    if any(k in t for k in ["khatauni", "खतौनी", "record of rights", "jamabandi", "जमाबंदी", "7/12", "satbara",
                             "adhikar abhilekh", "अधिकार अभिलेख"]):
        return "ror"
    if any(k in t for k in ["mutation", "नामांतरण", "दाखिल खारिज", "फेरफार"]):
        return "mutation"
    if any(k in t for k in ["khasra", "खसरा", "survey", "सर्वे"]):
        return "khasra"
    if any(k in t for k in ["map", "नक्शा", "cadastral", "scale"]):
        return "map"
    return "unknown"


def extract_fields(ocr: OCRResult, page: int = 1) -> ExtractionOutput:
    out = ExtractionOutput(doc_type=detect_doc_type(ocr.text))
    lines = ocr.text.split("\n")
    seen: dict[str, Extracted] = {}

    for li, raw in enumerate(lines):
        line = raw.strip()
        if not line:
            continue
        # A line can hold multiple "key: value" pairs:  "District : Lucknow  Tehsil : Sadar"
        # Split on ':' -> segments. seg[0] ends with key1; middle segments are "value_i key_{i+1}";
        # last segment is the final value.
        segs = [p.strip() for p in SEP.split(line) if p and p.strip()]
        if len(segs) < 2:
            # "Khasra 123/4" (no separator): label followed by numeric-ish value
            m = re.match(r"^(.*?[A-Za-zऀ-ॿ.]{2,})\s+([0-9०-९][0-9०-९/.\-A-Za-z]*)$", line)
            if not m:
                continue
            segs = [m.group(1), m.group(2)]
        pairs: list[tuple[str, float, str]] = []
        fname, kscore, _ = _match_key_suffix(segs[0])
        for seg in segs[1:-1]:
            words = seg.split()
            # find split point: value = words[:j], next key = words[j:]
            best_j, best_f, best_s = len(words), None, 0.0
            # j may be 0: a segment can be nothing but a label, as in
            # "अन्य विवरण : सिंचित : 0.35 हे. असिंचित : 0.27 हे."
            for j in range(max(0, len(words) - MAX_KEY_WORDS), len(words)):
                f, s = _match_key(" ".join(words[j:]))
                if f and s + 0.5 * (len(words) - j) > best_s + 0.5 * (len(words) - best_j):
                    best_j, best_f, best_s = j, f, s
            if fname:
                pairs.append((fname, kscore, " ".join(words[:best_j])))
            fname, kscore = best_f, best_s
        if fname:
            pairs.append((fname, kscore, segs[-1]))

        for fname, kscore, raw_val in pairs:
            val = _trim_value(raw_val, fname)
            if not val:
                continue
            conf = _score(fname, val, kscore, _line_conf(ocr.tokens, li))
            cand = Extracted(fname, val, conf, "rule", page, li, _line_bbox(ocr.tokens, li),
                             value_bbox=_span_bbox(ocr.tokens, li, line, raw_val.strip()),
                             evidence=line)
            if fname not in seen or seen[fname].confidence < conf:
                seen[fname] = cand

    # Category fields: search whole text for known vocabulary if not found by key
    if "land_classification" not in seen:
        v = _find_vocab(ocr.text, LAND_CLASSES)
        if v:
            seen["land_classification"] = Extracted("land_classification", v, 62.0, "ml", page, evidence="vocab")
    if "ownership_type" not in seen:
        v = _find_vocab(ocr.text, OWNERSHIP_TYPES)
        if v:
            seen["ownership_type"] = Extracted("ownership_type", v, 60.0, "ml", page, evidence="vocab")

    out.fields = list(seen.values())
    return out


def _find_vocab(text: str, vocab: list[str]) -> str | None:
    t = text.lower()
    for v in vocab:
        if v in t:
            return v
    return None


def _score(fname: str, value: str, key_score: float, ocr_conf: float) -> float:
    """Confidence = weighted blend of key-match, OCR token confidence and value validity."""
    validator = VALIDATORS.get(fname)
    if validator:
        valid = 1.0 if validator.match(value.translate(DEVANAGARI_DIGITS)) else 0.35
    else:
        valid = 0.9 if 2 <= len(value) <= 80 else 0.5
    conf = 0.35 * key_score + 0.40 * ocr_conf + 25.0 * valid
    return round(max(0.0, min(conf, 99.0)), 1)
