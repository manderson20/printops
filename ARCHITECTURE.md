# PrintOps Architecture

## 1. Purpose & Status

This document sets direction for PrintOps as it grows from an empty scaffold into a full enterprise print management platform. It describes intended module boundaries and core design principles — it is **not** a spec for what is currently implemented, and it will be revised as modules land. Treat it as the reference future work should build toward, not a changelog of what exists today; see `CHANGELOG.md` for the real, version-by-version account of what's shipped (a lot, as of this revision — most module status notes below reflect that).

## 2. System Overview / Monorepo Map

```
apps/web      Next.js frontend (admin UI, dashboards, end-user portal)
apps/api      FastAPI backend (REST API, business logic, IPP proxy — long term)
packages/shared  Generated OpenAPI client + shared TS types, consumed by apps/web
infra/        docker-compose for local dev: Postgres, Redis, CUPS
```

`apps/web` talks to `apps/api` over HTTP/WebSockets. `apps/api` talks to Postgres (system of record), Redis (queues/caching/pub-sub), and — eventually — to physical printers via IPP/IPPS, with CUPS available as a fallback rendering/legacy-protocol layer (JetDirect, LPD, SMB).

## 3. Core Principle: PrintOps as an IPP Proxy

PrintOps is not a monitoring layer bolted onto printers people already print to directly — every job is designed to flow **through** PrintOps first. Clients submit print jobs to PrintOps-advertised IPP queues; PrintOps applies policy (authentication, quotas, cost accounting, secure-release holds, RBAC) and then forwards the job to the physical printer's real IPP/IPPS endpoint.

This is what makes full visibility, policy enforcement, accurate cost accounting, and secure/follow-me release possible without proprietary vendor print-server software or client-side drivers.

**Implementation (Phase 1, done):** rather than hand-writing an IPP server, the proxy is built on CUPS (the reference IPP implementation) running natively on the host — see `infra/cups/README.md`. Each PrintOps-registered printer gets a CUPS queue whose device-uri (`printops://<printer-uuid>`) points at a custom backend (`infra/cups/backends/printops`, installed to `/usr/lib/cups/backend/printops`), not the real printer. CUPS invokes that backend for every job; it logs the job to PrintOps (`Job` model, `app/routers/jobs.py`, authenticated via a separate `PRINTOPS_BACKEND_TOKEN` — not user JWT, since it's a service-to-service call) and then delegates actual delivery to CUPS's own built-in `ipp` backend pointed at the real printer, reusing its already-correct IPP client rather than reimplementing Print-Job encoding. Verified end-to-end against a real printer.

**Also done:** AirPrint/mDNS advertisement, via a static Avahi service file per printer (`infra/cups/generate_avahi_service.py`) since this host's `cupsd` doesn't do its own DNS-SD publishing — gated per-printer by `Printer.airprint_enabled` (off by default). CUPS queue lifecycle is now auto-synced from the `printers` table: creating/updating (queue-affecting fields)/deleting a printer via the API calls `scripts/sync_cups_queue.sh` / `scripts/remove_cups_queue.sh` (`app/printers/queue_sync.py`) instead of requiring a manual run; failures are non-fatal and recorded on `Printer.queue_sync_error`, surfaced in the web UI with a manual resync option, rather than silently leaving a printer un-queued.

**Also done:** policy checks before forwarding — a job is held (spooled, not forwarded) rather than logged-and-forwarded unconditionally when either a per-printer page quota is exceeded or the target printer requires PIN-based release (`app/quotas/service.py:resolve_hold_reason`, checked in `app/routers/jobs.py:create_job` before the CUPS backend ever delivers to the real device). Held jobs release via a per-printer PIN kiosk (`/release/<token>`) or, for quota holds, an admin-only release queue. Follow-Me printing extends release to any Follow-Me-enabled printer, not just the one a job was sent to.

## 4. Apple-First Client Support & User Attribution

Roughly 99% of client devices in this deployment are macOS/iOS/iPadOS, so **AirPrint** — a constrained IPP/IPPS profile discovered via mDNS/Bonjour/DNS-SD — is the dominant, not secondary, printing path. Any design decision that would require installing an agent, driver, or configuration profile on a Mac or iOS device must be rejected in favor of one that works over stock AirPrint. The proxy's client-facing IPP endpoint must remain a fully compliant AirPrint target at all times. Non-Apple clients (Windows/Linux/ChromeOS via native IPP, legacy JetDirect/LPD/SMB) are supported by the same proxy but are the secondary case that shapes fewer decisions.

**User attribution fallback chain** — AirPrint jobs, especially from iOS, frequently omit or cannot cryptographically authenticate the IPP `requesting-user-name` attribute. Rather than accepting unreliable attribution, PrintOps resolves the submitting user through an ordered, pluggable chain of strategies:

1. **(Implemented)** Trust IPP `requesting-user-name` when present and non-generic.
2. **(Implemented)** Fall back to MDM-reported device-to-user mapping — Mosyle is the target MDM — by correlating source IP/hostname/device serial at job-submission time. `app/attribution/resolve.py` + `app/integrations/mosyle.py` + a Settings UI (encrypted-at-rest credentials, device-cache sync). The IP→MAC correlation step (`_lookup_mac_for_source`) that used to always return `None` is now backed by a ClassGuard integration (the org's own DHCP/DNS/web-filter platform, which sees client MACs on subnets the print server itself can't) — `app/integrations/classguard.py`.
   - This org uses **Mosyle Manager** (K-12 schools, `managerapi.mosyle.com/v2`), not Mosyle Business (enterprise/higher-ed, `businessapi.mosyle.com/v1`) — two different products/hosts/API versions. Manager's auth is a two-step JWT exchange (`POST /login` with `{accessToken, email, password}` in the body → bearer JWT in the response's `Authorization` header → that JWT plus `accessToken` in the body on every subsequent call), not the single-request Basic-auth-plus-header scheme Business docs describe — confirmed 2026-07-03 against a real `{"error":"accessToken Required"}` response and a verified reference implementation (github.com/instipod/pymosyle). Device records returned by `/listdevices` already embed the assigned user (`username`/`useremail`) directly, so no separate `/listusers` call exists or is needed.
