import hashlib
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal, get_db
from app.core.limiter import limit
from app.core.security import get_current_user, require_roles
from app.models.entities import DocStatus, Document, ExtractedField, FieldCorrection, LandParcel, User
from app.pipeline.normalize import normalize_field, parse_area
from app.pipeline.runner import _upsert_record, process_document
from app.schemas.schemas import DocumentDetail, DocumentOut, DocumentPage, VerifyIn
from app.services.audit import log_action
from app.services.validation import validate_document

router = APIRouter(prefix="/documents", tags=["documents"])
ALLOWED = {"image/png", "image/jpeg", "image/tiff", "application/pdf", "image/webp", "image/bmp"}
# Extension whitelist keyed to the accepted MIME types; anything else is stored as .bin.
ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".pdf", ".webp", ".bmp"}
MAX_FILES_PER_UPLOAD = 20


def _safe_extension(filename: str | None) -> str:
    """Return a whitelisted, lowercase extension; never trust the raw filename on disk."""
    ext = Path(filename or "").suffix.lower()
    return ext if ext in ALLOWED_EXTENSIONS else ".bin"


def _safe_filename(filename: str | None) -> str:
    """A display-only filename: strip any path components and cap the length."""
    name = Path(filename or "document").name.strip() or "document"
    return name[:256]


def _process_in_background(doc_id: str):
    db = SessionLocal()
    try:
        doc = db.get(Document, doc_id)
        if doc:
            process_document(db, doc)
    finally:
        db.close()


def _scope(q, user: User):
    """Operators/verifiers see only their jurisdiction; admin/viewer see all."""
    if user.role in ("operator", "verifier"):
        if user.state:
            q = q.filter(Document.state == user.state)
        if user.district:
            q = q.filter(Document.district == user.district)
    return q


@router.post("/upload", response_model=list[DocumentOut], status_code=201)
@limit(settings.rate_limit_upload)
async def upload(background: BackgroundTasks, request: Request, response: Response,
                 files: list[UploadFile] = File(...),
                 language: str = Form("eng+hin"), state: str | None = Form(None),
                 district: str | None = Form(None), doc_type: str = Form("unknown"),
                 sync: bool = Form(False), allow_duplicate: bool = Form(False),
                 db: Session = Depends(get_db), user: User = Depends(require_roles("operator"))):
    if len(files) > MAX_FILES_PER_UPLOAD:
        raise HTTPException(413, f"At most {MAX_FILES_PER_UPLOAD} files per upload")
    max_bytes = settings.max_upload_mb * 1024 * 1024
    created = []
    for f in files:
        if f.content_type not in ALLOWED:
            raise HTTPException(415, f"Unsupported file type {f.content_type} ({f.filename})")
        data = await f.read()
        if len(data) > max_bytes:
            raise HTTPException(413, f"{_safe_filename(f.filename)} exceeds "
                                     f"the {settings.max_upload_mb} MB limit")
        if not data:
            raise HTTPException(400, f"{_safe_filename(f.filename)} is empty")
        sha = hashlib.sha256(data).hexdigest()
        safe_name = _safe_filename(f.filename)
        # Byte-identical duplicate check: the same scan uploaded a second time is
        # almost always a mistake. Surface the existing doc's id so the frontend
        # can offer "open the existing document instead". Skip when the caller
        # explicitly asked (`allow_duplicate=true`) — useful for evaluation
        # datasets or when a downstream validation rule is what we're testing.
        if not allow_duplicate:
            existing = db.query(Document).filter(Document.sha256 == sha).first()
            if existing:
                raise HTTPException(409, {
                    "code": "duplicate_upload",
                    "detail": f"'{safe_name}' has already been uploaded on "
                              f"{existing.created_at.strftime('%Y-%m-%d %H:%M')}.",
                    "existing_document_id": existing.id,
                    "existing_status": existing.status.value,
                })
        doc = Document(original_filename=safe_name, stored_path="", mime_type=f.content_type,
                       sha256=sha, language=language, state=state or user.state,
                       district=district or user.district, doc_type=doc_type, uploaded_by=user.id)
        db.add(doc)
        db.flush()
        # Stored under the document's own UUID + a whitelisted extension, so a crafted
        # filename can neither traverse the filesystem nor collide with another upload.
        dest = Path(settings.upload_dir) / f"{doc.id}{_safe_extension(f.filename)}"
        dest.write_bytes(data)
        doc.stored_path = str(dest)
        db.commit()
        db.refresh(doc)
        log_action(db, user.id, user.username, "document.uploaded", "document", doc.id,
                   {"filename": safe_name, "sha256": sha},
                   request.client.host if request.client else None)
        if sync:  # CPU-bound work must never block the event loop
            await run_in_threadpool(process_document, db, doc)
        else:
            background.add_task(_process_in_background, doc.id)
        created.append(doc)
    return created


