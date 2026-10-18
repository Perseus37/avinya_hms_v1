### Critical Architecture & Concurrency Defects

#### ARCH-001: Connection Pool Exhaustion Across Worker Fleet

* **Category:** Architecture & Reliability
* **Severity:** CRITICAL
* **Location:** `backend/database.py:44` (`TenantEngineRegistry`), `Dockerfile:63`
* **Evidence:** `TenantEngineRegistry` caches up to 64 engines with `pool_size=3` and `max_overflow=5` (`MAX_CACHED_TENANT_ENGINES = 64`). The production Dockerfile launches 4 Gunicorn Uvicorn workers.


* **Problem:** Each worker process maintains its own independent memory and LRU cache. Across 4 workers, this permits $(3 + 5) \times 64 \times 4 = 2,048$ concurrent TCP sockets to PostgreSQL.


* **Impact:** Exceeds standard PostgreSQL `max_connections` (typically 100), throwing `OperationalError: FATAL: too many connections for role` and taking the entire platform offline.


* **Recommendation:** Deploy PgBouncer in front of PostgreSQL for transaction pooling; reduce `pool_size=1`, `max_overflow=2`, or adopt SQLAlchemy's `NullPool` for tenant engines, maintaining a persistent pool only for `hms_control_plane`.


* **Priority:** Immediate

#### ARCH-002: In-Memory Analytics Table Scans & OOM Vulnerability

* **Category:** Performance & Scalability
* **Severity:** CRITICAL
* **Location:** `backend/routers/v1/analytics_router.py:48-245`
* **Evidence:** Endpoints like `/api/analytics/executive-command-center` and `/api/analytics/financial-revenue-cycle` call `select(Bed).scalars().all()`, `select(PatientEncounter).scalars().all()`, `select(InpatientAdmission).scalars().all()`, and `select(Invoice).scalars().all()`.


* **Problem:** Every metric is computed using in-memory Python `for` loops, dict accumulations, and list comprehensions rather than database-level aggregations.


* **Impact:** Once a hospital accumulates tens of thousands of patient visits and invoices, a single request to the executive dashboard pulls hundreds of megabytes of raw rows into Python heap space, triggering Linux OOM-killer termination of the worker process.


* **Recommendation:** Refactor all aggregation metrics into SQL expressions using `func.count()`, `func.sum()`, `func.coalesce()`, and SQL-level `GROUP BY` and date truncation expressions.


* **Priority:** Immediate

#### ARCH-003: Check-Then-Act Race Condition in OPD Scheduling

* **Category:** Code & Concurrency
* **Severity:** HIGH
* **Location:** `backend/routers/v1/opd_router.py:44-59`, `132-154`
* **Evidence:** `check_schedule_overlap` runs `db.query(Appointment).filter(...)` to check for conflicting time slots, followed by an application-level `db.add(appointment)`.


* **Problem:** The read and insert steps are not serialized by a row lock, range lock, or database exclusion constraint.


* **Impact:** Two patients booking the same doctor for the same time slot concurrently will both pass the `SELECT` check and be double-booked.


* **Recommendation:** Add a PostgreSQL GiST exclusion constraint on `(doctor_id, tsrange(appointment_time, appointment_time + duration))` using the `btree_gist` extension, or serialize booking requests per doctor using Redis distributed locks or PostgreSQL advisory locks.


* **Priority:** Before Production

#### ARCH-004: In-Memory Conflict Loop in Surgical Scheduling

* **Category:** Performance & Concurrency
* **Severity:** HIGH
* **Location:** `backend/routers/v1/ot_router.py:61-82`
* **Evidence:** `schedule_surgery` queries all active surgical schedules from disk (`select(SurgicalSchedule).where(SurgicalSchedule.status != SurgeryStatusEnum.CANCELLED).scalars().all()`) and loops over them in Python to test start and end time boundaries.


* **Problem:** Full table scan inside Python application memory without database range queries.


* **Impact:** $O(N)$ memory growth and CPU overhead on every surgery booking, paired with check-then-act race conditions that allow double-booking of operation theatres and surgeons.


* **Recommendation:** Replace the Python loop with a database-level query using PostgreSQL range overlap operators: `tsrange(scheduled_start_time, scheduled_end_time) && tsrange(:new_start, :new_end)`.


