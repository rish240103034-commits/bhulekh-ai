# Changelog

All notable changes to Bhulekh-AI are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project aims to follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Frontend refresh-token integration.** The API client stores the refresh token, and on
  a `401` transparently calls `POST /auth/refresh` once (via a single shared in-flight
  refresh, so a burst of 401s triggers only one rotation) and retries the original request.
  Sign-out calls `POST /auth/logout` to revoke the refresh token server-side.
- **First-admin bootstrap CLI** (`python -m app.cli create-admin`) for production, where
  demo seeding is disabled. Supports `--reset-password`, reads the password from
  `BHULEKH_ADMIN_PASSWORD` or an interactive prompt (never from argv), and enforces the
  password policy.
- **Security test suite** (`tests/test_security.py`): token-type confusion rejected,
  refresh rotation single-use, logout/deactivation revoke refresh tokens, password policy,
  unauthenticated `401`s, forged-token rejection, security headers, and body-size limit.
- Documentation set: `ARCHITECTURE.md`, `SECURITY.md`, `CONTRIBUTING.md`,
  `CODE_OF_CONDUCT.md`, this `CHANGELOG.md`, and GitHub issue/PR templates.

### Changed
- Login page no longer pre-fills credentials; the demo-account hint is shown only in dev
  builds and lists the current seed passwords. Login inputs carry `autocomplete`
  attributes, and a rate-limited (`429`) sign-in shows a distinct message.
- CI workflows moved to their correct path under `.github/workflows/`.

## [1.1.0] — 2026-09-16 — Production hardening

### Added
- **Environment-aware configuration.** In `production` the app refuses to start with
  insecure defaults (weak `SECRET_KEY`, wildcard CORS/trusted-hosts, SQLite, demo seeding).
- **JWT access + refresh tokens** with a `type` claim and unique `jti`. Refresh tokens are
  stored hashed, single-use/rotated, and revoked on logout and account deactivation.
  New endpoints: `POST /auth/refresh`, `POST /auth/logout`.
- **Password policy** and **constant-time login** (dummy hash for unknown users) to defeat
  username enumeration by timing.
- **Rate limiting** (slowapi), **security-headers**, **body-size-limit**, and
  **request-context** middleware. **Upload hardening** (MIME/extension allow-list, size
  caps, filename sanitisation, UUID storage). Global exception handler that never leaks
  stack traces in production.
- **Alembic** migrations; **Docker** images (non-root backend with HEALTHCHECK,
  gunicorn+uvicorn; nginx frontend) and a PostgreSQL-by-default `docker-compose.yml`.
- Structured JSON logging; `/livez`, `/readyz`, and cached `/health` OCR diagnostics.
- CI (`ci.yml`) and CodeQL (`codeql.yml`) workflows; `ruff` config; pre-commit hooks.

### Changed
- **Demo credentials changed** (dev seed only): `admin / Admin@12345`,
  `verifier / Verify@12345`, `operator / Operate@12345`, `viewer / Viewer@12345`,
  `lrms / Lrms@12345`. The old `admin/admin123`-style logins no longer work.
- **Login response shape**: now returns `access_token`, `refresh_token`, `expires_in`,
  plus `role/username/full_name`.

### Migration notes
- A DB migration is required for the new `refresh_tokens` table and the
  `documents.diagnostics` column. Dev SQLite auto-handles it; for Postgres run
  `alembic upgrade head`.

## [1.0.0] — 2026-09-12 — Initial prototype

### Added
- Full stack built from scratch: FastAPI backend + OCR pipeline, React/Vite frontend,
  SIH documentation pack.
- Three cooperating OCR readers (ruled-table, region, full-page), multi-parcel data model
  (`LandParcel`), parcel-aware validation (`R09`–`R12`), and an editable parcel grid in the
  verification UI.
- Multilingual OCR (Tesseract `eng+hin+…`); validation rules `R01`–`R12`; confidence
  scoring with auto-verify / human-review routing; dashboards, audit trail, RBAC, and
  LRMS/DILRMP/GIS integration APIs.
- Fixed a Windows-only OCR bug: language packs are configured via `TESSDATA_PREFIX` rather
  than Tesseract's `--tessdata-dir` config string (which kept quotes on Windows and broke
  every language). Added a project-local tessdata option (`BHULEKH_TESSDATA_DIR`).

[Unreleased]: https://github.com/rish240103034-commits/bhulekh-ai/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/rish240103034-commits/bhulekh-ai/releases/tag/v1.1.0
[1.0.0]: https://github.com/rish240103034-commits/bhulekh-ai/releases/tag/v1.0.0
