FROM node:22-bookworm-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    DATABASE_URL=sqlite:////app/data/runtime/invoiceops.db \
    INVOICE_FIXTURE_MANIFEST=/app/data/runtime/synthetic/manifest.json
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend/ backend/
COPY scripts/ scripts/
COPY alembic.ini .
COPY data/evaluation/metrics.json data/evaluation/metrics.json
COPY --from=frontend /build/dist frontend/dist
RUN useradd --create-home --uid 10001 invoiceops && mkdir -p /app/data/runtime \
    && chown invoiceops:invoiceops /app/data/runtime
USER invoiceops
EXPOSE 8000
CMD ["sh", "-c", "python -m alembic upgrade head && python -m scripts.generate_dataset --output /app/data/runtime/synthetic && python -m scripts.seed_database --data /app/data/runtime/synthetic && exec uvicorn backend.app.main:app --host 0.0.0.0 --port 8000"]

