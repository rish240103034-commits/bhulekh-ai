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

# Devanagari label phrases that on this class of form act as column headers or
# section starts. If they appear INSIDE a value string, that value has bled into the
# next label region and should be truncated at their leading character.
_EMBEDDED_LABEL_KEYWORDS = (
    "फ़सल का विवरण", "फसल का विवरण",           # crop details column
    "भूमि स्वामी का नाम", "भूस्वामी का नाम",   # landowner column
    "कृषक का नाम",                             # cultivator column
    "क्षेत्रफल", "बीघा-बिस्वा",                 # area column
    "खसरा नं", "खसरा संख्या", "खसरा क्रमांक",
    "खाता नं", "खाता संख्या",
    "तहसील", "जिला", "ग्राम", "पट्टा", "परगना",
    "वर्ष", "विवरण",
    "रबी", "खरीफ", "खररीप",
)

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

SEP = re.compile(r"\s*[:;|：ः]\s*|\s+[-–—=]+\s*|\s{3,}")
# Devanagari visarga (ः, ः) is visually a colon; OCR outputs it in place of ':'
# in Hindi label:value pairs like "जिलाः मेरठ". Semicolon and full-width colon (：)
# similarly get emitted for the same character on this input.
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
    """Return (field_name, score 0-100) for a noisy OCR key fragment (whole string).

    Very short OCR fragments like "TA Te" trivially match short English keys such as
    "state" or "name" at exactly the fuzzy-cutoff score. Filter those before the
    match: a real label either contains a Devanagari letter or is a proper Latin
    word with at least one vowel and no lone-uppercase soup.
    """
    c = candidate.strip().lower().strip(".:()")
    if not c or len(c) < 2:
        return None, 0.0
    # Reject Latin-only fragments that don't look like a real English word: fewer
    # than 3 letters, no vowel, or a punctuation-dense burst. These are the classic
    # "TA Te / mate / P§" fragments Tesseract emits for handwritten Devanagari.
    if _DEVANAGARI_RE.search(c) is None:
        letters = [ch for ch in c if ch.isalpha()]
        if len(letters) < 4:
            return None, 0.0
        if not any(v in c for v in "aeiou"):
            return None, 0.0
    best = process.extractOne(c, _KEY_STRINGS, scorer=fuzz.ratio, score_cutoff=85)
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

    Tabular forms have a second problem: the label-value line runs into a column
    header on the same OCR line (e.g. "ग्राम: सलेमपुर, फ़सल का विवरण"). Any known
    label keyword embedded inside a value has to be treated as the start of a new
    field, not part of the current one.
    """
    v = value.strip()
    low = v.lower()
    cut = len(v)
    for phrase in TITLE_NOISE:
        idx = low.find(phrase.lower())
        if idx > 0:
            cut = min(cut, idx)
    # Truncate at any embedded label keyword: the current value ends before the next
    # label begins. Keyword must sit at a word boundary and not be at position 0
    # (otherwise the whole value is a label; the extractor already handled that).
    for keyword in _EMBEDDED_LABEL_KEYWORDS:
        idx = v.find(keyword)
        while 0 < idx < cut:
            before = v[idx - 1] if idx > 0 else " "
            if not before.isalnum() and before not in "ऀँंः":
                cut = idx
                break
            idx = v.find(keyword, idx + 1)
    v = v[:cut]
    v = re.sub(r"[_.\-–—~`'\"]{2,}", " ", v)          # dotted rules and fill-in underscores
    # Cut a trailing asterisk / curly-quote / stray star that Tesseract emits
    # for signature seals or margin marks. Do this before the outer strip so any
    # dangling punctuation from the tail is fully removed.
    v = re.sub(r"\s+[*×★•·`'\"“”‘’]+.*$", "", v)
    v = re.sub(r"\s{2,}", " ", v).strip(" .,;:_-–—|\"'()*×★•·“”‘’`")

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


# ---- Structural patterns common to Indian land-revenue registers ----
# A single Devanagari "word" (letter cluster), used to build a 1-to-N word name.
_DEV_WORD = r"[ऀ-ॿ]+"
# Words that would immediately end a person name: the relationship markers, role
# suffixes, common column headers. When any of these follow, the person name is over.
_NAME_STOP_WORDS = (
    "आत्मज|आत्मज़|वलद|पुत्र|पिता|मालिक|स्वामी|काश्तकार|कब्जेदार|कब्ज़ेदार|"
    "भूमि|भूस्वामी|कृषक|खातेदार|फ़सल|फसल|खसरा|खाता|क्षेत्रफल"
)
# Owner name: 1 to 4 Devanagari words, but no subsequent word may itself be a
# stop word. Prevents "राम लाल आत्मज..." from swallowing the आत्मज marker.
_DEV_OWNER_NAME = rf"{_DEV_WORD}(?:\s+(?!(?:{_NAME_STOP_WORDS})\b){_DEV_WORD}){{0,3}}"
# Father name: 1 or 2 Devanagari words — a standard Indian patronymic ("हरि सिंह",
# "गोपाल राम"). Bounded at 2 so the greedy quantifier can't gobble the NEXT owner
# name on a multi-parcel row where "आत्मज X Y आत्मज..." repeats.
_DEV_FATHER_NAME = rf"{_DEV_WORD}(?:\s+(?!(?:{_NAME_STOP_WORDS})\b){_DEV_WORD}){{0,1}}"

# Role suffix — name preceded by, or followed by, "- मालिक" / "- स्वामी" / "- काश्तकार".
_ROLE_OWNER_RE = re.compile(
    rf"({_DEV_OWNER_NAME})\s*[-–—:]\s*(?:मालिक|स्वामी|malik|swami)\b",
    re.IGNORECASE,
)
_ROLE_CULTIVATOR_RE = re.compile(
    rf"({_DEV_OWNER_NAME})\s*[-–—:]\s*(?:काश्तकार|कब्जेदार|कब्ज़ेदार|kashtkar|kabjedar)\b",
    re.IGNORECASE,
)
# आत्मज / वलद / पुत्र ("son of") sit BETWEEN a person and their father on Hindi
# revenue forms. The father name is bounded so it can't gobble the next owner's
# name — which on a multi-parcel sheet immediately follows the first father.
_FATHER_MARKER_RE = re.compile(
    rf"({_DEV_OWNER_NAME})\s+(?:आत्मज|आत्मज़|वलद|पुत्र|पिता\s*पुत्र)\s+({_DEV_FATHER_NAME})",
)
# Bigha-biswa area, in Devanagari or ASCII digits, either fully separated or bare.
_BIGHA_BISWA_RE = re.compile(
    r"([0-9०-९]+)\s*बीघा\s*([0-9०-९]+)?\s*(?:बिस्वा|बिस्वे)?",
)
# A parcel row on a khasra girdawari sheet has a khasra number immediately
# followed by the bigha-biswa area — that adjacency is more reliable than trying
# to anchor on the year at the start of a noise-heavy OCR line. Used only when
# the table detector could not recognise the ruled grid.
_KHASRA_ROW_RE = re.compile(
    r"(?<![०-९0-9])"                                    # not preceded by another digit
    r"([०-९]{2,4})"                                     # capture: khasra (Devanagari)
    r"(?![०-९0-9])"                                     # not followed by another digit
    r"[^०-९0-9\n]{1,10}?"                               # window with NO other digits — pins to
                                                        # the khasra nearest बीघा, so on a row
                                                        # like "२०५५ १०४ 3 बीघा" only १०४ matches
    r"बीघा"                                             # followed by a bigha area
)
# Straight state mentions on Haryana / UP / MP e-record portals (title bar text).
_STATE_MENTIONS = {
    "HARYANA": "Haryana", "हरियाणा": "हरियाणा",
    "UTTAR PRADESH": "Uttar Pradesh", "उत्तर प्रदेश": "उत्तर प्रदेश",
    "MADHYA PRADESH": "Madhya Pradesh", "मध्य प्रदेश": "मध्य प्रदेश",
    "RAJASTHAN": "Rajasthan", "राजस्थान": "राजस्थान",
    "MAHARASHTRA": "Maharashtra", "महाराष्ट्र": "महाराष्ट्र",
    "BIHAR": "Bihar", "बिहार": "बिहार",
    "PUNJAB": "Punjab", "पंजाब": "पंजाब",
    "GUJARAT": "Gujarat", "गुजरात": "गुजरात",
}


def find_khasra_rows(text: str) -> list[str]:
    """Recover khasra numbers from a multi-parcel sheet the table detector missed.

    Each parcel row of a khasra girdawari has the khasra number a short distance
    before the bigha area cell. The list returned here is ASCII-digit form, in OCR
    order, de-duplicated while preserving order. 4-digit numbers in the year range
    (18xx–21xx) are dropped: they are years, not khasra numbers.
    """
    seen: list[str] = []
    for m in _KHASRA_ROW_RE.finditer(text):
        raw = m.group(1)
        ascii_digits = raw.translate(DEVANAGARI_DIGITS)
        if not (2 <= len(ascii_digits) <= 5):
            continue
        # Drop year-shaped numbers so a row line like "२०५५ १०४ ३ बीघा" (where the
        # regex's window doesn't reach across, but similar variants do) never
        # elevates the year into a khasra.
        try:
            n = int(ascii_digits)
            if 1800 <= n <= 2100:
                continue
        except ValueError:
            continue
        if ascii_digits in seen:
            continue
        seen.append(ascii_digits)
    return seen


def _looks_like_person_name(name: str) -> bool:
    """Filter names that are almost certainly not a person.

    A land-classification token (पड़त, चरागाह, बंजर, आबादी…) that happens to sit
    above "मालिक" or "काश्तकार" in a column strip would otherwise steal the
    owner/cultivator field. The heuristic here is intentionally simple: reject
    if the value matches any known land-classification/crop vocabulary token, and
    require at least one Devanagari "word" of length ≥ 2. Real person names are
    almost always multi-word; single-word names are still allowed but only if
    they aren't in the closed vocabulary.
    """
    from app.pipeline.table import VOCABULARIES

    stripped = name.strip()
    if not stripped:
        return False
    if any(ch.isdigit() for ch in stripped):
        return False
    lower = stripped.lower()
    for vocab_terms in VOCABULARIES.values():
        for term in vocab_terms:
            if term == stripped or term.lower() == lower:
                return False
    # An OCR fragment like "9२०२६ कई त" — mostly punctuation or single-glyph tokens.
    tokens = [t for t in re.split(r"\s+", stripped) if len(t) >= 2]
    if not tokens:
        return False
    return True


def _apply_structural_patterns(text: str, seen: dict[str, "Extracted"], page: int) -> None:
    """Extract fields from structural patterns that don't fit the label:value model.

    These are the conventions land-revenue registers use where the label sits AFTER
    the value ("<name> - मालिक"), or where the separator is a relation word rather
    than a colon ("<name> आत्मज <father>"). The plain SEP-based extractor cannot see
    these because it splits on colon and looks for the label on the left.
    """
    # Owner name from role suffix — "<Name> - मालिक" / "<Name> - स्वामी"
    if "owner_name" not in seen:
        for m in _ROLE_OWNER_RE.finditer(text):
            name = re.sub(r"\s+", " ", m.group(1)).strip(" ,।-–—")
            if name and len(name) >= 3 and _looks_like_person_name(name):
                seen["owner_name"] = Extracted("owner_name", name, 78.0, "pattern-role",
                                                page, evidence=m.group(0))
                break

    # Possessor / cultivator — "<Name> - काश्तकार"
    if "possessor_name" not in seen:
        for m in _ROLE_CULTIVATOR_RE.finditer(text):
            name = re.sub(r"\s+", " ", m.group(1)).strip(" ,।-–—")
            if name and len(name) >= 3 and _looks_like_person_name(name):
                seen["possessor_name"] = Extracted("possessor_name", name, 76.0,
                                                    "pattern-role", page,
                                                    evidence=m.group(0))
                break

    # Father's name from the आत्मज / वलद / पुत्र separator — this is a strong signal
    # on its own (the marker specifically means "son of X"). Iterate matches so we
    # skip the ones whose father group captured a crop name (गेंहू, धान), a unit
    # (बिस्वा), or was otherwise not a person. Owner is filled from the token
    # before आत्मज, again with the same validity check.
    for m in _FATHER_MARKER_RE.finditer(text):
        candidate_owner = re.sub(r"\s+", " ", m.group(1)).strip(" ,।-–—")
        # Strip the area cell that spilled into the owner cell. The row usually
        # starts "<khasra> <area> <owner> आत्मज <father>", and after preprocess
        # the space between the area and the owner is sometimes lost. So drop any
        # leading digits and then any leading unit words (बीघा, बिस्वा, hectare
        # etc.) so a real person name is left.
        candidate_owner = re.split(r"[०-९0-9]+", candidate_owner)[-1].strip()
        # Repeatedly strip a leading unit word — the row is "<khasra> <n> <unit>
        # <owner>", and Tesseract may drop the whitespace after the unit so the
        # regex capture starts with "बिस्वा". Python's \b is Latin-only, so use
        # an explicit whitespace terminator.
        while True:
            trimmed = re.sub(
                r"^(?:बीघा|बिस्वा|बिंस्वा|बिंस्वे|हेक्टेयर|हे|एकड़|एकड|गुंठा|ha|hectare|acre)"
                r"(?:\s+|$)", "", candidate_owner)
            if trimmed == candidate_owner:
                break
            candidate_owner = trimmed.strip()
        father = re.sub(r"\s+", " ", m.group(2)).strip(" ,।-–—")
        father = re.split(r"[|/,;।]", father)[0].strip()
        owner_ok = (candidate_owner and len(candidate_owner) >= 3
                    and _looks_like_person_name(candidate_owner))
        father_ok = (father and len(father) >= 3
                     and _looks_like_person_name(father))
        if not (owner_ok and father_ok):
            continue
        if "owner_name" not in seen:
            # First valid pair is the reliable one; a longer capture later in the
            # OCR text is usually just noise the greedy matcher hasn't finished
            # trimming. Cross-pass merging happens in the runner's `_merge_score`.
            seen["owner_name"] = Extracted("owner_name", candidate_owner, 82.0,
                                            "pattern-relation", page,
                                            evidence=m.group(0))
        if "father_or_husband_name" not in seen:
            seen["father_or_husband_name"] = Extracted(
                "father_or_husband_name", father, 78.0, "pattern-relation",
                page, evidence=m.group(0))
        # First fully-valid pair is enough — later matches only produce noisier
        # variants (owner "5 बिस्वा राम लाल", father "हरि गेंहू").
        break

    # Plot area — bigha-biswa is the compound unit used across UP/Haryana/Bihar
    if "plot_area" not in seen:
        m = _BIGHA_BISWA_RE.search(text)
        if m:
            bigha = m.group(1)
            biswa = m.group(2)
            value = f"{bigha} बीघा" + (f" {biswa} बिस्वा" if biswa else "")
            seen["plot_area"] = Extracted("plot_area", value, 72.0, "pattern-area",
                                          page, evidence=m.group(0))

    # Khasra number(s) — pull them from row-start patterns when the table detector
    # missed the grid. On a single-row match, use it directly; on many rows, expose
    # the range plus the full list in the value so the reviewer can see all khasras.
    if "khasra_number" not in seen:
        rows = find_khasra_rows(text)
        if rows:
            value = rows[0] if len(rows) == 1 else f"{rows[0]}-{rows[-1]}"
            seen["khasra_number"] = Extracted(
                "khasra_number", value, 74.0, "pattern-rowlist", page,
                evidence=f"{len(rows)} row(s): {', '.join(rows)}",
            )

    # State — pattern-mentioned in the title bar of the record portal
    if "state" not in seen:
        upper = text.upper()
        for token, canonical in _STATE_MENTIONS.items():
            if token in upper or token in text:
                seen["state"] = Extracted("state", canonical, 90.0, "pattern-title",
                                          page, evidence=token)
                break


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
            if not val or _is_gibberish_for(fname, val):
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

    # Structural patterns run last so a stronger label:value reading is never overridden
    # by a heuristic pattern match. See `_apply_structural_patterns`.
    _apply_structural_patterns(ocr.text, seen, page)

    out.fields = list(seen.values())
    return out


# Fields whose value is a proper noun (place or person). On an Indian Devanagari form
# these are always in a Devanagari script; a Latin-only value here is almost always
# Tesseract misreading handwriting, not real data.
_NAME_FIELDS = {"village", "tehsil", "district", "state", "owner_name",
                "father_or_husband_name", "pargana"}
# A plausible Latin place/person name token: either a Title-Case word (`Meerut`,
# `Ram`) or a lowercase one (`meerut`) of at least 3 letters. All-caps 3-4 letter
# tokens like `TAX`, `TOIT` are Tesseract falling back to Latin gibberish for
# handwritten Devanagari and never a real name.
_LATIN_WORD_RE_INNER = re.compile(r"\b(?:[A-Z][a-z]{2,}|[a-z]{3,})\b")


def _is_gibberish_for(fname: str, value: str) -> bool:
    """Reject values that are almost certainly OCR noise, not real content.

    A name-type field with no Devanagari letter and no plausible Latin word is
    Tesseract falling back to Latin gibberish for handwritten Hindi — e.g. reading
    सदर as "TAX, TOIT". A short value that is mostly ASCII punctuation is a torn
    edge or a stray stroke. Devanagari vowel signs (मात्रा, U+093E..U+094D) are
    Unicode Marks, not Letters — so `.isalnum()` returns False for them; we
    therefore count only ASCII punctuation, not "everything not-alnum".
    """
    ascii_punct = sum(1 for ch in value
                      if ch in "!\"#$%&'()*+,;<=>?@[\\]^_`{}~“”‘’„«»")
    if ascii_punct >= 3 and len(value) < 24:
        return True
    if fname in _NAME_FIELDS:
        if not _DEVANAGARI_RE.search(value) and not _LATIN_WORD_RE_INNER.search(value):
            return True
    return False


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
