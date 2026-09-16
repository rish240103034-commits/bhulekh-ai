# Contributing to Bhulekh-AI

Thanks for your interest in improving Bhulekh-AI. This guide covers how to set up a dev
environment, the conventions the codebase follows, and how to get a change merged.

By participating you agree to abide by our [Code of Conduct](CODE_OF_CONDUCT.md).

## Getting started

See [`README.md`](README.md) for full setup. In short:

```bash
# Backend (dev, SQLite)
cd backend
python -m venv .venv && .venv\Scripts\activate      # Windows (use source .venv/bin/activate on *nix)
pip install -r requirements.txt -r requirements-dev.txt
python samples/generate_samples.py                    # demo scans
uvicorn app.main:app --reload --port 8000             # http://localhost:8000/docs

# Frontend
cd frontend
npm ci && npm run dev                                 # http://localhost:5173
```

You need **Tesseract** (with `hin`/`mar` packs) and **Poppler**. If a document extracts
nothing, open `http://localhost:8000/health` — it names the missing language pack. For a
no-admin install, drop `eng.traineddata` + `hin.traineddata` in a folder and set
`BHULEKH_TESSDATA_DIR` to it.

## Before you open a pull request

Run the same gates CI does:

```bash
# Backend
cd backend
ruff check .                                          # lint — must be clean
python samples/generate_samples.py && pytest          # tests — must pass
alembic upgrade head                                  # if you changed the schema

# Frontend
cd frontend
npm run build                                         # must build
```

We recommend installing the pre-commit hooks so this happens automatically:

```bash
pip install pre-commit && pre-commit install
```

The hooks run `ruff`, `gitleaks` (secret scanning), and file-hygiene checks.

## Conventions

**Backend**
- FastAPI, SQLAlchemy 2 with typed `Mapped[...]`, Pydantic v2.
- Lint/format with `ruff` (config in `backend/pyproject.toml`).
- Settings are env-driven with the `BHULEKH_` prefix (`app/core/config.py`). Never
  hard-code a secret or a path.
- Schema changes go through **Alembic** — generate and commit a migration; don't rely on
  the SQLite `create_all()` dev convenience.
- **Every state-changing action must be written to `audit_logs`** (`services/audit.py`).
- Add a validation rule = one `@rule("R##_name")` function in `services/validation.py`.
- Add a table-column mapping = extend `COLUMN_LEXICON` in `pipeline/table.py`.
- Add a test for new behaviour. Security-relevant changes belong in
  `tests/test_security.py`.

**Frontend**
- React 18 + Vite. Keep the API surface in `src/api/client.js`; don't scatter `axios`
  calls or `localStorage` token handling across components.

**Commits & PRs**
- Use clear, imperative commit messages; [Conventional Commits](https://www.conventionalcommits.org/)
  prefixes (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`) are appreciated.
- Keep a PR focused on one change. Fill in the pull-request template, describe how you
  tested, and note any schema migration or config change.
- Update [`CHANGELOG.md`](CHANGELOG.md) under *Unreleased* for user-facing changes.

## Reporting bugs & requesting features

Use the GitHub issue templates. For anything security-related, **do not** open a public
issue — follow [`SECURITY.md`](SECURITY.md) instead.
