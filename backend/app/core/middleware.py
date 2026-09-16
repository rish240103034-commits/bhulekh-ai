"""Custom ASGI middleware: security headers, body-size limits, request logging.

These are deliberately small and dependency-free. Rate limiting is handled separately
by slowapi (see ``app.core.limiter``) because it needs per-route decorators.
"""
from __future__ import annotations

import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("http")


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add hardening headers to every response.

    The CSP is intentionally strict: this API serves JSON and Swagger UI only, so it
    needs no third-party scripts of its own. The SPA is served by nginx with its own,
    slightly looser, policy (see ``frontend/nginx.conf``).
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        self._csp = (
            "default-src 'self'; "
            "img-src 'self' data:; "
            "script-src 'self' 'unsafe-inline'; "   # Swagger UI inlines a bootstrap script
            "style-src 'self' 'unsafe-inline'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'"
        )

    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        response.headers.setdefault("Permissions-Policy",
                                    "geolocation=(), microphone=(), camera=()")
        response.headers.setdefault("Content-Security-Policy", self._csp)
        if "server" in response.headers:
            del response.headers["server"]
        if settings.hsts_enabled:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject over-large request bodies before they are read into memory.

    A declared Content-Length over the cap is refused immediately; a streamed body with
    no length is guarded by the per-route upload validation as well. This is defence in
    depth against memory-exhaustion uploads.
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        super().__init__(app)
        self.max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > self.max_bytes:
                    return JSONResponse(
                        status_code=413,
                        content={"detail": f"Request body exceeds the "
                                           f"{self.max_bytes // (1024 * 1024)} MB limit"})
            except ValueError:
                return JSONResponse(status_code=400, content={"detail": "Invalid Content-Length"})
        return await call_next(request)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Attach a request id, time each request and emit one structured access log line.

    The request id is returned in ``X-Request-ID`` so a client-reported error can be
    traced to a single log entry.
    """

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        request.state.request_id = request_id
        start = time.perf_counter()
        try:
            response: Response = await call_next(request)
        except Exception:
            duration_ms = round((time.perf_counter() - start) * 1000, 1)
            logger.exception("request_failed", extra={"request_id": request_id,
                             "method": request.method, "path": request.url.path,
                             "duration_ms": duration_ms})
            raise
        duration_ms = round((time.perf_counter() - start) * 1000, 1)
        response.headers["X-Request-ID"] = request_id
        logger.info("request", extra={
            "request_id": request_id, "method": request.method, "path": request.url.path,
            "status": response.status_code, "duration_ms": duration_ms,
            "client": request.client.host if request.client else None})
        return response