* **Priority:** Before Production

#### ARCH-005: Event Loop Starvation in Synchronous HL7 Listener

* **Category:** Reliability & Concurrency
* **Severity:** CRITICAL
* **Location:** `backend/services/hl7_listener.py:79-115`
* **Evidence:** `handle_mllp_client` is an `asyncio` coroutine reading TCP bytes via `StreamReader` and `StreamWriter`, but calls `process_and_route_result(raw_msg)` synchronously on the same thread.


* **Problem:** `process_and_route_result` connects to PostgreSQL, creates sessions, and runs blocking queries (`ControlSessionLocal()`, `t_db.commit()`) directly on the event loop.


* **Impact:** Under high-volume lab analyzer output, the Python event loop freezes, blocking all other socket handlers, leading to dropped analyzer connections and lost telemetry.


* **Recommendation:** Wrap blocking execution inside `asyncio.to_thread(process_and_route_result, raw_msg)` or transition the service to `asyncpg` and SQLAlchemy `AsyncSession`.


* **Priority:** Immediate

#### ARCH-006: Polling-Induced Latency and CPU Thrashing in WebSockets

* **Category:** Performance & Concurrency
* **Severity:** MEDIUM
* **Location:** `backend/routers/v1/websocket_router.py:46-62`
* **Evidence:** `_send_pump` runs an infinite loop calling `pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)` followed by `await asyncio.sleep(0.05)`.


* **Problem:** Instead of using non-blocking asynchronous pub/sub listeners, the code polls synchronous Redis client connections inside an `asyncio` loop with a fixed 50ms delay.


* **Impact:** Inefficient CPU polling cycles and a guaranteed minimum 50ms latency penalty for emergency trauma and panic lab alerts.


* **Recommendation:** Migrate WebSocket routes to `redis.asyncio` and use `async for message in pubsub.listen():` for direct event-driven pushes.


* **Priority:** Planned

---

### Data Integrity & Database Constraints

#### DB-001: Missing Optimistic Concurrency Control on Invoices

* **Category:** Database & Financial Integrity
* **Severity:** HIGH
* **Location:** `backend/models.py:382-418` (`Invoice`), `backend/routers/v1/billing_router.py`
* **Evidence:** The `Invoice` model lacks a `version_id` column and relies entirely on pessimistic locking when updating financial balances.


* **Problem:** Multi-step financial transactions that occur across distributed API calls (such as partial deposits, payment receipting, and discount waivers) can overwrite each other if lock boundaries are released between steps.


* **Impact:** Discrepancies between `total_amount_paise`, `discount_amount_paise`, and `amount_paid_paise`, causing invoices to incorrectly display balances or stall in `PARTIAL` states.


* **Recommendation:** Add `version_id: Mapped[int] = mapped_column(Integer, default=1, nullable=False)` to `Invoice` and configure `__mapper_args__ = {"version_id_col": version_id}`.


* **Priority:** Before Production

#### DB-002: Idempotency Vulnerability in Payment Processing

* **Category:** Financial Integrity
* **Severity:** HIGH
* **Location:** `backend/models.py:440-454` (`PaymentReceipt`), `backend/routers/v1/billing_router.py:214-256`
* **Evidence:** `record_payment` checks for existing transactions via `db.query(PaymentReceipt).filter(...)` before inserting. The model only indexes `invoice_id` (`Index("ix_payment_receipts_invoice_id", "invoice_id")`).


* **Problem:** There is no database-level unique constraint on `(invoice_id, transaction_reference)` or `receipt_number`.


* **Impact:** Under concurrent client retries with the same `Idempotency-Key` or payment gateway webhook replays, two identical requests can simultaneously query the database, find no match, and record two separate payment receipts, double-charging the patient.


* **Recommendation:** Add `UniqueConstraint("invoice_id", "transaction_reference", name="uq_invoice_txn_ref")` and catch `IntegrityError` to return the existing receipt.


* **Priority:** Immediate

#### DB-003: Central Discovery Index Unique Constraint Failure for Newborns

