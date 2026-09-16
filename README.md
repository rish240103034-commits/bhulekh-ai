# Bhulekh-AI — Intelligent Land Record Digitization & Validation System

**Smart India Hackathon 2026 · Problem Statement 26018 · Ministry of Rural Development / Dept. of Land Resources (DoLR) · Theme: Smart Automation**

An AI-powered platform that converts scanned, handwritten, multilingual and legacy-PDF land records into
validated, structured digital records — with confidence scoring, business-rule and cross-database validation,
a human-in-the-loop verification workflow, a learning loop, audit trails, dashboards, RBAC and integration APIs
for LRMS / DILRMP / GIS.

```
                          ┌─ table reader ──► one LandParcel per khasra row
                          │   (grid detect, per-cell OCR, digit whitelist,
scan / PDF ──► Preprocess ┤    closed-vocabulary snapping, blank-cell skip)
               (OpenCV)   │
                          ├─ region reader ─► header/footer label:value fields
                          │   (gutter split, upscaled OCR, digit refinement)
                          │
                          └─ full-page OCR ─► searchable text of record
                                   │
                                   ▼
            Normalise ──► Validate ──► auto-verify (≥85 %) or human review ──► verified record
         (units, dates,  (R01–R12: rules, parcel                    │
          Devanagari      totals, duplicates,                       │
          digits)         LRMS cross-check)                         │
                   ▲                                                │
                   └────────── learning from verifier corrections ◄──┘
```

**Why three readers?** A khasra or khatauni sheet is not one block of text. It has a ruled
grid holding many plots (one row per khasra number, each with its own area, land type, crop
and cultivator) plus sparse label/value bands above and below it. A single OCR pass has to
choose one page-segmentation mode for all of that and reads the grid as running text, so the
column headers come out as if they were data and the rows are lost. Each reader is given only
the part of the page it suits.

## Repository layout

```
bhulekh-ai/
├── backend/                 FastAPI + SQLAlchemy + OpenCV + Tesseract
│   ├── app/
│   │   ├── api/             auth (JWT/RBAC), documents, dashboard/audit, integration
│   │   ├── core/            config, database, security
│   │   ├── models/          ORM: Document, ExtractedField, LandRecord, LandParcel, ValidationResult, AuditLog, FieldCorrection, ExternalRecord, User
│   │   ├── pipeline/        preprocess → ocr → table → regions → extract → normalize → learning → runner
│   │   └── services/        validation rules engine, stats, audit
│   ├── samples/             synthetic legacy-record generator (EN / HI / mixed, degraded, PDF)
│   ├── tests/               end-to-end API tests (pytest)
│   ├── Dockerfile · requirements.txt · .env.example
├── frontend/                React 18 + Vite + Recharts (login, upload, queue, verification, dashboards, audit, users, lookup)
├── docs/                    SIH documentation pack (architecture, tech table, scope table, feasibility, impact)
└── docker-compose.yml       Postgres + API + Web (nginx)
```

## Quick start (local, no Docker)

Prerequisites: Python 3.11+, Node 18+, Tesseract 5 with language packs, Poppler (for PDF).

```bash
# Ubuntu / Debian
sudo apt install tesseract-ocr tesseract-ocr-hin tesseract-ocr-mar tesseract-ocr-guj tesseract-ocr-ben \
                 tesseract-ocr-tam tesseract-ocr-tel tesseract-ocr-kan tesseract-ocr-mal poppler-utils
# Windows: install Tesseract from https://github.com/UB-Mannheim/tesseract/wiki (tick the Indian language packs)
#          and Poppler (https://github.com/oschwartz10612/poppler-windows/releases), add both to PATH.

cd backend
python -m venv .venv && . .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python samples/generate_samples.py                    # optional demo scans
uvicorn app.main:app --reload --port 8000             # API + Swagger at http://localhost:8000/docs

cd ../frontend
npm install && npm run dev                            # UI at http://localhost:5173
```

Demo accounts (dev seed only, `BHULEKH_SEED_DEMO_DATA=true`): `admin/Admin@12345`,
`verifier/Verify@12345`, `operator/Operate@12345`, `viewer/Viewer@12345`,
`lrms/Lrms@12345` (integration client).

