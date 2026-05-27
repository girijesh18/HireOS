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

# System packages required by WeasyPrint + Playwright Chromium
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl wget \
    libpango-1.0-0 libpangoft2-1.0-0 libpangocairo-1.0-0 \
    libcairo2 libgdk-pixbuf2.0-0 libffi-dev shared-mime-info \
    libnss3 libnspr4 libdbus-1-3 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 \
    libxfixes3 libxrandr2 libgbm1 libasound2 libx11-6 libxcb1 \
    libxext6 libxi6 libxtst6 fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Playwright Chromium (uses system deps installed above)
RUN playwright install chromium

# Backend source
COPY backend/ ./

# Built React frontend served as static files by FastAPI
COPY --from=frontend-build /frontend/dist ./static

# Persistent data dir — overridden by Fly volume at /app/data
RUN mkdir -p /app/data/chroma /app/data/output

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
