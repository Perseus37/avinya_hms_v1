# Avinya Hospital Management System (HMS) — v2.1.0
**Master Architecture, API Contract & Operations Handover**

---

## 1. System Topology & Multi-Tenant Architecture

Avinya HMS is a multi-tenant, federated enterprise healthcare platform built on **FastAPI**, **SQLAlchemy 2.x**, **PostgreSQL 15**, and **Redis 8**.

### 1.1 Database-Per-Tenant Isolation
* **Control Plane Database (`hms_control_plane`):**
  * Stores `tenant_registry` (`subdomain_slug`, `hospital_name`, `branch_code`, `db_name`, `deployment_mode` [`CLOUD` | `ON_PREM`], `is_active`, and per-tenant `backup_encryption_key`).
  * Stores `federated_nodes` (`api_key_prefix`, `api_key_hash`, `branch_code`, `is_active`) and `global_patient_pointers` (Privacy-Preserving Central Discovery Index mapping `phone_number` / `abha_number` $\rightarrow$ `branch_code` + `source_mrn`).
* **Tenant Databases (`hms_default`, `hms_apollo`, `hms_<slug>`):**
  * Physically isolated PostgreSQL databases dynamically resolved per HTTP request via `X-Hospital-Subdomain` header or the `Host` subdomain (`<slug>.avinyahms.com`).
  * Managed automatically at container startup by `prestart.py`, which iterates over all registered `CLOUD` tenants and runs `alembic upgrade head`.

### 1.2 Infrastructure Stack (`docker-compose.yml`)
| Service | Port / Exposure | Responsibility |
| :--- | :--- | :--- |
| **`reverse-proxy`** | `:80` | Traefik v2.11 wildcard subdomain router (`*.localhost`, `*.avinyahms.com`). |
| **`api`** | `127.0.0.1:8000` | Core HMS FastAPI application (`v1_app` mounted at `/v1`). |
| **`onboarding-portal`** | `127.0.0.1:8001` | Zero-touch tenant provisioning portal (`onboarding_app.py`). |
| **`db`** | `127.0.0.1:5432` | PostgreSQL 15 (`TimeZone=Asia/Kolkata`, `+05:30` IST). |
| **`redis`** | Internal `:6379` | Tenant-namespaced clinical taxonomy cache (`<slug>:taxonomy:<type>:<dept_id>`) & `RateLimiter`. |
| **`backup-sidecar`** | Background | Runs `backup_service.py` every 6 hours (`pg_dump -Fc` + PBKDF2-HMAC-SHA256 / AES-256 Fernet encryption + 7-day retention pruning). |
| **`mailserver`** | `:1025` / `:8025` | Mailpit SMTP & web UI for local notification testing. |

---

## 2. Security, Auditing & RBAC Matrix

### 2.1 Automatic Actor Stamping (`AuditMixin`)
Every clinical, financial, and master-data table inherits from `(AuditMixin, Base)`[cite: 1]. A SQLAlchemy `before_flush` event listener (`stamp_audit_actor`) reads `session.info["audit_user_id"]` and automatically stamps `created_by_id` and `updated_by_id` on every `INSERT` and `UPDATE`[cite: 1].

### 2.2 Tenant-Scoped JWT & Privilege Escalation Guards
* **JWT Claims:** Tokens minted by `POST /v1/api/auth/login` embed `sub` (user ID), `tenant_slug`, and `branch_code`. Replaying a token from `apollo` against `default` is blocked with **`403 Forbidden`**.
* **Brute-Force & Rate Limiting:**
  * `RateLimiter` enforces a Redis sliding window (`10 req/min` per IP on `/login`, `60 req/min` per node on `/federation/patients/discover`), returning **`429 Too Many Requests`**.
  * 5 consecutive failed password attempts lock the account (`UserStatusEnum.LOCKED`)[cite: 1] until unlocked by an administrator (`PATCH /v1/api/auth/users/{id}/unlock`).
