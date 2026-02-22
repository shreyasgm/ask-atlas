# Stage 1: Builder — install deps with uv
FROM ghcr.io/astral-sh/uv:0.6-python3.12-bookworm-slim AS builder
WORKDIR /app

# Deps first (layer caching — re-install only when manifests change)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# App code + data files
COPY src/ ./src/
COPY model_config.py db_table_descriptions.json db_table_structure.json LICENSE README.md ./

# Install the project itself (registers src package in venv)
RUN uv sync --frozen --no-dev

# Stage 2: Runtime — slim, no uv
FROM python:3.12-slim-bookworm AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 1000 atlas && useradd --uid 1000 --gid atlas --create-home atlas

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src/ /app/src/
COPY --from=builder /app/model_config.py /app/
COPY --from=builder /app/db_table_descriptions.json /app/db_table_structure.json /app/
COPY --from=builder /app/pyproject.toml /app/

RUN mkdir -p /app/logs && chown -R atlas:atlas /app

ENV PATH="/app/.venv/bin:$PATH"
USER atlas
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["uvicorn", "src.api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--timeout-keep-alive", "65"]
