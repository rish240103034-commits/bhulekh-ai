"""Normalisation of extracted values into canonical representations."""
from __future__ import annotations

import re
from datetime import datetime

DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")

# Area units -> square metres. Regional units vary; these are widely used values.
AREA_UNITS_SQM = {
    "ha": 10000.0, "hectare": 10000.0, "hect": 10000.0, "हेक्टेयर": 10000.0, "हे": 10000.0,
    "acre": 4046.86, "acres": 4046.86, "एकड़": 4046.86,
    "bigha": 2529.0,  # UP/Bihar pucca bigha (~27,225 sq ft); state-specific overrides in rules
    "बीघा": 2529.0,
    "biswa": 126.45, "बिस्वा": 126.45,
    "guntha": 101.17, "गुंठा": 101.17,
    "kanal": 505.857, "marla": 25.29, "cent": 40.47, "are": 100.0,
    "sqm": 1.0, "sq m": 1.0, "sq.m": 1.0, "वर्ग मीटर": 1.0,
    "sqft": 0.0929, "sq ft": 0.0929,
}


def to_ascii_digits(s: str | None) -> str | None:
    return s.translate(DEVANAGARI_DIGITS) if s else s


def normalize_number(s: str | None) -> str | None:
    if not s:
        return None
    s = to_ascii_digits(s)
    s = re.sub(r"\s+", "", s)
    s = s.replace("\\", "/").replace("|", "/")
    # Common OCR confusions in numeric fields
    s = s.replace("O", "0").replace("o", "0").replace("l", "1").replace("I", "1").replace("S", "5")
    return s.strip("./-") or None


def parse_area(s: str | None) -> tuple[float | None, str | None, float | None]:
    """'2.50 ha' -> (2.5, 'ha', 25000.0). Returns (value, unit, sqm)."""
    if not s:
        return None, None, None
    s = to_ascii_digits(s).lower().replace(",", ".")
    m = re.match(r"\s*([0-9]+(?:\.[0-9]+)?)\s*([a-zA-Zऀ-ॿ. ]*)", s)
    if not m:
        return None, None, None
    value = float(m.group(1))
    unit_raw = m.group(2).strip(". ").strip()
    unit = None
    for u in sorted(AREA_UNITS_SQM, key=len, reverse=True):
        if unit_raw.startswith(u):
            unit = u
            break
    sqm = round(value * AREA_UNITS_SQM[unit], 2) if unit else None
    return value, unit or (unit_raw or None), sqm


def parse_date(s: str | None) -> str | None:
    """Return ISO date (YYYY-MM-DD) if parsable, else original."""
    if not s:
        return None
    s = to_ascii_digits(s).strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%d-%m-%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return s


def normalize_name(s: str | None) -> str | None:
    if not s:
        return None
    s = re.sub(r"\s+", " ", s).strip(" .,:;-")
    s = re.sub(r"^(shri|smt|sri|श्री|श्रीमती)\.?\s+", "", s, flags=re.I)
    return s.title() if s.isascii() else s


def normalize_field(field_name: str, value: str | None) -> str | None:
    if value is None:
        return None
    if field_name in {"survey_number", "khasra_number", "khata_number", "mutation_number", "registration_number"}:
        return normalize_number(value)
    if field_name in {"mutation_date", "registration_date"}:
        return parse_date(value)
    if field_name in {"owner_name", "father_or_husband_name", "village", "tehsil", "district", "state"}:
        return normalize_name(value)
    if field_name == "plot_area":
        v, u, sqm = parse_area(value)
        return f"{v} {u}".strip() if v is not None else value
    if field_name == "record_year":
        return to_ascii_digits(value)
    return value.strip()
