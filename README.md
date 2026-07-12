# PrintOps

Open-source, self-hosted enterprise print management platform for K-12 schools, businesses, and MSPs — built on IPP/CUPS instead of proprietary vendor software.

> **Status: in active production use** at a real K-12 district. Every job flows through PrintOps as an IPP proxy (built on CUPS — see `ARCHITECTURE.md` §3), and the modules below are real, working code, not just direction — see `CHANGELOG.md` for the full version-by-version history. Printers are still added manually by IP (no network-wide auto-discovery yet), and this is still a single-tenant deployment (multi-tenancy is designed for but not built — see `ARCHITECTURE.md` §6).

## Features

- **AirPrint-first IPP proxy** — every job is logged, attributed, and forwarded through PrintOps, not just monitored from the side. Real capability auto-discovery per printer (duplex/color/finishing/paper trays/TLS support), fleet-wide background rediscovery, AirPrint/mDNS advertisement, and an MDM printer-resync tool for pushing capability fixes out to already-configured Macs.
- **User attribution** — an ordered fallback chain (IPP `requesting-user-name` → MDM device-to-user mapping via Mosyle + a MAC-lookup integration → Google Workspace/ChromeOS device records → unresolved), plus manual device-attribution overrides, identity-alias merging for duplicate/stale logins, and device-level (not just user-level) breakdowns.
- **Auth & RBAC** — Google Workspace SSO or a local admin fallback, with admin/viewer/read-only-OU-scoped roles enforced server-side, account pre-provisioning, and admin-configurable idle-session timeouts.
- **Cost accounting & toner management** — real per-color cartridge cost/yield (not a flat estimate), auto-detected cartridge part numbers from SNMP (HP/Canon), live toner-level polling with low-toner warnings and history charts, and a fleet-wide bulk cost/yield editor with CSV/PDF export.
- **Quotas & secure release** — per-printer, per-user page quotas with admin-release holds; a PIN-based print-release kiosk (badge/Employee ID at the device); Follow-Me printing (release at any enabled printer, not just the one you sent to); per-user release bypass.
- **Self-service web upload printing** — upload a PDF and print it without a client-configured queue, optionally restricted per printer by Google Workspace org unit.
- **Walk-up copier accounting** — track copies made directly at a shared MFP (not just jobs PrintOps proxied) and attribute them to the same person, with real vendor connectors (Canon, Konica Minolta, Kyocera, Ricoh, Xerox) plus a CSV import wizard for the rest, and SNMP-based estimation for untracked copy activity.
- **Reporting** — a live TV-dashboard view, a full Insights report (timelines, leaderboards, environmental/cost impact, saved snapshots), a per-user usage page, and CSV/print-to-PDF export throughout.
- **Monitoring** — SNMP page/copy/print counters with history charts, syslog collection from printers/MFPs, and a Zabbix integration for external fleet monitoring.
- **Not built yet**: network-wide printer auto-discovery (printers are added manually by IP), multi-tenancy, notifications (email/chat/webhook), and GraphQL — see `ARCHITECTURE.md` for direction on all four.

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

## Production / self-hosted install

The Quickstart above is for local development. For standing up a real
instance (school, business, MSP), run:

```bash
./scripts/setup.sh
```

It's the only command you need, even on a completely bare machine — it
installs Node/pnpm, Python/uv, and Docker if they're missing (via
`bootstrap.sh`), then walks you through your domain, an initial admin
login, and (optionally) automatic HTTPS via [Caddy](https://caddyserver.com/)
+ Let's Encrypt. It generates real secrets (JWT signing key, encryption key,
database password) instead of the `change-me` placeholders in the
`.env.example` files, brings up Postgres/Redis, runs migrations, builds the
web app, and can install both services under systemd so they survive a
reboot. Safe to re-run — it asks before overwriting any `.env` that's
already there, since regenerating secrets invalidates existing sessions and
can make already-encrypted integration credentials unreadable.

## Environment variables

Each app documents its own env vars in a `.env.example` (or `.env.local.example` for the web app):

- `infra/.env.example` — Postgres/Redis/CUPS credentials and ports for docker-compose
- `apps/api/.env.example` — `PRINTOPS_*` settings (JWT secret, CORS origins, dev user credentials)
- `apps/web/.env.local.example` — `NEXT_PUBLIC_API_URL`

## Google Sign-In (SSO)

Staff can log in with Google Workspace instead of the local admin/password
fallback, with admin/viewer/read-only-OU-scoped roles enforced across the
API. Entirely configured in-app (Settings → Integrations → Google Sign-In),
no env vars needed — see [`docs/google-sso-setup.md`](docs/google-sso-setup.md)
for the one-time Google Cloud Console setup and common pitfalls.

## Tests & linting

```bash
# API
cd apps/api && ruff check . && pytest

# Web
pnpm lint
```

CI (`.github/workflows/ci.yml`) runs the same checks on every push/PR.

## Contributing

Issues and PRs welcome — see `CONTRIBUTING.md`. Licensed under [GPLv3](./LICENSE).
