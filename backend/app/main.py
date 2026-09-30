"""Application entry point: app factory, middleware stack, lifecycle and health probes."""
from __future__ import annotations

import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text

from app.api import auth, dashboard, documents, integration
from app.core.config import settings
from app.core.database import Base, SessionLocal, engine, ensure_sqlite_columns
from app.core.limiter import limiter
from app.core.logging import configure_logging, get_logger
from app.core.middleware import BodySizeLimitMiddleware, RequestContextMiddleware, SecurityHeadersMiddleware
from app.core.security import hash_password
from app.models import entities  # noqa: F401  (register models with the metadata)
from app.models.entities import ExternalRecord, User

logger = get_logger("app")


async def _keepalive_loop() -> None:
    """Periodically ping our own public URL to keep the free-tier instance warm.

    Render (and similar hosts) spin the free-tier service down after ~15 min of
    no external traffic; the next request pays a 30-50 s cold-start. Making one
    HTTP request every N seconds — from within the process, going OUT through
    the platform to the public URL — counts as external traffic and keeps the
    instance awake.

    Runs only when ``BHULEKH_KEEPALIVE_ENABLED=true`` and a URL is resolvable
    (either ``BHULEKH_KEEPALIVE_URL`` explicitly or Render's own
    ``RENDER_EXTERNAL_URL`` env var). Off by default so local dev doesn't spam
    its own /livez.
    """
    import asyncio
    import os

    import httpx

    url = settings.keepalive_url or os.environ.get("RENDER_EXTERNAL_URL")
    if not url:
        logger.info("keepalive_disabled_no_url")
        return
    ping = url.rstrip("/") + "/livez"
    interval = max(60, settings.keepalive_interval_s)
    logger.info("keepalive_starting", extra={"url": ping, "interval_s": interval})

    # Give the app a moment to finish booting before the first ping.
    try:
        await asyncio.sleep(60)
    except asyncio.CancelledError:
        return

    async with httpx.AsyncClient(timeout=15) as client:
        while True:
            try:
                r = await client.get(ping)
                logger.info("keepalive_ping",
                            extra={"status": r.status_code, "url": ping})
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("keepalive_failed", extra={"error": str(exc)[:200]})
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                return


@asynccontextmanager
async def lifespan(_: FastAPI):
    import asyncio

    configure_logging()
    # In production the schema is owned by Alembic migrations; create_all() is a
    # developer convenience for SQLite so `uvicorn app.main:app` just works.
    if not settings.is_production:
        Base.metadata.create_all(bind=engine)
        ensure_sqlite_columns(Base)
    if settings.seed_demo_data:
        seed_demo_data()
    logger.info("startup", extra={"environment": settings.environment, "version": settings.version})

    # Optional free-tier keep-alive. The task holds no request-scoped state so
    # cancelling on shutdown is safe.
    keepalive_task: asyncio.Task | None = None
    if settings.keepalive_enabled:
        keepalive_task = asyncio.create_task(_keepalive_loop())

    yield

    if keepalive_task is not None:
        keepalive_task.cancel()
        try:
            await keepalive_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
    logger.info("shutdown")


def create_app() -> FastAPI:
    docs_url = None if settings.is_production else "/docs"
    redoc_url = None if settings.is_production else "/redoc"
    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        lifespan=lifespan,
        docs_url=docs_url,             # interactive docs are disabled in production
        redoc_url=redoc_url,
        description="AI-powered digitization and validation of legacy land records "
                    "(SIH 2026 · PS 26018).",
    )

    # Rate limiter wiring.
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)

    # Middleware. The last one added is the outermost (runs first on the request,
    # last on the response), so RequestContext wraps everything and the security
    # headers are applied to every response including error responses.
    app.add_middleware(BodySizeLimitMiddleware,
                       max_bytes=settings.max_request_body_mb * 1024 * 1024)
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins,
                       allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
                       expose_headers=["X-Request-ID"])
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_hosts)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestContextMiddleware)

    app.add_exception_handler(Exception, _unhandled_exception_handler)

    for router in (auth.router, documents.router, dashboard.router, integration.router):
        app.include_router(router, prefix=settings.api_v1_prefix)

    _register_health_routes(app)
    _mount_frontend(app)
    return app


