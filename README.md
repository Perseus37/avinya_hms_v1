# Avinya HMS

Avinya HMS is a multi-tenant hospital management platform. The repository contains a FastAPI hospital API, a separate SaaS control-plane application, a React onboarding console, PostgreSQL/Alembic tooling, Redis integration, and a pytest suite.

## Repository map

```text
backend/
  __init__.py              Importable backend package
  main.py                  Hospital API entry point
  core/                    Shared configuration, database access, audit, dependencies
  domains/<domain>/        Tenant-owned models, schemas, routers, and services
  control_plane/
    models.py               Control-plane SQLAlchemy models
    api/routers/            Provisioning, fleet, billing, migration, and console routes
    services/               Control-plane auth, catalogs, pricing, and provisioning
    alembic/                Independent control-plane Alembic history
    app.py                  Control-plane FastAPI composition
  schemas/common.py         Shared API schemas
  security/                 Encryption helpers
  scripts/                  Startup, migration, backup, and restore commands
  alembic/                  Tenant-database Alembic environment and revisions
onboarding-ui/
  src/components/          Shared control-plane UI components
  src/views/               Control-plane screens
  src/hooks/               Authenticated API and fleet/catalog state hooks
  src/lib/                  Shared UI helpers
tests/                     Pytest integration and workflow tests
docs/                      Architecture and operations documentation
Dockerfile                 UI build and Python runtime stages
docker-compose.yml         Local stack
docker-compose.prod.yml    Traefik-backed production stack
```

There is one active migration studio at `onboarding-ui/src/views/MigrationStudio.jsx`; the obsolete root-level duplicate and its unused Axios client have been removed.

For the current-versus-proposed module boundaries and known structural risks, see [docs/PROJECT_STRUCTURE.md](docs/PROJECT_STRUCTURE.md).
Tenant domains own their ORM models, request/response schemas, V1 routers, and
business services. Control-plane routes and services are imported from their
canonical `backend.control_plane.api.routers` and
`backend.control_plane.services` packages.

## Knowledge transfer and operations

- **[Complete Enterprise Guide & System Manuals](docs/complete_guide/README.md)** — the definitive, end-to-end documentation suite explaining the entire platform conceptually and operationally without needing to read code, complete with error troubleshooting runbooks and clinical safety guides.
- [Enterprise Master System Documentation](docs/ENTERPRISE_SYSTEM_DOCUMENTATION.md) — comprehensive enterprise master handbook detailing system topology, all 26 domains, clinical safety (CPOE, NEWS2, BCMA, Controlled Substances Vault), regulatory gateways (ABDM M1-M3, NHCX, HL7 v2 MLLP), RCM automated tariff/census billing, and NABH 5th Ed / JCI 7th Ed quality indicators.
- [Master System Architecture and Technical Specification](docs/SYSTEM_ARCHITECTURE_AND_DESIGN.md) — comprehensive high-level and low-level design (HLD & LLD), covering multi-tenancy, all 24 healthcare domains, envelope encryption, background queues, and disaster recovery.
- [System Inventory and Boundaries Specification](docs/SYSTEM_INVENTORY_AND_BOUNDARIES.md) — complete inventory of what the system contains (subsystems, tables, APIs, controls) versus what is explicitly shelved or out of scope (patient portal, tenant clinical UI).
- [Project Report and KT](docs/PROJECT_REPORT_AND_KT.md) explains the system
  topology, database ownership, backend domains, frontend files, tests, and
  recommended reading path for a new engineer.
- [Operations Runbook](docs/OPERATIONS_RUNBOOK.md) covers startup, health
  checks, releases, migrations, backups, restores, troubleshooting, and release
  readiness.
- [Migration Studio Guide](docs/MIGRATION_STUDIO.md) documents CSV imports,
  ETL operations, duplicate policies, and known limitations.
