# Consent and Retention Policy Inputs

Use this questionnaire to record hospital-approved decisions for consent and
retention. It is an intake document, not legal advice or an active policy.

## Status and safety boundary

- Policy decisions: **Not yet approved / incomplete**
- Enforcement mode: **OFF**
- Existing consent and retention records: **Unchanged**
- Deletion, anonymisation, and tenant retention dry-run: **Not implemented**

The proposals recorded below came from an initial discussion. They are not
approved requirements. Mark each answer as **Approved**, **Proposed**, or
**Not decided**, and include the approving authority and evidence where
applicable. Do not use a proposed answer to enable enforcement or destructive
processing.

## 1. Scope, jurisdiction, and approval

1. Which hospital(s)/tenant(s) does this policy cover?
2. Is policy configured separately for each hospital? If not, what is the
   approved shared-policy scope?
3. Which countries, states, and regulatory regimes apply? Does data cross
   borders, and if so, where?
4. Which authority approved this policy (hospital privacy/legal authority,
   clinical governance, or other)?
5. Provide approval reference, approval date, policy version, effective date,
   and review date.
6. Which decisions require counsel review, and who owns obtaining it?

**Response:**

| Decision | Answer | Status (Approved / Proposed / Not decided) | Approver / reference |
|---|---|---|---|
| Tenant scope and policy ownership |  |  |  |
| Jurisdictions and cross-border transfers |  |  |  |
| Approval and review details |  |  |  |

## 2. Consent purposes and legal basis

Confirm, edit, or remove each proposed purpose. Add any missing purpose. State
whether consent is required and the approved legal basis. A purpose marked
“not consent-gated” still needs an approved basis and scope.

| Proposed purpose | Keep / edit / remove | Code and display name | Consent required? | Legal basis | Status / approver |
|---|---|---|---|---|---|
| Sharing with external providers or health-information exchanges, including ABDM |  |  |  |  |  |
| Sharing with insurers/TPAs beyond information needed for a claim |  |  |  |  |  |
| Identifiable research |  |  |  |  |  |
| Marketing and health-awareness messages |  |  |  |  |  |
| Teaching, case presentations, photos, and media |  |  |  |  |  |
| Optional SMS/WhatsApp notifications |  |  |  |  |  |
| Registries and quality programs |  |  |  |  |  |
| Treatment |  |  |  |  |  |
| Hospital operations |  |  |  |  |  |
| Payment and claims |  |  |  |  |  |
| Other: |  |  |  |  |  |

Confirm whether treatment, hospital operations, and payment are not consent
gated. If any are gated, specify the workflow and emergency behavior.

## 3. Consent validity and capture

For every purpose that requires consent, complete the rules below. Attach a
purpose-specific table if rules differ.

| Purpose code | Required before collecting, using, disclosing (which?) | Scope: patient / episode / recipient / other | Expiry or until withdrawn | Capture methods | Who may consent? | No record means: deny / allow / ask | Status / approver |
|---|---|---|---|---|---|---|---|
|  |  |  |  |  |  |  |  |

Specify additional requirements:

- Information shown to the patient before consent:
- Language, accessibility, and interpreter requirements:
- How identity and representative authority are verified:
- How consent is evidenced, versioned, corrected, and audited:
- Whether consent may be partial by data category, recipient, or time period:

## 4. Exceptions and overrides

For each approved exception, state the allowed purposes and controls. “Never
override” purposes must be explicit. The proposed non-overridable purposes of
research and marketing are not approved unless confirmed below.

| Candidate exception | Approved? | Purposes it can cover | Authorized invoker | Required reason/evidence | Reviewer and review deadline | Status / approver |
|---|---|---|---|---|---|---|
| Emergency care |  |  |  |  |  |  |
| Court order or other statutory disclosure |  |  |  |  |  |  |
| Notifiable disease/public health reporting |  |  |  |  |  |  |
| Patient lacks capacity and has no representative |  |  |  |  |  |  |
| Internal audit or quality review |  |  |  |  |  |  |
| De-identified data use |  |  |  |  |  |  |
| Other: |  |  |  |  |  |  |

**Purposes that can never be overridden:**