* **Category:** Data Integrity & Registries
* **Severity:** CRITICAL
* **Location:** `backend/control_models.py:61-75` (`GlobalPatientPointer`), `backend/routers/v1/vital_registries_router.py:41-70`
* **Evidence:** `GlobalPatientPointer` defines `UniqueConstraint("phone_hash", "branch_code", name="uq_pointer_phone_branch")`. `register_maternity_birth` sets `phone_number=mother.phone_number` for live births.


* **Problem:** Because mother and newborn share the exact same phone number at the same branch code, their `phone_hash` is identical.


* **Impact:** Registering a birth for any mother already indexed in the Control Plane crashes with `IntegrityError` on `uq_pointer_phone_branch`, preventing birth record generation in the data plane.


* **Recommendation:** Update the unique constraint on `GlobalPatientPointer` to include the patient identifier or MRN: `UniqueConstraint("phone_hash", "branch_code", "source_mrn", name="uq_pointer_phone_branch_mrn")`.


* **Priority:** Immediate

#### DB-004: Default Phone Number Placeholder Causes Hash Collisions

* **Category:** Data Integrity & Privacy
* **Severity:** HIGH
* **Location:** `backend/models.py:255`, `backend/routers/v1/federation_router.py:35-43`
* **Evidence:** In `Patient`, `phone_number` defaults to `"0000000000"`. `normalize_phone_hash` maps missing/empty numbers to `"0000000000"` before running SHA-256.


* **Problem:** All patients registered without a phone number share the exact same SHA-256 hash in `GlobalPatientPointer`.


* **Impact:** Calling `/api/federation/patients/discover?phone_number=0000000000` aggregates clinical and demographic records from hundreds of unrelated patients across different hospitals into a single profile.


* **Recommendation:** Store `phone_number` as nullable without default `"0000000000"`; bypass `GlobalPatientPointer` creation for patients lacking a valid, verified phone number or ABHA ID.


* **Priority:** Immediate

#### DB-005: Split Billing Race Condition on Draft Invoices

* **Category:** Financial Integrity
* **Severity:** HIGH
* **Location:** `backend/services/auto_billing.py:38-66`
* **Evidence:** `get_or_create_draft_encounter_invoice` attempts to find an existing DRAFT invoice using `select(Invoice)...with_for_update()`. If `None`, it inserts a new `Invoice`.


* **Problem:** If two automated events occur simultaneously (e.g., automated bed charges and lab order creation), both transactions will find no draft invoice and insert separate DRAFT invoices for the same encounter.


* **Impact:** Inpatient stays split billing lines across multiple invoices, making full invoice reconciliation and financial clearance checks inconsistent.


* **Recommendation:** Add a partial unique constraint in PostgreSQL: `CREATE UNIQUE INDEX uq_encounter_draft_inv ON invoices (encounter_id) WHERE status = 'DRAFT';` and handle upserts using `ON CONFLICT DO NOTHING`.


* **Priority:** Before Production

#### DB-006: Missing Soft-Delete Tracking on Transactions

* **Category:** Auditability & Compliance
* **Severity:** MEDIUM
* **Location:** `backend/models.py:290-340`, `380-450`
* **Evidence:** `DiagnosticOrder`, `PatientEncounter`, and `InpatientAdmission` only maintain status enums.


* **Problem:** There are no `is_deleted` or `deleted_at` timestamp columns for soft-deletion on clinical transactional entities.


* **Impact:** Any database cleanup script or hard `DELETE` query permanently deletes medical history, violating healthcare record-retention regulations (e.g., NABH, HIPAA).


* **Recommendation:** Extend `AuditMixin` or create a `SoftDeleteMixin` providing `is_deleted: Mapped[bool]` and `deleted_at: Mapped[Optional[datetime]]` across all clinical entities.


* **Priority:** Planned

#### DB-007: Missing Composite Indexes for Financial and Clinical Reporting

* **Category:** Database Performance
* **Severity:** MEDIUM
* **Location:** `backend/models.py`
* **Evidence:** `invoices` only indexes `patient_id` and `encounter_id`. `patient_encounters` only indexes `(patient_id, status)`.


* **Problem:** Reporting routes filter heavily by date ranges and statuses.


* **Impact:** Aging A/R queries (`invoices.created_at`), occupancy analyses (`inpatient_admissions.discharge_time`), and outpatient scheduling queues trigger sequential table scans on PostgreSQL.


