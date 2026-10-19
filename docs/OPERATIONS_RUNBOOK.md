# Avinya HMS Operations Runbook

This runbook gives repeatable commands and decision points for local Docker
operations and a single-host Compose deployment. It is not a failover
guarantee. Adapt host paths and secret injection to the actual environment.
Never paste secrets into tickets, logs, shared chat, source code, or command
examples.

Related documents:

- [Project Report and KT](PROJECT_REPORT_AND_KT.md)
- [Production Deployment Guide](PRODUCTION_DEPLOYMENT.md)
- [Production Readiness Roadmap](PRODUCTION_READINESS_ROADMAP.md)
- [Migration Studio Guide](MIGRATION_STUDIO.md)

## 1. Safety rules

- Confirm the environment and tenant before every operation that changes data.
- Take and verify a recoverable backup before schema changes, bulk imports,
  destructive maintenance, or restore operations.
-   Do not run `docker compose down -v` against any environment with data; it
  removes named volumes. It is only appropriate when intentionally resetting a
  disposable local development database.
- Never solve a migration failure by repeatedly recreating containers or
  manually stamping Alembic. Preserve logs and inspect the failed revision.
- Do not test restores over a live tenant database. Restore into a new,
  isolated database and verify it before any controlled cutover.
- Treat patient data and decrypted dumps as highly sensitive. Restrict access,
  storage, retention and transfer.
- Do not claim success from container status alone; check API health, DB/Redis
  readiness, tenant login, backup freshness and restore evidence.

## 2. Local setup and startup (Windows PowerShell)

Run from the repository root (`V:\avinya_hms` in this workspace). Copy
`.env.example` to `.env`, then replace its placeholders with local-only
settings. Keep `.env` private and untracked; do not use production secrets for
local development.

```powershell
docker compose config --quiet
docker compose up -d --build
docker compose ps
docker compose logs --tail=150 db-init api onboarding-portal
```

Local endpoints as defined by the current Compose file:

| Service | Address |
| --- | --- |
| Tenant API / Swagger | `http://127.0.0.1:8000/docs` |
| Liveness | `http://127.0.0.1:8000/health` |
| Readiness (DB and Redis) | `http://127.0.0.1:8000/health/ready` |
| Onboarding console | `http://127.0.0.1:8080/` |
| PostgreSQL | Bound to `127.0.0.1:5432` |
| Redis | Bound to `127.0.0.1:6379` |

Do not expose local DB/cache ports to untrusted networks. The `test-runner`
service requires the `test` Compose profile:

```powershell
docker compose --profile test run --build --rm test-runner
```

## 3. Routine checks and observability

```powershell
docker compose ps
docker compose logs --since=30m db-init api clamav onboarding-portal
docker compose logs --since=24h backup-sidecar
curl.exe --fail --show-error http://127.0.0.1:8000/health
curl.exe --fail --show-error http://127.0.0.1:8000/health/ready
curl.exe --fail --show-error http://127.0.0.1:8080/
```

`/health` is a lightweight liveness response. `/health/ready` checks the
tenant database and Redis; Redis may be reported degraded while the DB check
still passes, so read the JSON response. The app logs include request IDs;
capture that ID when investigating an HTTP error.

### Reviewing procurement document download requests

Authorized requests for procurement invoice documents are recorded in the
tenant database before the file response is returned. Each row includes the
invoice number, actor ID and email snapshot, and request timestamp. It records
that an authorized download was initiated; it does not prove the client
received every byte. The audit row remains if the invoice or user is later
deleted, with the corresponding foreign key set to `NULL`. Restrict database
access to authorized operators and apply the hospital's approved retention
policy to these records.

To review recent events, connect to the correct tenant database with an
authorized read-only database account:

```sql
SELECT invoice_number, actor_user_id, actor_email, requested_at
FROM procurement_document_download_audits
WHERE requested_at >= CURRENT_TIMESTAMP - INTERVAL '30 days'
ORDER BY requested_at DESC;
```

The API container's healthcheck calls `/health/ready`; the onboarding portal
checks its own listener on `CONTROL_PLANE_PORT`. The backup sidecar and vault
do not serve HTTP, so they intentionally have no inherited API healthcheck.
Check their logs and backup freshness instead. On first startup, the backup
sidecar waits for `db-init` to finish; transient PostgreSQL connection errors
are logged and retried with backoff.

Check database/storage pressure and host monitoring using the VM/provider
tools. Compose does not provide a complete alerting, log-retention, uptime, or
off-host monitoring system. The browser API inspector is session-local and
does not substitute for server-side audit records.

## 3.1 Identity, operator access, and recovery

Apply the tenant and control-plane Alembic migrations before deploying the
identity lifecycle changes. Privileged tenant users must enroll an
authenticator when `MFA_REQUIRED_FOR_PRIVILEGED=true`; tenant users can enroll
optionally through `/api/auth/mfa/enroll`. Save the one-time MFA recovery codes
shown by `/api/auth/mfa/confirm` outside the browser. Tenant access tokens are
short-lived and server-tracked; `/api/auth/logout`, password change, and
password reset revoke all of that user's active sessions. Password reset
delivery requires a working configured mail provider.

Create a named control-plane operator only after the control-plane migration
has run:

```powershell
docker compose exec api python -m backend.scripts.create_control_plane_operator --email operator@example.org --name "Hospital Operations"
```

The command prompts for a strong password without echoing it. First sign-in
requires TOTP enrollment; subsequent sign-ins require a fresh TOTP code.
Control-plane access tokens expire after 15 minutes and are revocable at
`POST /api/auth/logout`. The React onboarding UI signs in with a named operator,
requires MFA enrollment at first sign-in and a fresh TOTP code on each login,
keeps the access token in memory only, and returns to sign-in when an operator
request receives 401. Refreshing the page requires signing in again. Static
Control Plane master-key authentication is disabled by default and must remain
disabled in production. `CONTROL_PLANE_LEGACY_MASTER_KEY_ENABLED=true` is only
for local migration of approved clients; switch it off once named operator
sessions are in use.

Tenant reset links are single-use and expire after 30 minutes. The email link
opens a same-origin password-reset form, which submits the token from the URL
fragment directly to the API without sending it in the HTTP request URL. If a
reset email is not delivered, check the mail-provider configuration and
server-side mail logs without exposing the token. Never request or forward
reset tokens through support tickets or chat. Deliver initial privileged-user
credentials only to the named administrator through a secure, one-time channel.
Privileged tenant accounts receive only a limited MFA setup challenge after
password authentication. They must request a short-lived code sent to the
account's registered email before an authenticator can be enrolled; the code is
single-use, stored hashed, and expires after 10 minutes. Do not share enrollment
codes or authenticator secrets.

