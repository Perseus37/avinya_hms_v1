# Production readiness and improvement roadmap

This is the working readiness checklist for the current Avinya HMS
implementation. It separates verified repository behavior from work that must
still be completed before live hospital data is accepted. A passing test suite
does not establish clinical safety, privacy compliance, production security, or
availability.

## Current baseline

| Area | Current evidence / status |
| --- | --- |
| Development database recovery | Development-only ORM schema recovery is guarded; a retained matching database is reused and drift is not silently repaired. |
| Tenant migration policy | Production startup migrates the control plane and a newly created default tenant, verifies existing active tenants against the checked-in Alembic head and ORM metadata, and blocks startup on mismatch. Fleet migration now requires a verified canary and returns a failing process status if any tenant fails. |
| Environment configuration | `.env.example` and `.env.production.example` document runtime settings. Real env files are ignored by Git. Ports, workers, timezone, tenant DB prefix, limits, and deployment-specific DNS settings are environment-configurable. |
| Production secret preflight | A local validator rejects missing/empty required secrets, template/example values, reused secret values, short JWT signing keys, and overly broad Unix env-file permissions without printing values. It does not prove secret provenance, access control on Windows, rotation, or recovery. |
| Credential history cleanup | The user confirmed the historical `deployments/apollo.env` values were dummy values. The file was removed from all current remote branch histories (`main`, `dev`, feature branch) and all reachable version tags by a coordinated history rewrite; only `v4.5.0` was published remotely and that tag was retargeted. GitHub's read-only merged PR #1 head still points to the old commit; request GitHub Support to purge cached PR objects if a full platform-level purge is desired. |
| Local checks | The onboarding UI installs cleanly, reports zero npm audit vulnerabilities, passes its 3 utility tests, and builds with Vite 8.3.4 and `@vitejs/plugin-react` 6.1.2. The production Docker image also builds with this dependency pair. Hosted PR #13 passed all four release-gate jobs (Python/PostgreSQL 17, UI install/audit/tests/build, Compose validation, production image/Trivy). |
| PostgreSQL 17 / backup / load-test preparation | On 2026-10-18, both opt-in tests passed against a newly created disposable PostgreSQL 17 container: control-plane/tenant migration parity and encrypted custom-format `pg_dump` → GPG → `pg_restore` into an isolated DB with a sample-row check. The container and generated databases were removed. This does not validate the host backup script, attached storage, remote rsync copy, attachments, or a production restore. |
| Live infrastructure verification | Not completed: disposable PostgreSQL 17 and hosted migration-parity checks pass, and hosted Trivy passes its configured threshold. Host backup/restore script on an attached disk, off-host rsync copy, 200-user load run, DNS/TLS exercise, and production/privacy sign-off remain unverified. |
| Production deployment status | The Compose deployment is a single-host starting point, not a high-availability or regulatory-compliance certification. Do not infer approval to handle patient data from builds/tests. |
| Tenant database connection trade-off | The application caches tenant `Engine` objects but uses SQLAlchemy `NullPool` for tenant connections, avoiding multiplied per-tenant pools across workers at the cost of reconnecting per request. | This is deliberate today; no PgBouncer or persistent tenant pool has been deployed or capacity-tested. Measure connection churn and PostgreSQL saturation at representative tenant/worker counts before changing the pool strategy or scaling out. |
| 2026 code-review audit disposition | Addressed first-login privileged MFA takeover with one-time registered-email enrollment proof; per-account failed-code throttling; WebSocket pump cancellation; Unicode-safe HL7 authentication and audited secret rotation; plaintext secret fail-closed behavior; break-glass clinical permission gating; and production AI/OCR image support. | Before rollout, migrate any plaintext control-plane tenant secrets with the rotation script while the previous release is available. Continue to require the legacy static control-plane key disabled and explicit trusted-proxy CIDRs. Privacy enforcement, complete FHIR interoperability, ABDM, statutory payroll, and external production exercises remain gaps below. |
| Identity lifecycle | Tenant APIs provide password change, expiring single-use email reset links with a same-origin reset form, TOTP MFA with one-time recovery codes for privileged users, short-lived server-tracked access sessions, per-user failed-MFA throttling and lockout, and all-session logout/revocation. Initial privileged MFA enrollment requires a single-use email code stored hashed and expiring after 10 minutes; a password-only challenge cannot create or confirm an authenticator. Tenant HTTP and WebSocket access requires a session ID and rejects purpose-scoped challenge tokens. | Tenant MFA is enforced for superusers and `ROLE_ADMIN` when `MFA_REQUIRED_FOR_PRIVILEGED=true` (enabled in the production example); other users may enroll optionally. Static control-plane master-key authentication is disabled by default and production validation rejects enabling it. Validate SMTP delivery, secure first-admin credential handoff, account recovery, PostgreSQL migrations, and operator access before production. |
| Interoperability and remaining product gaps | The read-only FHIR R4 Patient API has a capability statement and patient not-found `OperationOutcome`, with `patients:read` authorization and tenant-scoped data. FHIR Encounter/Observation/Condition/write mappings, ABDM profiles/consent/authentication, and live ABDM integration are not delivered. Break-glass grants require `patients:write`, are rate-limited per user, separately reviewed by `iam:write`, and alert failures do not block committed grants. HL7 credentials are tenant-specific, encrypted in the control plane, audited on rotation without storing plaintext, redacted from stored payloads, and results are checked against the PID patient. Privacy APIs maintain consent-decision history and versioned, hospital-approved retention schedules; these do not yet gate every data use or trigger deletion. Patient anonymisation is deliberately not implemented until an approved legal policy and complete data/backup/export inventory exist. Workforce APIs provide attendance, leave request/approval, effective-dated monthly fixed-salary profiles, auditable pro-rata payroll inputs/outputs, and require different preparers/finalizers for payroll periods. | Validate custom MRN/ABHA identifier systems and FHIR mappings with integration partners; do not present this API as ABDM-compliant or connected. Validate the emergency-access minimum-data set and hospital review process; monitor the pending-review endpoint until automated reminders exist. Validate service protocols, staffing, sterilization policies, recipient identity and witness procedures, and applicable jurisdictional retention/payroll requirements with hospital owners before production; these MVP APIs are not a substitute for clinical or regulatory approval. The privacy ledger is governance evidence, not consent enforcement across all modules; do not claim anonymisation or automated retention enforcement. Workforce payroll is a configurable arithmetic ledger, not statutory payroll: paid days and components are manually supplied and tax, PF, ESI, overtime, gratuity, payslips, bank disbursement, roster integration, leave balances, and statutory compliance remain out of scope. |

