# ── Stage 1: Build React frontend ─────────────────────────────────────────────
FROM node:20-alpine AS frontend-build
WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci --prefer-offline
COPY frontend/ ./
RUN npm run build

# ── Stage 2: Python backend + Playwright ──────────────────────────────────────
FROM python:3.11-slim

WORKDIR /app

# WeasyPrint system deps (Playwright installs its own via --with-deps)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl wget \
    libpango-1.0-0 libpangoft2-1.0-0 libpangocairo-1.0-0 \
    libcairo2 libffi-dev shared-mime-info fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Playwright: installs Chromium + all its system deps automatically
RUN playwright install --with-deps chromium

# Backend source
COPY backend/ ./

# Isolated venv for the vendored hiring-agent ATS scorer (its pinned deps must
# not clash with the backend's). ats_score.py invokes hiring_agent/.venv/bin/python.
RUN python -m venv hiring_agent/.venv \
    && hiring_agent/.venv/bin/pip install --no-cache-dir -r hiring_agent/requirements.txt

# Built React frontend served as static files by FastAPI
COPY --from=frontend-build /frontend/dist ./static

# Persistent data dir — overridden by Fly volume at /app/data
RUN mkdir -p /app/data/chroma /app/data/output

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