## 3.2 FHIR API scope

The backend exposes a FHIR R4 capability statement at
`/api/fhir/R4/metadata` and read-only Patient search/read endpoints at
`/api/fhir/R4/Patient` and `/api/fhir/R4/Patient/{id}`. Patient calls require a
tenant access token with `patients:read`; patient lookup remains within the
resolved tenant database. Supported search parameters are `_id`, `identifier`, `name`,
`birthdate`, `gender`, `_count`, and the implementation's `_offset` paging
parameter. MRN and ABHA values use the custom identifier systems
`urn:avinya:hms:mrn` and `urn:avinya:hms:abha`.

Patient not-found errors use FHIR `OperationOutcome`; other validation and
authorization errors may use the standard API error format. This is not an
ABDM integration. It does not implement ABDM consent/auth
workflows, registered profiles, Encounter or Observation resources, or FHIR
write operations. Obtain partner-profile and sandbox approval before
advertising or enabling an ABDM connection.

## 3.3 Emergency patient access

Authenticated users with `break_glass:request` may request a reasoned emergency grant with
`POST /api/break-glass/patients/{patient_id}/grants`. The grant lasts 30 minutes
and only the requesting account can use
`GET /api/break-glass/grants/{grant_id}/patient`; it exposes a limited patient
demographic and active-allergy view, not general clinical chart access. Each
use is durably recorded. Each account is limited to
`BREAK_GLASS_MAX_REQUESTS_PER_DAY` grants per UTC calendar day (default 5). A
`break_glass` WebSocket stream is restricted to users with `iam:read` and
receives the grant ID alert. Do not send PHI in the alert payload. A Redis
outage can delay the alert but does not undo or block the committed emergency
grant.
The default Attending Physician and Registered Nurse roles have
`break_glass:request`; administrators and superusers do not receive an implicit
exception. This dedicated permission does not grant `patients:write` or permit
patient registration and allergy updates. The migration preserves access for
existing custom roles that already had `patients:write`; tenant administrators
may grant the permission to additional roles after clinical approval.

Independent reviewers with `iam:write` use
`GET /api/break-glass/reviews/pending` and
`POST /api/break-glass/reviews/{grant_id}`. Self-review is rejected. Reviews
are due within 24 hours; overdue records are flagged in the pending list, but
the current MVP does not send an automatic reminder or escalation. Hospitals
must assign review ownership and monitor the queue until an alerting worker is
implemented.

## 3.4 Hospital support service workflows

The licensed tenant APIs provide ambulance vehicle/dispatch tracking
(`/api/ambulance`), patient dietary orders (`/api/dietary`), instrument-set and
sterilization-batch tracking (`/api/cssd`), and mortuary custody/release
(`/api/mortuary`). Access requires each module's corresponding license and
`<module>:read` or `<module>:write` permission. Status changes are recorded in
workflow history endpoints where provided.

These are workflow MVPs, not dispatch-center integrations, dietary/allergy
decision support, a validated sterilizer/traceability system, or a complete
medico-legal mortuary register. In particular, CSSD release depends on staff
recording both indicator results accurately; failed indicators quarantine the
batch and this MVP does not support reprocessing. Hospitals must validate
local SOPs, role assignments, device/dispatch integrations, and required
records before operational use.

## 3.5 Privacy governance

Users with `privacy:write` can append patient consent decisions at
`POST /api/privacy/patients/{patient_id}/consents`; `privacy:read` can inspect
the history. A withdrawal is accepted only when a matching grant is current.
Consent entries capture the decision, stated purpose, source, optional expiry,
evidence reference, actor, and timestamp. The consent API is an auditable
ledger, not a universal authorization gate: workflows must not assume that
recording a decision automatically changes data-sharing, treatment, research,
or communication behavior.

Retention schedules are appended through
`POST /api/privacy/retention-policies`. Every entry requires a legal basis,
hospital approval reference, effective date, and positive retention period.
Later entries preserve earlier policy history. No records are automatically
deleted or anonymised based on these schedules. Do not configure a schedule
without the hospital's legal/privacy owner approval. Anonymisation remains
unimplemented pending an approved policy and complete review of tenant data,
central indexes, backups, exports, and integrations; deactivation is not
anonymisation.

Hospital policy drafts can be recorded at `POST /api/privacy/policies` as
opaque consent or retention policy documents. Draft approval requires a
different user with `privacy:approve` and an approval reference. This records
workflow evidence only; it does not validate legal approval or change behavior.
The database constrains the policy enforcement mode to `OFF`, and there is no
endpoint to enable audit-only or enforcement. Existing consent and retention
records are not rewritten or interpreted by these drafts.

Legal holds can be registered at `POST /api/privacy/legal-holds` for a patient
and/or hospital-defined data category, then released by a different
`privacy:approve` user with a release reference. These records are not yet
connected to any purge process; no deletion or anonymisation operation exists.
Do not put patient-identifying details in policy documents or use them as a
substitute for the hospital's approved legal record.

## 3.6 Workforce and payroll MVP

The licensed `/api/workforce` API covers attendance entries, leave requests
with manager approval, effective-dated monthly fixed-salary profiles, payroll
periods, calculation history, and period finalization. Staff access is
permission-controlled with `hr:read`, `hr:write`, `hr:approve`, and
`hr:payroll`; salary and payroll reads additionally require the separately
granted `payroll:read` permission. The `workforce` module must also be licensed. Leave decisions
cannot be self-approved, and a finalized payroll period cannot be changed.

Payroll calculations use INR paise and the explicit formula
`monthly_fixed_paise * paid_days / working_days`, rounded to the nearest paise
(half up), then add manually entered earnings and subtract manually entered
deductions. The salary profile effective on the first day of the period is
used. Every calculation records its inputs, outputs, and actor, including
recalculations before finalization. Attendance and leave are not automatically
converted into paid days; payroll operators must verify paid days,
working-day counts, adjustments, and the calculation note against approved
source records.