## P0 — Required before first live patient data

| Work item | Why it matters | Acceptance evidence |
| --- | --- | --- |
| Validate the actual production secret set | Template placeholders, reused local credentials, weak salts, and shared keys can expose every tenant. Rotate any secret used outside local development. Store unique high-entropy values using the approved secret process; restrict `.env.production` to the deployment account and mode `0600`. | Run `python -m backend.scripts.validate_production_env --env-file .env.production` on the deployment host; it rejects `REPLACE_*`, obvious test/example credentials, empty required secrets, short JWT signing keys, repeated secret values, and broad Unix permissions. Secret provenance, strength beyond the minimum, rotation, Windows ACLs, and recovery procedures still require operator verification. |
| Resolve legacy encryption-key handling | Older local env files may contain `KMS_INTERNAL_*`, but the current code does not consume those keys. The app's actual envelope encryption is not a KMS integration. Backup/envelope salts must remain stable and recoverable or encrypted data may become unreadable. | Document the active key derivation/encryption format, perform a key backup/recovery drill, define rotation/migration behavior, and remove misleading unused variables from operator setups. |
| Test migrations on PostgreSQL 17 | SQLite tests do not establish PostgreSQL DDL, lock, extension, permission, or failure behavior. The repository's baseline revisions are fresh-install histories, and older revision IDs need a tested bridge. | The opt-in test passed once on a disposable PostgreSQL 17 container and verifies fresh migrations plus ORM parity. Still cover fresh Compose bootstrap, retained-volume restart, tenant provisioning, canary/fleet failure, restore, and customer history bridges. |
| Automate off-host encrypted backup delivery | Local encrypted dumps on the production VM do not protect against VM/host loss. | Schedule `deployments/backup-postgres.sh`, configure protected GPG key material and remote rsync/SSH destination on an independently administered host/NAS, verify checksums and retention, and alert on failed/missed jobs. The attached local disk is only a recovery copy. |
| Prove restore and recovery objectives | A successful dump is not proof that a hospital can be restored. | A disposable PostgreSQL 17 DB round-trip using custom-format `pg_dump`, GPG AES-256, and `pg_restore` passed and verified a sample row. Still run the host scripts from the attached backup mount and remote copy; restore control-plane and tenant snapshots and verify revisions, records, authentication and tenant isolation. Record measured RPO/RTO and owner sign-off. |
| Review production identity and privileged endpoints | The onboarding console still uses high-privilege shared setup keys. The UI now keeps them in memory only, and control-plane endpoints that require the second key fail closed when it is unconfigured. | Production access uses named operator accounts or an approved access proxy, MFA, least privilege, key rotation, rate limiting, audit review, secure browser controls, and a tested revocation process. If shared keys remain, document compensating controls and restrict network/browser access. |
| Verify database privilege and isolation | Bootstrap creates databases and tenant services connect using the configured PostgreSQL role. Over-privileged runtime access increases blast radius; insufficient privileges break onboarding. | Separate bootstrap/migration and runtime roles where feasible. Test that runtime cannot create/drop databases or read another tenant database, while approved provisioning can create one. Never expose PostgreSQL publicly. |
| Release gates and branch alignment | `.github/workflows/release-gates.yml` runs Python tests plus PostgreSQL 17 migration parity and dependency audits, UI install/audit/tests/build, Compose validation, production image build, and Trivy image scan. PR #13's coordinated Vite/plugin upgrade passed all four jobs and was merged to `main`; PR #14 synchronized `main` back into `dev` and passed all four jobs. `main` is an ancestor of `dev`; local `dev` was fast-forwarded to the merged sync commit. Dependabot PRs #3–#12 were resolved; tooling PR #13 was merged; no PRs are currently open. Branch-protection enforcement remains a repository-settings responsibility. | Continue periodic dependency/image review and operational readiness work. Do not infer staging or production readiness from branch alignment or passing CI. |
| Validate privacy and clinical workflows in India | Feature tests are not clinical validation or a legal assessment, and deployment geography alone does not establish compliance. | Indian hospital clinical, privacy/security, and legal owners review applicable data-protection, cybersecurity, medical-record retention, consent, access, breach response, and ABDM obligations if applicable; sign off tested workflows and operational controls before live use. |

