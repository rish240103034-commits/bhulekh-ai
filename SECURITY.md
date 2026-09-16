# Security Policy

Bhulekh-AI handles land-record documents — personal names, plot ownership, and
jurisdiction data — so security is a first-class concern. This document describes the
threat model, the controls in place, and how to report a vulnerability.

## Supported versions

This is a hackathon prototype under active development; security fixes are applied to the
`main` branch. There is no long-term-support branch yet.

## Reporting a vulnerability

**Please do not open a public issue for a security vulnerability.** Instead, use GitHub's
private **Security → Report a vulnerability** (private advisory) on the repository, or
contact the maintainers directly. Include:

- a description of the issue and its impact,
- steps to reproduce (a proof-of-concept if you have one),
- affected component/version.

We aim to acknowledge a report within a few days and to keep you updated as we work on a
fix. Please give us a reasonable window to remediate before any public disclosure.

---

## Threat model

**Assets:** land-record documents and extracted personal data; the verified record store;
the audit trail; operator/verifier/admin credentials; the LRMS/DILRMP integration.

**Trust boundaries:** the browser ↔ API boundary (untrusted client input); the API ↔
integration-client boundary (service accounts); file uploads (untrusted binary input).

**Adversaries considered:** an unauthenticated network attacker; a low-privilege
authenticated user attempting privilege escalation; a thief of a leaked token or database
dump; an uploader of malicious/oversized files; an attacker probing for valid usernames.

Out of scope for the prototype: a fully hardened host/OS, a WAF, secrets-manager
integration, and physical security — these belong to the deployment environment.

---

## Controls

### Authentication & session management
- **JWT access + refresh tokens.** Access tokens are short-lived (30 min default) and
  carry the role, so authorisation needs no DB read on the hot path. Refresh tokens are
  long-lived, **stored hashed** (SHA-256) in `refresh_tokens`, **single-use / rotated**,
  and **revoked** on logout and on account deactivation.
- **Token-type confusion prevented.** Every token carries a `type` claim (`access` /
  `refresh`) and a unique `jti`; a token presented to the wrong endpoint is rejected.
- **Constant-time login.** Verification runs even for unknown usernames (a dummy hash),
  so login timing does not reveal whether a username exists.
- **Password policy** (length + character classes) enforced on account creation and by the
  `create-admin` CLI.

### Authorisation
- **Role hierarchy** admin > verifier > operator > viewer, enforced in `require_roles`.
- **`integration` is a service role** granted only by explicit listing, never by rank, so a
  broad `require_roles("operator")` never accidentally admits a service account.

### HTTP hardening
- **Security-headers middleware:** Content-Security-Policy, `X-Content-Type-Options:
  nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy`, `Permissions-Policy`,
  COOP/CORP, and optional HSTS (enable only behind TLS). The `Server` header is stripped.
- **Body-size-limit middleware** rejects over-large request bodies before they are read.
- **Rate limiting** (slowapi): global default plus tighter limits on login/refresh and
  upload.
- **CORS / trusted-hosts** locked to configured origins/hostnames.

### Upload hardening
- MIME allow-list + extension whitelist, per-file and per-request size caps, filename
  sanitisation, storage under a UUID (no path traversal), a max-files-per-request cap, and
  empty-file rejection.

### Data protection & error handling
- Passwords hashed with `pbkdf2_sha256`; refresh tokens stored hashed.
- A **global exception handler never leaks stack traces in production** — the client gets
  an opaque error plus a request id, and the full detail is logged server-side.
- Every state-changing action is written to the append-only `audit_logs`.

### Fail-safe configuration
- In `production` the app **refuses to start** with insecure defaults: a placeholder/weak
  `SECRET_KEY`, wildcard CORS or trusted-hosts, a SQLite database, or demo seeding enabled
  (`config.py::_enforce_production_safety`).
- Demo seeding is disabled in production; bootstrap the first admin with
  `python -m app.cli create-admin` (see [`README.md`](README.md)).

### Supply chain & static analysis
- **CodeQL** (Python + JavaScript/TypeScript) runs in CI and weekly.
- **gitleaks** pre-commit hook to catch committed secrets; `ruff` lint gate.
- Dependencies are pinned in `backend/requirements*.txt` and `frontend/package-lock.json`.

---

## Verification

The controls above are exercised by `backend/tests/test_security.py`, which asserts:
token-type confusion is rejected, refresh rotation is single-use, logout and deactivation
revoke refresh tokens, the password policy holds, protected endpoints return 401
unauthenticated, forged tokens for non-existent users are rejected, security headers are
present, and the body-size limit fires. These run in CI on every push and pull request.

## Operator checklist for production

- [ ] Set a strong random `BHULEKH_SECRET_KEY` (`python -c "import secrets;
      print(secrets.token_urlsafe(48))"`).
- [ ] `BHULEKH_ENVIRONMENT=production`, real `BHULEKH_TRUSTED_HOSTS` and
      `BHULEKH_CORS_ORIGINS` (no `*`), PostgreSQL `BHULEKH_DATABASE_URL`,
      `BHULEKH_SEED_DEMO_DATA=false`.
- [ ] Terminate TLS in front of the app and set `BHULEKH_HSTS_ENABLED=true`.
- [ ] Run `alembic upgrade head`, then bootstrap the first admin via the CLI.
- [ ] Rotate any credential ever pasted into a chat, ticket, or log.