This is not a statutory payroll engine or a legal/accounting determination.
It does not calculate income-tax/TDS, provident fund, ESI, gratuity, overtime,
bonus, statutory leave accruals, or other jurisdiction-specific obligations;
nor does it produce payslips, make bank payments, or integrate with a roster
or attendance device. HR and payroll owners must validate inputs, rounding,
period close, authorizations, and all statutory calculations with a qualified
India payroll/accounting advisor before relying on outputs for payment.

## 4. Stop, restart, update

Safe restart without deleting data:

```powershell
docker compose restart api onboarding-portal
```

Rebuild an approved code change:

```powershell
docker compose up -d --build
docker compose ps
docker compose logs --tail=200 db-init api onboarding-portal
curl.exe --fail --show-error http://127.0.0.1:8000/health/ready
```

`docker compose down` stops containers but retains named volumes by default.
Avoid `down -v`, volume pruning, deleting Docker data directories, and
reinitializing PostgreSQL unless there is an approved, verified recovery plan.
The local Compose stack can create an empty schema from ORM metadata only when
development auto-create is enabled and Alembic revision files are missing.
Matching existing schemas are reused; drift causes startup to stop without
changing existing tables. Production requires its migration files. Production
startup migrates the control plane and initializes a newly created default
tenant, but intentionally skips migrations for existing tenant databases.
It verifies that each active production tenant is already at the checked-in
migration head and matches ORM metadata, and blocks startup otherwise. After
backup and staging validation, run the default-tenant canary and then an
explicitly approved fleet rollout; a routine restart must not substitute for
that decision.

For production, run commands from the deployment directory and pass both the
production Compose file and protected env file. First validate without
printing resolved configuration:

```powershell
docker compose -f docker-compose.prod.yml --env-file .env.production config --quiet
docker compose -f docker-compose.prod.yml --env-file .env.production ps
```

Deploy only a reviewed, pinned source revision and approved image. Avoid
floating tags as the release identifier. Production ingress, DNS, TLS,
firewall, secret delivery and provider costs are covered in
`PRODUCTION_DEPLOYMENT.md`.

## 5. Schema change and rollout

1. Identify whether the change affects tenant models, control-plane models, or
   both. Each has its own Alembic environment and history.
2. Capture a verified pre-change backup and record current revisions for the
   control DB and representative tenant databases.
3. Test the change on a disposable database and then a production-like staging
   copy using the production database engine/version where feasible.
4. Generate/review the revision against a development database. For local
   Compose on Windows, override the in-container database host with the local
   published port for this PowerShell session:

   ```powershell
   $env:POSTGRES_HOST = "127.0.0.1"
   $env:POSTGRES_PORT = "5432" # Must match POSTGRES_PUBLISHED_PORT in .env
   .\.venv\Scripts\python.exe -m backend.scripts.migrate --default-only -m "describe_change"
   ```

   The canary command generates and applies the revision to the local default
   tenant. Review and commit the generated migration. Never generate
   migrations in production.

   For a control-plane schema change, generate a revision using the control
   Alembic configuration from a development environment:

   ```powershell
   .\.venv\Scripts\python.exe -m alembic -c backend/control_plane/alembic.ini revision --autogenerate -m "describe_control_plane_change"
   ```

   Autogenerated migrations require human review. Check constraints, indexes,
   defaults, downgrade behavior, data backfills, lock duration and backwards
   compatibility.
5. For a production release, build the reviewed image before starting the new
   API. Run migration tasks without Compose dependencies so the new release
   can be applied while the currently deployed API remains available:

   ```sh
   docker compose -f docker-compose.prod.yml --env-file .env.production build api
   docker compose -f docker-compose.prod.yml --env-file .env.production run --rm --no-deps api python backend/scripts/migrate.py --default-only
   ```

   Verify the canary tenant, then after explicit fleet approval run:

   ```sh
   docker compose -f docker-compose.prod.yml --env-file .env.production run --rm --no-deps \
     -e MIGRATION_ACTOR=release-operator -e GIT_COMMIT_SHA=abc123def456 \
     api python backend/scripts/migrate.py --fleet
   docker compose -f docker-compose.prod.yml --env-file .env.production up -d
   ```

   `db-init` verifies every active production tenant is at the checked-in
   Alembic head and matches ORM metadata before allowing the API to start.
   The fleet command records a durable `STARTED` event and a terminal
   `SUCCEEDED`/`FAILED` event per tenant, grouped by run and attempt IDs. Each
   record includes timestamps, tenant database, Alembic revision or failure,
   release SHA when supplied, and operator attribution in the control-plane
   audit ledger. `MIGRATION_ACTOR` is operator-supplied attribution, not
   authenticated identity proof. A start event without a matching terminal
   event indicates an interrupted or unrecorded attempt and requires operator
   investigation before retry. Re-running records a new attempt; inspect the
   ledger and per-tenant output, not only the container exit code.
6. Verify each target revision, application workflows, readiness, logs,
   audit records and backup status. Keep rollback/forward-fix instructions
   ready. Database schema downgrade may be unsafe even if application rollback
   is available.

The one-shot `db-init` service runs `backend/scripts/bootstrap.py`. In
production it applies control-plane revisions and initializes a newly created
default tenant; it does not migrate existing tenant databases. It checks
existing active tenant revisions and schema parity and blocks startup if
rollout is incomplete. Development bootstrap may upgrade all active cloud
tenants. Neither mode replaces staging, backup verification, canary approval,
and explicit production fleet rollout.

## 6. PostgreSQL 17 encrypted backups

Production Compose is pinned to PostgreSQL 17. The host cron backup script
captures the control-plane DB and every database matching
`TENANT_DATABASE_PREFIX`, plus PostgreSQL global role definitions. Each custom
format dump is streamed through GPG AES-256 encryption; plaintext dumps are
not written to disk. Configure a protected GPG passphrase file outside the
repository and mount an encrypted attached disk at `BACKUP_HOST_PATH`.

Production configuration supports both storage layouts:

- **Combined attached storage:** mount one dedicated data disk at
  `/mnt/avinya-data`; set `BACKUP_STORAGE_MOUNTPOINT=/mnt/avinya-data`,
  `BACKUP_HOST_PATH=/mnt/avinya-data/backups`, and
  `ATTACHMENTS_HOST_PATH=/mnt/avinya-data/attachments`.
- **Separate storage:** mount different disks at `/mnt/avinya-backups` and
  `/mnt/avinya-attachments`; set `BACKUP_STORAGE_MOUNTPOINT` to
  `/mnt/avinya-backups`, `BACKUP_HOST_PATH=/mnt/avinya-backups/backups`, and
  `ATTACHMENTS_HOST_PATH=/mnt/avinya-attachments`. This reduces shared disk
  pressure but both disks remain on the same VM failure domain.

