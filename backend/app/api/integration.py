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


@router.get("/records_flat")
def records_flat(verified_only: bool = False, state: str | None = None, district: str | None = None,
                 village: str | None = None, page: int = 1, size: int = Query(200, le=2000),
                 db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Per-parcel denormalized view of the records database.

    A khasra sheet or khatauni page carries one holding-level record and many parcels
    (one per khasra number). The canonical ``/records`` endpoint returns one row per
    holding, which is what LRMS/DILRMP expects. This endpoint fans a multi-parcel
    document out into one row PER PARCEL, each carrying the header fields (state,
    district, tehsil, village, owner…) of the parent record so a UI table can display
    each khasra as its own row without a client-side join.

    Documents with no parcels still appear once, as their header row.
    """
    records = _query(db, verified_only, state, district, village).offset((page - 1) * size).limit(size).all()
    out: list[dict] = []
    for r in records:
        doc = r.document
        header = {
            "record_id": r.id,
            "document_id": r.document_id,
            "original_filename": doc.original_filename if doc else None,
            "state": r.state, "district": r.district, "tehsil": r.tehsil, "village": r.village,
            "khasra_number": r.khasra_number, "khata_number": r.khata_number,
            "survey_number": r.survey_number,
            "owner_name": r.owner_name, "father_or_husband_name": r.father_or_husband_name,
            "plot_area_sqm": r.plot_area_sqm, "land_classification": r.land_classification,
            "record_year": r.record_year,
            "mutation_number": r.mutation_number, "mutation_date": r.mutation_date,
            "is_verified": r.is_verified, "verified_by": r.verified_by, "verified_at": r.verified_at,
        }
        parcels = list(doc.parcels) if doc else []
        if not parcels:
            out.append({**header, "row_kind": "record", "parcel_row": None,
                        "crop": None, "possessor_name": None})
            continue
        for idx, p in enumerate(parcels):
            out.append({
                **header,
                # parcel-specific overrides
                "row_kind": "parcel",
                "parcel_row": p.row_index if p.row_index is not None else idx,
                "parcel_id": p.id,
                "khasra_number": p.parcel_number or header["khasra_number"],
                "owner_name": p.owner_name or header["owner_name"],
                "father_or_husband_name": p.father_name or header["father_or_husband_name"],
                "possessor_name": p.possessor_name,
                "crop": p.crop,
                "land_classification": p.land_classification or header["land_classification"],
                "plot_area_sqm": p.area_sqm if p.area_sqm is not None else header["plot_area_sqm"],
            })
    return out


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


SUGGEST_FIELDS = ("state", "district", "tehsil", "village", "land_classification",
                  "ownership_type", "owner_name", "father_or_husband_name",
                  "khata_number", "record_year")


@router.get("/suggestions")
def suggestions(state: str | None = None, district: str | None = None,
                tehsil: str | None = None, village: str | None = None,
                limit: int = Query(15, le=50), db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    """Field-value suggestions drawn from the reviewer's own jurisdiction.

    When a reviewer opens a doc for a village that has been digitized before, the
    same tehsil/district/land-classification/ownership-type values almost always
    reapply. Returning distinct historical values per field, ranked by recency,
    lets the UI offer a dropdown for every missing field — the reviewer never
    types a place name a second time.
    """
    from sqlalchemy import desc, func

    out: dict[str, list[str]] = {}
    for field in SUGGEST_FIELDS:
        col = getattr(LandRecord, field)
        q = (db.query(col, func.max(LandRecord.created_at).label("recent"))
             .filter(col.isnot(None), col != ""))
        # A jurisdiction filter narrows the pool but is not required: if the same
        # village has never been digitized, a district-wide value is still useful.
        if state and field != "state":
            q = q.filter(LandRecord.state == state)
        if district and field not in ("state", "district"):
            q = q.filter(LandRecord.district == district)
        if tehsil and field == "village":
            q = q.filter(LandRecord.tehsil == tehsil)
        rows = (q.group_by(col).order_by(desc("recent")).limit(limit).all())
        out[field] = [r[0] for r in rows]
    return out


@router.get("/villages/{name}/summary")
def village_summary(name: str, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    """One-shot aggregate of every record in a village: owners, khasras, area.

    A demo-friendly consolidated view — a judge or officer can point at "पलवल /
    रतनपुरा" and see how much of that village is digitized, who owns what, and
    where the pending review backlog sits.
    """
    from sqlalchemy import func

    q = db.query(LandRecord).filter(LandRecord.village.ilike(name))
    records = q.all()
    if not records:
        raise HTTPException(404, f"No records for village {name!r}")

    parcels: list = []
    from app.models.entities import LandParcel  # avoid circular
    for r in records:
        if r.document and r.document.parcels:
            parcels.extend(r.document.parcels)

    owners_by_name: dict[str, dict] = {}
    for r in records:
        if r.owner_name:
            entry = owners_by_name.setdefault(
                r.owner_name, {"owner_name": r.owner_name, "records": 0,
                                "khasras": [], "total_area_sqm": 0.0})
            entry["records"] += 1
            if r.khasra_number:
                entry["khasras"].append(r.khasra_number)
            entry["total_area_sqm"] += r.plot_area_sqm or 0.0
    for p in parcels:
        if p.owner_name:
            entry = owners_by_name.setdefault(
                p.owner_name, {"owner_name": p.owner_name, "records": 0,
                                "khasras": [], "total_area_sqm": 0.0})
            entry["records"] += 1
            if p.parcel_number:
                entry["khasras"].append(p.parcel_number)
            entry["total_area_sqm"] += p.area_sqm or 0.0

    khasras_all = sorted({p.parcel_number for p in parcels if p.parcel_number}
                        | {r.khasra_number for r in records if r.khasra_number})
    total_area = sum((r.plot_area_sqm or 0.0) for r in records) + \
                 sum((p.area_sqm or 0.0) for p in parcels if p.area_sqm)

    return {
        "village": name,
        "state": records[0].state, "district": records[0].district, "tehsil": records[0].tehsil,
        "record_count": len(records),
        "parcel_count": len(parcels),
        "verified_count": sum(1 for r in records if r.is_verified),
        "pending_review_count": sum(1 for r in records if not r.is_verified),
        "khasra_count": len(khasras_all),
        "khasra_range": ({"first": khasras_all[0], "last": khasras_all[-1]}
                         if khasras_all else None),
        "total_area_sqm": round(total_area, 2),
        "total_area_ha": round(total_area / 10000, 4),
        "owners": sorted(owners_by_name.values(),
                         key=lambda x: -x["total_area_sqm"])[:50],
        "documents": [{"id": r.document_id,
                       "created_at": r.document.created_at.isoformat() if r.document else None,
                       "status": r.document.status.value if r.document else None,
                       "khasra": r.khasra_number, "owner": r.owner_name,
                       "confidence": r.document.overall_confidence if r.document else None}
                      for r in records],
    }


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