## P1 — Required for a supported first cohort (1–10 hospitals)

| Work item | Why it matters | Acceptance evidence |
| --- | --- | --- |
| Add operational monitoring and alerting | Compose health checks only report basic process/readiness state; they are not an on-call service. | Alerts cover API availability/error rate/latency, DB disk/connection saturation, Redis health, backup freshness/failure, certificate expiry, host disk/memory/CPU, and restore-job failure. Logs are centralized with access controls and retention. |
| Make tenant migration outcomes durable and reviewable | The CLI now exits non-zero for failures, but a human still needs a complete, auditable migration record and safe retry semantics. | Per-tenant run ID, release/revision, start/end, success/failure reason, operator, and retry state are durably audited; rerun behavior is tested and no tenant is skipped silently. |
| Separate schema deployment from application startup | The new production gate prevents API startup on schema mismatch. The release process still needs careful app/schema compatibility sequencing. | Deployment runbook demonstrates backward-compatible expand/migrate/contract releases, canary verification, explicit fleet approval, health checks, and rollback/forward-fix in a staging environment. |
| Exercise capacity at representative scale | Hospital count alone is not a capacity metric; a single PostgreSQL server and four-worker API can be bottlenecks. | Start with 20 authenticated synthetic users for each of 10 tenants (200 concurrent users), ramp over 10 minutes and sustain 30 minutes; expand to 300/500 users after baseline. The included harness tests login, `/api/auth/me`, and `/health/ready` only. Add clinical, billing, reporting and backup workflows before claiming capacity. Initial p95 ≤1s, p99 ≤2s and <1% errors are provisional engineering targets, not a regulatory/industry certification. |
| Procurement invoice review and posting flow | Uploaded documents can be listed/filtered, retrieved, corrected by an authorized reviewer, reconciled against vendor/PO and mapped PO items, then approved into AP. Totals and dates are validated; posted records are locked and duplicate posting is blocked. | API end-to-end tests cover upload → manual review → list/detail → match → AP post, arithmetic validation, and duplicate-post/edit rejection. AI extraction remains advisory. The procurement workflow has no dedicated user interface; do not rely on it for live use until the separate document security/audit/retention/restore gaps are resolved. |
| Frontend analytics contract | Analytics APIs provide bounded UTC windows, SQL aggregates, explicit snapshot/period semantics, and no-store cache headers. Clinical quality/safety measures are windowed; inventory/blood radar uses hospital-configurable batch-detail limits and hospital defaults with per-medication low-stock/expiry overrides. | API tests cover finance, pharmacy, demographic, workforce access, executive aggregates, clinical thresholds, customized inventory alert settings, invalid windows, and migration/schema parity. PostgreSQL query plans/performance, privacy cohort controls, and production role review remain staging evidence. Quality/safety ratios need clinical-owner review; clinical thresholds are not validated surveillance rules. |
| Date-filtered record browsing | Invoice, prescription, diagnostic-order and appointment lists share bounded inclusive UTC calendar-date filters while retaining existing pagination, permission checks, and appointment datetime filters. Appointment status changes are transactionally recorded with actor, timestamp, from/to status and optional reason; an authorized history API exposes the log. | API tests cover date-window inclusion/exclusion and validation, status transition history and analytics, and migration/schema parity. Historical transitions before migration are irrecoverable; old appointments get only a current-status baseline. Diagnosis cohorts are not prevalence estimates, and appointment records do not measure unrecorded or unmet community demand. |
| Recorded unmet service demand | Staff can capture unavailable services, appointment-capacity gaps, missing specialists/equipment, sources and optional referral outcomes with a patient link only when needed. Aggregate analytics expose request reasons, linked catalog demand, department patterns and open backlog without returning free-text requests or patient identifiers. | API tests cover request validation, permissioned intake/listing, lifecycle transition audit and aggregate privacy boundary. Demand is manually captured, not total community need; reporting standards, follow-through on external referrals, retention, and clinical/operations interpretation must be validated with hospitals. |
| OPD appointment access and waitlist | Authorized staff can capture an appropriate requested appointment time window and explicitly convert a waiting entry into a real appointment. The transactional booking path rechecks doctor leave and slot conflicts and logs waitlist/appointment status history. | API tests cover date-window validation, patient/doctor validation, conflict-safe promotion, atomic history, and migration parity. Does not automatically prioritize or notify patients and must not replace emergency triage; verify clinical scheduling policy, cancellation/no-show handling, and user-facing contact workflows with hospitals. |
| Consultation and inpatient care timing | Appointment records snapshot planned slot minutes; status history measures arrival-to-consult wait and consultation duration against planned time. Bounded care-timing analytics report these plus scheduled start variance, encounter-to-admission interval, inpatient length-of-stay, and bed-stage durations across transfers. | API tests cover measured consultation start/end, planned-slot comparison, active inpatient elapsed time and bed stage, and migration parity. Values measure workflow timestamps, not pure face-to-face physician time or clinical decision-to-bed/discharge time. Validate data-entry discipline, timestamp accuracy, outlier handling, and fairness/context before using as individual performance or staffing targets. |
| Hospital-specific print output | Reports support separate `DESIGNED` and `PREPRINTED_DATA_ONLY` template variants, hospital-selected per-report defaults, one-off print-mode overrides, built-in A4/A5/80 mm sizes, custom physical dimensions, per-edge margins, and content alignment offsets. Data-only mode removes template headers/footers and generic styling for press-printed stock. | API tests cover mode selection, profile persistence/fork/clear, bounds, generated CSS, page-size CSS, and migration/schema parity. Hospital-owned template preparation, physical printer calibration, and clinical/legal review of official forms remain deployment work; `layout_schema` is not rendered into page positions. |
| Persist and protect tenant attachments | Procurement invoice uploads now persist in tenant-specific directories with random filenames, PDF/PNG/JPEG signature checks, a configurable size limit, permission-gated downloads, and a fresh tenant DB session for extraction. Local Compose uses a persistent named volume; production mounts `ATTACHMENTS_HOST_PATH`. | Partially implemented and covered by API tests for persistence, protected download, rejected file types/oversize content, and tenant-key path boundaries. Still required before live use: malware scanning, durable download audit, retention/deletion controls, general clinical-document workflows, cross-tenant authorization testing across live tenant DBs, and a host/off-host backup restore drill for actual files. |
| Document single-host incident and downtime procedures | The lean Compose deployment is one failure domain. | Operator runbook covers host loss, DB corruption, expired TLS, secret compromise, lost backups, restoration, patient-care downtime, escalation, and customer communication. |
| Secure document and integration paths | The production Compose API image uses a separate `production-ai` Docker target with pinned CPU-only OCR dependencies; development/testing images remain lean. Invoice AI imports are lazy and its model revision is pinned, and extraction failure retains documents for manual review. The standalone HL7 listener uses TLS plus tenant-scoped encrypted MSH-8 secrets, limits frame size, redacts credentials from stored payloads, matches results to the PID patient, audits secret rotation without logging secret values, and will not overwrite verified/cancelled results. Protected WebSocket streams use handshake subprotocol credentials, revalidate sessions periodically, and separate public waiting-room capacity. | Still validate authz, malware scanning, audit/retention of raw clinical payloads, SSRF/external resource restrictions, key rotation, and failure/retry behavior for every enabled integration. Run the listener only on an approved private network and configure equipment TLS/secret handling. |
| Remove or replace remote runtime UI dependency | The control-plane fallback console currently loads Tailwind from a public CDN; the built React UI is packaged locally. | Production operator screens load all assets locally or from a controlled, integrity-pinned asset service; deployment works with outbound internet restricted. |
| Add customer onboarding and offboarding controls | Provisioning is implemented, but lifecycle policies and support workflows need operational definition. | Approved process for tenant verification, admin handoff, access revocation, billing, data export, retention, deletion approval, and evidence is tested end-to-end. |
| Complete product surface for hospital users | Repository documentation says the tenant API exists but there is no separate tenant-facing hospital web UI. | Agree which roles/workflows are API-only versus delivered in a supported hospital UI; ship and validate the required frontline workflows before marketing a complete HMS. |

