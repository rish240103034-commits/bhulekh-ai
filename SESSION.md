# SESSION.md — Bhulekh-AI build log & handoff

> Living handoff document. Each working session appends a dated entry. A new
> Claude Code (or human) session should read this file first, then `README.md`
> and `ARCHITECTURE.md`, to pick up with full context.

- **Project:** Bhulekh-AI — Intelligent Land Record Digitization & Validation System
- **For:** Smart India Hackathon 2026 · Problem Statement 26018 · Ministry of Rural
  Development / DoLR · Theme: Smart Automation
- **Repo layout:** `backend/` (FastAPI + OCR), `frontend/` (React/Vite), `docs/`
  (SIH documentation pack), root `docker-compose.yml`.
- **Local project folder:** `D:\dIGITIZATIONLANFDRECORDS`
- **GitHub target:** `https://github.com/rish240103034-commits/bhulekh-ai` (public)

---

## Current status (as of 2026-09-16)

Working, tested prototype **plus** a production-hardening pass, now with the frontend auth
integration, the full documentation set, a security test suite, and a first-admin CLI all
landed. Backend test suite: **28 passing** (13 end-to-end + 15 security); `ruff check`
clean; frontend builds. The app runs offline on commodity hardware.

What the system does end-to-end: ingest scanned/hand-written/PDF land records →
image enhancement → multilingual OCR (Tesseract, eng+hin…) → **three cooperating
readers** (ruled-table reader for khasra/khatauni grids, region reader for
header/footer label:value bands, full-page OCR for searchable text) → field
extraction + normalisation → validation (R01–R12, incl. parcel-area reconciliation)
→ confidence scoring → auto-verify (≥85 %) or human review → verified `LandRecord`
+ one `LandParcel` per plot. Verifier corrections feed a learning loop. Dashboards,
audit trail, RBAC, and LRMS/DILRMP/GIS integration APIs are all present.

**Proven on a real document:** a genuine 1987 handwritten khasra sheet (Kanpur
Dehat, village Bilhaur) — all 6 parcel rows read exactly (36/36 cells incl. Hindi
names), header fields recovered, areas reconciled, 2 low-confidence fields correctly
flagged for review.

---

## Session log

### 2026-09-12 — core build + real-document table extraction
- Built the full stack from scratch (FastAPI backend, React frontend, docs pack).
- Diagnosed why a real khasra sheet extracted nothing: it is a **table** (6 plots)
  and the form uses **dash** separators, not colons. Built `pipeline/table.py`
  (grid detection from column rules, ink-band row recovery, per-cell OCR, digit
  whitelist for numeric columns, closed-vocabulary snapping, blank-cell skip) and
  `pipeline/regions.py` (gutter-split header/footer OCR). Added multi-parcel data
  model (`LandParcel`), parcel-aware validation (R09–R12), editable parcel grid in
  the verification UI.
- Fixed a Windows-only OCR bug: language packs were passed via Tesseract's
  `--tessdata-dir` inside pytesseract's config string, which keeps quotes on Windows
  → every language failed to load. Switched to `TESSDATA_PREFIX`; moved external-tool
  configuration into `config.py` so it is import-order-independent. Added a
  project-local tessdata option (`BHULEKH_TESSDATA_DIR`) for no-admin installs.
- Added `/health` OCR diagnostics that *exercise* each language pack, per-document
  extraction diagnostics, and an upload-time language-pack warning in the UI.

### 2026-09-16 — production hardening + repo scaffolding (THIS SESSION)
Goal: make it production-ready, add official docs, and push to GitHub.

**Security (`app/core/`, `app/api/auth.py`)**
- Environment-aware config (`development|staging|production`). In **production** the
  app *refuses to start* with insecure defaults: placeholder/weak `SECRET_KEY`,
  wildcard CORS or trusted-hosts, SQLite, or demo-seeding enabled
  (`config.py::_enforce_production_safety`).
- **JWT access + refresh tokens** with a `type` claim and unique `jti`. Refresh
  tokens are stored **hashed** (`refresh_tokens` table), are **single-use / rotated**,
  and are revoked on logout and on account deactivation. Access tokens short-lived
  (30 min default).
- **Password policy** (length + character classes) on user creation;
  **constant-time** login verification (dummy hash for unknown users) to avoid
  username enumeration by timing.
- **Rate limiting** (slowapi): global default + tighter limits on login/refresh and
  upload. Toggle via `BHULEKH_RATE_LIMIT_ENABLED` (off in tests).