> **Production** disables demo seeding. Bootstrap the first administrator with the CLI —
> the password is read from `BHULEKH_ADMIN_PASSWORD` (or prompted for), never from argv:
>
> ```bash
> BHULEKH_ADMIN_PASSWORD='<strong-password>' python -m app.cli create-admin --username admin --no-input
> ```
>
> Use `--reset-password` to reset a locked-out admin. See
> [`ARCHITECTURE.md`](ARCHITECTURE.md), [`SECURITY.md`](SECURITY.md), and
> [`CONTRIBUTING.md`](CONTRIBUTING.md) for design, threat model, and contribution guide.

### If documents come back empty, check the language packs first

Open **http://localhost:8000/health**. It exercises every installed pack rather than just
listing it, so a truncated download is reported too, and `hints` names what to do.

A missing Indic pack is the single most common cause of an empty extraction, and it does not
look like a missing pack: the page is found, the grid is found, but the Devanagari column
headers read as Latin gibberish, so no columns map and no rows are produced. The document's
**Extraction diagnostics** panel (under the raw OCR text) shows exactly that, and the upload
page warns before you upload. Install the pack with the Tesseract installer ("Additional
language data"), or without administrator rights by putting `<lang>.traineddata` and
`eng.traineddata` in a folder of your own and setting `BHULEKH_TESSDATA_DIR` to it.

## Quick start (Docker)

```bash
docker compose up --build       # web http://localhost:5173 · API http://localhost:8000/docs
```

Single-container image (SPA + API + OCR on one origin, used by the cloud deploy):

```bash
docker build -t bhulekh-ai .    # root Dockerfile: builds the React app, serves it from FastAPI
docker run -p 8000:8000 -e BHULEKH_SECRET_KEY=dev-only-key bhulekh-ai   # http://localhost:8000
```

## Deploy to the cloud (Render, one click)

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/rish240103034-commits/bhulekh-ai)

The repo ships a [`render.yaml`](render.yaml) Blueprint that provisions a free PostgreSQL
database plus one Docker web service. That service builds the [root `Dockerfile`](Dockerfile)
— which compiles the React SPA and serves it, the FastAPI API, and the OCR toolchain from a
**single origin** (so there is one public URL and no CORS). Steps:

1. Click the button above (or Render dashboard → **New → Blueprint**) and connect this repo.
2. Render reads `render.yaml`, generates the app secret, builds the image (installs
   Tesseract + Poppler), and gives you a live URL like `https://bhulekh-ai.onrender.com`.
3. Log in with a seeded demo account (see below) and try an upload.

It deploys in **staging** mode so the demo accounts are seeded and reviewers can log in
immediately; the security middleware (headers, rate limiting, hashed/rotated refresh
tokens, upload hardening) is active in every environment. For a hardened production
deployment set `BHULEKH_ENVIRONMENT=production` and `BHULEKH_SEED_DEMO_DATA=false`, then
bootstrap the first admin with `python -m app.cli create-admin`.

> Free Render instances sleep after ~15 min idle, so the first request after a lull takes
> ~30–50 s to wake; subsequent requests are fast.

## Run the tests

```bash
cd backend && python samples/generate_samples.py && pytest -q
```

The end-to-end suite covers authentication and RBAC, extraction from English and Hindi
sheets, multi-page PDFs, duplicate detection, the verification and learning loop, dashboards,
audit and the integration APIs — plus the multi-parcel khasra table: that a ruled sheet yields
one parcel per row with the right numbers, owners and areas (the unit is printed once in the
column header, so every row must still convert to square metres), that the parcel areas
reconcile with the total written on the sheet, and that a verifier can correct a row, add a
row the reader missed, and have both recorded in the audit trail and the learning store.

A dedicated security suite (`tests/test_security.py`) additionally asserts the auth
hardening: token-type confusion is rejected, refresh tokens rotate single-use, logout and
account deactivation revoke them, the password policy holds, protected endpoints reject
unauthenticated calls, forged tokens for non-existent users fail, the security headers are
present, and the body-size limit fires.

## How the requirements map to the code