* **27 Granular Permissions Across 9 Modules:**
  * `iam:read`, `iam:write`, `iam:unlock`
  * `mdm:read`, `mdm:write`, `mdm:delete`
  * `patients:read`, `patients:write`, `patients:delete`
  * `wards:read`, `wards:write`
  * `opd:read`, `opd:write`
  * `adt:read`, `adt:write`, `adt:discharge`
  * `diagnostics:read`, `diagnostics:write`, `diagnostics:verify`
  * `pharmacy:read`, `pharmacy:write`, `pharmacy:dispense`
  * `billing:read`, `billing:write`, `billing:pay`, `billing:reverse`, `billing:void`

---

## 3. Core Clinical & Operational Workflows

### 3.1 Master Patient Index (MPI), Clinical MDM & CPOE Safety
* **Registration (`POST /v1/api/patients`):** Auto-generates `MRN-XXXXXXXX`, enforces unique `mrn` and `abha_number` (`409 Conflict`), and upserts a `GlobalPatientPointer` in `hms_control_plane`.
* **5 Clinical Dictionaries (`/v1/api/clinical/...`):** `allergies`[cite: 2], `complaints`[cite: 2], `comorbidities`[cite: 2], `diagnoses` (ICD-10)[cite: 2], and `physical-exams`[cite: 2]. Cached in Redis under `<tenant_slug>:taxonomy:<type>:<dept_id>` and invalidated per-tenant on write/delete[cite: 2].
* **CPOE Allergy Hard-Stop (`services/cpoe.py`):** Evaluates direct allergen matches AND beta-lactam cross-reactivity (e.g., *Penicillin* allergy blocks *Amoxicillin* / *Augmentin* / *Ampicillin* / *Cefuroxime*). Triggers **`409 Conflict`** on `SEVERE` or `ANAPHYLACTIC` allergies while allowing `MILD`/`MODERATE`[cite: 1, 2].

### 3.2 Spatial Wards, OPD Appointments & ADT Encounters
* **Ward Hierarchy (`/v1/api/ip/...`):** `Floor` $\rightarrow$ `Ward` $\rightarrow$ `PhysicalRoom` $\rightarrow$ `Bed`[cite: 1]. `GET /v1/api/ip/beds/map-view` returns nested real-time occupancy. Manual `OCCUPIED` status overrides are blocked (`400`).
* **OPD Scheduling (`/v1/api/opd/appointments`):** Enforces dynamic doctor consultation buffers (`DoctorProfile.consultation_duration_minutes`, default 15 mins)[cite: 1] and a strict linear state machine: `SCHEDULED` $\rightarrow$ `ARRIVED` $\rightarrow$ `IN_CONSULTATION` (requires `encounter_id`) $\rightarrow$ `COMPLETED`[cite: 1].
* **ADT Encounters & Admissions (`/v1/api/adt/...`):**
  * Blocks duplicate active same-type encounters (`409`) while allowing concurrent `OPD` $\rightarrow$ `IPD` transition.
  * Uses `SELECT ... FOR UPDATE NOWAIT` on `Bed` rows during admissions, bed transfers (`OLD` $\rightarrow$ `HOUSEKEEPING`, `NEW` $\rightarrow$ `OCCUPIED`), and discharges (`Encounter` $\rightarrow$ `DISCHARGED`).

### 3.3 Phase 10: EMR Consultation (Vitals, BMI, SOAP Notes & ICD-10)
* **Vitals (`POST /v1/api/clinical/encounters/{id}/vitals`):** Records `temperature_f`, `pulse_bpm`, `bp_systolic`, `bp_diastolic`, `respiratory_rate`, `spo2_percent`, `weight_kg`, `height_cm`, and automatically computes `bmi`.
* **SOAP Notes (`POST /v1/api/clinical/encounters/{id}/notes`):** Authorable by Doctors (`opd:write`). Once signed via `PATCH /v1/api/clinical/notes/{id}/sign`, `is_signed=True` permanently locks the note against future `PUT` edits (**`409 Conflict` Medico-Legal Lock**).
* **ICD-10 Diagnoses (`POST /v1/api/clinical/encounters/{id}/diagnoses`):** Links `DiagnosisMaster` to the encounter and enforces at most one `PRIMARY` diagnosis per encounter (`409`).

