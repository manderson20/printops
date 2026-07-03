# PrintOps

Open-source, self-hosted enterprise print management platform for K-12 schools, businesses, and MSPs — built on IPP/CUPS instead of proprietary vendor software.

> **Status: early.** Manual printer CRUD with IPP-based capability auto-discovery, a minimal login, and a working IPP proxy (Phase 1: jobs route through PrintOps and get logged before being forwarded to the real printer, built on CUPS — see `ARCHITECTURE.md` §3) are working end-to-end against a real printer. Most modules — AirPrint/mDNS advertisement, network-wide discovery, quotas/policy, cost accounting, real RBAC, verified user attribution — are still just direction, not code.

## Repo layout

| Path               | What it is                                              |
|--------------------|----------------------------------------------------------|
| `apps/web`         | Next.js frontend (admin UI, dashboards)                  |
| `apps/api`         | FastAPI backend (REST API, eventually the IPP proxy core)|
| `packages/shared`  | Shared TS types / generated OpenAPI client                |
| `infra/`           | Local dev infra: docker-compose for Postgres, Redis, CUPS |

## Prerequisites

- Node.js 20 (see `.nvmrc`) + pnpm (`corepack enable`)
- Python 3.12+ (see `.python-version`) + [uv](https://docs.astral.sh/uv/) for managing it
- Docker + Docker Compose
- GitHub CLI (`gh`), for repo/auth workflows

On a fresh machine, `./scripts/bootstrap.sh` installs all of the above (idempotent — safe
to re-run, and the place to add future tooling as the project grows). Pass `--yes` to skip
the confirmation prompts before sudo-requiring steps (Docker, `gh`).

```bash
./scripts/bootstrap.sh
```

## Quickstart

```bash
# 1. Infra (Postgres, Redis, CUPS)
cd infra
cp .env.example .env
docker compose up -d
docker compose ps   # confirm postgres/redis/cups are up

# 2. API
cd ../apps/api
uv venv --python 3.12 .venv && source .venv/bin/activate
uv pip install -e ".[dev]"
cp .env.example .env
alembic upgrade head   # creates the printers table
uvicorn app.main:app --reload --port 8000
# -> http://localhost:8000/docs

# 3. Web (new terminal, from repo root)
pnpm install
cp apps/web/.env.local.example apps/web/.env.local
pnpm dev:web
# -> http://localhost:3000
```

The landing page at `localhost:3000` fetches `/healthz` from the API live — if it shows a green "ok" status, the full stack is wired up correctly. From there, "Manage Printers" leads to `/login` (default dev credentials: `admin` / `changeme`, see `apps/api/.env.example`), then to the printer list where you can add a printer by IP and PrintOps will probe it over IPP for its capabilities.

Schema changes go through Alembic: `cd apps/api && alembic revision --autogenerate -m "..."` after changing a model, then `alembic upgrade head`.

## Environment variables

Each app documents its own env vars in a `.env.example` (or `.env.local.example` for the web app):

- `infra/.env.example` — Postgres/Redis/CUPS credentials and ports for docker-compose
- `apps/api/.env.example` — `PRINTOPS_*` settings (JWT secret, CORS origins, dev user credentials)
- `apps/web/.env.local.example` — `NEXT_PUBLIC_API_URL`

## Google Sign-In (SSO)

Staff can log in with Google Workspace instead of the local admin/password
fallback, with admin/viewer roles enforced across the API. Entirely
configured in-app (Settings → Integrations → Google Sign-In), no env vars
needed — see [`docs/google-sso-setup.md`](docs/google-sso-setup.md) for the
one-time Google Cloud Console setup and common pitfalls.

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