3. **(Implemented)** Fall back to Google Admin Console (Workspace) device-to-user records for Chromebook/Google-managed devices — mirrors the Mosyle strategy above, same roster PrintOps already syncs.
4. Final fallback: still falls through to `"unknown"`/the raw CUPS value with `attribution_method="unresolved"` rather than being routed to a dedicated secure hold queue requiring release-time authentication — that specific "unknown user → forced hold" behavior described here is still just direction, not built. (A general PIN-based hold-and-release mechanism *is* built — see §3 — but it's triggered per-printer, not per-unresolved-attribution.)

This resolver chain is an interface with ordered strategies, so future identity sources (LDAP/AD, Microsoft Entra ID) can be added as additional fallback strategies without changing the job pipeline itself.

## 5. Module Boundaries

Each of the following will live under `apps/api/app/<module>/` (router + service + models slice) once built, so modules can later be split into separate deployable services if needed without a rewrite:

- **Discovery** — *(manual add + single-device probing implemented; network-wide scanning not yet built)* mDNS/DNS-SD/SNMP scanning to find printers automatically is still planned. Today, an admin manually enters a printer's IP (`apps/api/app/routers/printers.py`), PrintOps probes that one device over IPP, and a duplicate-detection check (name/IP/hostname/serial) warns before a likely accidental re-add.
- **Capability Detection** — *(implemented, including ongoing re-verification)* driven by IPP `Get-Printer-Attributes` at add-time, via a manual "rediscover" endpoint, and via a fleet-wide 30-minute background loop (not just add-time/manual); duplex/color/stapling/hole-punch/booklet/PIN/accounting-code/output-bin/TLS(IPPS)/default-page-size/per-tray-media support is parsed dynamically from whatever the device reports (`apps/api/app/printers/capabilities.py`), never hardcoded per model. Runs against a short list of candidate IPP resource paths, with a per-printer override for non-standard setups.
- **Queue Management** — *(implemented, in a simpler shape than originally envisioned)* rather than a full named queue-type system, this shipped as per-printer flags: `release_required` (secure/PIN-release), `follow_me_enabled` (release at any Follow-Me printer, not just the one a job was sent to, including virtual Follow-Me queues with no real device behind them), and per-color/per-user page quotas. No department/building queue grouping beyond the descriptive fields already on `Printer`.
- **Job Tracking & Analytics** — *(implemented)* every proxied job is logged (`app/models/job.py`, `app/routers/jobs.py`) with printer, submitting user (via the attribution chain in §4), device, document name, size, page count, color/duplex, and forward status. A live TV-dashboard view and a full Insights report (timelines, leaderboards, environmental/cost impact, admin-saved snapshots, CSV/print-to-PDF export) both consume this data.
- **Cost Accounting** — *(implemented, real-cost-driven rather than flat rate tables)* real per-printer, per-color toner cartridge cost and rated page yield (auto-detected part numbers for HP/Canon from SNMP) drive per-page cost, falling back to a flat org-wide rate only for an unconfigured printer; per-printer/per-user page quotas evaluate at job-submission time (allow/hold). Department chargebacks and budgets specifically are not built.
- **Secure Printing / Release** — *(implemented: PIN/Employee-ID + follow-me; badge/QR not built)* jobs park in a held state at the proxy layer (spooled, not forwarded) until released at a per-printer kiosk URL via Google Workspace Employee ID, or admin-released for a quota hold — a natural extension of the proxy model, not a bolt-on. Per-user release bypass is also supported.
- **Auth & RBAC** — *(implemented, simpler role set than originally envisioned)* Google Workspace OAuth or a local admin fallback; roles are admin / viewer / a read-only OU-scoped role (not the full Super-Administrator-to-Student hierarchy originally sketched), checked centrally via an API dependency, not ad hoc per router. Microsoft Entra ID, LDAP/AD, and SAML as additional identity providers are not built.
- **Notifications** — still not built. Email, Google Chat, Microsoft Teams, Slack, Discord, and generic webhooks, triggered by domain events (printer offline, low toner, job held, quota exceeded), remain direction only.
- **Integrations** — *(partially implemented)* Google Workspace (SSO + directory/roster sync + ChromeOS device attribution), Mosyle (MDM device roster + attribution), ClassGuard (MAC-lookup for attribution), Zabbix (external fleet polling), and an LDAP relay (serves the synced roster to printers/copiers for scan-to-email address-book lookups, not an identity-provider integration) are real. Microsoft 365, Jamf, Zammad, Snipe-IT, Tactical RMM, Prometheus, and SAML/Entra ID are not built.
- **Reporting** — *(implemented for export; scheduling not built)* CSV export and browser print-to-PDF are used throughout (Printers, Usage, Insights, Toner Cartridges); there is no scheduled/emailed report delivery yet.
- **Audit Logging** — still not built as a cross-cutting concern. Not every state-changing action is centrally recorded independent of which module triggered it.

## 6. Multi-Tenancy Approach

A single PrintOps deployment is intended to serve multiple organizations/districts, with MSPs managing several tenants under one control plane. The eventual data layer will enforce a `tenant_id` boundary on every table and every query/policy check as a first-class concern from day one — not retrofitted after the fact. The exact strategy (shared-schema with row-level scoping vs. schema-per-tenant vs. database-per-tenant) is an open decision (see §10), but whichever is chosen, no future module should be designed assuming a single-tenant world.

## 7. Extensibility: How Future Modules Plug In

Future modules — fleet management, supply inventory, automatic toner ordering, preventative maintenance tracking, printer lifecycle/asset management integration, AI-powered print optimization, predictive failure detection, OCR/document workflows, scan-to-cloud, print policy enforcement, digital signatures, sustainability dashboards — are all designed to consume the same printer registry, job/cost ledger, tenant model, and event stream (or notification/webhook bus) rather than requiring new proxy paths or client-facing protocols. Adding one should be additive: a new module directory, new adapters, new consumers of existing data — not a restructuring of the core.

## 8. API Surface Direction

REST, with every real domain endpoint under an `/api/v1` prefix now (`/healthz` and `/auth/*` stay unprefixed). GraphQL is still considered later as an additive read layer for complex dashboard queries, not a replacement for REST — not built.

## 9. Security Posture

HTTPS everywhere, CSRF protection on browser-facing state changes, MFA support, and rate limiting are still direction only. Two pieces are real: **server-side RBAC enforced per endpoint** (checked centrally via an API dependency — see §5's Auth & RBAC status) and **encryption at rest** for stored credentials/secrets that need to be recoverable (third-party integration credentials, printer reference passwords) via a Fernet key (`apps/api/app/core/crypto.py`) — not a blanket "every module gets this automatically" guarantee yet, applied where a module actually stores something sensitive. Auth itself is JWT-based, not a stub (see `apps/api/app/routers/auth.py` and §10's note on token storage).

## 10. Open Questions / Deferred Decisions

- IPP proxy transport and deployment shape (in-process with the API vs. standalone service; how it shares state with `apps/api`).
- Tenant data strategy: shared-schema row-level scoping vs. schema-per-tenant vs. database-per-tenant.
- Timing and shape of GraphQL support.
- Event bus / message queue choice for the domain event stream that notifications, audit logging, and future AI modules will consume (candidate: Redis Streams, given Redis is already in the stack).
- CUPS's role long-term: primarily a legacy-protocol bridge (JetDirect/LPD/SMB) and local rendering fallback, vs. a larger role in the IPP proxy itself.
- The current login page stores its JWT in `localStorage` (not an httpOnly cookie) — a deliberate minimal-scope choice, not a final design. Should be revisited (session cookie + CSRF protection, or a proper refresh-token flow) once the real Auth/RBAC module is built, per the Security Posture requirements in §9.

These are intentionally undecided — future sessions should treat them as open, not assume an answer from the absence of code.