* **Recommendation:** Add composite indexes:
* `Index("ix_invoices_status_created", "status", "created_at")`
* `Index("ix_admissions_discharge_time", "discharge_time")`
* `Index("ix_appointments_doc_status_time", "doctor_id", "status", "appointment_time")`



* **Priority:** Before Production

---

### Security, IAM & Authorization Weaknesses

#### SEC-001: Fail-Open Vulnerability in Rate Limiter

* **Category:** Security & Brute-Force Protection
* **Severity:** HIGH
* **Location:** `backend/dependencies.py:144-162` (`RateLimiter`)
* **Evidence:** `RateLimiter` wraps Redis calls in `try...except` and catches broad exceptions with `except Exception: return`.


* **Problem:** If Redis crashes, drops connections, or runs out of memory, the rate limiter silently allows all requests to pass.


* **Impact:** An attacker who triggers high load on Redis can bypass the 10-requests-per-minute threshold on `/api/auth/login` to launch brute-force credential stuffing attacks against hospital accounts.


* **Recommendation:** Catch specific connection errors; fail-closed for authentication routes or fall back to an internal in-memory thread-safe token bucket (e.g., using `collections.deque`) if Redis is unreachable.


* **Priority:** Immediate

#### SEC-002: Header-Based Subdomain Spoofing

* **Category:** Multi-Tenancy & Isolation
* **Severity:** HIGH
* **Location:** `backend/database.py:91-105` (`extract_subdomain_slug`)
* **Evidence:** `extract_subdomain_slug` extracts `X-Hospital-Subdomain` header directly: `header_override = request.headers.get("X-Hospital-Subdomain")`. If present, it overrides the `Host` header unconditionally.


* **Problem:** External clients can set arbitrary `X-Hospital-Subdomain` headers. Unless Traefik strips or sanitizes this header from inbound external requests, an attacker can route requests to other tenant databases.


* **Impact:** Circumvents domain isolation boundaries; allows tenant context manipulation across hospital boundaries.


* **Recommendation:** In production, ignore `X-Hospital-Subdomain` unless the request originates from verified internal control-plane networks, deriving tenant context strictly from the authenticated JWT claims or the validated `Host` header.


* **Priority:** Immediate

#### SEC-003: Timing-Attack User Enumeration in Authentication

* **Category:** Security
* **Severity:** MEDIUM
* **Location:** `backend/routers/v1/auth_router.py:44-53`
* **Evidence:**
```python
user = db.query(User).filter(User.email == payload.email).first()
if not user or not pwd_context.verify(payload.password, user.hashed_password):
    ...

```


* **Problem:** If the user does not exist, `pwd_context.verify` is never executed. If the user exists, bcrypt executes with cost factor 12 (taking ~100-250ms).


* **Impact:** Measurable difference in response latency allows attackers to enumerate valid staff email addresses and doctor usernames.


* **Recommendation:** Compute a dummy bcrypt verification against a pre-hashed string when `user is None` to ensure constant-time responses.


* **Priority:** Before Production

#### SEC-004: Hardcoded Static Master Secret for Envelope Encryption

* **Category:** Cryptography & Secrets Management
* **Severity:** HIGH
* **Location:** `backend/security/envelope.py:10-18`
* **Evidence:** `_MASTER_KEY = hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest()` derives the AES-256-GCM key directly using single-iteration SHA-256 without a cryptographic salt.


* **Problem:** All tenant JWT secrets and backup encryption keys across all hospitals in the control plane depend on a static `SECRET_KEY`.


* **Impact:** Rotating `SECRET_KEY` in environment variables breaks decryption for every tenant database secret in `TenantRegistry` without a data migration script.


* **Recommendation:** Use HKDF or PBKDF2 with a dedicated salt (`settings.ENVELOPE_ENCRYPTION_SALT`) for master key derivation, and implement a key-versioning prefix (e.g., `enc:v2:`) supporting multi-key rotation.


* **Priority:** Before Production

#### SEC-005: Unbounded File Buffer Consumption in Admin Bulk Importer

* **Category:** Security & Reliability
* **Severity:** HIGH
* **Location:** `backend/routers/v1/tenant_admin_router.py:165-225`
* **Evidence:** Both `bulk_import_services` and `bulk_import_medications` read the entire file payload into memory: `await file.read()`.