Procurement vendor invoice uploads are stored durably below
`ATTACHMENTS_STORAGE_DIR` in a directory for the active tenant. The API accepts
PDF, PNG, and JPEG files up to `MAX_ATTACHMENT_UPLOAD_BYTES` (20 MiB by
default); it checks the file signature, scans the staged file with the local
ClamAV service, then assigns a random server-side filename. A detection rejects
the upload, and a scanner error or unavailable service fails closed without
creating an invoice or dispatching OCR. The API waits for the scanner's health
check at startup and does not publish the scanner port. Production gives
ClamAV a separate egress network for signature updates; allow the required
ClamAV update traffic through the host's egress policy and monitor scanner
health and logs. The AI extraction task uses a separate tenant DB session and
does not delete the source document. Local Compose uses the persistent
`hms_attachments` volume; production uses `ATTACHMENTS_HOST_PATH`. Include
attachments in encrypted host backups and verify a restore before relying on
them.

The invoice path now has a durable audit of authorized download requests; it
records that a response was initiated, not that the client received every
byte. This is not yet a general clinical-document system. Retention/deletion
controls, cross-tenant authorization testing, and end-to-end restore evidence
for attachment files remain open. Do not treat a malware scan or file-signature
check as a production privacy approval.

For scanner operations, confirm `clamav` is healthy with `docker compose ps`
and review its logs for successful signature updates. The scanner has a
persistent definitions volume and a dedicated egress path in production; if
definitions cannot be refreshed or the service is unavailable, invoice uploads
are rejected. Do not bypass the scanner by restoring direct-to-storage uploads.
Include scanner memory and startup/signature-update time in host capacity and
deployment checks.

### Procurement vendor invoice workflow

The API workflow is:

1. Upload the source document with `POST /v1/api/procurement/invoices/upload-document?vendor_id=<id>`.
2. List/filter the review queue with `GET /v1/api/procurement/invoices?invoice_status=PENDING_VERIFICATION&vendor_id=<id>` or fetch one invoice with `GET /v1/api/procurement/invoices/<id>`.
3. A user with `procurement:write` verifies/corrects extracted header totals, dates, PO/GRN association, and line items using `PATCH /v1/api/procurement/invoices/<id>/review`. The API enforces invoice arithmetic, line-item subtotal consistency, same-vendor PO association, and PO-item references.
4. Reconcile using `PATCH /v1/api/procurement/invoices/<id>/reconcile`; a user with `finance:approve` then posts an eligible invoice using `POST /v1/api/procurement/invoices/<id>/approve`.

An invoice with no valid PO/item mapping remains pending reconciliation; a
reviewer must correct it rather than treating AI extraction as authoritative.
Posting is protected against repeat submissions, and posted invoices cannot be
edited through the review endpoint. The procurement API has no dedicated UI
yet, and production users should not rely on this workflow until the remaining
document security, audit, retention, and restore controls above are addressed.

### Frontend analytics APIs

The bounded analytics APIs are the preferred frontend contract for new
dashboard views:

- `GET /v1/api/analytics/dashboard?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD`
  returns period net billings, recorded payments, new patients, encounters and
  scheduled appointments, each compared with the immediately preceding period
  of equal length, plus current outstanding receivables. Requires `billing:read`.
- `GET /v1/api/analytics/trends?...` returns finalized net billings, recorded
  collections, encounters started and appointments scheduled. Use inclusive
  `start_date` / `end_date` values for a custom window of up to 366 days and
  optional `granularity=day|week|month|quarter|half_year|year`. The default is
  `day`; weeks are Monday-Sunday, and all calendar buckets use UTC. First and
  last buckets are clipped to the requested window; each point's `date` and
  `bucket_end` mark its inclusive bounds. Requires `billing:read`.
- `GET /v1/api/analytics/operational-throughput?...` returns encounters by
  type, appointments by current status and diagnostic tasks by current status.
  Requires `adt:read`.
- `GET /v1/api/analytics/appointment-performance?...` returns appointment
  outcomes, encounter-link counts, average booking lead time, and recorded
  status-transition counts. Requires `opd:read`; pre-migration appointments
  contain only a current-status baseline, not reconstructed transition history.
- `GET /v1/api/analytics/care-timings?...` returns aggregate appointment wait
  and consultation durations, actual-versus-snapshotted planned duration,
  scheduled-to-consult start delay, encounter-to-admission time, inpatient
  length of stay, and durations across bed assignment/transfer stages. Requires
  `opd:read` or `adt:read`.
- `GET /v1/api/analytics/clinical-vitals-and-early-warning?...` returns
  bounded aggregate vital-threshold and period fluid-balance counts. Requires
  `adt:read` or `opd:read`; the figures are not a validated NEWS2 score or
  clinical alerts.
- `GET /v1/api/analytics/financial-revenue-cycle?...` returns bounded invoice,
  receipt, claim-cohort, deposit, and refund metrics plus current receivables,
  deposit-liability, and tariff snapshots. Requires `billing:read`; the
  response documents each event date and snapshot limitation.
- `GET /v1/api/analytics/pharmacy-and-therapeutics?...` returns prescription
  and dispensed-unit metrics for the window plus current inventory/expiry
  snapshots. Requires `pharmacy:read` or `billing:read`.
- `GET /v1/api/analytics/patient-demographics-and-epidemiology?...` returns
  active-population and allergy snapshots plus diagnosis coding, distinct
  patients by diagnosis, new registrations, unique patients by appointment
  department and demographic cohorts, and appointment outcomes for the selected
  period. Requires `patients:read`, `opd:read`, or `adt:read`. Diagnosis counts
  are service-record cohorts, not population-prevalence estimates; appointment
  data cannot identify people who never sought care or services the hospital
  does not offer.
- `GET /v1/api/analytics/doctor-and-department-productivity?...` returns
  windowed staff activity and current identifiable staff roster details.
  Requires `executive_bi:read`; finance-only permission is insufficient.
- `GET /v1/api/analytics/executive-command-center?...` returns windowed
  encounters, appointments, discharges, transfers, and billing aggregates
  alongside a current bed-census snapshot. Requires one of `billing:read`,
  `adt:read`, `wards:read`, or `iam:read`.