## P2 — Growth and product improvements

| Work item | Trigger / value |
| --- | --- |
| Managed PostgreSQL, independent backup storage, and point-in-time recovery | Adopt when measured database operations or recovery objectives exceed the single-host design. Avoid a database per hospital unless isolation or customer requirements justify the cost. |
| Redundant application instances and managed ingress | Adopt when an agreed uptime target cannot tolerate maintenance or host failure. Add shared session/cache and externalize all required state first. An ALB in front of one VM does not eliminate the VM failure domain. |
| Tenant-level quotas and noisy-neighbor controls | Add as telemetry reveals contention; measure database connections, API concurrency, report jobs, storage, and background work per tenant. |
| Background job system | Move long report, import, backup, and integration work out of web workers when observed request latency or delivery guarantees justify a broker/worker service. |
| Observability and support console | Add tenant-aware, privacy-safe tracing, request correlation, audit search, backup/migration status, and actionable support diagnostics without exposing patient identifiers unnecessarily. |
| API and data lifecycle maturity | Version public APIs, publish compatibility guarantees, add tested export/retention/deletion workflows, and maintain a supported integration contract. |
| Accessibility, localization, and jurisdictional configuration | Prioritize based on hospitals and legal jurisdictions; validate date/time, number/currency, language, forms, invoice rules, and accessibility with users. |
| Security assurance | Establish a recurring threat model, dependency/container scanning, penetration test, incident exercise, access review, and remediation SLA as customer and regulatory expectations grow. |
| Release automation and disaster recovery automation | Build immutable versioned images once, promote the same artifact through staging/canary/fleet, retain rollback artifacts, and automate restore drills and evidence collection. |