* **Problem:** There is no maximum payload size enforcement before loading the uploaded buffer into RAM.


* **Impact:** Uploading an oversized CSV file (e.g., 500MB+) exhausts container memory, crashing the API process.


* **Recommendation:** Limit file size using an ASGI middleware check on `content-length` and parse the file as a streaming generator line by line.


* **Priority:** Before Production

#### SEC-006: Missing Subnet Validation on Direct MLLP Port 2575

* **Category:** Network & Infrastructure Security
* **Severity:** MEDIUM
* **Location:** `backend/services/hl7_listener.py:108-115`
* **Evidence:** `start_hl7_server` binds to `host="0.0.0.0", port=2575` without TLS or IP allowlisting.


* **Problem:** Minimal Lower Layer Protocol (MLLP) is an unencrypted, unauthenticated plain TCP framing protocol.


* **Impact:** Anyone with network access to port 2575 can inject spoofed HL7 `ORU^R01` messages to alter patient lab results.


* **Recommendation:** Enforce TLS wrapping (MLLP-over-TLS), bind exclusively to a segregated medical device VLAN, and validate client IP against `EquipmentMaster.ip_address`.


* **Priority:** Planned

---

### Clinical Safety & Biological Integrity

#### CLIN-001: Missing Compatibility Validation for Non-PRBC Blood Components

* **Category:** Clinical Safety
* **Severity:** CRITICAL
* **Location:** `backend/routers/v1/blood_pacs_router.py:76-110`
* **Evidence:** `cross_match_and_reserve_blood_bag` evaluates biological compatibility only when `component_type` is `PRBC`, `WHOLE_BLOOD`, or `FFP`.


* **Problem:** If `component_type` is `PLATELETS` or `CRYOPRECIPITATE`, the function bypasses compatibility checks and reserves the bag.


* **Impact:** Incompatible blood components can be issued and transfused, creating risk of adverse immune reactions.


* **Recommendation:** Define explicit compatibility rules for `PLATELETS` and `CRYOPRECIPITATE`, or reject cross-matching if no rule applies.


* **Priority:** Immediate

#### CLIN-002: Persistent Critical Panic Flags After LIS Specimen Rejection

* **Category:** Clinical Safety & Diagnostic Integrity
* **Severity:** HIGH
* **Location:** `backend/routers/v1/diagnostic_router.py:270-340`
* **Evidence:** If a task enters `RESULT_ENTERED` with `is_critical_panic = True`, and is subsequently updated to `SAMPLE_REJECTED` (e.g., hemolyzed sample), the code does not clear `result_value` or `is_critical_panic`.


* **Problem:** State regression does not reset previous test values.


* **Impact:** The rejected test remains in `/api/diagnostics/tasks/panic-alerts`, triggering false alerts for nursing and clinical staff.


* **Recommendation:** When updating status to `SAMPLE_REJECTED`, explicitly set `task.is_critical_panic = False` and `task.result_value = None`.


* **Priority:** Immediate

#### CLIN-003: String-Matching Fragility in Consultation Auto-Billing

* **Category:** Financial & Domain Logic
* **Severity:** MEDIUM
* **Location:** `backend/services/auto_billing.py:68-96`
* **Evidence:**
```python
desc = f"OPD CONSULTATION: {doc_profile.user.full_name} ({doc_profile.specialization})"
existing = db.execute(select(InvoiceLineItem).where(
    InvoiceLineItem.invoice_id == invoice.id,
    InvoiceLineItem.description == desc
)).scalars().first()

```


* **Problem:** Line item idempotency relies on exact string equality of doctor name and specialization.


* **Impact:** If doctor profile details change during an active consultation, the duplicate check fails, and the patient is billed twice for the consultation.


* **Recommendation:** Add a `service_type` or metadata column on `InvoiceLineItem` (e.g., `reference_entity_type="DOCTOR_CONSULTATION"`, `reference_entity_id=doctor_user_id`) to enforce idempotency independently of user-facing descriptions.


* **Priority:** Before Production

#### CLIN-004: Lack of FEFO Enforcement on Pharmacy Dispensing