### 3.4 Diagnostics (LIS/RIS) & Separation of Duties
* **Order Explosion (`POST /v1/api/diagnostics/orders`):** Recursively expands composite packages (`is_order_set=True`, e.g., `PKG_CBC`)[cite: 1] into atomic `DiagnosticTask` rows[cite: 1] while guarding against circular dependencies (`400`).
* **Separation of Duties (`PATCH /v1/api/diagnostics/tasks/{id}/result`):** Entering results requires `diagnostics:write`. Transitioning a task to `VERIFIED` requires `diagnostics:verify` AND enforces `performed_by_id != current_user.id` (`403 Forbidden`) for non-superusers.

### 3.5 Pharmacy Dispensing & Enterprise Billing Ledger
* **Atomic Dispensing (`POST /v1/api/pharmacy/prescriptions/{id}/dispense`):** Locks the `InventoryBatch` row[cite: 1], blocks expired or insufficient stock (`409`), deducts stock, and automatically appends a `PHARMACY: ...` charge to the patient's `DRAFT` `Invoice`[cite: 1].
* **Billing Integrity (`/v1/api/billing/invoices`):**
  * Authoritative catalog pricing overrides any client-supplied `unit_price_paise` when `service_id` is linked.
  * Auto-billed `PHARMACY:` line items cannot be manually deleted from an invoice (`403`).
  * Payments (`POST .../payments`) support `Idempotency-Key` headers to prevent double-charging and require payment reversal (`DELETE .../payments/{id}` via `billing:reverse`) before voiding an invoice.

### 3.6 Visual Drag-and-Drop & Switchable Report Engine
* **6 Pre-Seeded System Templates (`services/standard_templates.py`):**
  1. `SYS_OPD_CASE_SHEET_A4` (`OPD_APPOINTMENT_SLIP` — Patient Demographics, BMI/Vitals strip, and 480px blank doctor handwriting canvas)
  2. `SYS_INV_A4_CLASSIC` (`INVOICE` — Standard A4 Tax Invoice)
  3. `SYS_INV_THERMAL_80MM` (`INVOICE` — 80mm POS Thermal Slip)
  4. `SYS_ERX_LETTERHEAD_A4` (`PRESCRIPTION` — eRx with NMC Reg & Allergy Banner)
  5. `SYS_DIAG_REPORT_A4` (`DIAGNOSTIC_REPORT` — Verified Lab Report)
  6. `SYS_IPD_DISCHARGE_A4` (`DISCHARGE_SUMMARY` — IPD Discharge Summary)
* **Customization Workflow (`/v1/api/reports/templates`):**
  * Query draggable variables: `GET /v1/api/reports/templates/variables/{report_type}`
  * Fork system template: `POST /v1/api/reports/templates/fork/{source_code}`
  * Reposition visual canvas widgets (`layout_schema`) & HTML/CSS: `PUT /v1/api/reports/templates/{id}`
  * Switch hospital default: `PATCH /v1/api/reports/templates/{id}/set-default`
  * Live sandbox preview: `POST /v1/api/reports/templates/preview` (rendered via `jinja2.sandbox.SandboxedEnvironment` to block SSTI).
* **5 Switchable Print Endpoints (all accept `?template_code=...`):**
  * `GET /v1/api/opd/appointments/{id}/print`
  * `GET /v1/api/billing/invoices/{id}/print`
  * `GET /v1/api/pharmacy/prescriptions/{id}/print`
  * `GET /v1/api/diagnostics/orders/{id}/print` (blocks unverified tasks with `409`)
  * `GET /v1/api/adt/admissions/{id}/discharge-summary/print` (blocks active admissions with `400`)

---

## 4. Essential Operations Runbook (PowerShell)

```powershell
# 1. Run the complete 85-scenario automated test suite
python -m pytest -v

# 2. Start / Rebuild the full multi-tenant Docker stack
docker compose up -d --build

# 3. Trigger an immediate encrypted backup of all active CLOUD tenants
docker compose exec api python backup_service.py

# 4. Decrypt and restore a tenant backup into a target database
docker compose exec api python restore_tenant.py `
  --file backups/hms_apollo_YYYYMMDD_HHMMSS.dump.enc `
  --db-name hms_apollo_restored `
  --key "<TENANT_BACKUP_ENCRYPTION_KEY>"

