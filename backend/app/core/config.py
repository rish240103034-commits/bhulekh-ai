"""Application configuration.

All settings are environment-driven with the ``BHULEKH_`` prefix and have safe
local-development defaults. In production (``BHULEKH_ENVIRONMENT=production``) the
weak defaults that exist for convenience — the signing key, the default admin
password, permissive CORS — are refused at startup so a deployment cannot
accidentally ship with them.
"""
from __future__ import annotations

import secrets
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The literal placeholder that must never reach production.
INSECURE_SECRET = "change-me-in-production-please"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="BHULEKH_", extra="ignore",
                                      case_sensitive=False)

    # ---- Environment ----
    environment: Literal["development", "staging", "production"] = "development"
    app_name: str = "Bhulekh-AI | Intelligent Land Record Digitization & Validation"
    version: str = "1.1.0"
    api_v1_prefix: str = "/api/v1"

    # ---- Security / auth ----
    secret_key: str = INSECURE_SECRET
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7
    # Minimum password strength for user-created accounts.
    password_min_length: int = 10
    # Argon2/bcrypt are heavier; pbkdf2_sha256 is pure-python and dependency-free.
    password_scheme: str = "pbkdf2_sha256"

    # ---- Rate limiting ----
    rate_limit_enabled: bool = True
    rate_limit_default: str = "200/minute"      # applies to most endpoints
    rate_limit_login: str = "10/minute"         # brute-force protection on auth
    rate_limit_upload: str = "30/minute"        # heavy CPU-bound endpoint

    # ---- HTTP hardening ----
    # Comma-separated in the env var; JSON list also accepted.
    cors_origins: list[str] = Field(default=["http://localhost:5173", "http://127.0.0.1:5173"])
    trusted_hosts: list[str] = Field(default=["*"])   # locked down to real hostnames in prod
    max_upload_mb: int = 25
    max_request_body_mb: int = 30               # hard cap on any request body
    hsts_enabled: bool = False                  # enable only behind TLS

    # ---- Database ----
    # SQLite by default so the project runs with zero setup; Postgres in production.
    database_url: str = "sqlite:///./bhulekh.db"
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_recycle_s: int = 1800

    # ---- Storage ----
    storage_dir: Path = Path("./storage")
    upload_dir: Path = Path("./storage/uploads")
    processed_dir: Path = Path("./storage/processed")

    # ---- OCR ----
    ocr_engine: str = "tesseract"               # tesseract | easyocr
    ocr_languages: str = "eng+hin"
    ocr_dpi: int = 200
    ocr_timeout_s: int = 120
    tesseract_cmd: str | None = None
    poppler_path: str | None = None
    tessdata_dir: str | None = None

    # ---- Pipeline thresholds ----
    auto_accept_threshold: float = 85.0
    review_threshold: float = 60.0
    duplicate_similarity_threshold: float = 90.0

    # ---- Observability ----
    log_level: str = "INFO"
    log_json: bool = True                       # structured logs for aggregation
    seed_demo_data: bool = True                 # disable in production

    @field_validator("cors_origins", "trusted_hosts", mode="before")
    @classmethod
    def _split_csv(cls, v):
        """Accept a comma-separated string as well as a JSON list."""
        if isinstance(v, str) and not v.strip().startswith("["):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @model_validator(mode="after")
    def _enforce_production_safety(self):
        """Refuse to start in production with development-only defaults."""
        if not self.is_production:
            return self
        problems: list[str] = []
        if self.secret_key == INSECURE_SECRET or len(self.secret_key) < 32:
            problems.append("BHULEKH_SECRET_KEY must be set to a strong random value (>= 32 chars)")
        if "*" in self.trusted_hosts:
            problems.append("BHULEKH_TRUSTED_HOSTS must list real hostnames (no '*') in production")
        if any(o == "*" for o in self.cors_origins):
            problems.append("BHULEKH_CORS_ORIGINS must not be '*' in production")
        if self.database_url.startswith("sqlite"):
            problems.append("BHULEKH_DATABASE_URL should point at PostgreSQL in production")
        if self.seed_demo_data:
            problems.append("BHULEKH_SEED_DEMO_DATA must be false in production")
        if problems:
            raise ValueError("Insecure production configuration:\n  - " + "\n  - ".join(problems))
        return self


settings = Settings()

for _d in (settings.storage_dir, settings.upload_dir, settings.processed_dir):
    _d.mkdir(parents=True, exist_ok=True)


def generate_secret_key() -> str:
    """Convenience for operators: `python -c 'from app.core.config import generate_secret_key as g; print(g())'`."""
    return secrets.token_urlsafe(48)


def configure_external_tools() -> None:
    """Point Tesseract at its binary and language packs, from settings.

    Lives here, and runs at import of this module, because every OCR module imports
    ``settings``: doing it in one of them instead would make it depend on import order,
    and whichever module happened to be imported first would silently decide whether
    a custom tessdata folder took effect.

    The tessdata folder is set through ``TESSDATA_PREFIX`` rather than Tesseract's
    ``--tessdata-dir`` flag on purpose: pytesseract splits its config string with shlex
    in NON-POSIX mode on Windows, which leaves the quotes attached to a quoted path,
    so Tesseract looks for a directory whose name literally contains quote characters
    and every language fails to load. An environment variable needs no quoting.
    """
    import os
    import shutil
    import sys

    if settings.tessdata_dir:
        os.environ["TESSDATA_PREFIX"] = str(settings.tessdata_dir)

    if sys.platform != "win32":
        return
    try:
        import pytesseract
    except ImportError:
        return

    if settings.tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = settings.tesseract_cmd
    elif not shutil.which("tesseract"):
        for candidate in (r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                          r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
                          os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe")):
            if os.path.exists(candidate):
                pytesseract.pytesseract.tesseract_cmd = candidate
                break


configure_external_tools()