- `GET /v1/api/analytics/clinical-quality-and-safety?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD`
  returns windowed SQL aggregates for note signatures, medication
  administration, triage/MLC, surgery/checklists, implants, diagnostics, and
  radiation. Requires `opd:read`, `adt:read`, or `diagnostics:read`. These
  operational ratios are not accreditation or compliance scores; event dates
  differ by measure, and an average is `null` when there are no measurements.
- `GET /v1/api/analytics/inventory-and-blood-radar` returns current inventory,
  blood-bank, and governance snapshots. Requires `pharmacy:read`,
  `diagnostics:read`, or `iam:read`. It aggregates metrics in SQL and returns
  low-stock and near/expired batch details up to the hospital's configured
  `inventory_alert_detail_limit`; total alert counts, returned counts, and
  truncation flags distinguish capped details from totals. The hospital policy
  configures default low-stock units and FEFO expiry-warning days; each
  medication can override either threshold independently, with an unset
  override inheriting the hospital default. Expired blood bags are excluded
  from available units, but the endpoint is not a time-windowed event report.
  Configure hospital defaults and the detail limit through
  `PUT /v1/api/admin/policies`; configure per-medication overrides through
  the medication create/update APIs or medication bulk import.

Windows are inclusive UTC dates, default to the latest 30 days, and are capped
at 366 days. Future end dates and reversed ranges are rejected. Responses
include generation time, metric-definition version and explicit definitions
or interpretation notes. Patient-level identifiers are not returned, although
the workforce and inventory legacy endpoints include identifiable staff or
batch detail rows subject to their permissions.
Comparison percentage is `null` when the prior period is zero (undefined, not
zero growth). All responses disable shared/browser caching. These APIs use
date-bounded SQL aggregates and dedicated time indexes; apply the tenant
Alembic migration and verify schema parity before deploying the version that
uses them. They are not substitutes for clinical surveillance or real-time
alerting. Financial revenue-cycle, pharmacy, clinical vitals, demographics,
workforce, and executive command-center endpoints also accept the bounded UTC
window and aggregate event data in SQL. Population, allergy, roster, bed
census, receivables, inventory, and tariff data are current snapshots, not
period flows. The clinical vitals endpoint
returns encounter-level fluid-threshold counts instead of encounter IDs. Its
vital thresholds are not a validated NEWS2 implementation, and period fluid
totals are not rolling clinical observation windows or alerts; neither
replaces clinician review. Workforce roster details are identifiable and
restricted to `executive_bi:read`.
Clinical-quality-and-safety event measures are windowed SQL aggregates, but
their operational ratios require clinical-owner review. Supply/blood radar
metrics are current-state snapshots with capped batch alert details.

#### Date-filtered record browsing and appointment history

Invoice, prescription, diagnostic-order, and appointment lists accept inclusive
UTC calendar dates as `start_date=YYYY-MM-DD&end_date=YYYY-MM-DD`. Supply both
dates; ranges are limited to 366 days. The first three filter on record
creation time, while appointments filter on scheduled appointment time.
Existing appointment `date_start` / `date_end` datetime filters remain
available for compatibility. Lists remain permission-checked and paginated.
Appointment status changes are recorded atomically with their actor and
optional reason and can be read from
`GET /v1/api/opd/appointments/{appointment_id}/status-history`. Existing
appointments receive a migration-time current-status baseline; earlier
transitions cannot be reconstructed.

#### Recording unmet service demand

Authenticated OPD or diagnostics staff can record demand that could not be met
through `POST /v1/api/opd/service-demand`, including requested service,
department, reason (service absent, appointment capacity, specialist or
equipment unavailable), source, and an optional patient link. Patient linkage
is optional; the free-text service name can contain sensitive information and
is only returned by the permission-checked record-list API, never by analytics.
Requests begin as `OPEN`; permitted resolutions include referring out,
marking a service available, resolving, or closing. Referring out requires a
facility name. Current status transitions and actors are exposed at
`GET /v1/api/opd/service-demand/{id}/history`.

`GET /v1/api/analytics/service-demand?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD`
provides reason/source/status counts, demand by department and linked catalog
service, unique linked-patient counts, uncatalogued request totals, and current
open backlog. The period uses request creation time; current status and open
backlog are explicitly snapshots. Recorded requests are staff-captured unmet
demand, not a measure of all community need. Review reason coding with
clinical/operations owners and do not interpret increased reports alone as
proof that investment is warranted.

#### OPD appointment waitlist

When no suitable slot is available, authorized staff can add a patient, doctor,
department, and requested UTC time window with
`POST /v1/api/opd/appointment-waitlist`. The waitlist is paginated and filterable
by patient, doctor, and status. A staff member explicitly selects an available
time within that window using
`POST /v1/api/opd/appointment-waitlist/{id}/book`; the server rechecks doctor
leave and schedule conflicts under a transaction and creates the appointment
and status history atomically. A conflict leaves the request waiting. Staff
can cancel a waiting entry, and status history is available at
`GET /v1/api/opd/appointment-waitlist/{id}/history`. The feature does not
automatically prioritize patients or send notifications; use clinical
triage/emergency workflows instead of a waitlist for urgent cases.

#### Consultation and inpatient time measurement

Appointments snapshot the doctor's configured consultation slot length when
booked; a reschedule snapshots the then-current slot configuration. The
`/v1/api/analytics/care-timings` endpoint measures patient arrival-to-consult
wait using the `ARRIVED` and `IN_CONSULTATION` history timestamps, then actual
consultation duration through `COMPLETED`. It reports sample counts, average,
minimum and maximum, the difference between actual and planned durations, and
how often completed consultations were shorter or longer than the planned
slot. It also measures scheduled-time-to-consult-start, where negative means
the doctor started early.

For IPD, admission record creation through discharge is reported as completed
length of stay; active admissions are elapsed snapshots. The endpoint also
reports encounter-creation-to-admission and admission-to-initial-bed-assignment
intervals and time in each bed type between initial assignment, transfers,
discharge, or the report timestamp. These timestamps reflect application
events, not necessarily clinical decision time or face-to-face care. Delayed
status entry and documentation, interruptions, or late discharge recording
affect results. Legacy appointments have no planned-duration snapshot, and
history before instrumentation is unavailable. Use the measured sample counts
and cohort definitions; do not compare department/doctor performance without
operational context or clinical review.

#### Hospital print profiles