---

## 11. Addendum (v3.0.0): Phases 9, 11, 12 & 13 Enterprise Clinical & Financial Modules

### 11.1 Phase 9A–9D: Insurance, TPA, Plan Tariffs, Auto-Billing, Discounts & Advance Deposits
* **Insurer Plans & Negotiated Tariff Rate Cards (`InsuranceProvider`, `InsurancePlan`, `InsurancePlanTariff`):**
  * Supports `TPA`, `INSURER`, `GOVERNMENT_SCHEME`, and `CORPORATE` payers.
  * When a patient holds an active `PatientInsurancePolicy` linked to an `InsurancePlan` (`apply_tariff_automatically=True`), `resolve_patient_service_price_paise()` automatically bills the negotiated `tariff_price_paise` instead of the hospital rack rate (`ServiceCatalog.base_price_paise`).
  * `POST /v1/api/insurance/plans/compare-estimate` compares multiple plans side-by-side (Rack Total, MoU Savings, Excluded Items, Room-Rent Excess, Insurer Payable, and Patient Out-of-Pocket).
* **Cashless Pre-Authorization (`InsurancePreAuth`):**
  * Requires an `ACTIVE` encounter[cite: 1], a non-expired policy, `requested_amount_paise <= sum_insured_paise`, and a billable ICD-10 diagnosis (`DiagnosisMaster.is_billable == True`, otherwise `400 Bad Request`)[cite: 1].
* **Smart Line-by-Line Claim Adjudication (`InsuranceClaim`):**
  * Evaluates every `InvoiceLineItem`[cite: 1]: pushes non-covered items (`is_covered == False`) and daily room-rent excess above `plan.max_room_rent_daily_paise` 100% to `patient_copay_paise`, applies `copay_percentage` to the eligible pool, and posts an atomic `PaymentReceipt` (`PaymentMethodEnum.INSURANCE`)[cite: 1] upon `PATCH /v1/api/insurance/claims/{id}/settle`.
* **5 Automated Clinical Billing Hooks (`services/auto_billing.py`):**
  1. **OPD Consultation:** Transitioning an appointment to `IN_CONSULTATION`[cite: 5] auto-bills `DoctorProfile.consultation_fee_paise`[cite: 1].
  2. **IPD Multi-Bed Stay:** Discharging an admission[cite: 3] inspects all `BedTransferLog` segments[cite: 1, 3] and auto-bills each bed's `base_daily_rate_paise * days`[cite: 1].
  3. **Diagnostics CPOE:** Ordering tests/packages[cite: 6] auto-bills top-level `ordered_service_ids`[cite: 2, 6] (charging parent package price once) and automatically credits/removes them if the order is cancelled while the invoice is in `DRAFT`.
  4. **Surgery & OT Completion:** Completing a surgery auto-bills the procedure fee, `OperationTheatre.hourly_rate_paise`, and all `SurgicalImplantLog` serial-tracked prosthetics.
  5. **Blood Bank Transfusion Issue:** Issuing a cross-matched blood bag auto-bills `BloodBagInventory.processing_fee_paise`.
  * *Tamper Guard:* All auto-billed line items (`PHARMACY:`, `OPD CONSULTATION:`, `IPD BED CHARGE:`, `DIAGNOSTICS [`, `SURGERY [`, `SURGICAL IMPLANT [`, `BLOOD BANK [`) are locked against manual deletion (`403 Forbidden`) in `billing_router.py`[cite: 4].
* **Manager-Authorized Discounts & Advance Deposits (`billing_router.py`):**
  * `PATCH /v1/api/billing/invoices/{id}/discount` requires `billing:reverse` / `billing:void` and computes `net_amount_paise = total_amount_paise - discount_amount_paise`.
  * `POST /v1/api/billing/deposits` collects upfront IPD/OPD deposits; `POST .../invoices/{id}/apply-deposit` draws down deposits via `PaymentMethodEnum.ADVANCE_ADJUSTMENT`; `POST .../deposits/{id}/refund` issues an immutable `RefundVoucher`.

