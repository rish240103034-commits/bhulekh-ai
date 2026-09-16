import hashlib
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, Request, UploadFile
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
async def upload(background: BackgroundTasks, request: Request,
                 files: list[UploadFile] = File(...),
                 language: str = Form("eng+hin"), state: str | None = Form(None),
                 district: str | None = Form(None), doc_type: str = Form("unknown"),
                 sync: bool = Form(False),
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
