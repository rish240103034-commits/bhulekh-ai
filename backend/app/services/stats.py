"""Dashboard aggregations."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import Integer, cast, func
from sqlalchemy.orm import Session

from app.models.entities import (
    DocStatus,
    Document,
    ExtractedField,
    FieldCorrection,
    ValidationResult,
)


def dashboard_stats(db: Session, state: str | None = None, district: str | None = None) -> dict:
    q = db.query(Document)
    if state:
        q = q.filter(Document.state == state)
    if district:
        q = q.filter(Document.district == district)
    docs = q.all()
    total = len(docs)
    by_status = {s.value: 0 for s in DocStatus}
    for d in docs:
        by_status[d.status.value] += 1
    processed = total - by_status["uploaded"] - by_status["processing"]
    done = by_status["auto_verified"] + by_status["verified"] + by_status["rejected"] + by_status["pending_review"]
    auto_rate = (by_status["auto_verified"] / done * 100) if done else 0.0

    conf_docs = [d.overall_confidence for d in docs if d.overall_confidence]
    avg_conf = sum(conf_docs) / len(conf_docs) if conf_docs else 0.0
    ms = [d.processing_ms for d in docs if d.processing_ms]
    avg_ms = sum(ms) / len(ms) if ms else 0.0

    # field accuracy = reviewed fields not corrected / reviewed fields
    reviewed_docs = [d.id for d in docs if d.status in (DocStatus.VERIFIED, DocStatus.REJECTED)]
    if reviewed_docs:
        n_fields = db.query(func.count(ExtractedField.id)).filter(ExtractedField.document_id.in_(reviewed_docs)).scalar()
        n_corr = db.query(func.count(FieldCorrection.id)).filter(FieldCorrection.document_id.in_(reviewed_docs)).scalar()
        accuracy = (1 - n_corr / n_fields) * 100 if n_fields else 0.0
    else:
        accuracy = 0.0

    def _count(col):
        out: dict[str, int] = {}
        for d in docs:
            k = getattr(d, col) or "unknown"
            out[k] = out.get(k, 0) + 1
        return out

    ids = [d.id for d in docs]
    errors = []
    if ids:
        rows = (db.query(ValidationResult.rule_id, ValidationResult.severity, func.count())
                .filter(ValidationResult.document_id.in_(ids), ValidationResult.passed.is_(False))
                .group_by(ValidationResult.rule_id, ValidationResult.severity).all())
        errors = [{"rule": r, "severity": s, "count": c} for r, s, c in rows]
        errors.sort(key=lambda e: -e["count"])

    low = []
    if ids:
        rows = (db.query(ExtractedField.field_name, func.avg(ExtractedField.confidence),
                         func.sum(cast(ExtractedField.needs_review, Integer)))
                .filter(ExtractedField.document_id.in_(ids)).group_by(ExtractedField.field_name).all())
        low = [{"field": f, "avg_confidence": round(float(a or 0), 1), "needs_review": int(n or 0)} for f, a, n in rows]
        low.sort(key=lambda x: x["avg_confidence"])

    def _geo(col):
        agg: dict[str, dict] = {}
        for d in docs:
            k = getattr(d, col) or "Unknown"
            a = agg.setdefault(k, {"name": k, "total": 0, "verified": 0, "pending": 0, "failed": 0})
            a["total"] += 1
            if d.status in (DocStatus.VERIFIED, DocStatus.AUTO_VERIFIED):
                a["verified"] += 1
            elif d.status == DocStatus.PENDING_REVIEW:
                a["pending"] += 1
            elif d.status == DocStatus.FAILED:
                a["failed"] += 1
        for a in agg.values():
            a["progress_pct"] = round(a["verified"] / a["total"] * 100, 1) if a["total"] else 0
        return sorted(agg.values(), key=lambda x: -x["total"])

    today = datetime.now(UTC).date()
    daily = []
    for i in range(13, -1, -1):
        day = today - timedelta(days=i)
        n = sum(1 for d in docs if d.created_at and d.created_at.date() == day)
        daily.append({"date": day.isoformat(), "documents": n})

    return {
        "total_documents": total,
        "processed": processed,
        "pending_review": by_status["pending_review"],
        "verified": by_status["verified"] + by_status["auto_verified"],
        "rejected": by_status["rejected"],
        "failed": by_status["failed"],
        "auto_verified_rate": round(auto_rate, 1),
        "avg_extraction_confidence": round(avg_conf, 1),
        "avg_field_accuracy": round(accuracy, 1),
        "avg_processing_ms": round(avg_ms),
        "status_breakdown": by_status,
        "doc_type_breakdown": _count("doc_type"),
        "language_breakdown": _count("language"),
        "error_statistics": errors,
        "by_state": _geo("state"),
        "by_district": _geo("district"),
        "low_confidence_fields": low,
        "daily_throughput": daily,
    }
