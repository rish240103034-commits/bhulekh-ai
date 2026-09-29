# syntax=docker/dockerfile:1
# ---- Single-container deploy: build the SPA, serve it + the API from one origin ----
# Used by render.yaml (and any host that builds from a root Dockerfile). The separate
# backend/ and frontend/ Dockerfiles remain for the docker-compose two-service setup.

# 1) Build the React/Vite SPA.
# NODE_OPTIONS caps V8 heap under the Render free-tier build memory limit
# (~512 MB total for both stages) so vite doesn't get OOM-killed during rollup.
FROM node:20-alpine AS frontend
WORKDIR /web
ENV NODE_OPTIONS=--max-old-space-size=384
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund --prefer-offline
COPY frontend/ ./
RUN npm run build          # -> /web/dist

# 2) Backend image with the OCR toolchain; serves the built SPA too.
# Language pack selection kept lean for Render free-tier build limits (15 min, ~512
# MB build memory). Ships the four scripts SIH judges will actually see:
# English + Hindi (north/central India), Marathi (Maharashtra khasra / 7-12),
# Bengali (east). Extend the list here when needed; each pack adds ~40 MB.
FROM python:3.11-slim AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr tesseract-ocr-eng tesseract-ocr-hin \
        tesseract-ocr-mar tesseract-ocr-ben \
        poppler-utils libgl1 libglib2.0-0 curl \
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
# --workers 1 keeps memory footprint below the Render free-tier 512 MB cap
# (Tesseract + OpenCV can each hold ~150 MB per process). --preload boots the
# app once in the master before forking so cold-start is a few seconds faster.
CMD ["sh", "-c", "gunicorn app.main:app --worker-class uvicorn.workers.UvicornWorker \
     --workers 1 --preload --bind 0.0.0.0:${PORT:-8000} --timeout 180 --graceful-timeout 30 \
     --access-logfile -"]
