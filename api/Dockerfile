# Build from the repository root: docker build -f api/Dockerfile .
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng libpango-1.0-0 \
      libpangoft2-1.0-0 libharfbuzz-subset0 fonts-dejavu-core curl \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1

WORKDIR /app/api
COPY api/pyproject.toml api/uv.lock api/.python-version ./
RUN uv sync --frozen --no-install-project
COPY api/ ./
RUN uv sync --frozen
COPY templates/ /app/templates/
COPY seed/business.json seed/rules.json /app/seed/

RUN useradd --system --uid 10001 opsinbox && mkdir -p /var/lib/opsinbox/model-cache \
 && chown -R opsinbox /var/lib/opsinbox
USER opsinbox

EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn opsinbox.main:app --host 0.0.0.0 --port 8000"]
