"""SQLAlchemy ORM models."""
from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(UTC)


class DocStatus(str, enum.Enum):
    UPLOADED = "uploaded"
    PROCESSING = "processing"
    EXTRACTED = "extracted"          # pipeline done, awaiting validation decision
    AUTO_VERIFIED = "auto_verified"  # high confidence + rules passed
    PENDING_REVIEW = "pending_review"
    VERIFIED = "verified"            # human approved
    REJECTED = "rejected"
    FAILED = "failed"


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(128), default="")
    hashed_password: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(32), default="operator")  # admin/verifier/operator/viewer/integration
    state: Mapped[str | None] = mapped_column(String(64), nullable=True)      # jurisdiction scoping
    district: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    original_filename: Mapped[str] = mapped_column(String(256))
    stored_path: Mapped[str] = mapped_column(String(512))
    mime_type: Mapped[str] = mapped_column(String(64))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    page_count: Mapped[int] = mapped_column(Integer, default=1)
    doc_type: Mapped[str] = mapped_column(String(64), default="unknown")  # ror/khasra/mutation/sale_deed/map
    language: Mapped[str] = mapped_column(String(32), default="eng+hin")
    state: Mapped[str | None] = mapped_column(String(64), nullable=True)
    district: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[DocStatus] = mapped_column(Enum(DocStatus), default=DocStatus.UPLOADED, index=True)
    overall_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    ocr_text: Mapped[str] = mapped_column(Text, default="")
    quality_score: Mapped[float] = mapped_column(Float, default=0.0)  # image quality 0-100
    processing_ms: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    diagnostics: Mapped[dict | None] = mapped_column(JSON, nullable=True)   # why a page read the way it did
    uploaded_by: Mapped[str | None] = mapped_column(String(32), ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    fields: Mapped[list[ExtractedField]] = relationship(back_populates="document", cascade="all, delete-orphan")
    validations: Mapped[list[ValidationResult]] = relationship(back_populates="document", cascade="all, delete-orphan")
    record: Mapped[LandRecord | None] = relationship(back_populates="document", uselist=False, cascade="all, delete-orphan")
    parcels: Mapped[list[LandParcel]] = relationship(back_populates="document", cascade="all, delete-orphan",
                                                       order_by="LandParcel.row_index")

    @property
    def parcel_count(self) -> int:
        return len(self.parcels)


class ExtractedField(Base):
    """One extracted key/value with confidence and provenance."""
    __tablename__ = "extracted_fields"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    document_id: Mapped[str] = mapped_column(String(32), ForeignKey("documents.id"), index=True)
    field_name: Mapped[str] = mapped_column(String(64), index=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalized_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(32), default="ocr")   # ocr | rule | ml | human
    page: Mapped[int] = mapped_column(Integer, default=1)
    bbox: Mapped[dict | None] = mapped_column(JSON, nullable=True)   # {x,y,w,h} on page for UI highlight
    corrected_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    corrected_by: Mapped[str | None] = mapped_column(String(32), nullable=True)

    document: Mapped[Document] = relationship(back_populates="fields")


class LandRecord(Base):
    """Canonical, structured land record produced after verification."""
    __tablename__ = "land_records"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    document_id: Mapped[str] = mapped_column(String(32), ForeignKey("documents.id"), unique=True)
    state: Mapped[str | None] = mapped_column(String(64), index=True)
    district: Mapped[str | None] = mapped_column(String(64), index=True)
    tehsil: Mapped[str | None] = mapped_column(String(64))
    village: Mapped[str | None] = mapped_column(String(128), index=True)
    survey_number: Mapped[str | None] = mapped_column(String(64), index=True)
    khasra_number: Mapped[str | None] = mapped_column(String(64), index=True)
    khata_number: Mapped[str | None] = mapped_column(String(64), index=True)
    plot_area_value: Mapped[float | None] = mapped_column(Float)
    plot_area_unit: Mapped[str | None] = mapped_column(String(16))
    plot_area_sqm: Mapped[float | None] = mapped_column(Float)
    land_classification: Mapped[str | None] = mapped_column(String(64))
    owner_name: Mapped[str | None] = mapped_column(String(256), index=True)
    father_or_husband_name: Mapped[str | None] = mapped_column(String(256))
    ownership_type: Mapped[str | None] = mapped_column(String(64))
    mutation_number: Mapped[str | None] = mapped_column(String(64))
    mutation_date: Mapped[str | None] = mapped_column(String(32))
    registration_number: Mapped[str | None] = mapped_column(String(64))
    registration_date: Mapped[str | None] = mapped_column(String(32))
    record_year: Mapped[str | None] = mapped_column(String(16))
    extra: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    verified_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    document: Mapped[Document] = relationship(back_populates="record")


class LandParcel(Base):
    """One row of a khasra / khatauni table: a single plot inside the holding.

    A khata (holding) commonly covers several khasra numbers, each with its own area,
    land type, crop and cultivator, so a document yields many parcels while the
    document-level LandRecord carries the shared header (village, tehsil, khata no.).
    """
    __tablename__ = "land_parcels"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    document_id: Mapped[str] = mapped_column(String(32), ForeignKey("documents.id"), index=True)
    row_index: Mapped[int] = mapped_column(Integer, default=0)

    parcel_number: Mapped[str | None] = mapped_column(String(64), index=True)   # khasra / survey no.
    area_text: Mapped[str | None] = mapped_column(String(64))
    area_value: Mapped[float | None] = mapped_column(Float)
    area_unit: Mapped[str | None] = mapped_column(String(32))
    area_sqm: Mapped[float | None] = mapped_column(Float)
    land_classification: Mapped[str | None] = mapped_column(String(64))
    crop: Mapped[str | None] = mapped_column(String(64))
    owner_name: Mapped[str | None] = mapped_column(String(256), index=True)
    possessor_name: Mapped[str | None] = mapped_column(String(256))
    father_name: Mapped[str | None] = mapped_column(String(256))
    share: Mapped[str | None] = mapped_column(String(64))
    remarks: Mapped[str | None] = mapped_column(Text)

    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    field_confidences: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    bbox: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    page: Mapped[int] = mapped_column(Integer, default=1)
    source: Mapped[str] = mapped_column(String(16), default="table")           # table | human
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    document: Mapped[Document] = relationship(back_populates="parcels")


class ValidationResult(Base):
    __tablename__ = "validation_results"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    document_id: Mapped[str] = mapped_column(String(32), ForeignKey("documents.id"), index=True)
    rule_id: Mapped[str] = mapped_column(String(64))
    severity: Mapped[str] = mapped_column(String(16))   # error | warning | info
    passed: Mapped[bool] = mapped_column(Boolean)
    message: Mapped[str] = mapped_column(Text)
    field_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    document: Mapped[Document] = relationship(back_populates="validations")


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    actor_name: Mapped[str] = mapped_column(String(64), default="system")
    action: Mapped[str] = mapped_column(String(64), index=True)
    entity_type: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)


