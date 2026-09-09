# Serving image for the Gradio chat UI and GET /health.
# Health does not need chroma_db or an API key. Chat still imports the agent
# stack, so the image installs the full project deps.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    GRADIO_ANALYTICS_ENABLED=False \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# onnxruntime (via chromadb) needs libgomp at import time, and app.py imports
# the agent stack on boot. curl is for a local HEALTHCHECK; the CI smoke test
# uses the runner's curl against the published port.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app.py projects.yaml ./
COPY agent/ ./agent/
RUN uv sync --frozen --no-dev

RUN useradd --create-home --uid 1000 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8080

# Shell form so $PORT expands at runtime (Cloud Run). Default 8080 for docker run.
CMD uvicorn app:app --host 0.0.0.0 --port ${PORT:-8080}
