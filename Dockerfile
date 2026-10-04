# =====================================================================
# STAGE 1: BASE RUNTIME (Shared OS, PostgreSQL Client & Python Deps)
# =====================================================================
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH="/app/backend:/app/backend/core:/app/backend/scripts"

WORKDIR /app

RUN groupadd --system avinya && useradd --system --gid avinya --create-home avinya

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --root-user-action=ignore -r requirements.txt


# =====================================================================
# STAGE 2: DEVELOPMENT TARGET (Hot-Reload Uvicorn)
# =====================================================================
FROM base AS development
COPY --chown=avinya:avinya . .
RUN mkdir -p /app/backups && chown -R avinya:avinya /app
USER avinya
EXPOSE 8000
CMD ["sh", "-c", "python backend/scripts/prestart.py && exec uvicorn main:app --host 0.0.0.0 --port 8000 --reload"]


# =====================================================================
# STAGE 3: AUTOMATED TEST RUNNER TARGET (Strictly for 'dev' Branch)
# =====================================================================
FROM base AS testing
RUN pip install --root-user-action=ignore pytest pytest-cov
COPY --chown=avinya:avinya . .
USER avinya
CMD ["pytest", "-v", "--durations=10"]


# =====================================================================
# STAGE 4: PRODUCTION TARGET (Locked Non-Root 4-Worker Gunicorn)
# =====================================================================
FROM base AS production
COPY --chown=avinya:avinya . .
RUN mkdir -p /app/backups /app/deployments && chown -R avinya:avinya /app
USER avinya
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["sh", "-c", "python backend/scripts/prestart.py && exec gunicorn main:app --workers 4 --worker-class uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000 --access-logfile - --error-logfile -"]