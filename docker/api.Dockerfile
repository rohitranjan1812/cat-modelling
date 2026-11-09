# syntax=docker/dockerfile:1.7
FROM python:3.12-slim-bookworm AS base
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app

# Dependencies first for layer caching
COPY backend/pyproject.toml backend/uv.lock backend/README.md ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project

COPY backend/src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH" CATMOD_DATA_DIR=/data/portfolios
RUN useradd --create-home --uid 10001 catmod && mkdir -p /data && chown catmod /data
USER catmod
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s CMD python -c "import urllib.request,sys;sys.exit(urllib.request.urlopen('http://127.0.0.1:8000/health').status!=200)"
CMD ["uvicorn", "catmod.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
