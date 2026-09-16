"""AI-driven learning loop.

Every human correction is stored (FieldCorrection). From those we derive:
  * a per-field correction lexicon (OCR string -> corrected string) applied automatically
    when the same noisy value shows up again (exact or fuzzy match);
  * per-field accuracy statistics used to recalibrate confidence
    (a field that reviewers often fix gets its confidence scaled down).
This is deliberately lightweight (no GPU) so it runs in the API process; the same
tables can feed a periodic fine-tuning job for the OCR/NER models.
"""
from __future__ import annotations

from collections import defaultdict

from rapidfuzz import fuzz, process
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.entities import ExtractedField, FieldCorrection


class CorrectionLexicon:
    def __init__(self, db: Session):
        rows = db.query(FieldCorrection).filter(FieldCorrection.corrected_value.isnot(None)).all()
        self.map: dict[str, dict[str, str]] = defaultdict(dict)
        for r in rows:
            if r.ocr_value and r.corrected_value and r.ocr_value != r.corrected_value:
                self.map[r.field_name][r.ocr_value.strip().lower()] = r.corrected_value

    def apply(self, field_name: str, value: str | None) -> tuple[str | None, bool]:
        if not value or field_name not in self.map:
            return value, False
        key = value.strip().lower()
        if key in self.map[field_name]:
            return self.map[field_name][key], True
        cand = process.extractOne(key, list(self.map[field_name].keys()), scorer=fuzz.ratio, score_cutoff=92)
        if cand:
            return self.map[field_name][cand[0]], True
        return value, False


def field_reliability(db: Session) -> dict[str, float]:
    """Return per-field factor in [0.7, 1.0]: fraction of reviewed values that were NOT corrected."""
    total = dict(db.query(ExtractedField.field_name, func.count()).filter(
        ExtractedField.source != "human").group_by(ExtractedField.field_name).all())
    corrected = dict(db.query(FieldCorrection.field_name, func.count()).group_by(FieldCorrection.field_name).all())
    factors = {}
    for f, n in total.items():
        if n < 5:
            continue
        acc = 1 - corrected.get(f, 0) / n
        factors[f] = max(0.7, min(1.0, 0.7 + 0.3 * acc))
    return factors
