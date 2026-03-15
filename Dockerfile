# Pool Server v3: FastAPI + SQLite + KeyDB (external), lease-based
#
# Build:  docker build -t bbdata/pool-server .
# Run:    docker run -d --network host -v /data/pool:/data bbdata/pool-server

# --- Stage 1: Build frontend ---
FROM node:20-alpine AS frontend-builder
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# --- Stage 2: Runtime ---
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends curl ca-certificates && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

COPY --from=frontend-builder /frontend/dist ./frontend/dist

ENV WEB_CONCURRENCY=4

EXPOSE 8421

HEALTHCHECK --interval=15s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -sf http://localhost:8421/health | grep -q '"status":"ok"' || exit 1

CMD ["sh", "-c", "python -m uvicorn app.main:app --host 0.0.0.0 --port 8421 --workers ${WEB_CONCURRENCY}"]
