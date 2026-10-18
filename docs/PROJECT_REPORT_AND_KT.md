# Avinya HMS — Project Report and Knowledge Transfer

**Audience:** engineers, operators, reviewers, and a successor team
**Status:** source-oriented handover, based on the repository in this workspace
**Last checked:** 2026-10-16

This is the maintained high-level project and codebase guide. It describes
implemented structure and deployment topology; it is not a compliance
certification, a complete API reference, or a substitute for verifying the
target deployment. Pair it with the [Operations Runbook](OPERATIONS_RUNBOOK.md)
and [Production Deployment Guide](PRODUCTION_DEPLOYMENT.md).

## 1. Executive summary

Avinya HMS is a multi-tenant hospital-management backend and SaaS control
plane. It includes a FastAPI tenant API, a separate FastAPI control-plane API,
a React onboarding/fleet console, PostgreSQL, Redis, Alembic migrations,
encrypted database backups, and pytest integration tests.

Each registered tenant has a separate PostgreSQL database. The control-plane
database stores the tenant registry, commercial/provisioning configuration,
fleet/audit records, and federation discovery metadata. The tenant databases
store that hospital's users, clinical and operational records, and tenant
configuration. This database-per-tenant design reduces accidental
cross-tenant query exposure, but does not alone prove tenant isolation: request
routing, credentials, authorization, network boundaries, backups, and
operations still need to be configured correctly.

The React UI in this repository is the **onboarding/control-plane console**. It
is not a complete hospital staff/clinical web application. The tenant API
provides endpoints and Swagger documentation; a separate staff UI is not
present in the current source tree.

## 2. Runtime topology and request flow

```text
Operator browser
  └─ Onboarding React SPA (built into onboarding-ui/dist)
       └─ Control-plane FastAPI (backend/control_plane/app.py)
            ├─ hms_control_plane PostgreSQL database
            └─ tenant DB connections for fleet operations

Hospital client / integration
  └─ Traefik in production (direct host port locally)
       └─ Tenant FastAPI (backend/main.py; root routes and /v1 mount)
            ├─ Host/subdomain -> tenant registry -> isolated tenant DB
            ├─ hms_control_plane lookup for routing/configuration
            └─ Redis for rate limiting, cache/events, and realtime streams

Local backup sidecar
  └─ active CLOUD tenant dumps -> encrypted artifacts in hms_backups
       └─ optional local-only vault copy in docker-compose.yml

Production host cron
  └─ PostgreSQL 17 control-plane + tenant pg_dump -> GPG encrypted snapshots
       └─ BACKUP_HOST_PATH local disk + optional rsync/SSH off-host copy
```

### Components and responsibilities

| Component | Entry point / deployment | Responsibility |
| --- | --- | --- |
| Tenant API | `backend/main.py`; local `api` service; production Traefik routes | Authentication, permission-checked tenant endpoints, clinical/operational workflows, health probes, real-time WebSockets. Routes are available at the root `/api/...` and also mounted under `/v1/api/...`. |
| Control plane | `backend/control_plane/app.py`; `onboarding-portal` service | Tenant onboarding/provisioning, pricing/catalogs, fleet operations, migration studio API, snapshots/audit UI API, SaaS administration. Serves the built React SPA when `onboarding-ui/dist` exists in the image. |
| Onboarding console | `onboarding-ui/src/`; Vite build copied into Docker image | Control-plane dashboards and operator workflows; requests go to same-origin `/api` in the built deployment. |
| PostgreSQL | Compose `db` service | One PostgreSQL server/container with the control-plane database and a separate database for each tenant. |
| Redis | Compose `redis` service | Authenticated Redis for app features including caching, rate limits, and pub/sub; persistence is enabled in Compose. |
| Migrations | `backend/scripts/bootstrap.py`, `backend/scripts/migrate.py`, two Alembic histories | Initialize/upgrade control-plane and tenant schemas; bootstrap failure blocks API availability. |
| Backups | `backend/scripts/backup_service.py`, `deployments/backup-postgres.sh`, `deployments/restore-postgres.sh` | Local Compose tenant snapshots plus production PostgreSQL 17 host-cron backups, GPG encryption, checksums, retention, isolated restore, and optional rsync/SSH off-host copy. |

### Tenant request routing

