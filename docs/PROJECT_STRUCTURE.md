# Project structure and structural findings

For the detailed project report, domain-by-domain file map, knowledge-transfer
reading path, operational boundaries, and limitations, see
[PROJECT_REPORT_AND_KT.md](PROJECT_REPORT_AND_KT.md). For commands and
production maintenance safety, see [OPERATIONS_RUNBOOK.md](OPERATIONS_RUNBOOK.md).

This document reflects the repository layout as it exists today. The proposed layout below remains a longer-term destination for consolidating application composition, infrastructure, and deployment concerns.

## Current structure

```text
backend/
  __init__.py             Importable backend package
  main.py                  Hospital API composition and /v1 mount
  core/
    config.py              Environment-backed settings
    database.py            Shared control-plane and tenant DB access
    audit.py               Shared tenant ORM audit mixin/listener
    dependencies.py        Authentication, authorization, request dependencies
    event_bus.py            Shared Redis event publishing
    seed.py                 Tenant reference-data bootstrap
  domains/<domain>/        Tenant-owned models, schemas, routers, and services
  control_plane/
    models.py               Control-plane SQLAlchemy models
    api/routers/            SaaS, provisioning, fleet, migration, and console routes
    services/               Control-plane auth, catalogs, pricing, and provisioning
    alembic/                Independent control-plane Alembic history
    app.py                   Control-plane FastAPI composition
  schemas/common.py         Shared API schemas
  security/                 Encryption helpers
  scripts/                  Bootstrap, staged migration, backup, restore
  alembic/                  Tenant database migration environment/history
onboarding-ui/
  src/components/           Shared control-plane UI components
  src/views/                Control-plane screens, including MigrationStudio
  src/hooks/                Authenticated API and fleet/catalog state hooks
  src/lib/                  Shared UI helpers
tests/                       Pytest suite
docs/                        Architecture and operations documents
```

There are two ASGI applications: `backend/main.py` serves the hospital API and mounts its V1 app at `/v1`; `backend/control_plane/app.py` serves tenant provisioning and administration. Compose launches both applications directly from their canonical modules. They share configuration and database modules. Tenant ORM models, domain schemas, tenant routers, and business services are owned by `backend/domains/<domain>/`.

Control-plane persistence models live in `backend/control_plane/models.py`. HTTP endpoints are composed from `backend/control_plane/api/routers/`, while shared authentication, pricing, and hospital blueprint logic live under `backend/control_plane/services/`. The control-plane Alembic environment remains under `backend/control_plane/alembic/`.

The React console uses `useControlPlaneApi` for authenticated JSON and multipart requests and `useFleetData` for fleet/catalog synchronization. The active migration screen is `onboarding-ui/src/views/MigrationStudio.jsx`; its duplicate root-level screen and unused Axios client have been removed. Vite proxies development `/api` calls to the backend.

Patient registration and updates are exposed by the tenant API in
`backend/domains/patients/`; this repository does not include a tenant-facing
patient registration UI. Patient `address` remains optional, and the structured
`pincode`, `city`, `district`, `state`, and `village` fields are also optional
for backward compatibility. Authenticated clients with `patients:write` can
request locality suggestions from
`GET /v1/api/patients/pincode-suggestions?pincode=560001`. The endpoint validates
six-digit Indian PIN codes and returns postal-office/locality names with their
district and state; clients can use those names to suggest village values and
leave city entry editable. The lookup forwards only the PIN code to the
external provider. If the lookup is unavailable, it responds with HTTP 502 so
clients can allow manual entry.

## Structural risks and current fixes

