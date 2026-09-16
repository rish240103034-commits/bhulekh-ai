"""Validation engine: business rules + cross-database verification + duplicate detection.

Rules are plain functions registered in RULES; each returns a list of (passed, severity, message, field).
Adding a state-specific rule = adding one function.
"""
from __future__ import annotations

import re
from collections.abc import Callable

from rapidfuzz import fuzz
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.entities import Document, ExternalRecord, LandRecord, ValidationResult

Outcome = tuple[bool, str, str, str | None]  # passed, severity, message, field
Rule = Callable[[Session, Document, LandRecord], list[Outcome]]
RULES: dict[str, Rule] = {}


def rule(rule_id: str):
    def deco(fn: Rule):
        RULES[rule_id] = fn
        return fn
    return deco


# ---------- Business rules ----------
@rule("R01_mandatory_fields")
def mandatory(db, doc, rec):
    out = []
    required = ["owner_name", "village"]
    identifier_any = ["survey_number", "khasra_number", "khata_number"]
    if doc.parcels:
        # On a multi-parcel sheet the owners live in the table, not the header.
        named = sum(1 for p in doc.parcels if p.owner_name)
        out.append((named == len(doc.parcels), "error",
                    f"{named} of {len(doc.parcels)} parcel rows have an owner name", "owner_name"))
        required = ["village"]
    for f in required:
        ok = bool(getattr(rec, f))
        out.append((ok, "error", f"Mandatory field '{f}' {'present' if ok else 'missing'}", f))
    ok = any(getattr(rec, f) for f in identifier_any)
    out.append((ok, "error", "At least one of survey/khasra/khata number " + ("present" if ok else "missing"),
                None))
    return out


@rule("R02_area_range")
def area_range(db, doc, rec):
    if rec.plot_area_sqm is None:
        return [(False, "warning", "Plot area missing or unit not recognised", "plot_area")]
    ok = 1.0 <= rec.plot_area_sqm <= 5_000_000  # 1 sqm .. 500 ha
    return [(ok, "error" if not ok else "info",
             f"Plot area {rec.plot_area_sqm} sqm {'within' if ok else 'outside'} plausible range", "plot_area")]


@rule("R03_identifier_format")
def identifier_format(db, doc, rec):
    out = []
    for f in ("survey_number", "khasra_number", "khata_number"):
        v = getattr(rec, f)
        if v is None:
            continue
        ok = v.replace("/", "").replace("-", "").isalnum() and any(ch.isdigit() for ch in v)
        out.append((ok, "warning", f"{f} '{v}' {'looks valid' if ok else 'has unexpected characters'}", f))
    return out


@rule("R04_date_consistency")
def date_consistency(db, doc, rec):
    out = []
    for f in ("mutation_date", "registration_date"):
        v = getattr(rec, f)
        if not v:
            continue
        ok = len(v) == 10 and v[:2] in ("18", "19", "20")
        out.append((ok, "warning", f"{f} '{v}' {'parsed to ISO date' if ok else 'could not be parsed'}", f))
    if rec.mutation_date and rec.registration_date and len(rec.mutation_date) == 10 == len(rec.registration_date):
        ok = rec.mutation_date >= rec.registration_date
        out.append((ok, "warning", "Mutation date should not precede registration date", "mutation_date"))
    return out


@rule("R05_low_confidence_fields")
def low_conf(db, doc, rec):
    low = [f.field_name for f in doc.fields if f.confidence < settings.review_threshold]
    if low:
        return [(False, "warning", f"Low-confidence fields require review: {', '.join(low)}", None)]
    return [(True, "info", "No field below review threshold", None)]


@rule("R06_duplicate_document")
def duplicate_document(db, doc, rec):
    dup = db.query(Document).filter(Document.sha256 == doc.sha256, Document.id != doc.id).first()
    if dup:
        return [(False, "error", f"Exact duplicate of document {dup.id} ({dup.original_filename})", None)]
    return [(True, "info", "No byte-identical duplicate", None)]


@rule("R07_duplicate_record")
def duplicate_record(db, doc, rec):
    """Near-duplicate: same village + same identifier + similar owner name."""
    if not rec.village:
        return []
    q = db.query(LandRecord).filter(LandRecord.document_id != doc.id, LandRecord.village == rec.village)
    for other in q.limit(500):
        same_id = any(getattr(rec, f) and getattr(rec, f) == getattr(other, f)
                      for f in ("khasra_number", "survey_number", "khata_number"))
        if not same_id:
            continue
        sim = fuzz.token_set_ratio(rec.owner_name or "", other.owner_name or "")
        if sim >= settings.duplicate_similarity_threshold:
            return [(False, "error", f"Possible duplicate record (doc {other.document_id}, owner similarity {sim:.0f}%)",
                     None)]
        return [(False, "warning", f"Same parcel identifier as doc {other.document_id} but different owner "
                                   f"(similarity {sim:.0f}%) — possible mutation/transfer", "owner_name")]
    return [(True, "info", "No duplicate parcel record", None)]


@rule("R08_cross_database")
def cross_database(db, doc, rec):
    """Cross-verify against LRMS/DILRMP mirror (ExternalRecord table; swap for a live API client)."""
    q = db.query(ExternalRecord)
    if rec.village:
        q = q.filter(ExternalRecord.village.ilike(rec.village))
    matches = []
    for ident in ("khasra_number", "survey_number"):
        v = getattr(rec, ident)
        if v:
            matches = q.filter(getattr(ExternalRecord, ident) == v).all()
            if matches:
                break
    if not matches:
        return [(False, "warning", "No matching record in LRMS/DILRMP mirror (new record or unmapped parcel)", None)]
    ext = matches[0]
    out = []
    sim = fuzz.token_set_ratio(rec.owner_name or "", ext.owner_name or "")
    out.append((sim >= 85, "warning", f"Owner name vs {ext.source}: '{ext.owner_name}' (similarity {sim:.0f}%)",
                "owner_name"))
    if rec.plot_area_sqm and ext.plot_area_sqm:
        diff = abs(rec.plot_area_sqm - ext.plot_area_sqm) / ext.plot_area_sqm
        out.append((diff <= 0.05, "warning", f"Area differs from {ext.source} by {diff*100:.1f}%", "plot_area"))
    if rec.khata_number and ext.khata_number:
        out.append((rec.khata_number == ext.khata_number, "warning",
                    f"Khata number vs {ext.source}: {ext.khata_number}", "khata_number"))
    return out