`backend/core/database.py` resolves tenant context from a permitted
`X-Hospital-Subdomain` override in development/testing (or when explicitly
configured), otherwise from the request hostname, with a configured default
tenant fallback. It checks the control-plane registry, tenant active status,
on-prem routing constraints, and maintenance mode before creating a tenant DB
session. Tenant database engines are managed by a bounded registry.

Tenant credentials are not interchangeable by design: JWTs are signed with
tenant-specific keys and carry a tenant slug; `get_current_user` loads an
active tenant-local user and request dependencies check permissions. Recent
code changes also bind protected realtime subscriptions to active users and
stream permissions.

## 3. Databases, ownership, and migrations

There are two SQLAlchemy declarative bases and two migration histories:

| Schema owner | ORM metadata | Alembic config / history | Examples |
| --- | --- | --- | --- |
| Tenant/business data | `backend.core.database.Base`; domain models under `backend/domains/*/models.py` | `backend/alembic.ini`, `backend/alembic/` | Patients, users, encounters, invoices, diagnostics |
| Control-plane data | `ControlBase` and `backend/control_plane/models.py` | `backend/control_plane/alembic.ini`, `backend/control_plane/alembic/` | Tenant registry, catalog, global patient pointers, control-plane audit |

Do not place a model on the wrong base. A schema change needs a reviewed
migration in the correct history. The normal Docker startup `db-init` service
runs `backend/scripts/bootstrap.py`: it ensures the control and default tenant
databases exist, applies both Alembic histories, seeds catalogs, creates the
default tenant registry when needed, migrates active CLOUD tenant databases,
and seeds the default tenant. It is configured as a one-shot dependency of the
API; bootstrap failure should be diagnosed, not bypassed by restarting or
recreating services repeatedly.

