FROM node:24-bookworm-slim AS web-build

WORKDIR /build/web

COPY web/package.json web/package-lock.json ./
RUN npm ci

COPY web/ ./
RUN npm run typecheck && npm run build


FROM python:3.11-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=10000

WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

RUN groupadd --gid 10001 appuser \
    && useradd --uid 10001 --gid appuser --create-home \
        --shell /usr/sbin/nologin appuser

COPY --chown=appuser:appuser map/server/ ./map/server/
COPY --chown=appuser:appuser map/scripts/ ./map/scripts/
COPY --chown=appuser:appuser map/data/ ./map/data/
COPY --chown=appuser:appuser NOTICE ./NOTICE
COPY --chown=appuser:appuser LICENSES/ ./LICENSES/
COPY --from=web-build --chown=appuser:appuser /build/web/dist/ ./web/dist/

RUN mkdir -p map/cache && chown appuser:appuser map/cache

USER appuser

EXPOSE 10000

CMD ["sh", "-c", "exec uvicorn app:app --host 0.0.0.0 --port \"$PORT\" --app-dir map/server"]
