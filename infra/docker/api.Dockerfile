# syntax=docker/dockerfile:1
#
# Image dùng chung cho cả ba entrypoint: api, agent, worker.
# Một codebase, ba process — ADR-0001 quy tắc 4.

FROM python:3.13-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# ─── Lớp dependency (cache riêng, ít thay đổi) ───────────────────────────────
FROM base AS deps

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
# Nếu đã sinh requirements.lock.txt thì build production PHẢI cài từ file khoá:
#     COPY requirements.lock.txt ./
#     RUN pip install -r requirements.lock.txt
RUN pip install --no-cache-dir -r requirements.txt

# ─── Lớp runtime ─────────────────────────────────────────────────────────────
FROM base AS runtime

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 app

COPY --from=deps /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin

COPY --chown=app:app services/api /app
COPY --chown=app:app packages /app/packages

ENV PYTHONPATH=/app

USER app

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/healthz || exit 1

CMD ["python", "-m", "uvicorn", "app.entrypoints.http:app", "--host", "0.0.0.0", "--port", "8000"]