- **Security headers** middleware (CSP, X-Content-Type-Options, X-Frame-Options,
  Referrer-Policy, Permissions-Policy, COOP/CORP, optional HSTS), **body-size limit**
  middleware, and a **request-context** middleware (request id + structured access log).
- **Upload hardening**: MIME allow-list + extension whitelist, per-file and
  per-request size caps, filename sanitisation, stored under UUID (no path traversal),
  max files per request, empty-file rejection.
- Global exception handler that never leaks stack traces in production (opaque error +
  request id, full detail logged).

**Infra**
- **Alembic** migrations wired to app settings + models; initial migration generated
  and verified to build the full schema from empty. (SQLite dev convenience via
  `create_all` still exists for non-production.)
- **Docker**: backend image runs as non-root user, has a HEALTHCHECK, uses
  gunicorn+uvicorn workers; frontend multi-stage build → nginx with security headers,
  asset caching, API reverse-proxy. `docker-compose.yml` now defaults to
  **PostgreSQL + production env**, runs `alembic upgrade head` on start, and requires
  a real secret (`.env.docker.example` provided).
- Structured JSON logging (`app/core/logging.py`).

**CI / quality**
- GitHub Actions `ci.yml` (ruff lint → alembic upgrade check → pytest+coverage →
  frontend build → docker build) and `codeql.yml` (Python + JS security scan).
- `ruff` config in `backend/pyproject.toml`; `.pre-commit-config.yaml`
  (ruff, gitleaks, hygiene hooks). Lint is currently **clean**.

**Docs / repo**
- `LICENSE` (MIT), expanded `.gitignore`, this `SESSION.md`.
- Still to finish this session (see "Next steps"): `ARCHITECTURE.md`, `SECURITY.md`,
  `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `CHANGELOG.md`, `.github/` issue/PR
  templates, and the frontend refresh-token integration + updated demo credentials.

### 2026-09-16 (continued) — finished the "Next steps" list (THIS SESSION)
Picked up from the Next-steps list below; items 1–4 are now done and verified.

- **Moved CI workflows** from the repo root to `.github/workflows/ci.yml` + `codeql.yml`
  (they had landed at root). Added `.github/pull_request_template.md` and
  `.github/ISSUE_TEMPLATE/{bug_report.md,feature_request.md,config.yml}`.
- **Frontend auth integration** (`frontend/src/api/client.js`): stores `refresh_token`,
  and on a 401 (for non-auth calls, once per request) transparently calls
  `POST /auth/refresh` and retries. A single shared in-flight refresh coalesces a burst of
  401s into one rotation (refresh tokens are single-use server-side, so parallel refreshes
  would revoke each other). Sign-out calls `POST /auth/logout`. Tokens are kept out of the
  stored `user` blob. `Login.jsx`: no pre-filled creds, dev-only demo hint with the new
  passwords, `autocomplete` attrs, and a distinct message on 429.
- **Security tests** (`backend/tests/test_security.py`, 15 tests): token-type confusion
  (access→refresh and refresh→bearer), tampered/forged-token rejection, refresh
  rotation/reuse, logout + deactivation revoke refresh tokens, password policy
  (parametrised), unauthenticated 401s, security headers present, body-size 413.
- **First-admin CLI** (`backend/app/cli.py`): `python -m app.cli create-admin
  --username … [--full-name …] [--role …] [--reset-password] [--no-input]`. Password from
  `BHULEKH_ADMIN_PASSWORD` or an interactive prompt — **never** from argv (shell
  history/process table). Enforces the password policy; writes an audit log. Verified
  end-to-end (weak rejected, strong created, duplicate guarded, reset works).
- **Docs**: `ARCHITECTURE.md`, `SECURITY.md` (threat model + the controls, cross-linked to
  the test suite), `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1),
  `CHANGELOG.md` (Keep a Changelog; 1.0.0 / 1.1.0 / Unreleased). Updated `README.md` (new
  demo creds, the create-admin bootstrap, doc links, test description) — note the seeded
  `bhulekh.db` in `backend/` still holds the OLD demo users; delete it to reseed.

**Still open:** the Postgres `alembic upgrade head` path was not run this session (no local
Postgres); the CI job covers the SQLite migration. Pushing to GitHub is still a manual step
(see "Pushing to GitHub" below) — this is not a git repo yet locally (`git init` needed).

---

## Important behaviour changes to know about

