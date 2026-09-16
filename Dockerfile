# syntax=docker/dockerfile:1
# ---- Single-container deploy: build the SPA, serve it + the API from one origin ----
# Used by render.yaml (and any host that builds from a root Dockerfile). The separate
# backend/ and frontend/ Dockerfiles remain for the docker-compose two-service setup.

# 1) Build the React/Vite SPA.
FROM node:20-alpine AS frontend
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build          # -> /web/dist

# 2) Backend image with the full OCR toolchain; serves the built SPA too.
FROM python:3.11-slim AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr tesseract-ocr-eng tesseract-ocr-hin tesseract-ocr-mar \
        tesseract-ocr-guj tesseract-ocr-ben tesseract-ocr-tam tesseract-ocr-tel \
        tesseract-ocr-kan tesseract-ocr-mal tesseract-ocr-pan tesseract-ocr-ori \
        tesseract-ocr-urd poppler-utils libgl1 libglib2.0-0 curl \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    BHULEKH_FRONTEND_DIST=/app/static

WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ .
COPY --from=frontend /web/dist /app/static

# Unprivileged user; owns only the writable storage tree.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/storage \
    && chown -R appuser:appuser /app/storage
USER appuser

# The platform (Render, etc.) provides $PORT; default to 8000 for a local run.
ENV PORT=8000
EXPOSE 8000

# gunicorn + uvicorn workers, bound to the platform port. Schema is created on startup
# for non-production environments (see app.main.lifespan); no separate migrate step.
CMD ["sh", "-c", "gunicorn app.main:app --worker-class uvicorn.workers.UvicornWorker \
     --workers 2 --bind 0.0.0.0:${PORT:-8000} --timeout 180 --graceful-timeout 30 \
     --access-logfile -"]
