# Architecture and knowledge transfer

This file is the stable landing page for architecture and KT material. Detailed
source maps live in [Project Report and KT](PROJECT_REPORT_AND_KT.md);
operational commands and safety procedures live in
[Operations Runbook](OPERATIONS_RUNBOOK.md).

## Read these documents

1. [Master System Architecture and Technical Specification](SYSTEM_ARCHITECTURE_AND_DESIGN.md) — comprehensive high-level and low-level design (HLD & LLD), covering multi-tenancy, all 24 healthcare domains, envelope encryption, background queues, and disaster recovery.
2. [System Inventory and Boundaries Specification](SYSTEM_INVENTORY_AND_BOUNDARIES.md) — complete inventory of what the system contains (subsystems, tables, APIs, controls) versus what is explicitly shelved or out of scope (patient portal, tenant clinical UI).
3. [Project Report and KT](PROJECT_REPORT_AND_KT.md) — system topology,
   responsibilities, database boundaries, backend/frontend file map, tests,
   limitations, and onboarding reading order.
4. [Operations Runbook](OPERATIONS_RUNBOOK.md) — local/production checks,
   releases, schema migrations, backups/restores, troubleshooting, and
   release checklist.
5. [Project Structure](PROJECT_STRUCTURE.md) — current versus intended package
   structure and migration notes.
6. [Production Deployment](PRODUCTION_DEPLOYMENT.md) — provider, networking,
   secrets, cost and VM deployment guidance.
7. [Migration Studio](MIGRATION_STUDIO.md) — import workflow, ETL blocks,
   duplicate policies, and safety limitations.
8. [Onboarding Improvements](ONBOARDING_IMPROVEMENTS.md) — existing analytics,
   audit-field inventory and improvement backlog.

The earlier content of this Markdown file contained implementation claims
that no longer matched the repository and has been replaced. The companion
`ARCHITECTURE_AND_KT.docx` is a legacy artifact and has not been regenerated;
prefer the Markdown documents above until it is reviewed.