Printable reports render an HTML template with a selected paper size. Existing
template sizes are A4 portrait/landscape, A5 portrait, and 80 mm thermal. Every
tenant starts in `DESIGNED` mode, which prints the template's header, body,
footer, and design CSS. Reports accept an optional `print_mode` query override
for a one-off print; invoice print/PDF, OPD appointment, prescription,
diagnostic, discharge, MLC, birth/death registry, surgery, insurance, and
purchase-order routes share the mode-aware resolver.

For preprinted stationery, create or fork a template with
`print_mode=PREPRINTED_DATA_ONLY`, set its page size and HTML/CSS so the data
lands in the press-printed fields, and make it the default for that mode.
`PREPRINTED_DATA_ONLY` suppresses the template header/footer and generic
decorative table CSS, and uses a zero-margin page/body baseline; the template's
own CSS is retained for field positioning. Then set the hospital's per-report
default through `PUT /v1/api/admin/policies`, for example:

Templates can additionally store `paper_width_mm` and `paper_height_mm`
together for custom stock, per-edge `page_margin_*_mm` values, and
`content_offset_x_mm` / `content_offset_y_mm` alignment offsets. Dimensions
are bounded from 10 to 1000 mm, margins from 0 to 100 mm, and offsets from
-100 to 100 mm; configured margins must leave a positive printable area. When
custom dimensions are absent, the selected built-in page size remains in use.
Updates can explicitly clear optional values by sending `null` (both paper
dimensions must be cleared together).

```json
{
  "report_print_modes": {
    "OPD_APPOINTMENT_SLIP": "PREPRINTED_DATA_ONLY"
  }
}
```

The policy update is rejected unless that report type has an active default
template in the requested mode. An administrator can change the hospital
default later; a print request can temporarily override it with
`?print_mode=DESIGNED` or `?print_mode=PREPRINTED_DATA_ONLY`. If a template code
and explicit mode are both supplied, they must match. Each variant has its own
default template, allowing a hospital to keep both designs without overwriting
either. Data-only mode cannot infer press form coordinates: the hospital must
calibrate and validate its HTML/CSS on the actual printer and stock before
using it for official documents. Driver scaling, printer margins, and stock
feed can still affect alignment; software settings do not replace physical
calibration. The stored `layout_schema` is not currently translated into print
positioning; HTML/CSS is the rendering source of truth.

#### Existing analytics coverage and remaining work

The broader legacy BI surface currently reports:

- Executive operations: current bed census/occupancy, admissions, length of
  stay and discharge outcomes, readmission proxy, appointment status funnel,
  and billed-revenue-per-bed/encounter estimates.
- Finance: all-time invoice/collection and receivables summaries, invoice-age
  buckets, service-stream attribution, top billed services, payment methods,
  insurance/preauthorization claims, deposits, and refunds.
- Population and clinical quality: age/gender/blood-group cohorts, ABHA-link
  counts, allergies and coded diagnoses; note signatures, eMAR, triage/MLC,
  surgery/checklists, implants, diagnostics and radiation summaries.
- Pharmacy and supply: prescription fill quantity, inventory valuation and
  expiry exposure, blood-bank availability/reactions, selected governance
  counters.
- Workforce: doctor and department encounter/appointment/order volumes,
  registration metadata, fee values, and estimated consultation revenue.

That breadth is not a guarantee that every KPI is clinically validated or
financially reconciled. Remaining analytics work, in priority order:

1. Verify query plans and performance for windowed and current-snapshot SQL
   aggregates using PostgreSQL 17 and realistic tenant data. Add/adjust indexes
   only from measured plans; SQLite tests do not establish PostgreSQL behavior.
2. Add durable event timestamps/history where a true outcome or turnaround
   trend is needed. Current appointment and diagnostic statuses are snapshots,
   not historical state transitions; the available data cannot reliably
   establish conversion, turnaround, or service-level performance.
3. Reconcile finance metrics with accounting rules: credit notes/refunds,
   deposits, insurer settlements, discounts allocated to service lines, and
   due-date-based receivables aging need tested source-of-truth definitions.
4. Verify authorization matrices, small-cohort disclosure controls, and
   frontend consumption before production use.
5. Keep clinical risk thresholds as operational aggregates only until
   hospital clinical owners approve the rule, denominator, observation period,
   and escalation workflow. These APIs do not issue alerts.

On the Linux deployment host, install `gpg`, `rsync` (if using a remote copy),
and Docker Compose. Create the passphrase file through the approved secret
process and restrict it to the deployment account (for example `chmod 600`).
Do not store the passphrase in `.env.production`, the repository, or a cron
command. Run the backup once manually before enabling cron:

```sh
cd /opt/avinya_hms
GPG_PASSPHRASE_FILE=/etc/avinya/backup.gpg \
  INCLUDE_ATTACHMENTS=true \
  BACKUP_REMOTE_DEST=backup@nas.example:/srv/avinya \
  bash ./deployments/backup-postgres.sh
```

`INCLUDE_ATTACHMENTS=true` archives the configured attachment directory into
the same encrypted snapshot set. Omit it if the path is not in use yet.
`BACKUP_REMOTE_DEST` is optional but required for protection from loss of the
production VM. It uses SSH/rsync to copy the already-encrypted snapshot folder
to a separately administered host/NAS. A second partition or attached disk on
the production VM is **not** off-host backup.

Example daily cron entry (adjust repository, passphrase path, remote target,
and the maintenance window):

```cron
0 2 * * * cd /opt/avinya_hms && GPG_PASSPHRASE_FILE=/etc/avinya/backup.gpg INCLUDE_ATTACHMENTS=true BACKUP_REMOTE_DEST=backup@nas.example:/srv/avinya bash ./deployments/backup-postgres.sh >> /var/log/avinya-backup.log 2>&1
```

The script uses a lock to prevent overlap, rejects unsafe database names,
checks each archive, writes SHA-256 checksums, and retains 14 days of local
snapshots by default (`RETENTION_DAYS` overrides it). Monitor cron exit status,
local/remote freshness, disk space, and checksum results; cron logs alone are
not alerting. `BACKUP_REMOTE_DEST` must have SSH key-based access configured
for the deployment account, and remote storage must enforce its own retention
and restricted access.

Verify the encrypted dump before any restore:

```sh
sha256sum --check /srv/avinya/backups/avinya-pg17-<run-id>/SHA256SUMS
```

Restore a single database into a **new isolated database**. The script refuses
targets without a `restore_` prefix and never overwrites an existing DB. It
retains a failed isolated target for diagnosis; it does not switch tenant
routing:

```sh
GPG_PASSPHRASE_FILE=/etc/avinya/backup.gpg \
  bash ./deployments/restore-postgres.sh \
  --file /srv/avinya/backups/avinya-pg17-<run-id>/hms_example.dump.gpg \
  --target-db restore_hms_example_20261016
```

Check the restored DB's Alembic revision, ORM schema parity, representative
records and application-level authentication/workflows. Global roles are
included as a separate encrypted SQL artifact and are not applied automatically.
Attachment archives also require a controlled isolated extraction; never
extract an untrusted archive over a live attachment path.

The older `backend/scripts/backup_service.py` supports encrypted tenant-only
snapshots for local/manual recovery and the control-plane UI. Production
scheduled jobs use the host script above to include the control plane and
provide a direct cron/remote-copy path.

## 7. PostgreSQL 17 and synthetic load validation

Run the migration integration test only against a disposable PostgreSQL 17
server using an administrative connection. It creates uniquely named
`test_*` databases and drops only those generated names:

```sh
RUN_POSTGRES17_INTEGRATION=1 \
POSTGRES17_TEST_URL='postgresql+psycopg2://<test-user>:<test-password>@127.0.0.1:<port>/postgres' \
  .venv/bin/python -m pytest -q tests/test_postgres17_integration.py
```

The test applies both Alembic histories and compares the resulting PostgreSQL
schemas with their ORM metadata. It does not test volume initialization,
existing customer history bridges, tenant provisioning, backup/restore, or a
production permission model.

For the encrypted dump/restore test, also set
`POSTGRES17_DOCKER_CONTAINER` to the name of the disposable PostgreSQL 17
container and `POSTGRES17_CONTAINER_PORT` if its internal port is not 5432.
The test creates uniquely named temporary databases, runs `pg_dump`,
round-trips the custom-format archive through GPG AES-256, restores into a
second temporary database, checks a sample row, and drops only its generated
test databases. It also downloads Alpine/GnuPG if those images are not already
available locally. Never point it at a production database/container.

For performance, prepare **20 synthetic, non-patient-data test accounts per
tenant** for ten staging tenants. Store the CSV outside Git with mode `0600`;
columns are `tenant,base_url,email,password`. Run from a separate load
generator host so test traffic does not compete with the server:

```sh
.venv/bin/python tests/load/tenant_read_load.py \
  --users-file /secure/staging/tenant-load-users.csv \
  --users-per-tenant 20 \
  --ramp-seconds 600 \
  --duration-seconds 1800 \
  --report-file /secure/results/tenant-load-200.json
```

The harness logs each account in once, then performs authenticated
`/api/auth/me` reads and `/health/ready` checks, producing per-path p50/p95/p99
latencies, throughput, response codes and tenant request counts. It does not
yet model writes, clinical workflows, reports, file uploads or backup load.
Run first at 20 accounts/tenant (200 concurrent users); repeat at 30 and 50
accounts/tenant (300 and 500 users) only after reviewing error rate, database
connections, CPU, memory, disk, and logs.

As a provisional engineering gate—not an external healthcare or industry
certification—target less than 1% failed requests and p95 ≤1 second / p99 ≤2
seconds for authenticated read requests at the agreed 200-user load. Adjust
these against an approved service objective and clinically reviewed workflows.
Do not run against production or real patient data.

## 8. Restore procedure (high impact)

Restore only with an approved incident/change record. Preserve the existing
database; restore to a new target DB first. Confirm the backup filename,
tenant identity, timestamp and access to the correct backup key through the
approved secret-management process. Do not print or commit the decrypted dump
or key.

For PostgreSQL 17 host backups described in section 6, use
`deployments/restore-postgres.sh`. It reads the protected passphrase file from
`GPG_PASSPHRASE_FILE`, checks the snapshot checksum manifest when present and
validates the encrypted archive, requires a new database name beginning with
`restore_`, and refuses to overwrite an existing database. It restores one
database; global role definitions and attachment archives require separate,
reviewed handling.

Do not confuse that deployment script with the legacy tenant helper
`backend/scripts/restore_tenant.py` shown below. The legacy helper accepts its
encryption key on the command line, which may expose it through process
arguments or shell history, and it has different overwrite behavior. Do not
use it for routine production recovery until its secret-input and target
database safeguards have been reviewed and improved.

Legacy helper example only—not the recommended PostgreSQL 17 host-backup
restore path. Replace placeholders using the protected operator procedure;
never paste real keys into a shared terminal transcript:

```powershell
docker compose exec api python backend/scripts/restore_tenant.py `
  --file /app/backups/<tenant>_<timestamp>.dump.enc `
  --db-name <new_isolated_restore_db> `
  --key <retrieve-through-approved-protected-process>
