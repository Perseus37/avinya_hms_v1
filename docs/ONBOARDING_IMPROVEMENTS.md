# Onboarding portal: audit status and improvement backlog

## Current portal analytics

The existing Executive Command Center already summarizes fleet activity,
commercial ARR, hospital-reported revenue/bed census, module adoption, backup
inventory, and a tenant leaderboard. Prefer improving that dashboard rather
than creating a second analytics page. Before expanding it, add explicit
metric definitions, data-freshness labels, and time-range filters; current
metrics mix current snapshots with cumulative database totals and should not
be presented as a time series.

The current dashboard now includes an onboarding-funnel snapshot for registered,
database-ready, configured, first-admin-sign-in, and active tenants, plus
all-time provisioning/email failure counts and median time from tenant registry
creation to first admin login. The first-login timestamp is persisted going
forward; it is not backfilled for existing users. Database readiness is a live
tenant database probe, and configured means the entitlement/quota JSON is
present—not that a formal readiness checklist has been signed off.

Provisioning welcome email now uses the configured SMTP/AWS SES/Azure
Communication Services provider and never includes generated credentials.
Successful provisioning and email delivery remain separate states.

## Low/no-additional-service backlog

These can run with the current Docker Compose stack, but should be implemented
as separate, testable tasks rather than unverified charts:

1. **Operational health:** last successful backup/restore drill, failed
   migrations, stale tenants, API error rates, and disk/database headroom.
   Mark the timestamp and source of each signal; do not imply a health check
   proves a backup is restorable.
2. **Trend reporting:** date-range filters for onboarding, license changes,
   and tenant operational events. Store timestamped events instead of
   reconstructing history from current rows.
3. **Accessibility and workflow polish:** keyboard navigation, visible
   focus/disabled states, responsive table actions, and actionable empty/error
   states for every portal page.
4. **Migration quality of life:** durable job status, cancel/retry where safe,
   exportable row errors, composite-key duplicate policies, resumability, and
   rollback/compensation guidance. The current `BackgroundTasks` worker is
   process-bound and is not durable.

The remaining items require application work and local PostgreSQL/Compose resources,
not a paid analytics service. Reliable uptime history, off-host backup
verification, and multi-instance job processing need additional persistent
infrastructure or external services.

## Audit-field inventory

The current tenant ORM metadata contains **72 mapped tables**. **70 inherit
`AuditMixin`** and declare `created_at`, `updated_at`, `created_by_id`, and
`updated_by_id`; the two current exceptions are `federation_transfer_logs`
and the `role_permissions` association table. This describes ORM declarations,
not necessarily every already-deployed database revision—verify actual schemas
with migrations/database inspection before relying on the fields. The inventory
does not include the separate control-plane database tables.

The audit mixin sets timestamps using database defaults and ORM update hooks.
Authenticated tenant requests populate `Session.info["audit_user_id"]`, so
normal ORM flushes stamp actor IDs. System, bulk, or background code must set
actor context explicitly; bulk SQL operations can bypass ORM flush listeners.
Control-plane records have their own `created_at` and actor string and are not
tenant `AuditMixin` rows.

Migration Studio imports run under the vendor setup key, not a tenant identity.
Job start/completion/failure and aggregate counts are recorded in the
control-plane audit ledger with the vendor-console actor. Row-level tenant
`created_by_id` / `updated_by_id` are left null rather than attributed to a
fabricated user ID; timestamps are populated through ORM/database behavior.
If row-level attribution to an individual is required, first add an
authenticated vendor/operator identity and a deliberate cross-database actor
mapping. Do not treat the vendor setup key as a person.

## Configuration single source of truth

Changeable runtime defaults belong in `backend/core/config.py` (`Settings`)
with deployment overrides supplied by environment variables. Current
configurable operational limits include the maximum list page size and CSV
upload, preview, and expression bounds. Database URLs, tenant URL templates,
timezone, auth token lifetime, and credentials already use settings.

Do not move security invariants, protocol syntax, fixed enum values, or
algorithmic rules into environment settings solely to eliminate literals.
Environment settings should be validated and documented; deployment-specific
secrets belong in a secret manager or protected `.env.production`, not source.

| Setting | Default | Controls |
| --- | ---: | --- |
| `MAX_PAGE_SIZE` | 200 | Maximum accepted page size in shared pagination validation. |
| `MIGRATION_PREVIEW_ROWS` | 5 | Number of transformed sample rows shown in preview. |
| `MIGRATION_PREVIEW_ERROR_LIMIT` | 100 | Maximum preview validation errors returned. |
| `MIGRATION_MAX_UPLOAD_BYTES` | 52,428,800 | CSV upload limit (50 MiB). |
| `MIGRATION_MAX_EXPRESSION_DEPTH` | 20 | Maximum nested expression depth. |
| `MIGRATION_MAX_INPUTS` | 50 | Maximum inputs for combine/coalesce. |