| PS requirement | Where |
|---|---|
| Multilingual recognition (major Indian languages) | `pipeline/ocr.py` — Tesseract packs (hin, mar, guj, ben, tam, tel, kan, mal, pan, ori, urd) or EasyOCR; multi-pass language ordering; Devanagari digit normalisation |
| Extraction from scanned PDFs / images / historical docs | `pipeline/preprocess.py` (denoise, deskew, CLAHE, adaptive threshold, quality score), `pdf2image` for PDFs |
| Classification into predefined fields | `pipeline/extract.py` — multilingual lexicon, fuzzy label matching, per-field regex validators, doc-type detection |
| Multi-parcel tabular records (khasra / khatauni / 7-12 grids) | `pipeline/table.py` — grid detection from the column rules, row recovery from ink bands when the row rules are too faint, per-cell OCR, digit-whitelist pass for numeric columns, closed-vocabulary snapping for land type and crop, blank-cell detection; one `LandParcel` per row |
| Header/footer fields on a form | `pipeline/regions.py` — splits each band at its blank gutters and re-reads it upscaled, then re-reads numeric values with a digit-only model |
| Automated validation (rules, cross-DB, duplicates) | `services/validation.py` — R01…R12 rule engine, `ExternalRecord` LRMS/DILRMP mirror, SHA-256 & fuzzy duplicate detection, parcel-area reconciliation against the stated total, irrigation split, plausible dates |
| Confidence scoring + uncertain-field flagging | `extract._score` + quality & reliability factors in `pipeline/runner.py`; `needs_review` flag, thresholds in `core/config.py` |
| Human-assisted verification workflow | `POST /documents/{id}/verify`, `frontend/src/pages/Verify.jsx` (side-by-side scan, click-to-highlight, editable field list **and editable parcel grid** with add/remove row, approve/reject) |
| AI learning mechanism | `pipeline/learning.py` — corrections lexicon + per-field reliability recalibration from `FieldCorrection` |
| Integration with LRMS / DILRMP / GIS / cadastral maps | `api/integration.py` — JSON, CSV, GeoJSON exports, parcel lookup, external sync |
| Secure repository, metadata, audit trails | `storage/` + SHA-256, `AuditLog` on every action, `GET /audit` |
| Dashboards | `services/stats.py` + `pages/Dashboard.jsx` — processed, accuracy, status, pending, errors, state/district progress |
| APIs for government platforms | OpenAPI at `/docs`, bearer-token auth, `integration` role |
| Role-based access control | `core/security.py` — admin / verifier / operator / viewer / integration, jurisdiction scoping |

## Configuration (`backend/.env`)

| Variable | Default | Purpose |
|---|---|---|
| `BHULEKH_DATABASE_URL` | `sqlite:///./bhulekh.db` | Use `postgresql+psycopg2://…` in production |
| `BHULEKH_OCR_ENGINE` | `tesseract` | `easyocr` for better handwriting (pip install easyocr) |
| `BHULEKH_OCR_LANGUAGES` | `eng+hin` | Default pack combination |
| `BHULEKH_AUTO_ACCEPT_THRESHOLD` | `85` | Documents at/above this confidence with no rule *errors* auto-verify |
| `BHULEKH_REVIEW_THRESHOLD` | `60` | Fields below this are flagged for review |
| `BHULEKH_SECRET_KEY` | — | JWT signing key (change it!) |
| `BHULEKH_TESSERACT_CMD` | auto | Path to `tesseract.exe`; auto-detected in the default Windows install locations |
| `BHULEKH_POPPLER_PATH` | auto | Path to Poppler's `bin`; auto-detected on Windows (needed only for PDFs) |
| `BHULEKH_TESSDATA_DIR` | unset | Folder holding `*.traineddata`. Use it to keep language packs inside the project when `C:\Program Files` is not writable. It replaces Tesseract's search path, so it must also contain `eng.traineddata`. |

## Extending

* **New state-specific rule** → add a function decorated with `@rule("R09_…")` in `services/validation.py`.
* **New field / label** → add to `FIELD_LEXICON` (and a regex in `VALIDATORS`) in `pipeline/extract.py`.
* **Live LRMS/DILRMP API** → replace the `ExternalRecord` query inside rule `R08_cross_database` with an HTTP client.
* **New table column** → add its header spellings to `COLUMN_LEXICON` in `pipeline/table.py`; numeric columns go in `NUMERIC_COLUMNS`, closed-vocabulary ones in `VOCABULARIES`.
* **Handwriting** → set `BHULEKH_OCR_ENGINE=easyocr`, or plug a TrOCR / IndicOCR model into `pipeline/ocr.py` (same `OCRResult` contract).
* **Async at scale** → move `process_document` to a Celery/RQ worker; the API already queues via `BackgroundTasks`.
