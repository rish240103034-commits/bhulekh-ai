"""Integration APIs for LRMS / DILRMP / GIS / other government platforms.

* GET  /records                 -> paginated verified records (JSON) with filters
* GET  /records/{id}            -> single record
* GET  /records/export.csv      -> bulk CSV for LRMS import
* GET  /records/export.geojson  -> GeoJSON features (geometry attached when a cadastral centroid is known)
* POST /external/sync           -> push an LRMS/DILRMP snapshot into the cross-verification mirror
* GET  /lookup                  -> parcel lookup by village + khasra/survey (for citizen-service apps)
"""
from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user, require_roles
from app.models.entities import ExternalRecord, LandRecord, User
from app.schemas.schemas import RecordOut
from app.services.audit import log_action

router = APIRouter(prefix="/integration", tags=["integration"])

RECORD_COLS = ["id", "document_id", "state", "district", "tehsil", "village", "survey_number", "khasra_number",
               "khata_number", "plot_area_value", "plot_area_unit", "plot_area_sqm", "land_classification",
               "owner_name", "father_or_husband_name", "ownership_type", "mutation_number", "mutation_date",
               "registration_number", "registration_date", "record_year", "is_verified", "verified_at"]


def _query(db: Session, verified_only: bool, state, district, village):
    q = db.query(LandRecord)
    if verified_only:
        q = q.filter(LandRecord.is_verified.is_(True))
    if state:
        q = q.filter(LandRecord.state == state)
    if district:
        q = q.filter(LandRecord.district == district)
    if village:
        q = q.filter(LandRecord.village.ilike(village))
    return q


@router.get("/records", response_model=list[RecordOut])
def records(verified_only: bool = True, state: str | None = None, district: str | None = None,
            village: str | None = None, page: int = 1, size: int = Query(100, le=1000),
            db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _query(db, verified_only, state, district, village).offset((page - 1) * size).limit(size).all()


@router.get("/records/export.csv")
def export_csv(state: str | None = None, district: str | None = None, db: Session = Depends(get_db),
               user: User = Depends(get_current_user)):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(RECORD_COLS)
    for r in _query(db, True, state, district, None).yield_per(500):
        w.writerow([getattr(r, c) for c in RECORD_COLS])
    log_action(db, user.id, user.username, "integration.export_csv", "land_record", None, {"state": state})
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=land_records.csv"})


@router.get("/records/export.geojson")
def export_geojson(state: str | None = None, district: str | None = None, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    feats = []
    for r in _query(db, True, state, district, None).all():
        geom = (r.extra or {}).get("geometry")  # populated when linked to a cadastral map / GIS layer
        props = {c: getattr(r, c) for c in RECORD_COLS if c not in ("verified_at",)}
        feats.append({"type": "Feature", "geometry": geom, "properties": props})
    return {"type": "FeatureCollection", "features": feats}


@router.get("/lookup", response_model=list[RecordOut])
def lookup(village: str, khasra_number: str | None = None, survey_number: str | None = None,
           db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    q = db.query(LandRecord).filter(LandRecord.village.ilike(village), LandRecord.is_verified.is_(True))
    if khasra_number:
        q = q.filter(LandRecord.khasra_number == khasra_number)
    if survey_number:
        q = q.filter(LandRecord.survey_number == survey_number)
    return q.all()


class ExternalIn(BaseModel):
    source: str = "LRMS"
    state: str | None = None
    district: str | None = None
    village: str | None = None
    khasra_number: str | None = None
    survey_number: str | None = None
    khata_number: str | None = None
    owner_name: str | None = None
    plot_area_sqm: float | None = None


@router.post("/external/sync", status_code=201)
def sync_external(rows: list[ExternalIn], db: Session = Depends(get_db),
                  user: User = Depends(require_roles("admin", "integration"))):
    if not rows:
        raise HTTPException(400, "No rows")
    for r in rows:
        db.add(ExternalRecord(**r.model_dump()))
    db.commit()
    log_action(db, user.id, user.username, "integration.external_sync", "external_record", None, {"rows": len(rows)})
    return {"inserted": len(rows)}
