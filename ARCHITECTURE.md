# Architecture — Bhulekh-AI

Intelligent Land Record Digitization & Validation System · SIH 2026 · PS 26018
(Ministry of Rural Development / DoLR).

This document explains how the system is put together: the request/data flow, the OCR
pipeline, the data model, and the security and operational architecture. For a narrative
of *why* things are the way they are, see [`SESSION.md`](SESSION.md); for how to run it,
see [`README.md`](README.md).

---

## 1. System overview

```
                         ┌───────────────────────────────────────────────┐
   Browser (SPA)         │                 Backend (FastAPI)             │
 ┌───────────────┐  HTTPS │  ┌─────────┐   ┌──────────────┐   ┌─────────┐ │
 │ React + Vite  │───────▶│  │  API    │──▶│   Pipeline    │──▶│  DB     │ │
 │ nginx (prod)  │◀───────│  │ routers │   │ (OCR + rules) │   │ (SQLite │ │
 └───────────────┘        │  └─────────┘   └──────────────┘   │ /Postgres)│
                          │       │  Tesseract + Poppler (local, offline) │
                          └───────┼───────────────────────────────────────┘
                                  ▼
                     LRMS / DILRMP / GIS integration APIs
```

Everything runs **offline on commodity hardware**. OCR is local (Tesseract + Poppler);
no document ever leaves the deployment. The only outbound integration points are the
optional LRMS/DILRMP sync endpoints, which the operator drives.

- **Frontend** (`frontend/`): React 18 + Vite SPA. Served by nginx in production, which
  also reverse-proxies `/api` to the backend and adds its own security headers.
- **Backend** (`backend/`): FastAPI + SQLAlchemy 2 (typed `Mapped[...]`) + Pydantic v2.
  Serves the REST API, runs the OCR/validation pipeline, and owns the data model.
- **Database**: SQLite for zero-setup development; PostgreSQL in production (schema owned
  by Alembic migrations).

---

## 2. Request & data flow

An upload travels through the system as follows:

1. **Upload** (`api/documents.py`) — MIME + extension allow-list, per-file and
   per-request size caps, filename sanitisation, storage under a UUID (no path
   traversal). A `Document` row is created per file.
2. **Pipeline** (`pipeline/runner.py`) orchestrates, either inline (`sync=true`, used by
   tests and small batches) or in a background task:
   - **preprocess** (`preprocess.py`) — deskew, denoise, contrast/threshold; PDF pages
     rasterised via Poppler at `ocr_dpi`.
   - **OCR** (`ocr.py`) — Tesseract, multilingual (`eng+hin`, …), per language pack
     exercised at `/health` so a missing pack is diagnosed, not silently empty.
   - **three cooperating readers**:
     - `table.py` — ruled-table reader for khasra/khatauni grids (column-rule detection,
       ink-band row recovery, per-cell OCR, digit whitelist for numeric columns,
       closed-vocabulary snapping, blank-cell skip).
     - `regions.py` — header/footer band reader for `label:value` (and dash-separated)
       fields via gutter splitting.
     - full-page OCR for searchable text.
   - **extract + normalize** (`extract.py`, `normalize.py`) — field extraction, unit and
     script normalisation, date parsing (incl. Fasli years).
   - **learning** (`learning.py`) — verifier corrections are stored and re-applied to the
     same OCR value on reprocess (`source = "learned"`).
3. **Validation** (`services/validation.py`) — rules `R01`–`R12`, including cross-database
   match (`R08`), duplicate detection (`R06`), and parcel-area reconciliation
   (`R09`–`R12`). Each rule is one `@rule("R##_name")` function.
4. **Confidence & routing** — a document scoring **≥ `auto_accept_threshold` (85 %)** is
   auto-verified; otherwise it is routed to human review, with low-confidence fields
   flagged.
5. **Verification** (`api/documents.py`) — a verifier approves/rejects, edits fields and
   parcel rows; the result is a verified `LandRecord` + one `LandParcel` per plot. Edits
   feed the learning store. Every state change is written to `audit_logs`.
6. **Serve / integrate** — dashboards, parcel lookup, CSV/GeoJSON export, and the external
   sync endpoints.

---

## 3. The three-reader OCR strategy

Real land records are not one shape. The pipeline runs three readers and merges their
output rather than forcing one parser:

| Reader        | Handles                                   | Key technique                               |
|---------------|-------------------------------------------|---------------------------------------------|
| `table.py`    | Multi-plot khasra/khatauni **grids**      | Column-rule geometry → per-cell OCR; numeric-column digit whitelist; `COLUMN_LEXICON` snapping |
| `regions.py`  | Header/footer **label:value** bands       | Gutter split, dash *and* colon separators   |
| full-page OCR | Free text, searchability                  | Whole-page Tesseract                         |

This is why a genuine 1987 handwritten khasra sheet (6 plots) reads as six parcels with
header fields recovered, rather than as one flat blob or nothing at all.

Adding a new table column mapping = extend `COLUMN_LEXICON` in `pipeline/table.py`.

---

## 4. Data model (`models/entities.py`)

- **User** — `role` in {admin, verifier, operator, viewer, integration}; optional
  `state`/`district` jurisdiction scoping; `is_active`.
- **RefreshToken** — hashed token (`token_hash`), `jti`, `user_id`, `expires_at`,
  `revoked`; supports rotation and revocation.
- **Document** — the ingested file, its pages, status, and `diagnostics`.
- **ExtractedField** — one field with `source` (`ocr`/`human`/`learned`), `confidence`,
  raw and normalized values.
- **LandRecord** — the verified record (one per document).
- **LandParcel** — one plot/row of a multi-parcel sheet.
- **Validation** — one rule result (`rule_id`, `passed`, `severity`, `message`).
- **ExternalRecord** — the LRMS/DILRMP mirror used by cross-database validation and lookup.
- **AuditLog** — append-only record of every state-changing action.

---

## 5. Security architecture

See [`SECURITY.md`](SECURITY.md) for the full threat model. In brief:

- **AuthN** — JWT **access** (short-lived, carries role for DB-free authz on the hot
  path) + **refresh** tokens (long-lived, stored **hashed**, single-use/rotated, revocable
  on logout and deactivation). Every token carries a `type` claim + unique `jti`; a token
  presented at the wrong endpoint is rejected.
- **AuthZ** — role hierarchy admin > verifier > operator > viewer; `integration` is a
  service role granted only by explicit listing, never by rank (`security.py::require_roles`).
- **Login** — constant-time verification (dummy hash for unknown users) to defeat username
  enumeration by timing; password policy on account creation.
- **Transport/HTTP** — security-headers middleware (CSP, XFO, nosniff, Referrer-Policy,
  Permissions-Policy, COOP/CORP, optional HSTS), body-size-limit middleware, request-context
  middleware (request id + structured access log).
- **Uploads** — MIME + extension allow-list, size caps, filename sanitisation, UUID storage.
- **Fail-safe config** — in `production` the app **refuses to start** with insecure defaults
  (weak `SECRET_KEY`, wildcard CORS/trusted-hosts, SQLite, demo seeding on).
- **Rate limiting** — slowapi: global default + tighter limits on login/refresh and upload.

---

## 6. Operational architecture

- **Migrations** — Alembic owns the production schema (`alembic upgrade head`). SQLite dev
  uses `create_all()` + `ensure_sqlite_columns()` for zero-setup convenience.
- **First-admin bootstrap** — production disables demo seeding, so
  `python -m app.cli create-admin` creates (or `--reset-password` resets) the initial admin.
- **Containers** — backend image runs as non-root with a HEALTHCHECK, gunicorn + uvicorn
  workers; frontend is a multi-stage build → nginx. `docker-compose.yml` defaults to
  PostgreSQL + production env and runs migrations on start.
- **Observability** — structured JSON logging (`core/logging.py`); `/livez` (liveness),
  `/readyz` (readiness incl. DB), `/health` (OCR diagnostics, cached 60 s).
- **CI** — `.github/workflows/ci.yml` (ruff → alembic upgrade check → pytest+coverage →
  frontend build → docker build) and `codeql.yml` (Python + JS security scan).

---

## 7. Configuration

All settings are environment-driven with the `BHULEKH_` prefix (`core/config.py`). See
[`backend/.env.example`](backend/.env.example) for every key, and `.env.docker.example`
for the compose deployment. Key thresholds: `auto_accept_threshold` (85), `review_threshold`
(60), `duplicate_similarity_threshold` (90).