@router.get("/search")
def global_search(q: str, limit: int = Query(30, le=200),
                  db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """One search box across everything the system holds.

    Given a query, look for it in:
      * `LandRecord` fields (owner, village, khasra, khata, survey, tehsil, district),
      * per-document extracted fields (`ExtractedField.value`),
      * the raw OCR text (`Document.ocr_text`),
      * parcel rows (`LandParcel.parcel_number`, owner, crop, land_classification).

    Returns a homogenous list of {kind, doc_id, snippet, extra} so the UI can render
    one results table regardless of which layer produced the hit.
    """
    from sqlalchemy import or_
    from app.models.entities import ExtractedField, LandParcel, LandRecord

    like = f"%{q.strip()}%"
    if len(q.strip()) < 2:
        raise HTTPException(400, "Search query needs at least 2 characters")

    hits: list[dict] = []
    seen: set[str] = set()   # per-document dedupe so one doc doesn't spam the list

    def add(doc_id: str, kind: str, snippet: str, extra: dict | None = None) -> None:
        key = f"{doc_id}:{kind}"
        if doc_id in seen and kind != "ocr":
            return
        seen.add(doc_id)
        hits.append({"document_id": doc_id, "kind": kind,
                     "snippet": snippet[:180], "extra": extra or {}})

    # Records (owner / village / khasra / khata etc)
    rec_query = _scope(db.query(Document).join(LandRecord, LandRecord.document_id == Document.id), user)
    rec_hits = (rec_query.filter(or_(
        LandRecord.owner_name.ilike(like), LandRecord.father_or_husband_name.ilike(like),
        LandRecord.village.ilike(like), LandRecord.district.ilike(like),
        LandRecord.tehsil.ilike(like), LandRecord.state.ilike(like),
        LandRecord.khasra_number.ilike(like), LandRecord.khata_number.ilike(like),
        LandRecord.survey_number.ilike(like),
    )).limit(limit).all())
    for d in rec_hits:
        r = d.record
        add(d.id, "record",
            f"{r.village or '—'} · {r.owner_name or '—'} · khasra {r.khasra_number or '—'}",
            {"filename": d.original_filename, "status": d.status.value})

    # Parcels
    p_query = _scope(db.query(Document).join(LandParcel, LandParcel.document_id == Document.id), user)
    p_hits = (p_query.filter(or_(
        LandParcel.owner_name.ilike(like), LandParcel.parcel_number.ilike(like),
        LandParcel.crop.ilike(like), LandParcel.land_classification.ilike(like),
    )).limit(limit).all())
    for d in p_hits:
        for p in d.parcels:
            if any((p.owner_name or "").lower().find(q.lower()) >= 0
                   or (p.parcel_number or "").lower().find(q.lower()) >= 0
                   or (p.crop or "").lower().find(q.lower()) >= 0
                   for _ in [1]):
                add(d.id, "parcel",
                    f"parcel {p.parcel_number or '—'} · owner {p.owner_name or '—'} · {p.crop or ''}".strip(),
                    {"filename": d.original_filename, "parcel_row": p.row_index})
                break

    # Extracted fields
    ef_query = _scope(db.query(Document).join(ExtractedField, ExtractedField.document_id == Document.id), user)
    ef_hits = ef_query.filter(ExtractedField.value.ilike(like)).limit(limit).all()
    for d in ef_hits:
        matching = next((f for f in d.fields if f.value and q.lower() in f.value.lower()), None)
        if matching:
            add(d.id, "field",
                f"{matching.field_name} = {matching.value}",
                {"filename": d.original_filename})

    # Raw OCR text — coarse contains match. Windowed snippet.
    ocr_query = _scope(db.query(Document), user)
    ocr_hits = ocr_query.filter(Document.ocr_text.ilike(like)).limit(limit).all()
    for d in ocr_hits:
        idx = d.ocr_text.lower().find(q.lower()) if d.ocr_text else -1
        if idx < 0:
            continue
        start = max(0, idx - 60); end = min(len(d.ocr_text), idx + len(q) + 60)
        add(d.id, "ocr", f"…{d.ocr_text[start:end].strip()}…",
            {"filename": d.original_filename})

    return {"query": q, "hits": hits[:limit], "total": len(hits)}


@router.get("", response_model=DocumentPage)
def list_documents(page: int = 1, size: int = Query(20, le=200), status: str | None = None,
                   q: str | None = None, state: str | None = None, district: str | None = None,
                   db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    query = _scope(db.query(Document), user)
    if status:
        query = query.filter(Document.status == DocStatus(status))
    if state:
        query = query.filter(Document.state == state)
    if district:
        query = query.filter(Document.district == district)
    if q:
        query = query.filter(Document.original_filename.ilike(f"%{q}%") | Document.ocr_text.ilike(f"%{q}%"))
    total = query.count()
    items = query.order_by(Document.created_at.desc()).offset((page - 1) * size).limit(size).all()
    return DocumentPage(items=items, total=total, page=page, size=size)


@router.get("/{doc_id}", response_model=DocumentDetail)
def get_document(doc_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    doc = _scope(db.query(Document), user).filter(Document.id == doc_id).first()
    if not doc:
        raise HTTPException(404, "Document not found")
    log_action(db, user.id, user.username, "document.viewed", "document", doc.id)
    return doc


@router.get("/{doc_id}/file")
def get_file(doc_id: str, processed: bool = False, page: int = 1,
             db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    doc = _scope(db.query(Document), user).filter(Document.id == doc_id).first()
    if not doc:
        raise HTTPException(404, "Document not found")
    if processed:
        p = Path(settings.processed_dir) / f"{doc.id}_p{page}.png"
        if not p.exists():
            raise HTTPException(404, "Processed page not available")
        return FileResponse(p, media_type="image/png")
    return FileResponse(doc.stored_path, media_type=doc.mime_type, filename=doc.original_filename)


@router.post("/{doc_id}/reprocess", response_model=DocumentOut)
def reprocess(doc_id: str, db: Session = Depends(get_db), user: User = Depends(require_roles("operator"))):
    doc = db.get(Document, doc_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    log_action(db, user.id, user.username, "document.reprocess", "document", doc.id)
    return process_document(db, doc)


@router.delete("/{doc_id}", status_code=204)
def delete_document(doc_id: str, db: Session = Depends(get_db),
                    user: User = Depends(require_roles("admin"))):
    """Delete a document and every row derived from it. Admin only.

    Cascades take care of ExtractedField / ValidationResult / LandRecord /
    LandParcel. The stored file on disk is best-effort — if it's already
    gone (e.g. after a fresh deploy) we still complete the DB delete.
    """
    doc = db.get(Document, doc_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    stored = doc.stored_path
    log_action(db, user.id, user.username, "document.deleted", "document", doc.id,
               {"filename": doc.original_filename, "status": doc.status.value})
    db.delete(doc)
    db.commit()
    if stored:
        try:
            Path(stored).unlink(missing_ok=True)
        except OSError:
            pass
    return None


@router.post("/{doc_id}/verify", response_model=DocumentDetail)
def verify(doc_id: str, body: VerifyIn, request: Request, db: Session = Depends(get_db),
           user: User = Depends(require_roles("verifier"))):
    doc = _scope(db.query(Document), user).filter(Document.id == doc_id).first()
    if not doc:
        raise HTTPException(404, "Document not found")
    if doc.status not in (DocStatus.PENDING_REVIEW, DocStatus.AUTO_VERIFIED, DocStatus.EXTRACTED, DocStatus.VERIFIED):
        raise HTTPException(409, f"Document in status {doc.status.value} cannot be verified")

    existing = {f.field_name: f for f in doc.fields}
    changes = []
    for c in body.corrections:
        f = existing.get(c.field_name)
        if f is None:
            f = ExtractedField(document_id=doc.id, field_name=c.field_name, value=None, confidence=0.0,
                               source="human")
            db.add(f)
            existing[c.field_name] = f
            doc.fields.append(f)
        if (f.normalized_value or "") != (c.value or ""):
            db.add(FieldCorrection(document_id=doc.id, field_name=c.field_name, ocr_value=f.value,
                                   corrected_value=c.value, language=doc.language, doc_type=doc.doc_type))
            changes.append({"field": c.field_name, "from": f.normalized_value, "to": c.value})
        f.corrected_value = c.value
        f.normalized_value = normalize_field(c.field_name, c.value)
        f.corrected_by = user.id
        f.confidence = 100.0
        f.needs_review = False
        f.source = "human"
    if body.parcels is not None:
        changes.extend(_apply_parcel_edits(db, doc, body.parcels, user.id))
    db.flush()
    _upsert_record(db, doc)
    validate_document(db, doc)

    if body.decision == "approve":
        doc.status = DocStatus.VERIFIED
        doc.record.is_verified = True
        doc.record.verified_by = user.id
        doc.record.verified_at = datetime.now(UTC)
    else:
        doc.status = DocStatus.REJECTED
        doc.record.is_verified = False
    db.commit()
    log_action(db, user.id, user.username, f"document.{body.decision}d", "document", doc.id,
               {"corrections": changes, "remarks": body.remarks}, request.client.host if request.client else None)
    db.refresh(doc)
    return doc


PARCEL_EDITABLE = ("parcel_number", "land_classification", "crop", "owner_name",
                   "possessor_name", "father_name", "share", "remarks")


def _apply_parcel_edits(db: Session, doc: Document, edits, actor_id: str) -> list[dict]:
    """Apply verifier edits to the parcel table.

    Rows are matched by id; a row with no id is a parcel the verifier added by hand
    (a line the table reader missed), and a row the verifier removed is deleted.
    Every changed cell is recorded as a FieldCorrection so the learning loop sees it.
    """
    existing = {p.id: p for p in doc.parcels}
    kept: set[str] = set()
    changes: list[dict] = []
    # The unit is printed once in the table header, so a row the verifier retypes or
    # adds carries a bare figure and must inherit the unit the rest of the table uses.
    units = [p.area_unit for p in doc.parcels if p.area_unit]
    default_unit = max(set(units), key=units.count) if units else None

    for order, edit in enumerate(edits):
        parcel = existing.get(edit.id) if edit.id else None
        if parcel is None:
            parcel = LandParcel(document_id=doc.id, row_index=order, source="human", confidence=100.0)
            db.add(parcel)
            doc.parcels.append(parcel)
        kept.add(parcel.id)
        parcel.row_index = order

        for attr in PARCEL_EDITABLE:
            new = getattr(edit, attr, None)
            old = getattr(parcel, attr, None)
            if (new or None) != (old or None):
                db.add(FieldCorrection(document_id=doc.id, field_name=attr, ocr_value=old,
                                       corrected_value=new, language=doc.language, doc_type=doc.doc_type))
                changes.append({"parcel_row": order + 1, "field": attr, "from": old, "to": new})
                setattr(parcel, attr, new)

        if (edit.area_text or None) != (parcel.area_text or None):
            db.add(FieldCorrection(document_id=doc.id, field_name="area", ocr_value=parcel.area_text,
                                   corrected_value=edit.area_text, language=doc.language, doc_type=doc.doc_type))
            changes.append({"parcel_row": order + 1, "field": "area", "from": parcel.area_text,
                            "to": edit.area_text})
            parcel.area_text = edit.area_text
        parcel.area_value, parcel.area_unit, parcel.area_sqm = parse_area(parcel.area_text)
        if parcel.area_value is not None and parcel.area_sqm is None and default_unit:
            parcel.area_value, parcel.area_unit, parcel.area_sqm = parse_area(
                f"{parcel.area_value} {default_unit}")
        if changes and parcel.source != "human":
            parcel.source = "human"
        parcel.needs_review = False
        parcel.confidence = 100.0

    for pid, parcel in existing.items():
        if pid not in kept:
            changes.append({"parcel_row": parcel.row_index + 1, "field": "row", "from": parcel.parcel_number,
                            "to": None})
            doc.parcels.remove(parcel)
            db.delete(parcel)
    return changes


@router.delete("/{doc_id}", status_code=204)
def delete_document(doc_id: str, db: Session = Depends(get_db), user: User = Depends(require_roles("admin"))):
    doc = db.get(Document, doc_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    try:
        Path(doc.stored_path).unlink(missing_ok=True)
    except OSError:
        pass
    db.delete(doc)
    db.commit()
    log_action(db, user.id, user.username, "document.deleted", "document", doc_id)
