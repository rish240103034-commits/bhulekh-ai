"""Structured logging.

In production (``BHULEKH_LOG_JSON=true``) logs are emitted as one JSON object per line,
which log aggregators (Loki, CloudWatch, ELK) parse without a grok pattern. In
development they fall back to a readable console format. Any ``extra=`` fields on a log
call are merged into the JSON object, so the access-log line carries request id, path,
status and latency as first-class fields.
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import sys

from app.core.config import settings

_RESERVED = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}
_configured = False


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": _dt.datetime.fromtimestamp(record.created, _dt.UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging() -> None:
    global _configured
    if _configured:
        return
    handler = logging.StreamHandler(sys.stdout)
    if settings.log_json:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(settings.log_level.upper())
    # uvicorn's own access log duplicates ours; silence it.
    logging.getLogger("uvicorn.access").disabled = True
    _configured = True


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(f"bhulekh.{name}")