def validate_document(db: Session, doc: Document) -> list[ValidationResult]:
    db.query(ValidationResult).filter(ValidationResult.document_id == doc.id).delete()
    rec = doc.record
    results: list[ValidationResult] = []
    for rid, fn in RULES.items():
        try:
            outcomes = fn(db, doc, rec)
        except Exception as exc:  # a broken rule must never block processing
            outcomes = [(False, "warning", f"Rule crashed: {exc}", None)]
        for passed, sev, msg, field in outcomes:
            r = ValidationResult(document_id=doc.id, rule_id=rid, severity=sev, passed=passed,
                                 message=msg, field_name=field)
            db.add(r)
            results.append(r)
    db.flush()
    return results


# ---------- Parcel-level rules (multi-row khasra / khatauni sheets) ----------
def _sum_parcel_area(doc: Document) -> float | None:
    areas = [p.area_sqm for p in doc.parcels if p.area_sqm]
    return round(sum(areas), 2) if areas else None


def _stated_area_sqm(rec: LandRecord, key: str) -> float | None:
    """Header totals are stored as text in `extra`; convert to square metres."""
    from app.pipeline.normalize import parse_area

    raw = (rec.extra or {}).get(key) if rec else None
    if not raw:
        return None
    # These sheets quote hectares unless they say otherwise.
    value, unit, sqm = parse_area(raw if any(c.isalpha() for c in str(raw)) else f"{raw} ha")
    return sqm


@rule("R09_parcel_area_total")
def parcel_area_total(db, doc, rec):
    """The parcel areas must add up to the total written on the sheet."""
    if not doc.parcels:
        return []
    summed = _sum_parcel_area(doc)
    stated = _stated_area_sqm(rec, "total_area")
    if summed is None:
        return [(False, "warning", "No parcel area could be read, so the total cannot be checked", "area")]
    if stated is None:
        return [(True, "info", f"Parcel areas total {summed} sqm (no stated total to compare)", None)]
    diff = abs(summed - stated)
    tolerance = max(stated * 0.02, 50.0)       # 2%, with a floor for rounding in small holdings
    ok = diff <= tolerance
    return [(ok, "warning",
             f"Parcel areas total {summed} sqm vs stated total {stated} sqm "
             f"(difference {diff:.0f} sqm)", "area")]


@rule("R10_parcel_identifiers")
def parcel_identifiers(db, doc, rec):
    """Every parcel needs a well-formed, unique khasra/survey number."""
    if not doc.parcels:
        return []
    out = []
    numbers = [p.parcel_number for p in doc.parcels]
    missing = [p.row_index + 1 for p in doc.parcels if not p.parcel_number]
    out.append((not missing, "error",
                f"Parcel rows without a number: {missing}" if missing else "Every parcel row has a number",
                "parcel_number"))
    present = [n for n in numbers if n]
    duplicates = {n for n in present if present.count(n) > 1}
    out.append((not duplicates, "error",
                f"Duplicate parcel numbers on this sheet: {sorted(duplicates)}" if duplicates
                else "Parcel numbers are unique", "parcel_number"))
    malformed = [n for n in present if not n.replace("/", "").isdigit()]
    out.append((not malformed, "warning",
                f"Parcel numbers with unexpected characters: {malformed}" if malformed
                else "Parcel numbers are well formed", "parcel_number"))
    return out


@rule("R11_irrigation_split")
def irrigation_split(db, doc, rec):
    """Irrigated + unirrigated area should reconcile with the total."""
    total = _stated_area_sqm(rec, "total_area")
    irrigated = _stated_area_sqm(rec, "irrigated_area")
    unirrigated = _stated_area_sqm(rec, "unirrigated_area")
    if not (total and (irrigated or unirrigated)):
        return []
    split = (irrigated or 0) + (unirrigated or 0)
    diff = abs(split - total)
    ok = diff <= max(total * 0.02, 50.0)
    return [(ok, "warning",
             f"Irrigated + unirrigated = {split:.0f} sqm vs total {total:.0f} sqm "
             f"(difference {diff:.0f} sqm)", "irrigated_area")]


@rule("R12_plausible_dates")
def plausible_dates(db, doc, rec):
    """Catch impossible years — a common symptom of a misread handwritten digit."""
    from datetime import date

    out = []
    this_year = date.today().year
    for field_name, raw in (("record_date", (rec.extra or {}).get("record_date") if rec else None),
                            ("mutation_date", rec.mutation_date if rec else None),
                            ("registration_date", rec.registration_date if rec else None),
                            ("record_year", rec.record_year if rec else None)):
        if not raw:
            continue
        years = [int(y) for y in re.findall(r"\b([0-9]{4})\b", str(raw))]
        if not years:
            out.append((False, "warning", f"{field_name} '{raw}' has no recognisable year", field_name))
            continue
        bad = [y for y in years if y < 1850 or y > this_year]
        out.append((not bad, "warning",
                    f"{field_name} '{raw}' has an implausible year {bad}" if bad
                    else f"{field_name} year looks plausible", field_name))
    return out
