# ---- Stage 1: build the TIQCollect React SPA ----
FROM node:20-alpine AS frontend
WORKDIR /fe
COPY frontend/package*.json ./
RUN npm ci || npm install
COPY frontend/ ./
RUN npm run build

# ---- Stage 2: FastAPI backend serving /api/v1 + the built SPA ----
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

# libgomp1 is required by ortools (route optimisation); postgresql-client gives
# the entrypoint pg_isready and psql to gate seeding on the database being up.
RUN apt-get update && apt-get install -y --no-install-recommends \
      libgomp1 postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./
# faster-whisper is only used when TRANSCRIPTION_PROVIDER=local; it and its PyAV
# dependency are the heaviest thing in the file and this deployment uses the
# hosted OpenAI provider, so it is dropped from the image. Leaving it in roughly
# doubles the build for a code path nothing reaches.
RUN grep -v '^faster-whisper' requirements.txt > /tmp/req.txt \
    && pip install -r /tmp/req.txt

COPY backend/ ./
# Built SPA served same-origin by FastAPI (see app/main.py — the block is a
# no-op when this directory is absent, which is what keeps local dev unchanged).
COPY --from=frontend /fe/dist ./static

COPY docker-entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 8300
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8300"]