## Cost-conscious scaling approach

1. **Internal demo / pre-sales:** existing hardware or short-lived free credits
   with synthetic data only. No uptime, confidentiality, or disaster-recovery
   promise.
2. **First live hospital:** one maintained Linux VM with encrypted disk, shared
   app services, PostgreSQL per-tenant databases, TLS, restricted operations,
   off-host encrypted backups, monitoring, and verified restore. Cost is not
   zero; choose a monthly ceiling from live provider quotes and load tests.
3. **Up to ten hospitals:** keep the shared single-host topology only while
   measured headroom and signed risk acceptance support it. Re-check compute,
   database/storage growth, backup time, support burden, and downtime impact
   after each onboarding. Do not buy a per-hospital VM or Kubernetes by default.
4. **Beyond the accepted risk/capacity envelope:** externalize PostgreSQL and
   backups first, then add redundant app capacity and failover. Rehearse the
   migration and re-price total networking, storage, monitoring, and support.

Provider free tiers and budget alerts are not spending caps, SLAs, or
healthcare-data safeguards. The lowest-cost *responsible* option depends on
region, traffic, storage, regulatory obligations, and support needs; obtain
current quotes before selecting a production host.

## Release checklist

- [ ] Approved commit/tag and immutable image identity recorded.
- [ ] Required CI checks pass; dependency/container/image review complete.
- [ ] Production secrets are unique, protected, recoverable, and free of template placeholders.
- [ ] Staging uses the target PostgreSQL major version and representative data volume.
- [ ] Off-host backup completed and restore verification is current.
- [ ] Migration reviewed; canary and fleet scope approved; rollback/forward-fix written.
- [ ] Active tenants are at the release's checked-in Alembic head and schema parity passes.
- [ ] DNS/TLS, firewall, health/readiness, alerting, and operator access verified.
- [ ] Clinical/business owner and privacy/security owners approve the release.
- [ ] Post-deploy smoke checks, audit review, backup freshness, and rollback decision owner are assigned.