`backend/scripts/migrate.py` supports a default-tenant canary/revision flow,
then a fleet or single-tenant rollout. Review generated Alembic code before
applying it. The current tenant history includes a baseline revision; replacing
old revision IDs is not automatically safe for an already-deployed database.
Check the revision history of each database before changing a migration chain.
More detail and commands are in the [runbook](OPERATIONS_RUNBOOK.md#schema-change-and-rollout).

The current model inventory previously checked in this workspace had 72 tenant
tables: 70 inherit the tenant `AuditMixin`; `federation_transfer_logs` and
`role_permissions` do not. This describes ORM declarations, not every
deployed schema or every SQL write path. Actor IDs depend on normal ORM flush
and authenticated session context; background, bulk, or system operations
require explicit handling.

## 4. Backend source map

### Repository root

| Path | Purpose |
| --- | --- |
| `backend/` | Python application source, Alembic histories, scripts, and package modules. |
| `onboarding-ui/` | React/Vite/Tailwind control-plane console source and build manifest. |
| `tests/` | Pytest integration/regression suite. |
| `docs/` | Architecture, production deployment, migration, and KT material. |
| `docker-compose.yml` | Local development Compose services and persistent volumes. |
| `docker-compose.prod.yml` | Traefik-backed production Compose template; it needs secure production configuration and review before use. |
| `Dockerfile` | UI build stage, Python base, development/testing/production targets. Production target runs as the `avinya` non-root user. |
| `requirements.txt` | Python dependencies. |
| `pytest.ini` | Pytest configuration. |
| `.dockerignore` | Controls Docker build context. |
| `.gitignore` | Excludes local/generated and secret files; verify before copying/deploying. |
| `.env` | Local runtime configuration/secrets if present. Never publish or copy into a production bundle. |
| `Audit.md` | Audit-related project notes; verify against current implementation before relying on its claims. |
| `frontend/` | Currently empty in the inspected source tree; not the active onboarding UI. |
| `.github/workflows/` | Intended CI workflow location; no workflow files were present in the inspected workspace. Restore/replace CI before relying on hosted automated checks. |

Keep generated/dependency artifacts out of KT and source changes: `.venv/`,
`node_modules/`, `dist/`, `__pycache__/`, `.pytest_cache/`, bytecode, local DB
files, logs, backups, and secret env files.

### `backend/` shared and application packages

| Path | Purpose / key files |
| --- | --- |
| `backend/main.py` | Creates the tenant FastAPI app, logging/request IDs, CORS, exception handlers, router composition, `/v1` mount, `/` redirect, liveness `/health`, and DB/Redis readiness `/health/ready`. |
| `backend/core/config.py` | Pydantic `Settings`: environment variables, DB/Redis URLs, tenant URL generation, runtime limits, and platform settings. |
| `backend/core/mailer.py` | Shared SMTP, AWS SES, and Azure Communication Services delivery with optional PDF attachments. |
| `backend/core/database.py` | `Base`, `ControlSessionLocal`, tenant context resolution, DB creation and session dependencies, tenant engine registry, and Redis pool. |
| `backend/core/dependencies.py` | Current user/JWT, permission/module dependencies, pagination, rate limiting, federation authentication, and tenant resource quotas. |
| `backend/core/audit.py` | `AuditMixin` timestamp/actor columns and SQLAlchemy `before_flush` actor stamping. |
| `backend/core/event_bus.py` | Tenant-namespaced Redis pub/sub event publishing. |
| `backend/core/seed.py` | Initial tenant permission/role/user and universal reference-data seeding. |
| `backend/security/envelope.py` | Encrypt/decrypt tenant secrets stored in the control plane. Review key management and rotation separately from source code. |
| `backend/schemas/common.py` | Shared API response schemas such as paginated results. |
| `backend/alembic.ini`, `backend/alembic/` | Tenant schema history/environment; `versions/` contains committed tenant revisions. |
| `backend/control_plane/` | Separate control-plane app, persistence, routes, services, and its own migration history; details below. |
| `backend/scripts/` | Startup, staged migrations, backup, and restore commands; details below. |
| `backend/domains/` | Tenant-owned product domains; detailed map below. |

### `backend/control_plane/`

| Path | Purpose |
| --- | --- |
| `app.py` | Control-plane FastAPI composition, CORS, exception/audit handling, route registration, static SPA serving, lifecycle hooks. |
| `models.py` | `ControlBase`, tenant registry, federation nodes/global pointers, audit and snapshot/control-plane tables. |
| `api/routers/fleet_ops.py` | Fleet list/details, health/schema/backup operations, snapshots, controls, audit views. |
| `api/routers/pricing.py` | Pricing, bundle/module, blueprint, and catalog API. |
| `api/routers/provisioning.py` | Tenant provisioning endpoints. |
| `api/routers/migration.py` | CSV upload/analysis, safe ETL preview and execution, duplicate policy logic, audit outcomes. |
| `api/routers/network.py` | Control-plane network/topology endpoints. |
| `api/routers/saas_billing.py` | SaaS billing-related endpoints. |
| `api/routers/ui_console.py` | Portal console-specific API support. |
| `services/auth.py` | Control-plane/vendor-key authentication helpers. |
| `services/blueprints.py` | Provisioning blueprint definitions and behavior. |
| `services/catalogs.py` | Catalog seeding and shared catalog retrieval. |
| `services/migration_catalog.py` | ORM-driven destination table/field catalog for the migration UI. |
| `services/pricing.py` | Pricing calculations and commercial catalog logic. |
| `alembic.ini`, `alembic/env.py`, `alembic/versions/` | Independent control-plane schema migration environment and revisions. |

### `backend/scripts/`

| File | Purpose |
| --- | --- |
| `bootstrap.py` | Container startup database creation, control-plane migration/bootstrap, tenant migration, and default tenant seed. |
| `migrate.py` | Generate/apply staged tenant revisions and apply a revision to one tenant or the active CLOUD fleet. |
| `backup_service.py` | `pg_dump` active CLOUD tenant databases, encrypt snapshots, and optionally copy into the local vault volume in local Compose. |
| `deployments/backup-postgres.sh` | Production host-cron backups of PostgreSQL 17 control-plane and tenant DBs, with GPG encryption, checksums, retention, optional attachment archive, and rsync/SSH replication. |
| `deployments/restore-postgres.sh` | Verify/decrypt one production dump into a new isolated `restore_*` DB without overwriting an existing DB. |
| `restore_tenant.py` | Decrypt and `pg_restore` an encrypted backup into a target DB; potentially destructive to the target DB. |

### Tenant domain packages

Each folder owns related tenant ORM models, request/response schemas, router
endpoints, and services. An `__init__.py` marks the Python package; it may also
re-export the router used by `backend/main.py`.

| Domain folder | Files and responsibility |
| --- | --- |
| `analytics/` | `router.py`: operational, financial, and clinical intelligence endpoints. |
| `billing/` | `models.py`, `schemas.py`, `router.py`, `services.py`: invoices, line items, payments, deposits/refunds, billing calculations and lifecycle. |
| `bloodbank/` | `models.py`, `schemas.py`, `router.py`: blood inventory, compatibility/crossmatch, transfusion safety, radiology study metadata. |
| `clinical/` | `models.py`, `schemas.py`, `router.py`, `services.py`, `nursing_er_router.py`, `vital_registries_router.py`: notes, vitals, diagnoses, ER/triage, nursing and clinical registries. |
| `diagnostics/` | `models.py`, `schemas.py`, `router.py`, `services.py`: diagnostic catalogs/orders/tasks, lab/RIS flows, results and verification. |
| `encounters/` | `models.py`, `schemas.py`, `opd_router.py`, `adt_router.py`: appointments, OPD encounters, admissions, bed movement and discharge. |
| `facility/` | `models.py`, `schemas.py`, `router.py`: floors, wards, rooms, beds and shifts. |
| `federation/` | `models.py`, `schemas.py`, `router.py`: partner clients, privacy-aware patient discovery/import and federation transfer logs. |
| `finance/` | `models.py`, `router.py`: expenses and recurring finance records. |
| `identity/` | `models.py`, `schemas.py`, `auth_router.py`, `passwords.py`: users, roles, permissions, login/JWT, and shared password hashing. |
| `insurance/` | `models.py`, `schemas.py`, `router.py`: insurance plans/policies, preauthorization and claim lifecycle. |
| `integrations/` | `models.py`, `schemas.py`, `router.py`, `listener.py`: equipment/integration records and integration listener behavior. |
| `patients/` | `models.py`, `schemas.py`, `router.py`, `services.py`: patient identity, allergies, patient master data and related services. |
| `pharmacy/` | `models.py`, `schemas.py`, `router.py`: medications, inventory, prescriptions, dispensing and return records. |
| `policies/` | `models.py`, `router.py`: tenant policy settings and privacy/configuration endpoints. |
| `realtime/` | `router.py`: WebSocket endpoints, JWT/user/permission checks, and tenant event subscriptions. |
| `reports/` | `models.py`, `router.py`, `templates.py`, `profiles.py`, `pdf.py`: sandboxed customizable report templates, tenant invoice profiles, PDF export, preview, and print endpoints. |
| `surgery/` | `models.py`, `schemas.py`, `router.py`: theatres, surgery schedule, checklist, anesthesia and implant tracking. |

Use an existing domain package for a new feature where possible. Keep database
models, schemas, routes, and business rules close to their owning domain;
register routes centrally in `backend/main.py`. Shared infrastructure belongs
under `core/`, not in a random domain router.

## 5. Onboarding UI source map

| Path | Purpose |
| --- | --- |
| `onboarding-ui/package.json`, `package-lock.json` | Frontend dependencies and reproducible npm lock. Use `npm ci` for clean installs/builds. |
| `onboarding-ui/index.html`, `src/main.jsx` | HTML shell and React bootstrap. |
| `src/App.jsx` | Top-level view selection, shared state, toast notifications, API inspector, fleet refresh coordination. |
| `src/components/Sidebar.jsx` | Navigation and operator key/theme controls. |
| `src/components/HospitalDrawer.jsx` | Tenant detail drawer and fleet actions. |
| `src/components/KeyGatedFeeOverride.jsx` | Sensitive fee-override UI requiring its key gate. |
| `src/components/RadialGauge.jsx` | Dashboard gauge component. |
| `src/hooks/useControlPlaneApi.js` | Shared authenticated API request logic and browser-session API call inspector log. |
| `src/hooks/useFleetData.js` | Fetch/synchronize tenant fleet, overview, analytics, catalogs, topology, audit and schema status data. |
| `src/lib/constants.js` | Frontend display/config constants and formatting helpers; review when adding changeable UI defaults. |
| `src/views/CommandCenter.jsx` | Fleet and commercial dashboard. |
| `src/views/FleetExplorer.jsx` | Tenant fleet exploration. |
| `src/views/ProvisioningStudio.jsx` | Tenant provisioning workflow. |
| `src/views/MigrationStudio.jsx` | Visual CSV/ETL migration workflow. |
| `src/views/CanaryPipeline.jsx` | Schema migration/canary operator view. |
| `src/views/FederationMesh.jsx` | Federation/operator network view. |
| `src/views/AuditVault.jsx` | Snapshot and audit-ledger view. |
| `src/index.css`, `tailwind.config.js`, `postcss.config.js` | UI styles, Tailwind and PostCSS configuration. |
| `vite.config.js` | Dev server and API proxy settings. |

The onboarding console currently persists entered setup/control-plane keys in
browser local storage. Use it only in a trusted, controlled browser profile and
clear the values after the operator session.

## 6. Test suite and quality gates

`tests/conftest.py` creates SQLite control-plane and tenant databases, tenant
registry entries, seeded data, test user tokens and a fake Redis. The suite is
integration-oriented and tests application behavior through FastAPI clients as
well as selected model/service helpers.

| Test files | Coverage |
| --- | --- |
| `test_01_system_and_tenancy.py` | Startup/system endpoints and tenant routing/isolation. |
| `test_02_iam_and_rbac_matrix.py` | Login, roles, permissions and escalation guards. |
| `test_03_mpi_and_clinical_mdm.py` | Patient identity and clinical dictionaries. |
| `test_04_wards_opd_and_adt.py` | Facility, appointments, encounters/admissions. |
| `test_05_diagnostics_and_lis.py` | Diagnostics catalog, orders, tasks, results. |
| `test_06_pharmacy_and_billing.py` | Pharmacy workflows and billing integrity. |
| `test_07_federation_and_backups.py` | Federation/privacy and backup behavior. |
| `test_08_emr_vitals_and_reports.py` | Clinical charting, vitals and reports. |
| `test_09`–`test_12` | Insurance, nursing/ER, surgery, blood bank and radiology workflows. |
| `test_13`–`test_19` | Analytics, extended clinical/operational edge cases, concurrency and regressions. |
| `test_20_control_plane_catalogs.py` | Control-plane catalogs, migration expressions/duplicate policies, tenant URL and mapping behavior. |
| `test_21_control_plane_migrations.py` | Control-plane schema migration/adoption/repair behavior. |
| `test_22_domain_package_boundaries.py` | Tenant metadata boundaries and shared password hashing. |
| `test_23_compose_redis_config.py` | Compose Redis configuration regression checks. |

Most recently, the local full pytest suite reported **167 passed**. This is not
evidence that PostgreSQL-specific migrations, cloud DNS/TLS, production
secrets, production capacity, or regulatory controls have been validated.
Re-run tests against the current revision before release.

## 7. Current limitations and production decisions

1. **No clinical staff UI in this repository.** The React app is for onboarding
   and fleet administration; `/docs` is API Swagger.
2. **One-host Compose is a single failure domain.** Local Docker volumes do not
   provide off-host recovery or high availability.
3. **Off-host backups still require operator setup.** The production host
   backup script writes encrypted snapshots to an attached local mount and
   optionally copies them by rsync/SSH. Until a separate remote destination,
   credentials, retention, monitoring, and restore drill are configured,
   VM/host loss can still destroy the backup copy.
4. **Migration Studio uses in-process background work.** A process/container
   stop may interrupt an import. Audit rows contain aggregate job counts and
   bounded samples, not a durable/resumable queue state.
5. **API docs are enabled by the app defaults.** Restrict Swagger/OpenAPI in
   production at the application/proxy level if public docs are not desired.
6. **CI needs attention.** No workflow files were present in the inspected
   `.github/workflows/` directory. Add required tests/build/migration checks
   before depending on automated release gates.
7. **Audit mixin coverage is partial.** ORM table inheritance is not the same
   as an audit guarantee on every write path. See
   `ONBOARDING_IMPROVEMENTS.md`.
8. **Compliance is unverified.** Confirm applicable privacy, security,
   residency, retention, breach-response and healthcare obligations with
   qualified reviewers before storing real patient data.

## 8. Recommended knowledge-transfer path

For a new engineer, read in this order:

1. This document's sections 2–4 for service boundaries and ownership.
2. `backend/core/config.py`, `backend/core/database.py`, and
   `backend/core/dependencies.py` to understand configuration, tenant
   resolution and authorization.
3. `backend/main.py` and `backend/control_plane/app.py` to understand route
   composition and application startup.
4. One vertical workflow end-to-end (for example patient registration:
   `domains/patients/router.py` → schema/model/service → tenant DB dependency →
   relevant tests).
5. `backend/scripts/bootstrap.py` and both migration histories before changing
   schema or deployment behavior.
6. The [Operations Runbook](OPERATIONS_RUNBOOK.md) before running maintenance,
   migration, backup or restore commands.
7. The [Migration Studio guide](MIGRATION_STUDIO.md) before importing data.

Start with a small feature/test change locally. Do not change multiple domains,
both migration histories, secrets, and production infrastructure in a single
unreviewed change.
