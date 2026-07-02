# PrintOps

Open-source, self-hosted enterprise print management platform for K-12 schools, businesses, and MSPs — built on IPP/CUPS instead of proprietary vendor software.

> **Status: early scaffold.** This repo currently contains a working skeleton (health-checked API, landing page, dev infra, CI) with no domain features yet. See [ARCHITECTURE.md](./ARCHITECTURE.md) for where this is headed.

## Repo layout

| Path               | What it is                                              |
|--------------------|----------------------------------------------------------|
| `apps/web`         | Next.js frontend (admin UI, dashboards)                  |
| `apps/api`         | FastAPI backend (REST API, eventually the IPP proxy core)|
| `packages/shared`  | Shared TS types / generated OpenAPI client                |
| `infra/`           | Local dev infra: docker-compose for Postgres, Redis, CUPS |

## Prerequisites

- Node.js 20 (see `.nvmrc`) + pnpm (`corepack enable`)
- Python 3.12+ (see `.python-version`)
- Docker + Docker Compose

## Quickstart

```bash
# 1. Infra (Postgres, Redis, CUPS)
cd infra
cp .env.example .env
docker compose up -d
docker compose ps   # confirm postgres/redis/cups are up

# 2. API
cd ../apps/api
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
uvicorn app.main:app --reload --port 8000
# -> http://localhost:8000/docs

# 3. Web (new terminal, from repo root)
pnpm install
cp apps/web/.env.local.example apps/web/.env.local
pnpm dev:web
# -> http://localhost:3000
```

The landing page at `localhost:3000` fetches `/healthz` from the API live — if it shows a green "ok" status, the full stack is wired up correctly.

## Environment variables

Each app documents its own env vars in a `.env.example` (or `.env.local.example` for the web app):

- `infra/.env.example` — Postgres/Redis/CUPS credentials and ports for docker-compose
- `apps/api/.env.example` — `PRINTOPS_*` settings (JWT secret, CORS origins, dev user credentials)
- `apps/web/.env.local.example` — `NEXT_PUBLIC_API_URL`

## Tests & linting

```bash
# API
cd apps/api && ruff check . && pytest

# Web
pnpm lint
```

CI (`.github/workflows/ci.yml`) runs the same checks on every push/PR.

## Contributing

This is an early-stage open-source project — issues and PRs welcome. Licensed under [GPLv3](./LICENSE).