- [Production Deployment Guide](docs/PRODUCTION_DEPLOYMENT.md) covers VM/cloud
  deployment choices, cost guidance, secrets, networking, and project copying.
- [Production Readiness Roadmap](docs/PRODUCTION_READINESS_ROADMAP.md) tracks
  go-live blockers, evidence requirements, scaling gates, and future work.

`docs/PROJECT_REPORT_AND_KT.md` is the current source-oriented handover.
`docs/ARCHITECTURE_AND_KT.md` and the existing
`docs/ARCHITECTURE_AND_KT.docx` contain legacy material; verify it against
current code before relying on it.

## Local development

Copy `.env.example` to `.env`, replace the development placeholders with unique
local values, and keep `.env` untracked. The same environment file is the source
for deployment-specific ports, worker count, timezone, tenant database prefix,
and runtime limits; the application no longer falls back to embedded values
for those settings. Start the stack with:

```sh
docker compose up --build
```

Compose runs `backend/scripts/bootstrap.py` in the one-shot `db-init` service
before starting the API. It creates the configured control-plane and default
tenant databases when missing and applies the control-plane migration history.
Development also upgrades active cloud tenants. Production initializes a newly
created default tenant but deliberately does not migrate existing tenants at
every restart. It verifies active production tenant revisions and ORM schema
parity, and blocks startup if a tenant was not explicitly migrated. Use the
canary/fleet process below. This prevents a routine container restart from
silently rolling schema changes across the hospital fleet. PostgreSQL and
Redis are exposed only on the local host in this Compose file.

Both Compose configurations require `REDIS_PASSWORD`. Compose passes only that variable to the Redis container; Redis fails at startup if it is absent, and its health check authenticates with it.

Backend modules, tests, Alembic environments, and ASGI launch targets now use the `backend.*` package namespace; the runtime no longer needs custom `PYTHONPATH` entries.

To run the onboarding UI separately during development, start its Vite server
from `onboarding-ui/` with `npm run dev`. Vite proxies `/api` requests to the
`VITE_API_PROXY_TARGET` configured in `.env`. The built UI uses same-origin
`/api` requests.

Tenant URLs are generated centrally by `Settings.tenant_url()` using
`BASE_DOMAIN` and `ENVIRONMENT`: local development uses
`http://<slug>.<BASE_DOMAIN>:<API_PORT>`, while production uses
`https://<slug>.<BASE_DOMAIN>` through the production reverse proxy. These
deployment-specific values are required in the environment file rather than
silently defaulted in Python. Set `TENANT_URL_TEMPLATE` only when a deployment
needs a custom URL, using `{slug}` and `{BASE_DOMAIN}` placeholders. Set
`CORS_ORIGINS` once for both API and control-plane CORS, and `APP_TIMEZONE`
once for API/worker/Postgres timezone configuration. The API root redirects to
Swagger (`/docs`); this repository does not currently include a separate
tenant-facing hospital web UI.

The onboarding console sends the configured Master Setup Key with API requests. If the server also sets `CONTROL_PLANE_MASTER_KEY`, enter that value in the separate **Control Plane Secret** field; it is sent as `X-Control-Plane-Secret`. Both values are retained in the browser's local storage, so use the console only in a trusted browser profile and clear the saved keys when finished.

Run tests locally with:

```sh
python -m pytest -q
```

The Docker `testing` build target includes the test suite and `pytest.ini`:

```sh
docker compose --profile test run --build --rm test-runner
```

## Migrations

Compose runs schema initialization and migration as a one-shot `db-init` service before starting the API. A failure leaves that service exited and blocks API startup. The onboarding portal starts after the API is healthy. Standalone Docker runs do not run bootstrap automatically; start the database and run `python backend/scripts/bootstrap.py` before starting the API.

