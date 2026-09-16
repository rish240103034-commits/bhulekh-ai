"""Rate limiting via slowapi.

Keyed by client IP. The default limit applies process-wide through the middleware;
sensitive routes (login, upload) add a tighter per-route limit with the ``@limit``
decorator. When ``BHULEKH_RATE_LIMIT_ENABLED=false`` the decorators become no-ops so
tests and single-user desktop runs are not throttled.

For a multi-instance deployment set ``storage_uri`` to a Redis URL so the counters are
shared; the in-memory default is correct for a single instance.
"""
from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import settings

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[settings.rate_limit_default] if settings.rate_limit_enabled else [],
    enabled=settings.rate_limit_enabled,
    headers_enabled=True,
)


def limit(rate: str):
    """Per-route limit that respects the global enable flag."""
    if not settings.rate_limit_enabled:
        def _noop(func):
            return func
        return _noop
    return limiter.limit(rate)