### 11.2 Phase 11: Inpatient Nursing eMAR, Fluid I/O & Emergency Room (ER) Triage / MLC
* **Bedside eMAR (`MedicationAdministrationLog`):** Re-runs `check_allergy_hard_stop()` at the moment of bedside `ADMINISTERED` charting (`409 Conflict` if a new severe allergy was charted after the eRx was written) and requires mandatory clinical notes for `HELD`, `REFUSED`, or `MISSED` doses (`400`).
* **Fluid Balance (`FluidBalanceLog`):** Tracks `INTAKE` vs `OUTPUT` in mL and computes `net_balance_ml`.
* **ER Triage & MLC (`ERTriageAssessment` & `MedicoLegalCase`):** Enforces `EncounterTypeEnum.ER`[cite: 1], sorts active ER queues by `RED_RESUSCITATION` priority, registers doctor-authored `MLC-YYYYMMDD-...` cases, locks police intimation (`409`), and prints the official SHO Police Intimation Certificate (`SYS_MLC_POLICE_INTIMATION_A4`).

### 11.3 Phase 12: Operation Theatre (OT), WHO Safety Checklist & Implant Traceability
* **Conflict Guard (`SurgicalSchedule`):** Blocks overlapping time windows in the same `OperationTheatre` or for the same `lead_surgeon_id` (`409 Conflict`).
* **WHO Surgical Safety Hard-Stops (`WHOSurgicalSafetyChecklist`):** Blocks `IN_PROGRESS` until Sign-In/Time-Out checks are confirmed (`400`), and blocks `COMPLETED` until `sponge_instrument_count_verified` (Sign-Out) and `OperativeAnesthesiaRecord` are recorded (`400`). Prints `SYS_OT_OPERATIVE_NOTE_A4`.

### 11.4 Phase 13: Blood Bank Transfusion Safety & Radiology PACS/DICOM
* **ABO/Rh Compatibility Hard-Stop (`BloodCrossMatchOrder`):** Evaluates `PRBC_COMPATIBILITY_MATRIX` and `FFP` rules against `patient.blood_group`[cite: 1]; incompatible blood groups are blocked with `409 Conflict`. Adverse reactions quarantine the bag as `REACTION_HALTED`.
* **Radiology PACS (`RadiologyStudyMetadata`):** Attaches `study_instance_uid`, `accession_number`, `modality`, `pacs_viewer_url`, and `radiation_dose_mgy` strictly to `ServiceTypeEnum.RADIOLOGY` tasks[cite: 1] (`400` if `LAB`[cite: 1]).

### 11.5 Updated Automated Test Matrix (119 Scenarios Total)
| Test File | Count | Module |
| :--- | :---: | :--- |
| `test_01` – `test_08` | **94** | Core Tenancy, IAM/RBAC, MPI/MDM/CPOE, Wards/OPD/ADT, LIS, Pharmacy/Billing, Federation/Backups, EMR & Switchable Templates |
| `test_09_insurance_and_auto_billing.py` | **14** | TPA/Insurer Policies, Pre-Auth Guards, Co-Pay Claims, Auto-Billing (OPD, IPD Beds, Diagnostics), Plan Tariffs, Room Caps, Plan Estimator, Discounts & Advance Deposit Refunds |
| `test_10_nursing_emar_and_er_mlc.py` | **5** | Inpatient eMAR, Bedside Allergy Hard-Stop, Fluid I/O Balance, ER Acuity Triage Queue & MLC Police Intimation Print |
| `test_11_operation_theatre_and_surgery.py` | **3** | OT Theatre/Surgeon Overlap Guard, WHO Safety Checklist Hard-Stops, Surgery/Implant Auto-Billing & Operative Print |
| `test_12_blood_bank_and_radiology_pacs.py` | **3** | ABO/Rh Compatibility Hard-Stop (`409`), Universal Donor Issue + Auto-Billing + Reaction Quarantine, and PACS DICOM Study Attachment |