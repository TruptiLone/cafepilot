# CaféPilot public demo: one container serves the web app and the API.
# Build from the REPOSITORY ROOT (the seeds read ../data/*.json):
#   docker build -f business-core/Dockerfile -t cafepilot-demo .
# Run (Postgres settings, MODEL_API_KEY and DEMO_ACCESS_CODE come from the environment):
#   docker run --rm -p 8000:8000 --env-file business-core/.env -e POSTGRES_HOST=host.docker.internal \
#     -e DEMO_ACCESS_CODE=your-code cafepilot-demo

# --- 1. the web app -------------------------------------------------------------------------------
FROM node:22-slim AS web
WORKDIR /web
COPY business-core/frontend/package.json business-core/frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY business-core/frontend/index.html business-core/frontend/tsconfig.json business-core/frontend/vite.config.ts ./
COPY business-core/frontend/src ./src
RUN npm run build

# --- 2. the API, serving the built web app -------------------------------------------------------
FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/business-core
WORKDIR /app/business-core

COPY business-core/requirements.txt ./
RUN pip install -r requirements.txt

# Same layout as the repository, so scripts find ../data.
COPY data /app/data
COPY business-core/alembic.ini ./
COPY business-core/alembic ./alembic
COPY business-core/backend ./backend
COPY business-core/scripts ./scripts
COPY business-core/assets ./assets
COPY business-core/demo-assets/receiving ./demo-assets/receiving
COPY business-core/demo-assets/promo-samples ./demo-assets/promo-samples
COPY --from=web /web/dist ./frontend/dist

# Run as a non-root user; var/ holds saved photos, post drafts and the model-call log (temporary).
RUN useradd --create-home --uid 10001 cafepilot \
    && mkdir -p var \
    && chown -R cafepilot:cafepilot var
USER cafepilot

# The public demo: access code required, a private café per visitor, the web app served from here.
# MUSE_DAILY_LIMIT is a safe default; set it (and DEMO_ACCESS_CODE, MODEL_API_KEY) on the host.
ENV PUBLIC_DEMO=1 \
    PER_VISITOR_DATABASES=1 \
    FRONTEND_DIST=/app/business-core/frontend/dist \
    MUSE_DAILY_LIMIT=200 \
    PORT=8000
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s \
  CMD python -c "import urllib.request, os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/health', timeout=4)"
CMD ["./scripts/container_start.sh"]
