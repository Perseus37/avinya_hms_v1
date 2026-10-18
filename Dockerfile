# =====================================================================
# STAGE 0: REACT CONTROL PLANE UI BUILDER (Compiles onboarding-ui/dist)
# =====================================================================
FROM node:20-alpine AS ui-builder
WORKDIR /ui
COPY onboarding-ui/package.json ./
COPY onboarding-ui/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY onboarding-ui/ ./
RUN npm run build

# =====================================================================
# STAGE 1: BASE RUNTIME (Shared OS, PostgreSQL Client & Python Deps)
# =====================================================================
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN groupadd --system avinya && useradd --system --gid avinya --create-home avinya

RUN apt-get update && apt-get install -y --no-install-recommends poppler-utils \
    curl \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-integrations.txt ./
RUN pip install --root-user-action=ignore \
        -r requirements.txt -r requirements-integrations.txt \
    && pip install --root-user-action=ignore --upgrade "setuptools==84.0.0"

# =====================================================================
# STAGE 2: DEVELOPMENT TARGET (Hot-Reload Uvicorn)
# =====================================================================
FROM base AS development
COPY --chown=avinya:avinya . .
COPY --from=ui-builder --chown=avinya:avinya /ui/dist /app/onboarding-ui/dist
RUN mkdir -p /app/backups /vault && chown -R avinya:avinya /app /vault
USER avinya
EXPOSE 8000
CMD ["sh", "-c", "exec uvicorn backend.main:app --host 0.0.0.0 --port ${API_PORT:?API_PORT is required} --reload"]

# =====================================================================
# STAGE 3: AUTOMATED TEST RUNNER TARGET (Strictly for 'dev' Branch)
# =====================================================================
FROM base AS testing
RUN pip install --root-user-action=ignore pytest pytest-cov
COPY --chown=avinya:avinya . .
COPY --from=ui-builder --chown=avinya:avinya /ui/dist /app/onboarding-ui/dist
USER avinya
CMD ["pytest", "-v", "--durations=10"]

# =====================================================================
# STAGE 4: PRODUCTION TARGET (Locked Non-Root Gunicorn)
# =====================================================================
FROM base AS production
COPY --chown=avinya:avinya . .
COPY --from=ui-builder --chown=avinya:avinya /ui/dist /app/onboarding-ui/dist
RUN mkdir -p /app/backups /vault && chown -R avinya:avinya /app /vault
USER avinya
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f "http://localhost:${API_PORT:?API_PORT is required}/health/ready" || exit 1

CMD ["sh", "-c", "exec gunicorn backend.main:app --workers ${GUNICORN_WORKERS:?GUNICORN_WORKERS is required} --worker-class uvicorn.workers.UvicornWorker --bind 0.0.0.0:${API_PORT:?API_PORT is required} --access-logfile - --error-logfile -"]