def _mount_frontend(app: FastAPI) -> None:
    """Serve a built SPA from the same origin as the API, if one is configured.

    Registered last, so the API (``/api/v1/*``) and health (``/livez`` etc.) routes are
    matched first; everything else is served from the build directory, with an
    ``index.html`` fallback for client-side routes (so a refresh on ``/documents/123``
    still loads the app). A no-op when ``frontend_dist`` is unset or missing, which is
    the case in development and tests.
    """
    from pathlib import Path

    dist = settings.frontend_dist
    if not dist:
        return
    dist_path = Path(dist)
    if not (dist_path / "index.html").exists():
        logger.warning("frontend_dist set but no index.html found", extra={"dir": dist})
        return

    from starlette.exceptions import HTTPException as StarletteHTTPException
    from starlette.staticfiles import StaticFiles

    _api_prefixes = ("api", "health", "livez", "readyz", "docs", "redoc", "openapi")

    class _SPAStaticFiles(StaticFiles):
        async def get_response(self, path: str, scope):
            # With html=True, StaticFiles raises a 404 for a missing file rather than
            # returning one; catch it and serve the SPA shell so client-side routes
            # (e.g. a refresh on /documents/123) load the app. API/doc paths keep their 404.
            try:
                return await super().get_response(path, scope)
            except StarletteHTTPException as exc:
                if exc.status_code == 404 and not path.startswith(_api_prefixes):
                    return await super().get_response("index.html", scope)
                raise

    app.mount("/", _SPAStaticFiles(directory=str(dist_path), html=True), name="frontend")
    logger.info("serving frontend", extra={"dir": str(dist_path)})


# --------------------------------------------------------------------------- error handling
async def _rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(status_code=429,
                        content={"detail": f"Rate limit exceeded: {exc.detail}"})


async def _unhandled_exception_handler(request: Request, exc: Exception):
    """Never leak a stack trace or internal message to the client.

    The full exception is logged with the request id; the client gets an opaque error
    plus that id so a support request can be tied to the log entry.
    """
    request_id = getattr(request.state, "request_id", None)
    logger.exception("unhandled_exception", extra={"request_id": request_id,
                     "path": request.url.path})
    detail = "Internal server error"
    if not settings.is_production:
        detail = f"{type(exc).__name__}: {exc}"
    return JSONResponse(status_code=500,
                        content={"detail": detail, "request_id": request_id})


# --------------------------------------------------------------------------- health probes
_ocr_cache: dict = {"ts": 0.0, "value": None}
_OCR_CACHE_TTL_S = 60


def _register_health_routes(app: FastAPI) -> None:
    @app.get("/livez", tags=["system"])
    def livez():
        """Liveness: the process is up. Cheap; safe for a k8s livenessProbe."""
        return {"status": "alive", "version": settings.version}

    @app.get("/readyz", tags=["system"])
    def readyz():
        """Readiness: dependencies are reachable. Used by a k8s readinessProbe / LB."""
        db_ok, db_err = True, None
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception as exc:  # noqa: BLE001
            db_ok, db_err = False, str(exc)[:200]
        status_code = 200 if db_ok else 503
        return JSONResponse(status_code=status_code, content={
            "status": "ready" if db_ok else "not_ready",
            "database": {"ok": db_ok, "error": db_err}})

    @app.get("/health", tags=["system"])
    def health():
        """Detailed OCR diagnostics — open this first when documents come back empty.

        Each language pack is exercised, not merely listed: a truncated .traineddata
        file appears in the language list but fails on use, which otherwise looks like
        a model that cannot read the page. The result is cached briefly so repeated
        calls cannot be used to spin up Tesseract processes.
        """
        now = time.time()
        if _ocr_cache["value"] is not None and now - _ocr_cache["ts"] < _OCR_CACHE_TTL_S:
            return _ocr_cache["value"]
        value = _ocr_health()
        _ocr_cache.update(ts=now, value=value)
        return value