* **Category:** Regulatory Compliance & Clinical Operations
* **Severity:** MEDIUM
* **Location:** `backend/routers/v1/pharmacy_router.py:220-310`
* **Evidence:** `dispense_medication` accepts an arbitrary `batch_id` from the client. It verifies that the batch is not expired, but does not check if an earlier expiring batch exists.


* **Problem:** Bypasses FEFO (First-Expiry, First-Out) inventory dispensing principles.


* **Impact:** Batches with shorter expiration dates remain on shelves and expire, resulting in drug wastage and potential dispensing errors.


* **Recommendation:** Validate that no older, unexpired batch with available stock exists for the medication, or require an explicit manager override flag to bypass FEFO.


* **Priority:** Planned

---

### Multi-Tenancy & Federation

#### FED-001: Linear Connection Fan-Out in Central MPI Discovery

* **Category:** Scalability & Reliability
* **Severity:** HIGH
* **Location:** `backend/routers/v1/federation_router.py:165-255`
* **Evidence:** In `discover_and_aggregate_patient_across_branches`, the code iterates over every pointer:
```python
for ptr in pointers:
    branch_engine = tenant_engine_pool.get_engine(tenant_reg.db_name)
    with BranchSession() as branch_db:
        ...

```


* **Problem:** Opens and queries database sessions sequentially for every hospital tenant matching the pointer list.


* **Impact:** For a patient who has visited 5 branches, the request sequentially opens 5 database connections, leading to latency spikes and connection pool pressure. If any one branch database is down or slow, the entire discovery call fails or times out.


* **Recommendation:** Query target branches in parallel using a worker pool, or isolate cross-branch lookups behind an asynchronous federation service with circuit breakers per node.


* **Priority:** Before Production

#### FED-002: Deactivated Patients Remain Discoverable in Central Index

* **Category:** Privacy & Federation
* **Severity:** MEDIUM
* **Location:** `backend/routers/v1/patient_router.py:100-112`
* **Evidence:** When `deactivate_patient` is called, it sets `patient.is_active = False` on the local database, but does not update `GlobalPatientPointer` in the Control Plane.


* **Problem:** Deactivation does not synchronize with the central discovery registry.


* **Impact:** Other hospital branches discover records of deactivated patients, leading to stale demographic and clinical pointers across the network.


* **Recommendation:** When soft-deleting or deactivating a patient locally, delete or mark the corresponding `GlobalPatientPointer` in `hms_control_plane` as inactive.


* **Priority:** Before Production

#### FED-003: Missing Isolation Check in Bulk Spatial Matrix Generator

* **Category:** Multi-Tenancy
* **Severity:** MEDIUM
* **Location:** `backend/routers/v1/tenant_admin_router.py:53-105`
* **Evidence:** `generate_hospital_spatial_matrix` resolves departments using:
```python
depts = {d.code: d for d in db.query(Department).all()}

```


It maps `w_spec.department_code` against `depts.get(...)`.


* **Problem:** If a department code does not exist in the tenant's database, it raises an HTTP 400 after earlier floor and ward records have already been flushed via `db.flush()`.


* **Impact:** Partial failure leaves orphaned floors and wards if subsequent loops fail and transaction rollbacks are not consistently handled across all paths.


* **Recommendation:** Validate all department codes in the payload upfront before opening the insert loop.


* **Priority:** Planned

---

### Frontend & UI Issues

#### UI-001: Unbounded File Payload Memory Spike in Control Plane

* **Category:** Frontend Stability & Performance
* **Severity:** MEDIUM
* **Location:** `onboarding-ui/src/views/MigrationStudio.jsx` (CSV upload flow)
* **Evidence:** `handleFileUpload` uploads raw CSV files via standard `FormData` without client-side file size caps or chunked transfer encoding.


* **Problem:** Uploading a large legacy export (e.g., 200MB+) freezes browser execution while constructing the form data and waiting for backend response.


* **Impact:** Browser tabs crash or become unresponsive during legacy hospital data migration.


* **Recommendation:** Implement client-side chunked file parsing (e.g., using `PapaParse` with worker threads) to preview column headers locally before streaming records in batches to the backend.


* **Priority:** Planned

#### UI-002: Hardcoded Fallback Master Key in LocalStorage