| Area | Finding | Status |
|---|---|---|
| Migration history | The current tenant and control-plane baseline revisions are `6f591125aedc` and `95769c3ab4b3`. The tenant Alembic environment binds only tenant `Base` metadata, while control-plane Alembic has an independent configuration and history. | Fresh initialization is covered by tests; these baselines are not backward-compatible with removed revision IDs and have not been live-tested against PostgreSQL. |
| Control-plane schema | The control plane has an independent Alembic history. Bootstrap adopts compatible unversioned schemas and refuses incompatible schemas; it rejects drift in already-versioned schemas until a reviewed migration repairs it. | Fresh schema and safe unversioned adoption are covered by SQLite migration tests; not live-tested on PostgreSQL. |
| Startup failure handling | The one-shot bootstrap aborts on database, migration, or seed failure, leaving the API gated on successful database initialization. | Failure blocks API startup without an API restart loop; investigate the failed step rather than stamping migrations blindly. |
| Control-plane catalog bootstrap | Pricing/onboarding catalog tables are part of control-plane ORM metadata and its Alembic baseline. Bootstrap/test setup seed catalog rows idempotently; the entitlement handler passes its control-plane DB session to the pricing calculator. | Fixed; catalog seed is idempotent, while changes to catalog schema require control-plane migrations. |
| Migration startup | Compose runs `backend/scripts/bootstrap.py` as the one-shot `db-init` service before API startup. It creates configured databases, migrates schemas, and seeds the control plane and default tenant. | Migration failures block API startup; existing persistent data is not deleted. |
| Development schema recovery | Local Compose enables ORM `create_all` only as a fallback when revision files are missing. It creates a schema only in an empty database and reuses an existing database only when it matches current ORM metadata. | Production does not enable fallback; schema drift is never silently repaired or wiped. |
| Cached tenant engine lifecycle | Fleet/control-plane endpoints disposed tenant engines after requests even though those engines are owned and cached by `TenantEngineRegistry`. This could invalidate shared connections and caused the in-memory tenant databases used by tests to lose their schema between requests. Endpoints now close sessions but leave cached engines to the registry. | Fixed. |
| Docker test image | `.dockerignore` excluded `tests/` and `pytest.ini`, even though the Dockerfile test target runs pytest. They are now explicitly included in the build context. | Fixed. |
| Onboarding startup ordering | The portal could start before API bootstrap initialized the control-plane database. Both Compose definitions now gate portal startup on API health. | Fixed. |
| Frontend image reproducibility | Docker used `npm install` despite a committed lockfile. The UI build now uses `npm ci`. | Fixed. |
| Import/package boundaries | Backend modules, tests, Alembic environments, and ASGI entry points now use the `backend.*` namespace. Docker and pytest no longer add backend subdirectories to `PYTHONPATH`; scripts are an explicit package as well. | Migrated for current runtime paths; retain a small repository-root bootstrap only in directly executed scripts/Alembic environments. |
| Domain boundaries | Tenant ORM definitions, V1 schemas, tenant routers, and business services are owned by domain packages. Shared event publishing remains in `core`. All tenant models share one `Base` and audit metadata. | Canonical package imports are used throughout the application and tests; migration metadata parity is validated. |
| Control-plane boundaries | Route implementations are grouped under `control_plane/api/routers/`; authentication, pricing, and provisioning blueprints are under `control_plane/services/`. Migration ownership is independent. | Canonical route composition and control-plane migration tests pass. |
| Frontend duplication | A root-level migration studio and unused Axios client duplicated the active view and shared request hook. | Removed; `onboarding-ui/src/views/MigrationStudio.jsx` is the single active screen. |
| Redis configuration | Both Compose files require `REDIS_PASSWORD`, pass only that variable into Redis, and authenticate the health check. | Development Compose and a secret-free YAML regression test pass; production config awaits deployment `.env.production`. |
| Documentation drift | The old README described root-level source files and Compose tool profiles that do not exist. The current README now documents the actual tracked layout and startup paths. Other detailed handover claims still require verification against code and deployments. | Partly fixed; continue updating docs with validated behavior. |
| Production configuration | `docker-compose.prod.yml` requires a deployment-provided `.env.production`; it is absent from the checked-out worktree. | Expected to be supplied securely by deployment; production config has not been validated. |
| CI configuration | CI workflow files are deleted in the current worktree. | Left unchanged because the deletion may be intentional; restore/replace CI before relying on automated checks. |
| Generated structure dump | A root-level generated filesystem listing duplicated this maintained structure document and was not used by the application. | Removed; this document is the maintained source map. |

## Recommended destination

Keep the hospital product and SaaS control plane as separate application packages. Move toward explicit importable packages, independent database/migration ownership, and domain-contained code:

```text
src/
  avinya/
    shared/
      config.py
      database/
      security/
      observability/
    hms_api/
      app.py
      api/v1/
      domains/
        patients/
        clinical/
        scheduling/
        diagnostics/
        pharmacy/
        billing/
        ...
    control_plane/
      app.py
      api/
      services/
        provisioning.py
        fleet_migrations.py
        billing.py
migrations/
  tenant/
  control_plane/
scripts/
tests/
  unit/
  integration/
  migrations/
onboarding-ui/
```

Each domain should own its models, schemas, routes, and services where practical. Keep API versioning at the HTTP boundary rather than duplicating domain logic for each version. Keep infrastructure and deployment files at the repository root.

## Safe migration sequence for the restructure

1. Confirm which revision IDs are recorded in every deployed database and decide how the new baseline relates to those histories. Test the migration path against a copy of a representative database before deployment.
2. Keep backend imports package-qualified and add new modules under their owning package instead of restoring flat import paths.
3. Keep tenant and control-plane migration histories independent; both startup paths now apply explicit Alembic migrations. Continue validating revisions against a live PostgreSQL instance before deployment.
4. Keep models, schemas, routes, and business services in their owning packages; import them through canonical package paths and avoid recreating retired facade modules.
5. Restore or replace CI workflows, then require import checks, unit/integration tests, migration upgrade tests, and frontend builds before deployment.
