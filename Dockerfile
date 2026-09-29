# Production image: the built SPA and the FastAPI API in one container. The
# Collections platform builds this for fieldops.transorg.ai; the same image runs
# the API, the Celery worker and beat (the role is the command). Local development
# uses docker-compose.yml + Dockerfile.dev instead.

# ---- Stage 1: build the TIQCollect React SPA ----
# Node 22: react-router 8 requires >= 22.22. `npm ci` alone: a lockfile that does
# not match package.json must fail the build, not be papered over by `npm install`.
FROM node:22-alpine AS frontend
WORKDIR /fe
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
# Map tiles (src/lib/mapTiles.ts): the public Mapbox token and the hosts allowed
# to use it are inlined at build time. Empty is valid: OSM tiles, and a warning.
ARG VITE_MAPBOX_TOKEN=""
ARG VITE_ALLOWED_MAP_HOSTS=""
RUN npm run build

# ---- Stage 2: FastAPI backend serving /api/v1 + the built SPA ----
FROM python:3.12-slim
WORKDIR /app
# MPLCONFIGDIR: the retrain path renders plots, and the app user's home is /app.
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 MPLCONFIGDIR=/tmp/matplotlib

# libgomp1 is required by ortools (route optimisation); postgresql-client gives
# the entrypoint pg_isready and psql to gate seeding on the database being up.
RUN apt-get update && apt-get install -y --no-install-recommends \
      libgomp1 postgresql-client \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin app \
    && chown app:app /app
# /app itself (not only what is copied into it) belongs to the app user: Celery
# beat writes its schedule file into the working directory, and the demo scripts
# create docs/rollback/ under it at run time.

COPY backend/requirements.txt ./
# faster-whisper only serves TRANSCRIPTION_PROVIDER=local and is the heaviest
# package in the file; this image uses the hosted provider.
RUN grep -v '^faster-whisper' requirements.txt > /tmp/req.txt \
    && pip install -r /tmp/req.txt

# Owned by the app user: retraining writes new model versions under
# app/ml/artifacts and the demo operator scripts write rollback manifests.
# .dockerignore keeps tests and non-serving model evidence out.
COPY --chown=app:app backend/ ./
# Built SPA served same-origin by FastAPI (app/main.py; a no-op when absent).
COPY --chown=app:app --from=frontend /fe/dist ./static

COPY docker-entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

USER app
EXPOSE 8300
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
# WEB_CONCURRENCY: uvicorn workers, default 1; the deployment decides. Since
# RESTRUCTURE-PLAN 1.8 the rate limiter counts in Redis, so more than one is safe
# while REDIS_URL is reachable (/ready reports rate_limit_storage).
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port 8300 --workers ${WEB_CONCURRENCY:-1}"]