* **Category:** Security
* **Severity:** LOW
* **Location:** `onboarding-ui/src/App.jsx:136-140`
* **Evidence:**
```javascript
const [masterKey, setMasterKey] = useState(
  () => localStorage.getItem("avinya_master_key") || "master_vendor_setup_key_2026_secure"
);

```


* **Problem:** A default fallback secret key string is stored directly in source code and evaluated in the browser.


* **Impact:** Exposes the intended naming scheme and format of the vendor control key in static client bundles.


* **Recommendation:** Remove hardcoded defaults; require explicit entry or authentication via a login session.


* **Priority:** Optional

---

### DevOps & Infrastructure

#### OPS-001: Shell Loop Failure Mode in Backup Vault

* **Category:** Reliability & Disaster Recovery
* **Severity:** HIGH
* **Location:** `docker-compose.yml:68-83` (`backup-vault`)
* **Evidence:** The service runs an inline shell loop:
`sh -c "while true; do for f in /source/*.dump.enc; do ... cp ... sha256sum ... chmod 400 ... sleep 30; done"`.


* **Problem:** Shell scripting is unmonitored, provides no telemetry, lacks log-rotation, and can silently crash on edge cases (e.g., filenames with spaces, disk-full conditions).


* **Impact:** The system believes backups are being sealed in the immutable storage vault when the loop has crashed, leaving the platform without disaster recovery artifacts.


* **Recommendation:** Replace the shell container with an instrumented Python or Go sidecar daemon that reports backup metrics to Prometheus and emits structured logs.


* **Priority:** Before Production

#### OPS-002: Missing Head Revisions in Canary Migration Workflow

* **Category:** Database Operations
* **Severity:** HIGH
* **Location:** `backend/scripts/migrate.py:70-110`, `README.md`
* **Evidence:** The migration workflow relies on `detect_schema_diff` during startup. If a developer changes `models.py` without generating an Alembic migration script, `migrate_default_canary` prompts interactively via stdin:
`input("\n[?] Enter migration comment: ")`.


* **Problem:** In automated CI/CD runners or headless Docker Compose restarts, standard input is closed.


* **Impact:** Container boot hangs indefinitely or aborts startup with an EOFError.


* **Recommendation:** Fail immediately with exit code 1 if unversioned schema changes are detected in non-interactive production environments, requiring committed migrations before deployment.


* **Priority:** Immediate

---

### Master Remediation Checklist

```markdown
- [ ] 1. (ARCH-001) Refactor connection pooling in TenantEngineRegistry & deploy PgBouncer.
- [ ] 2. (ARCH-002) Rewrite analytics_router.py queries using SQL func.sum/func.count aggregations.
- [ ] 3. (ARCH-005) Wrap synchronous SQLAlchemy calls in hl7_listener.py inside asyncio.to_thread.
- [ ] 4. (DB-002) Add unique constraint on PaymentReceipt (invoice_id, transaction_reference).
- [ ] 5. (DB-003) Fix GlobalPatientPointer unique constraint to include source_mrn for newborn records.
- [ ] 6. (DB-004) Eliminate default '0000000000' phone number fallback to prevent MPI collisions.
- [ ] 7. (DB-005) Enforce partial unique index on invoices (encounter_id WHERE status = 'DRAFT').
- [ ] 8. (SEC-001) Configure fail-closed or in-memory fallback for authentication rate limiter.
- [ ] 9. (SEC-002) Restrict X-Hospital-Subdomain header extraction to internal networks.
- [ ] 10. (SEC-004) Add cryptographic salt and version prefix to envelope encryption key derivation.
- [ ] 11. (CLIN-001) Add compatibility rules for Platelets and Cryoprecipitate in blood cross-matching.
- [ ] 12. (CLIN-002) Reset critical panic flags upon diagnostic sample rejection.
- [ ] 13. (CLIN-003) Decouple doctor fee auto-billing idempotency checks from text descriptions.
- [ ] 14. (FED-001) Parallelize cross-tenant MPI discovery queries and add error handling.
- [ ] 15. (FED-002) Propagate patient deactivation to GlobalPatientPointer in control plane.
- [ ] 16. (OPS-001) Replace backup vault shell loop with an instrumented backup daemon.
- [ ] 17. (OPS-002) Prevent interactive stdin prompts during non-interactive container migrations.

```