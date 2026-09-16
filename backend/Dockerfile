# syntax=docker/dockerfile:1
# ---- Backend image: FastAPI + OCR toolchain, run as a non-root user ----
FROM python:3.11-slim AS base

# System deps: Tesseract with the Indian language packs, Poppler for PDF rendering,
# and the shared libs OpenCV needs. No build toolchain is kept in the final image.
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr tesseract-ocr-eng tesseract-ocr-hin tesseract-ocr-mar \
        tesseract-ocr-guj tesseract-ocr-ben tesseract-ocr-tam tesseract-ocr-tel \
        tesseract-ocr-kan tesseract-ocr-mal tesseract-ocr-pan tesseract-ocr-ori \
        tesseract-ocr-urd poppler-utils libgl1 libglib2.0-0 curl \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run as an unprivileged user; give it ownership of the writable storage tree only.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/storage \
    && chown -R appuser:appuser /app/storage
USER appuser

EXPOSE 8000

# Container-level liveness check (compose/k8s can also use /livez).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/livez || exit 1

# gunicorn manages uvicorn workers for production; override for a single dev worker.
CMD ["gunicorn", "app.main:app", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--workers", "2", "--bind", "0.0.0.0:8000", \
     "--timeout", "180", "--graceful-timeout", "30", "--access-logfile", "-"]