```

After restore:

1. Verify the target schema revision and database integrity.
2. Run application-level checks against the isolated restored database.
3. Confirm tenant registry routing still points to the live DB; do not promote
   or switch DNS/tenant mapping until approved.
4. Record validation, data-loss point, operators, source snapshot and any
   cutover/rollback action in the incident/change record.
5. Remove temporary decrypted dumps securely and verify the off-host snapshot
   remains available.

## 9. Tenant onboarding and migration studio operations

Use the Onboarding Portal's Provisioning Studio to create a tenant and
configure its persisted plan, modules, blueprint, quotas and initial data
options. Verify the saved tenant appears in the fleet view and can be resolved
by its host/subdomain. Do not assume API `/docs` is a tenant user interface.

For CSV import, read [Migration Studio Guide](MIGRATION_STUDIO.md) first.
Important operational constraints:

- Back up the tenant and test a small CSV before a large import.
- Preview is a sample/advisory check, not a transaction guarantee.
- Duplicate policies require a mapped, single-column unique key. Update can
  overwrite mapped clinical/business values.
- Work runs in an in-process background task, not a durable queue. Avoid
  restarts during import. Check the control-plane forensic audit ledger for
  final counts and bounded row errors.
- Protect setup/control-plane keys and use a trusted browser profile. The UI
  keeps entered keys in memory only; re-enter them after a page reload.
- Set `TRUSTED_PROXY_IPS` to the exact ingress proxy address/CIDR values if
  the service sits behind a reverse proxy. Forwarded client IPs are ignored
  unless the direct peer is trusted; leaving this unset may group clients by
  the proxy address for rate limiting.
- `WS_MAX_CONNECTIONS` caps authenticated WebSocket connections per API
  worker. Public waiting-room connections have a separate
  `WS_MAX_WAITING_ROOM_CONNECTIONS` cap.

### HL7/MLLP listener

The optional listener refuses to start unless `HL7_TLS_CERT_FILE` and
`HL7_TLS_KEY_FILE` are configured. A tenant administrator with `mdm:write`
rotates a tenant-specific secret at `/api/equipment/hl7-secret/rotate`; the
plaintext is returned once. Configure only that hospital's equipment to send
the value in HL7 `MSH-8`. The secret is encrypted in the control-plane
database, redacted from stored HL7 payloads, and can be re-encrypted with the
envelope-key rotation script. Install the TLS certificate/key on the listener
host, configure equipment for TLS, and restrict network access to the equipment
VLAN or an approved VPN. Do not expose the listener directly to the public
internet. Results must include a PID patient identifier matching the diagnostic
task's patient; verified or cancelled results cannot be updated. Idle
connections are closed after `HL7_CLIENT_IDLE_TIMEOUT_SECONDS` (default 300,
maximum 3600 seconds).

The production Compose image uses the `production-ai` target. It installs the
pinned CPU-only PyTorch wheel from the PyTorch index separately, then resolves
the remaining OCR runtime from PyPI, avoiding a mixed-index install.
`requirements-ai-pytorch.txt` and `requirements-ai.txt` hold those respective
pins. Standard development/testing images intentionally omit the large ML
dependencies.
Extraction failures still leave the source document available for manual
review, are logged, and keep the invoice pending verification; they are never
treated as successful extraction. The model repository is pinned to an
immutable revision.

### Staging API load baseline

`tests/load/api_baseline.js` is an opt-in k6 read-only staging baseline. It
ramps to 200 virtual users, holds that level for 15 minutes, then ramps down.
The provisional acceptance thresholds are p95 response latency below 500 ms
and fewer than 1% failed requests/checks; treat these as this deployment's
engineering targets, not a regulatory or universal industry certification.
It requires a dedicated staging access token with `patients:read`, a staging
tenant slug, and an HTTPS base URL. Never point it at production or use
patient-identifying search terms. Obtain approval from the staging owner before
running it, then record the tested image SHA, database size, workload, and
results in the release evidence.

### Envelope-encryption key rotation

Back up the control-plane database before rotating tenant secrets. Stop API and
backup writers, set the new `SECRET_KEY` and `ENVELOPE_ENCRYPTION_SALT`, and
temporarily set `ENVELOPE_ENCRYPTION_PREVIOUS_SECRET_KEY` and
`ENVELOPE_ENCRYPTION_PREVIOUS_SALT` to the old pair. Run
`python -m backend.scripts.rotate_envelope_keys`; it re-encrypts the tenant JWT
and backup keys without printing them. Verify tenant login and a backup-key
read, then remove both previous-key settings and restart the services. Before
deploying a release that rejects plaintext tenant secrets, run this script
against the existing control-plane registry while the previous release is
still available; verify it reports every migrated secret and returns zero on a
second run. The new release intentionally refuses to use plaintext registry
secrets. Never
rotate the encryption key without retaining the old pair until the conversion
has completed and verification succeeds.

## 10. Troubleshooting decision table

| Symptom | First checks | Safe next action |
| --- | --- | --- |
| API unavailable after deploy | `docker compose ps`; `db-init` exit state; migration/API logs; DB health | Preserve logs; inspect failed revision and schema state. Do not loop restarts or stamp Alembic blindly. |
| `/health` passes, `/health/ready` fails | DB/Redis container health, environment, network names, credentials, DB logs | Restore DB/Redis connectivity; readiness failure must be resolved before treating service as ready. |
| Onboarding page loads but catalog is empty | API health, control-plane migration/seeding logs, portal network/API calls, control-plane DB access | Inspect migration/seed result and request ID; avoid manually creating catalog tables outside migrations. |
| Tenant hostname returns 404/403 | DNS/Host, slug, tenant registry `is_active`, deployment mode, tenant URL config | Verify registry and route host. Do not change active status without authorization. |
| Login fails for all users | Tenant resolution, DB readiness, seed status, account lock/status, request host/header | Confirm the correct tenant host and user status; never reset hashes or copy users directly as a shortcut. |
| Restore reports failure | Target DB, file/key match, temporary disk space, pg_restore output and PostgreSQL logs | Keep source backup and current production DB unchanged; repeat only against an isolated target after diagnosis. |
| Migration Studio reports row errors | Preview output, table protected-field restriction, required fields, foreign keys, unique constraints | Correct the CSV/mapping and preview again. Do not switch to Allow to hide unexplained constraint errors. |
| Backups missing/stale | Sidecar status/logs, mounted volume, control-plane tenant state, pg_dump tool/DB reachability, free space | Resolve backup failure and make a verified off-host copy; take an approved manual snapshot if needed. |

## 11. Release checklist

- [ ] Source revision and image are pinned and reviewed.
- [ ] Full backend tests and frontend production build pass on the release
  candidate.
- [ ] Both Alembic histories were reviewed and tested on compatible staging
  databases.
- [ ] Pre-change encrypted backup exists, is copied off-host, and restore
  evidence is current.
- [ ] Required environment settings and secrets are available through the
  approved protected process; no secrets are in Git/image/log output.
- [ ] Public ingress, TLS, DNS, firewall and private DB/Redis reachability are
  reviewed for the actual provider.
- [ ] `/health`, `/health/ready`, operator login, tenant routing and critical
  business workflows were checked.
- [ ] Migration/startup logs contain no unresolved failure; target database
  revisions were checked.
- [ ] Backup and disk monitoring are active; restore/contact/rollback owners
  are known.
- [ ] Privacy, threat-model, regulatory and operational sign-off is recorded
  before loading real patient data.

## 12. Incident records and access

When an account is suspended, locked, or deactivated through the tenant IAM
status endpoint, or automatically locked after password or MFA failures, all
existing tenant login sessions are revoked. Reactivating the account or letting
the automatic lockout expire restores eligibility to sign in but does not
restore old bearer tokens or WebSocket sessions; the user must authenticate
again.

Use the organization's approved incident/change-management system. Capture
timestamps, environment, tenant slug (avoid unnecessary patient identifiers),
request IDs, service/revision versions, operator identity, observed impact,
backups and actions. Restrict log and forensic-record access; logs may contain
sensitive operational data. Rotate any secret exposed in shell history,
browser storage, logs, a ticket, or an untrusted machine.
