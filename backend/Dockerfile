FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.17 /uv /uvx /bin/

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

# Install dependencies first (cached layer), then copy the source.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .

EXPOSE 8000
# Apply migrations, seed demo data if the database is empty, then serve.
CMD ["sh", "-c", "alembic upgrade head && python -m scripts.seed --if-empty && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