class FieldCorrection(Base):
    """Learning store: every human correction is kept to improve extraction over time."""
    __tablename__ = "field_corrections"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(String(32), index=True)
    field_name: Mapped[str] = mapped_column(String(64), index=True)
    ocr_value: Mapped[str | None] = mapped_column(Text)
    corrected_value: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str] = mapped_column(String(32), default="eng+hin")
    doc_type: Mapped[str] = mapped_column(String(64), default="unknown")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class ExternalRecord(Base):
    """Stub of an external LRMS / DILRMP dataset used for cross-database verification."""
    __tablename__ = "external_records"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32), default="LRMS")
    state: Mapped[str | None] = mapped_column(String(64))
    district: Mapped[str | None] = mapped_column(String(64))
    village: Mapped[str | None] = mapped_column(String(128), index=True)
    khasra_number: Mapped[str | None] = mapped_column(String(64), index=True)
    survey_number: Mapped[str | None] = mapped_column(String(64), index=True)
    khata_number: Mapped[str | None] = mapped_column(String(64))
    owner_name: Mapped[str | None] = mapped_column(String(256))
    plot_area_sqm: Mapped[float | None] = mapped_column(Float)


class RefreshToken(Base):
    """A refresh token issued to a user.

    Only the SHA-256 hash of the token is stored, so a database leak does not hand over
    live sessions. A token is usable only while its row exists and ``revoked`` is false
    and ``expires_at`` is in the future; logout, rotation and admin action all work by
    revoking rows.
    """
    __tablename__ = "refresh_tokens"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    jti: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), index=True)
    user_agent: Mapped[str | None] = mapped_column(String(256), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