- **Demo credentials changed** (old `admin/admin123` etc. no longer valid). New seed
  users (dev only, `BHULEKH_SEED_DEMO_DATA=true`):
  `admin / Admin@12345`, `verifier / Verify@12345`, `operator / Operate@12345`,
  `viewer / Viewer@12345`, `lrms / Lrms@12345`.
- **Login response shape changed**: now returns `access_token`, `refresh_token`,
  `expires_in`, plus `role/username/full_name`. New endpoints: `POST /auth/refresh`,
  `POST /auth/logout`.
- **Health endpoints**: `/livez` (liveness), `/readyz` (readiness incl. DB), `/health`
  (OCR diagnostics, cached 60 s). The frontend reads `/health`.
- **A DB migration is required** because of the new `refresh_tokens` table and the
  `documents.diagnostics` column. In dev SQLite this is auto-handled; for Postgres run
  `alembic upgrade head`.

---

## How to run

### Backend (dev, SQLite)
```bash
cd backend
python -m venv .venv && .venv\Scripts\activate      # Windows
pip install -r requirements.txt -r requirements-dev.txt
python samples/generate_samples.py                    # demo scans (optional)
uvicorn app.main:app --reload --port 8000             # http://localhost:8000/docs
```
Requires Tesseract (with `hin`, `mar` packs) + Poppler. If a document extracts
nothing, open `http://localhost:8000/health` — it names the missing language pack.
No-admin language packs: put `eng.traineddata` + `hin.traineddata` in a folder and set
`BHULEKH_TESSDATA_DIR` to it (already used at `D:\dIGITIZATIONLANFDRECORDS\tessdata`).

### Frontend
```bash
cd frontend
npm ci && npm run dev                                 # http://localhost:5173
```

### Full stack (production-like, Docker)
```bash
cp .env.docker.example .env      # set BHULEKH_SECRET_KEY and POSTGRES_PASSWORD
docker compose up --build        # web :8080, api :8000 (internal), postgres
```

### Tests & lint
```bash
cd backend && python samples/generate_samples.py && pytest       # 13 tests
ruff check .
```

---

## Next steps (pick up here)

1. ~~Frontend auth integration~~ — **done** (2026-09-16 cont.). `client.js` rotates on 401.
2. ~~Finish the doc set~~ — **done**. All docs + `.github/` templates landed.
3. ~~Security tests~~ — **done**. `backend/tests/test_security.py`, 15 tests, passing.
4. ~~First-admin bootstrap for production~~ — **done**. `python -m app.cli create-admin`.
5. **Push to GitHub** — still to do (not a local git repo yet). See below.
6. **Verify the Postgres migration path on a real Postgres** (`alembic upgrade head`) — CI
   exercises the SQLite migration only. Also consider code-splitting the frontend bundle
   (Vite warns it is >500 kB).
7. **Reseed the dev DB** if you want the new demo passwords locally: delete
   `backend/bhulekh.db` (it still has the old seed users) and restart with
   `BHULEKH_SEED_DEMO_DATA=true`.

## Pushing to GitHub

The repo `https://github.com/rish240103034-commits/bhulekh-ai` already exists (empty,
public). From a machine with normal network access (e.g. your PC in Claude Code):

```bash
git init -b main         # if not already a git repo
git add -A
git commit -m "feat: production-hardened Bhulekh-AI (SIH 2026 PS 26018)"
git remote add origin https://github.com/rish240103034-commits/bhulekh-ai.git
git push -u origin main  # authenticate with your GitHub PAT when prompted
```

> Note: the Cowork cloud sandbox could push via git (token worked for git), but the
> Anthropic proxy blocks GitHub's REST API, so the empty repo had to be created in the
> browser. From your own machine there is no such restriction. **Revoke any PAT pasted
> into chat** at https://github.com/settings/tokens once done.

---


## Files NOT synced to the local folder (create these locally) — RESOLVED

All four now exist locally: `.github/workflows/ci.yml` and `codeql.yml` (moved into
`.github/workflows/` this session), and `.pre-commit-config.yaml` + `.env.docker.example`
at the repo root.

## Conventions
- Backend: FastAPI, SQLAlchemy 2 (typed `Mapped[...]`), Pydantic v2, ruff (config in
  `backend/pyproject.toml`), migrations via Alembic. Settings are env-driven with the
  `BHULEKH_` prefix (`app/core/config.py`).
- Every state-changing action is written to `audit_logs`.
- Add a new validation rule = one `@rule("R##_name")` function in
  `app/services/validation.py`. Add a table column mapping = extend `COLUMN_LEXICON`
  in `app/pipeline/table.py`.
