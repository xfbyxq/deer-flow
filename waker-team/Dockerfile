# ---- Stage 1: Build frontend ----
FROM node:20-slim AS frontend-build
WORKDIR /app/frontend

COPY frontend/package.json frontend/pnpm-lock.yaml* ./
RUN corepack enable && pnpm install --frozen-lockfile 2>/dev/null || pnpm install

COPY frontend/ .
RUN pnpm build

# ---- Stage 2: Python runtime ----
FROM python:3.12-slim
WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy Python project metadata and install deps
COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen 2>/dev/null || uv sync

# Copy app code
COPY app/ app/
COPY scripts/ scripts/

# Copy built frontend as static files
COPY --from=frontend-build /app/frontend/dist app/web

# Data volume
VOLUME /app/data

# Environment defaults (overridable via docker-compose / .env)
ENV DATABASE_URL=sqlite+aiosqlite:///./data/waker_team.db
ENV MCP_HOST=0.0.0.0
ENV MCP_PORT=8765

EXPOSE 8000 8765

# Start both API server and MCP server
CMD ["sh", "-c", "uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 & uv run python -m app.mcp.server & wait"]
