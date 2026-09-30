from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---- Auth ----
class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int                      # access-token lifetime in seconds
    role: str
    username: str
    full_name: str


class RefreshIn(BaseModel):
    refresh_token: str = Field(min_length=10, max_length=4096)


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.\-]+$")
    password: str = Field(min_length=1, max_length=256)
    full_name: str = Field(default="", max_length=128)
    role: str = "operator"
    state: str | None = Field(default=None, max_length=64)
    district: str | None = Field(default=None, max_length=64)


class UserOut(ORM):
    id: str
    username: str
    full_name: str
    role: str
    state: str | None
    district: str | None
    is_active: bool


# ---- Documents ----
class FieldOut(ORM):
    id: str
    field_name: str
    value: str | None
    normalized_value: str | None
    confidence: float
    needs_review: bool
    source: str
    page: int
    bbox: dict | None
    corrected_value: str | None
    # Shadow-Mode: independent AI's reading for the same field, and how it
    # compared. Populated whenever the two extraction passes ran; the UI uses
    # these to render side-by-side and to flag "human decides" mismatches.
    shadow_value: str | None = None
    shadow_source: str | None = None
    shadow_agreement: str | None = None


class ValidationOut(ORM):
    rule_id: str
    severity: str
    passed: bool
    message: str
    field_name: str | None


class RecordOut(ORM):
    id: str
    state: str | None
    district: str | None
    tehsil: str | None
    village: str | None
    survey_number: str | None
    khasra_number: str | None
    khata_number: str | None
    plot_area_value: float | None
    plot_area_unit: str | None
    plot_area_sqm: float | None
    land_classification: str | None
    owner_name: str | None
    father_or_husband_name: str | None
    ownership_type: str | None
    mutation_number: str | None
    mutation_date: str | None
    registration_number: str | None
    registration_date: str | None
    record_year: str | None
    is_verified: bool
    verified_by: str | None
    verified_at: datetime | None


class ParcelOut(ORM):
    id: str
    row_index: int
    parcel_number: str | None
    area_text: str | None
    area_value: float | None
    area_unit: str | None
    area_sqm: float | None
    land_classification: str | None
    crop: str | None
    owner_name: str | None
    possessor_name: str | None
    father_name: str | None
    share: str | None
    remarks: str | None
    confidence: float
    needs_review: bool
    field_confidences: dict | None
    bbox: dict | None
    page: int
    source: str


class ParcelIn(BaseModel):
    id: str | None = None
    row_index: int = 0
    parcel_number: str | None = None
    area_text: str | None = None
    land_classification: str | None = None
    crop: str | None = None
    owner_name: str | None = None
    possessor_name: str | None = None
    father_name: str | None = None
    share: str | None = None
    remarks: str | None = None


class DocumentOut(ORM):
    id: str
    parcel_count: int = 0
    original_filename: str
    mime_type: str
    page_count: int
    doc_type: str
    language: str
    state: str | None
    district: str | None
    status: str
    overall_confidence: float
    quality_score: float
    processing_ms: int
    error: str | None
    diagnostics: dict | None = None
    created_at: datetime
    updated_at: datetime


class DocumentDetail(DocumentOut):
    ocr_text: str
    fields: list[FieldOut]
    validations: list[ValidationOut]
    record: RecordOut | None
    parcels: list[ParcelOut]


class DocumentPage(BaseModel):
    items: list[DocumentOut]
    total: int
    page: int
    size: int


# ---- Verification ----
class FieldCorrectionIn(BaseModel):
    field_name: str
    value: str | None


class VerifyIn(BaseModel):
    decision: str = Field(pattern="^(approve|reject)$")
    corrections: list[FieldCorrectionIn] = []
    parcels: list[ParcelIn] | None = None      # omit to leave parcels untouched
    remarks: str | None = None


# ---- Dashboard ----
class DashboardStats(BaseModel):
    total_documents: int
    processed: int
    pending_review: int
    verified: int
    rejected: int
    failed: int
    auto_verified_rate: float
    avg_extraction_confidence: float
    avg_field_accuracy: float
    avg_processing_ms: float
    status_breakdown: dict[str, int]
    doc_type_breakdown: dict[str, int]
    language_breakdown: dict[str, int]
    error_statistics: list[dict[str, Any]]
    by_state: list[dict[str, Any]]
    by_district: list[dict[str, Any]]
    low_confidence_fields: list[dict[str, Any]]
    daily_throughput: list[dict[str, Any]]


class AuditOut(ORM):
    id: int
    actor_name: str
    action: str
    entity_type: str
    entity_id: str | None
    detail: dict | None
    created_at: datetime