**Counsel review required/completed (include reference):**

## 5. Withdrawal

Confirm or edit each proposed behavior:

| Question | Approved behavior | Status / approver |
|---|---|---|
| When does withdrawal take effect? |  |  |
| What happens to queued or in-flight exports, shares, and messages? The proposal is to cancel future actions. |  |  |
| Must prior recipients be notified? What can/cannot be recalled? |  |  |
| Does statutory record retention continue after withdrawal? |  |  |
| What consequences must be explained to the patient? |  |  |
| Should clinicians see an alert? For which purposes and roles? |  |  |
| Does withdrawal affect treatment, hospital operations, or payment? |  |  |

## 6. Patient groups and special handling

For each group, state whether a distinct rule is required now, the rule owner,
and the legal/clinical approval reference. “Deferred” means no new behavior is
implemented for that group until approved.

| Patient group | Distinct rule now? | Approved rule or deferred scope | Counsel/clinical review and reference |
|---|---|---|---|
| Minors and guardian authority/age of majority |  |  |  |
| Patients lacking capacity |  |  |  |
| Mental-health patients |  |  |  |
| HIV and sexual/reproductive health |  |  |  |
| Medico-legal cases |  |  |  |
| VIP/privacy-flagged patients |  |  |  |
| Deceased patients |  |  |  |
| Foreign nationals/medical tourism |  |  |  |
| Research participants |  |  |  |
| Other: |  |  |  |

## 7. Retention schedule and data inventory

An approved schedule requires a reviewed inventory of where each category is
stored or copied. Include tenant databases, control-plane data, files/object
storage, audit logs, backups, exports, integrations, and downstream recipients
as applicable. Do not infer table mappings or retention periods from code.

| Data category | Purpose/system of record | Tables, fields, file stores, and copies/exports | Retention period | Clock-start event/date | Action: retain/archive/delete/anonymise | Legal-hold trigger and authority | Backup/export treatment | Status / approver |
|---|---|---|---|---|---|---|---|---|
|  |  |  |  |  |  |  |  |  |

Additional decisions:

- Which records must be retained by law or professional rules, and for how
  long?
- How are backups treated (expiry, immutable snapshots, restore-time
  reapplication of deletion/holds)?
- How are exports and downstream recipients handled?
- Are deletion, anonymisation, or archival actions permitted? Define
  irreversibility and verification requirements.
- What legal-hold events apply, who can place/release a hold, and what evidence
  is required?
- Who approves a data inventory and retention schedule?

## 8. Validation and rollout authorization

1. Which staging tenant/environment is approved?
2. Must validation use synthetic data only? If not, identify the explicit
   approval and safeguards for any real data.
3. What tests, acceptance criteria, audit evidence, and rollback plan are
   required?
4. Is the approved initial phase still **OFF**, followed by staging
   audit-only, and a separate approval before enforcement?
5. Who authorizes each phase and provides the change/approval reference?
6. Are any dry-run reports approved? If yes, confirm counts-only output by
   default and any separately approved access to identifiers.

**Response:**

| Phase / environment | Approved scope and acceptance criteria | Approver / reference | Date |
|---|---|---|---|
| Staging validation |  |  |  |
| Audit-only |  |  |  |
| Enforcement (separate approval required) |  |  |  |
| Retention dry-run (separate approval required) |  |  |  |

## Implementation boundary

Returning this document does not itself activate policy or authorize code
changes that affect patient workflows or dispose of data. Before implementation,
the answers must be approved, internally consistent, scoped to a tenant, and
supported by the required data inventory and validation plan. Enforcement,
retention dry-runs, deletion, and anonymisation remain disabled until their
specific prerequisites and approvals are met.

## Structured policy drafts (schema version 1)

Completed answers must be provided as normalized JSON fields that conform to
the versioned consent or retention schema. The importer wraps these section
answers in `schema_version: 1`; it does not guess values from prose or a
spreadsheet. A draft is rejected if its schema
version is unknown, it contains unsupported fields, a reference is unknown,
or the policy is internally inconsistent. Questionnaire prose is not
automatically interpreted as policy.

For a consent policy, use a JSON envelope with `policy_type` set to `CONSENT`
and the questionnaire's structured fields under `answers`:

```json
{
  "policy_type": "CONSENT",
  "answers": {
    "policy_code": "hospital-policy-v1",
    "purposes": [
      {
        "code": "EXTERNAL_SHARING",
        "display_name": "External sharing",
        "consent_required": false,
        "legal_basis": "Not decided; draft only."
      }
    ],
    "data_use_points": {
      "fhir.patient.read": "EXTERNAL_SHARING"
    },
    "exceptions": [],
    "never_override": [],
    "groups": [],
    "withdrawal": {
      "effective": "immediate",
      "cancel_queued": true,
      "cancel_in_flight": true,
      "notify_prior_recipients": "never",
      "statutory_retention_continues": true,
      "patient_notice": "Draft notice; not approved.",
      "clinician_alert_purposes": [],
      "does_not_affect_treatment": true
    }
  }
}
```

The importer converts this to the schema-v1 policy under `policy_document`.
Alternatively, the API draft endpoint accepts an already-versioned
`policy_document`. The supported
fields are purpose definitions, data-use-point mappings, exceptions, the
never-override list, patient groups, and withdrawal behavior. Consent methods,
scope, legal basis, no-record behavior, and purpose references are validated.
New rule types require an explicit schema/engine change; they are not evaluated
as executable expressions.
Schema-v1 consent validity currently supports `until_withdrawn`, `fixed_term`,
and `per_episode`. It supports configured age-based groups; other patient flags
remain deferred until those fields and their trusted data sources are added.
Exceptions can be validated and evaluated by the pure engine, but request
routes do not yet accept exception claims or create the post-event review
queue. Withdrawal effects on queued work and recipient notification are stored
as policy answers but are not wired to outbound workflows.

The registry and audit hooks cover FHIR Patient read/search, federation
discovery/pointer publication, patient-index reads, selected clinical records,
ADT/admission and nursing/ER records, statutory registries, OPD scheduling,
billing ledgers and patient documents, prescriptions, diagnostic orders and
reports, insurance patient documents, ambulance/dietary/mortuary workflows,
break-glass patient access, and operative-note printing. A CI test classifies
every API GET route as either a registered point or an explicitly listed
non-patient/deferred route, and focused checks require coverage across the
patient, clinical, ADT, OPD, billing, pharmacy, diagnostics, insurance,
emergency, registry, and service-operation routers.

This is not end-to-end enforcement. Analytics endpoints return aggregates and
are explicitly deferred because the engine does not yet support cohort-level
consent filtering or small-cell privacy controls. Governance and operational
master-data endpoints are not patient data-use points. POST/PUT/PATCH response
coverage and any integration/export path not represented by a GET operation
still need a dedicated review. ENFORCE remains unavailable.

For retention, `policy_type` is `RETENTION`; its `answers` include a
category code, system of record, reviewed locations, retention days, clock
start, action, legal-hold triggers, and backup/export treatment. This schema
does not create or run retention jobs. The category-count helper requires an
explicit inventory adapter and returns aggregate counts only.

Validate and upload a structured JSON policy export as a draft:

```powershell
$env:AVINYA_PRIVACY_API_TOKEN = "<authorized tenant token>"
python -m backend.scripts.import_privacy_policy .\policy.json `
  --api-url https://<tenant-host>
```

The importer validates locally and submits to the existing tenant policy
draft endpoint. The environment variable keeps the token out of command-line
arguments; do not paste tokens into logs or source control. Draft creation does
not approve or activate the policy.

The synthetic strict, lenient, no-emergency, and minors fixtures under
`tests/fixtures/privacy_policy_*.json` are examples for tests only, not hospital
recommendations or valid legal policies.

Audit-only activation is disabled unless `PRIVACY_ALLOW_AUDIT_ONLY=true` is
explicitly set in a non-production deployment. Application configuration
rejects that setting in production, and the activation route independently
rejects production. The retention dry-run similarly requires
`PRIVACY_ALLOW_RETENTION_DRY_RUN=true` in non-production and is disabled in
production. The audit and retention reports contain aggregate counts only.
`ENFORCE` is not an activatable mode; no production privacy enforcement is
enabled.