def _ocr_health() -> dict:
    import shutil

    import numpy as np
    import pytesseract

    from app.pipeline.ocr import _available_tess_langs
    from app.pipeline.preprocess import _poppler_path

    tess_ok, version, tess_err = True, None, None
    try:
        version = str(pytesseract.get_tesseract_version())
    except Exception as exc:  # noqa: BLE001
        tess_ok, tess_err = False, (f"{exc}. Install Tesseract (UB-Mannheim build) or set "
                                    f"BHULEKH_TESSERACT_CMD in backend/.env")

    installed = sorted(_available_tess_langs()) if tess_ok else []
    probe = np.full((60, 200), 255, dtype=np.uint8)
    usable, broken = [], {}
    for lang in installed:
        if lang == "osd":
            continue
        try:
            pytesseract.image_to_string(probe, lang=lang, config="--oem 1 --psm 6", timeout=20)
            usable.append(lang)
        except Exception as exc:  # noqa: BLE001
            broken[lang] = str(exc)[:200]

    wanted = [l for l in settings.ocr_languages.replace(",", "+").split("+") if l]
    absent = [l for l in wanted if l not in usable]
    poppler = _poppler_path() or shutil.which("pdftoppm")

    hints = []
    if not tess_ok:
        hints.append(tess_err)
    if absent:
        hints.append(
            f"Language pack(s) missing or unusable: {', '.join(absent)}. Documents in that "
            f"script will extract nothing and tabular records will not be detected. Re-run the "
            f"Tesseract installer and tick the language under 'Additional language data', or "
            f"place <lang>.traineddata in a folder and set BHULEKH_TESSDATA_DIR "
            f"(the file must be ~1-15 MB; a few hundred bytes means the download failed).")
    if broken:
        hints.append(f"Installed but unusable (likely a corrupt download): {', '.join(broken)}")
    if not poppler:
        hints.append("PDF uploads need Poppler: unzip to C:\\poppler or set BHULEKH_POPPLER_PATH")

    return {
        "status": "ok" if (tess_ok and not absent and poppler) else "degraded",
        "ocr_engine": settings.ocr_engine,
        "tesseract": {
            "ok": tess_ok,
            "command": pytesseract.pytesseract.tesseract_cmd,
            "version": version,
            "languages_installed": installed,
            "languages_usable": usable,
            "languages_unusable": broken or None,
            "default_languages_wanted": wanted,
            "default_languages_missing": absent,
        },
        "tessdata_dir": settings.tessdata_dir,
        "poppler_for_pdf": {"ok": bool(poppler), "path": poppler},
        "hints": hints or None,
    }


# --------------------------------------------------------------------------- demo seed
DEMO_USERS = [
    ("admin", "Admin@12345", "System Administrator", "admin", None, None),
    ("verifier", "Verify@12345", "Tehsil Verifier (Lucknow)", "verifier", None, None),
    ("operator", "Operate@12345", "Data Entry Operator", "operator", None, None),
    ("viewer", "Viewer@12345", "DoLR Viewer", "viewer", None, None),
    ("lrms", "Lrms@12345", "LRMS Integration Client", "integration", None, None),
]

DEMO_EXTERNAL = [
    ("LRMS", "Uttar Pradesh", "Lucknow", "Rampur", "123/4", None, "45", "Ram Prasad Verma", 25000.0),
    ("LRMS", "Uttar Pradesh", "Lucknow", "Rampur", "127", None, "48", "Sita Devi", 8093.0),
    ("DILRMP", "Maharashtra", "Pune", "Wagholi", None, "56/2", "112", "Sunil Patil", 12140.0),
    ("DILRMP", "Madhya Pradesh", "Bhopal", "Kolar", "301", None, "9", "Mohan Lal Sharma", 40468.0),
]


def seed_demo_data() -> None:
    """Seed demo users and an LRMS mirror. Never runs in production (config forbids it)."""
    db = SessionLocal()
    try:
        if not db.query(User).first():
            for username, pw, name, role, st, di in DEMO_USERS:
                db.add(User(username=username, hashed_password=hash_password(pw),
                            full_name=name, role=role, state=st, district=di))
        if not db.query(ExternalRecord).first():
            for src, st, di, vi, kh, sv, ka, ow, ar in DEMO_EXTERNAL:
                db.add(ExternalRecord(source=src, state=st, district=di, village=vi,
                                      khasra_number=kh, survey_number=sv, khata_number=ka,
                                      owner_name=ow, plot_area_sqm=ar))
        db.commit()
    finally:
        db.close()


app = create_app()