Update this document as items are completed, blocked, or superseded. Completion
must reference test/restore evidence or an accountable owner and due date; do
not mark an item complete based only on implementation code existing.

## Current continuation — 2026-10-18

- Repository branches are synchronized: `main` is at `04ca39c`; `dev` is at
  merge commit `458585f`, which includes `main`. The local checkout is on `dev`
  at that commit with a clean worktree. PR #14 performed the synchronization;
  its Python/PostgreSQL, UI, Compose, and production-image checks passed.
- Latest code release work is merged. No pull requests are open. This does
  **not** mean the product is ready for live hospital data.
- Next gate is an isolated staging rehearsal, not a production deployment:
  use synthetic data and PostgreSQL 17; exercise fresh bootstrap, retained
  volume restart, tenant provisioning, canary migration, schema parity, and
  application smoke checks. Record the staging environment, commit, migration
  heads, outcomes, and operator.
- The opt-in repository tests were rerun locally against a fresh disposable
  PostgreSQL 17 container: **2 passed** in 44.76 seconds (migration/schema
  parity and encrypted dump/GPG/restore sample-row round-trip). The test
  container and generated databases were removed afterward.
- Diagnosed the local `docker compose up -d --build` startup log from
  `backup-sidecar`: it raced `db-init` (control-plane database not created yet)
  and also encountered PostgreSQL recovery. The running sidecar later completed
  a backup. Its image-level HTTP healthcheck was also probing port 8000 in
  services with no API listener, marking the sidecars and onboarding portal
  unhealthy. The current fix gates the backup sidecar on successful `db-init`,
  retries transient SQLAlchemy `OperationalError`s, gives onboarding its own
  port healthcheck, disables HTTP checks for non-HTTP sidecars, and makes API
  health report database readiness. Focused tests (18 passed), Compose syntax
  validation, and a production image build passed locally. These changes are
  not yet in the running Compose containers; rebuild/recreate after merging
  their PR.
- Before any live-data decision, an operator must run the encrypted host backup
  script with the real protected passphrase file and independent remote
  destination, verify remote checksums, restore into a new isolated PostgreSQL
  database, and verify application records and attachment files. Record
  measured recovery time and an agreed recovery-point objective. No production
  host, remote destination, or protected key was available in this session, so
  this operational drill remains **not run**.
- Also outstanding: production secret/key recovery, database least-privilege
  and tenant-isolation review, monitoring/alerting, DNS/TLS/firewall exercise,
  and clinical/privacy owner sign-off. Do not use patient data until required
  owners approve these controls.