The local Compose stack explicitly enables `DEVELOPMENT_AUTO_CREATE_SCHEMA`. If Alembic revision files are absent, bootstrap creates tables from the complete tenant/control-plane ORM metadata only when the database has no application tables. If a volume already contains a schema matching the current models, it is reused without resetting data. Existing schema drift is reported and left untouched. This fallback is limited to `development`, `dev`, and `testing`; production Compose does not enable it and still requires checked-in migrations. `docker compose down` preserves volumes and data; deleting a local database volume is destructive and should be done only for disposable development data.

Tenant databases use the Alembic environment under `backend/alembic/`. The
control-plane database has its own Alembic configuration and revision history
under `backend/control_plane/`. Schema changes must include a reviewed Alembic
revision. Development bootstrap upgrades active tenants; production startup
does not migrate existing tenants. Use the default-tenant canary followed by
an explicitly approved fleet rollout:

Tenant provisioning persists the selected pricing model, billing cycle, module entitlements, quotas, blueprint, and initial seed options. The fleet list and tenant command drawer read those saved settings back from the control-plane and tenant databases.

```powershell
# From the repository root, with local Compose PostgreSQL running and its
# published port reflected in POSTGRES_PORT for this command:
$env:POSTGRES_HOST = "127.0.0.1"
python -m backend.scripts.migrate --default-only -m "describe_change"
```

The command generates and applies a tenant revision against the local default
tenant. Review and commit the generated file before building a release. Never
generate revisions on production. For production, build the reviewed image,
run the default canary and fleet migration as one-off tasks, then start the
stack; see the [production rollout guide](docs/PRODUCTION_DEPLOYMENT.md).

Create control-plane schema revisions with:

```sh
python -m alembic -c backend/control_plane/alembic.ini revision --autogenerate -m "describe_control_plane_change"
```

Generate tenant revisions through the canary flow above. Review every
generated migration before deployment. Production `db-init` upgrades the
control-plane schema and initializes a newly created default tenant only;
existing tenant migrations remain an explicit release step. Bootstrap detects
incompatible unversioned control-plane schemas rather than stamping them
automatically.

For an existing unversioned control-plane database, bootstrap adopts it only when its schema already matches the current models or differs only by missing tables; it adds missing tables, validates the complete schema, and then records the baseline. It refuses to stamp databases with incompatible columns or constraints, and it rejects drift in versioned databases unless a reviewed migration repairs it. Do not replace an established Alembic revision chain with a new baseline for databases that may already have recorded older revision IDs; plan and test a bridge or controlled re-baseline first.

The current tenant and control-plane baselines are `6f591125aedc` and `95769c3ab4b3`, respectively. These are fresh-install schemas; existing databases stamped with earlier revision IDs need a tested migration bridge before deployment. Control-plane migrations are tested on SQLite for fresh installation, safe adoption of a compatible unversioned schema, repeated startup, and explicit rejection of schema drift. PostgreSQL offline SQL generation succeeds; a live PostgreSQL upgrade was not run in this environment.

## Production deployment

Copy `.env.production.example` to a protected `.env.production` on the
deployment host, replace every placeholder, and supply secrets through an
approved secret manager. Never use the example values as production
credentials. Production Compose requires its deployment-specific DNS/TLS
settings and does not enable development schema creation. Validate without
printing resolved values:

```sh
docker compose -f docker-compose.prod.yml --env-file .env.production config --quiet
docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build
```

Production bootstrap does not roll migrations across existing tenant databases.
Use staging, a reviewed migration, an explicit canary tenant, a verified
backup, and an approved fleet rollout before deploying application code that
requires the new schema. The Compose file is a deployment template, not
evidence of regulatory compliance or production readiness. Validate security
controls, backups and restores, migration compatibility, capacity, and
applicable healthcare regulations in the target environment.

For end-to-end VM deployment commands, AWS/Azure/GCP options, cost trade-offs, backup guidance, and clean project-copy commands, see [docs/PRODUCTION_DEPLOYMENT.md](docs/PRODUCTION_DEPLOYMENT.md).
