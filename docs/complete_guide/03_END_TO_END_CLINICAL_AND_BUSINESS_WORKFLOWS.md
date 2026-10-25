# Chapter 3: End-to-End Clinical & Business Workflows
## A Comprehensive Operational Guide to All 26 Hospital Domains & Patient Safety Gates

---

## 1. The Patient Lifecycle: Outpatient (OPD) Consultation Journey

The Outpatient Department (OPD) manages the arrival, triage, consultation, and treatment of non-admitted ambulatory patients.

```mermaid
sequenceDiagram
    autonumber
    actor Patient as Patient
    actor Reception as Receptionist / Clerk
    actor Nurse as Triage Nurse
    actor Doctor as Attending Physician
    participant HMS as Avinya Hospital Engine

    Patient->>Reception: Arrives at Hospital Front Desk
    Reception->>HMS: Register / Lookup Patient (MPI Demographics)
    Note over HMS: Encrypts Aadhaar/Phone with AES-256-GCM Envelope Key
    HMS-->>Reception: Return Master Patient Record (MRN)
    
    Reception->>HMS: Book Appointment / Create OPD Queue Token
    HMS-->>Reception: Issue OPD Token (e.g., TOKEN-CARDIO-14)
    
    Patient->>Nurse: Enters Triage Room
    Nurse->>HMS: Record Vital Signs (BP, Pulse, Temp, SpO2)
    Note over HMS: Automatically calculates baseline NEWS2 score
    
    Patient->>Doctor: Enters Consultation Chamber
    Doctor->>HMS: Fetch Chart (Allergies, Past Encounters, Active Meds)
    Doctor->>HMS: Document SOAP Clinical Note & ICD-10 Diagnoses
    Doctor->>HMS: Prescribe Medications or CPOE Order Set
    Note over HMS: Synchronously runs DDI, Drug-Disease & Allergy Checks
    Doctor->>HMS: Finalize Encounter & Issue Digital Prescription
```

### Detailed Workflow Stages:
1. **Patient Registration & MPI Lookup:**
   - If a patient is new, the clerk enters their demographic data. The system generates an immutable Medical Record Number (`MRN-YYYYMM-XXXXXX`).
   - Sensitive identifiers (Aadhaar number, mobile number, postal address) are encrypted at the column level using **AES-256-GCM**.
   - If the patient has visited other hospitals in a corporate group, an audited cross-tenant MPI lookup (`/mpi/lookup`) can resolve the pointer record with mandatory justification.
2. **Triage & Vital Signs:**
   - The triage nurse records blood pressure, pulse rate, respiratory rate, and oxygen saturation.
   - The system immediately evaluates these vitals against **NEWS2 early warning thresholds**. If the vitals indicate high clinical risk, an immediate alert is displayed.
3. **Doctor Consultation & SOAP Documentation:**
   - The physician documents findings in structured **SOAP notes**:
     - *Subjective:* Chief complaints, patient history of present illness.
     - *Objective:* Physical examination findings, current vital signs.
     - *Assessment:* ICD-10 coded clinical diagnoses (e.g., `I10` Essential Hypertension, `E11` Type 2 Diabetes).
     - *Plan:* Prescriptions, diagnostic orders, and follow-up advice.
4. **Prescription Safety Checks:**
   - The moment the doctor adds a medication, the **Drug-Drug Interaction (DDI)** and **Drug-Disease Contraindication** engines execute synchronously before the order can be signed.

---

## 2. Inpatient (IPD / ADT) Admission Lifecycle

Inpatient care governs the admission, accommodation, nursing, and discharge of hospitalized patients.

```mermaid
flowchart TD
    subgraph ADMISSION["1. Inpatient Admission"]
        DECISION["Consultant Orders Admission"]
        BED_SELECT["Ward & Bed Selection"]
        ATOMIC_LOCK["Pessimistic Row Lock (FOR UPDATE NOWAIT)"]
        ADM_RECORD["Create InpatientAdmission (ACTIVE)"]
    end

    subgraph WARD_CARE["2. Inpatient Clinical Care"]
        BEDSIDE_BCMA["Bedside BCMA Medication Administration"]
        MIDNIGHT_CENSUS["Automated Midnight Census Charge Run (00:00 Daily)"]
        LAB_TESTS["Diagnostic Lab Results & Delta Checks"]
        NEWS2_MONITOR["Continuous Vital Signs & NEWS2 Tracking"]
    end

    subgraph DISCHARGE_GATE["3. Three-Tier Discharge Clearance Gate"]
        GATE1{"1. Clinical Clearance?<br/>(Consultant Sign-Off)"}
        GATE2{"2. Pharmacy Clearance?<br/>(Unconsumed Drugs Returned)"}
        GATE3{"3. Billing Clearance?<br/>(Zero Balance / TPA Pre-Auth)"}
        FINAL_DISCHARGE["Patient Discharged & Bed Set to VACANT"]
    end

    DECISION --> BED_SELECT --> ATOMIC_LOCK --> ADM_RECORD
    ADM_RECORD --> WARD_CARE
    WARD_CARE --> GATE1
    GATE1 -->|Approved| GATE2
    GATE2 -->|Approved| GATE3
    GATE3 -->|Approved| FINAL_DISCHARGE
```

### High-Concurrency Bed Reservation:
When an admission clerk selects a bed, the database executes:
```sql
SELECT * FROM beds WHERE id = :bed_id FOR UPDATE NOWAIT;
```
If two clerks attempt to reserve the same bed at the exact same instant, the second request immediately fails with an operational conflict rather than waiting or allowing a double-booking. The bed transitions from `VACANT` to `OCCUPIED`.

### The Three-Tier Discharge Clearance Gate:
To eliminate revenue leakage and medical negligence during discharge, Avinya HMS enforces a strict **three-key cryptographic gate**:
1. **Key 1: Clinical Sign-Off:** The attending consultant must author a finalized discharge summary, post-discharge instructions, and follow-up medication prescriptions.
2. **Key 2: Pharmacy Clearance:** The ward pharmacy must inspect the patient's bedside drug stock. All unconsumed medication ampoules or tablets must be physically returned and credited to the invoice.
3. **Key 3: Billing Clearance:** The cashier reconciles all midnight census charges, lab fees, and surgical consumables against insurance pre-authorization limits. The patient must clear any remaining copay.
*Until all three clearance flags are set to `TRUE`, the API strictly refuses to discharge the patient or release the bed.*

---

## 3. Clinical Decision Support (CDS) & Patient Safety Suite

Avinya HMS features an integrated suite of clinical safety systems designed to prevent medical errors before they reach the patient:

```mermaid
flowchart LR
    subgraph CPOE_CDS["Clinical Decision Support Suite"]
        ORDER_SETS["1. CPOE Order Sets<br/>(Evidence-based bundle protocols)"]
        NEWS2["2. NEWS2 Scoring<br/>(Physiological deterioration alert)"]
        DDI["3. Cross-Encounter DDI<br/>(Drug-drug interactions)"]
        CONTRA["4. Drug-Disease Engine<br/>(Active ICD-10 contraindications)"]
        DELTA["5. LIS Delta Checking<br/>(Acute specimen shift detection)"]
    end

    ORDER_SETS --> DDI
    ORDER_SETS --> CONTRA
    NEWS2 -->|Emergency Escalation| RRT["Rapid Response Team"]
    DELTA -->|Hemolyzed Flag| PATH["Pathologist Review"]
```

### 3.1 CPOE Standardized Protocol Order Sets (`OrderSet`)
In tertiary hospitals, complex acute conditions (e.g., Sepsis, Acute Myocardial Infarction, Stroke) require rapid, synchronized interventions. Avinya HMS supports **Computerized Physician Order Entry (CPOE) Order Sets**:
- Rather than typing twelve individual orders, the doctor selects an approved protocol (e.g., `"SEPSIS_BUNDLE"`).
- The order set atomically generates:
  - *Diagnostic Orders:* Blood Cultures x2, Serum Lactate stat, Complete Blood Count, Arterial Blood Gas.
  - *Medications:* IV Broad-Spectrum Antibiotics stat, Normal Saline 30ml/kg bolus.
  - *Nursing Directives:* Insert Foley Catheter, hourly urine output monitoring, continuous pulse oximetry.
- All items execute in a single atomic database transaction.

### 3.2 NEWS2 Physiological Deterioration Early Warning Scoring
The platform evaluates vital signs using the **National Early Warning Score 2 (NEWS2)** protocol:
- **Parameters Scored (0 to 3 each):** Respiration rate, Oxygen saturation (SpO2), Supplemental oxygen dependency, Systolic blood pressure, Heart rate, Consciousness level (AVPU scale), and Temperature.
- **Automated Escalation Triggers:**
  - *Total Score 0–4 (Low Risk):* Routine ward observations every 4 to 6 hours.
  - *Total Score 5–6 or individual parameter score of 3 (Medium Risk):* Urgent review by ward nurse and resident medical officer within 30 minutes; increase vital monitoring frequency to every hour.
  - *Total Score >= 7 (High Clinical Risk):* **Emergency Alarm.** Automatically alerts the hospital Rapid Response Team (RRT) or Intensive Care Consultant for immediate bedside evaluation and potential ICU transfer.

### 3.3 Drug-Disease Contraindication Engine
Avinya HMS cross-references prescribed active generic molecules against the patient's active ICD-10 diagnoses:
- **Beta-blockers (e.g., Propranolol, Atenolol):** Hard stop in patients with active Bronchial Asthma (`J45`) due to bronchospasm risk.
- **NSAIDs (e.g., Ibuprofen, Diclofenac):** Major warning/hard stop in patients with active Peptic Ulcer Disease (`K27`) or Severe Renal Failure (`N18.5`).
- **Metformin:** Hard stop in patients with acute lactic acidosis or advanced chronic kidney disease.

### 3.4 Cross-Encounter Drug-Drug Interaction (DDI) Engine
The DDI engine checks for potential interactions not just within the current prescription, but across **all active medications from all recent hospital visits and inpatient stays**.
- **Severity Levels:**
  - `CRITICAL_HARD_STOP`: Absolute contraindication (e.g., Sildenafil + Nitrates). The system physically refuses to sign the prescription.
  - `MAJOR_WARNING`: Serious potential toxicity. Requires explicit physician clinical override with a mandatory documented justification string (minimum 15 characters).
  - `MODERATE_INFO`: Cautionary advice displayed for clinical awareness.

---

## 4. Bedside Medication Administration (BCMA) & Controlled Substances Vault

```mermaid
sequenceDiagram
    autonumber
    actor Nurse as Ward Registered Nurse
    participant Cart as Bedside Mobile Workstation
    participant Scanner as 2D Barcode Optical Scanner
    participant BCMA as BCMA Safety Gate
    participant Vault as Controlled Substances Vault

    Nurse->>Cart: Select Patient & Scheduled Medication Task
    Nurse->>Scanner: Scan Patient 2D Wristband Barcode
    Scanner-->>Cart: Transmit MRN (e.g., MRN-202610-004218)
    Nurse->>Scanner: Scan Medication Vial Unit-Dose Barcode
    Scanner-->>Cart: Transmit Drug NDC + Batch Code
    
    Cart->>BCMA: POST /v1/api/pharmacy/bcma/verify
    BCMA-->>BCMA: Verify 5-Rights (Patient, Drug, Dose, Route, Time)
    
    alt WRONG PATIENT OR WRONG DRUG
        BCMA-->>Cart: 422 Hard Safety Stop (ADMINISTRATION BLOCKED)
        Note over Cart: Screen turns RED. Audio alert sounds. Action logged.
    else 5-RIGHTS VERIFIED
        BCMA-->>Cart: 200 OK (Verified for Administration)
        Nurse->>Cart: Administer Medication & Confirm Dose
        
        opt IF SCHEDULE H/X NARCOTIC (e.g., Fentanyl / Morphine)
            Cart->>Vault: Initiate Controlled Substance Movement
            actor Witness as Second Registered Nurse
            Witness->>Cart: Authenticate Dual-Witness (Credentials + TOTP)
            Vault-->>Vault: Deduct physical stock & log residual wastage (ml)
            Vault-->>Cart: Movement Authenticated & Logged
        end
    end
```

### 4.1 BCMA 5-Rights Bedside Verification
To prevent medication administration errors, Avinya HMS implements optical **Barcode Medication Administration (BCMA)**:
1. **Right Patient:** Verifies scanned wristband barcode matches the inpatient encounter.
2. **Right Drug:** Verifies scanned medication barcode resolves to the physician's prescribed order.
3. **Right Dose:** Verifies scanned unit strength matches the prescribed dosage.
4. **Right Route:** Verifies formulation (e.g., IV injection vs oral tablet) matches prescribed route.
5. **Right Time:** Verifies administration occurs within the authorized clinical window (e.g., scheduled time ±60 minutes).
- **Strict Hard Stops:** A mismatch in Patient or Drug triggers an immediate, un-bypassable hard stop (`422 Unprocessable Entity`), locking the screen and alerting the ward supervisor.

### 4.2 Schedule H/X Controlled Substances Perpetual Vault
In strict compliance with statutory narcotics regulations (NDPS Act), high-risk controlled substances (Morphine, Fentanyl, Pethidine, Ketamine, Midazolam) are tracked in an immutable digital perpetual vault:
- **Perpetual Balance Tracking:** Every ampoule received or dispensed updates the physical balance in real-time.
- **Dual-Witness Digital Co-Signature:** Administering a narcotic requires two independent licensed nurses to authenticate at the workstation with credentials and TOTP.
- **Wastage Forensics:** When a partial dose is administered (e.g., 2ml given from a 5ml ampoule), the remaining 3ml must be physically discarded and witnessed. The system records the administered volume, wasted volume, administering nurse ID, and witness nurse ID into an unalterable audit log.

---

## 5. Diagnostic Laboratory (LIS), Native HL7 v2 MLLP & Delta Checking

```mermaid
flowchart LR
    subgraph CLINICAL_ORDER["1. Diagnostic Ordering"]
        DOCTOR["Physician Orders Lab Panel"]
        LIS_ORDER["DiagnosticOrder Created (LOINC/CPT)"]
        BARCODE["Specimen Tube Barcode Printed"]
    end

    subgraph AUTOMATED_ANALYZER["2. Analyzer Execution (:2575)"]
        ROCHE["Roche Cobas Analyzer"]
        MLLP_SOCK["TCP/MLLP Socket Listener"]
        ORU_PARSE["Unsolicited ORU^R01 Message Parser"]
    end

    subgraph SAFETY_AND_FILING["3. Safety Verification & Filing"]
        TEST_MAP["Equipment Test Mapping Engine"]
        DELTA_EVAL["Automated Numeric Delta Check"]
        PANIC_ALERT["Critical Panic Value Alert (LL/HH)"]
        PATH_SIGN["Senior Pathologist Digital Sign-Off"]
    end

    DOCTOR --> LIS_ORDER --> BARCODE
    BARCODE --> ROCHE
    ROCHE -->|TCP MLLP \x0b...\x1c\x0d| MLLP_SOCK --> ORU_PARSE
    ORU_PARSE --> TEST_MAP --> DELTA_EVAL
    DELTA_EVAL -->|Acute Delta Shift| PANIC_ALERT
    DELTA_EVAL --> PATH_SIGN
```

### 5.1 Native HL7 v2 MLLP Analyzer Interface
Avinya HMS features a built-in TCP socket listener running on **port 2575**. Automated clinical chemistry and hematology analyzers connect directly to this port:
- Analyzers transmit unsolicited `ORU^R01` observation messages formatted with standard MLLP framing (`\x0b` start block, `\x1c\x0d` end block).
- The system parses the message in memory, extracts the analyzer mnemonic (e.g., `K+` for Potassium, `GLUC` for Blood Glucose), maps it via `EquipmentTestMapping` to internal catalog codes, and returns an immediate HL7 `MSA` ACK handshake.

### 5.2 Historic Laboratory Delta Checking
Before any numeric lab result is presented to clinicians, the **LIS Delta Checking Engine** executes an automatic retrospective check:
- It fetches the patient's immediate previous valid result for that same analyte within a configurable window (e.g., 48 hours).
- **The Evaluation:** If a patient's Potassium jumps from 3.8 mEq/L to 6.8 mEq/L in 4 hours, this represents an acute, physiologically improbable delta.
- **Clinical Significance:** An acute shift often indicates a pre-analytical error—such as sample hemolyzation or blood drawn upstream of an active IV infusion. The system automatically flags the result with `DELTA_CHECK_VIOLATION` and holds it for pathologist verification, preventing false clinical interventions.

### 5.3 Critical Panic Value Alerting
Results breaching life-threatening thresholds (`LL`, `HH`, `PANIC`) trigger immediate automated WebSocket alert broadcasts to ward nursing stations and attending physicians.

---

## 6. Revenue Cycle Management (RCM), CDM & Billing Automation

```mermaid
flowchart TD
    subgraph CHARGE_EVENTS["Charge Capture Events"]
        CENSUS["1. Automated Midnight Inpatient Census (00:00 Daily)"]
        OT_SURGERY["2. Operation Theatre Surgical Procedure Sign-Off"]
        LAB_BILL["3. Diagnostic Lab / Radiology Execution"]
        PHARM_BILL["4. Pharmacy Bedside Medication Dispensation"]
    end

    subgraph RCM_CORE["Revenue Cycle & Tariff Resolution"]
        LEDGER["ChargeCaptureEvent Ledger<br/>(Idempotent source_reference deduplication)"]
        TARIFF["Multi-Tariff MoU Rate Card Resolver"]
        ROOM_CAP["Statutory Insurance Room Rent Daily Cap"]
    end

    subgraph INVOICING["Statutory Invoicing & Claims"]
        INVOICE["Patient Invoice Draft"]
        GST["Line-Item GST Engine (CGST, SGST, IGST)"]
        NHCX_CLAIM["NHCX Insurance Claim Bundle"]
    end

    CENSUS --> LEDGER
    OT_SURGERY --> LEDGER
    LAB_BILL --> LEDGER
    PHARM_BILL --> LEDGER

    LEDGER --> TARIFF --> ROOM_CAP --> INVOICE
    INVOICE --> GST
    INVOICE --> NHCX_CLAIM
```

### 6.1 Multi-Tariff MoU Rate Card Resolution Engine
Hospitals maintain multiple pricing schedules:
- **Cash / Self-Pay:** Standard hospital base Chargemaster (CDM) rates.
- **Corporate MoUs:** Discounted corporate partner rates.
- **TPA / Health Insurance:** Contractually negotiated tariff rate cards (`InsurancePlanTariff`).
The `resolve_service_tariff` engine evaluates the patient's active policy, applies negotiated service rates, and enforces **contractual daily room rent caps** (e.g., insurance covers max ₹5,000/day for private rooms; any excess room charge is automatically apportioned to the patient's copay ledger).

### 6.2 Automated Midnight Inpatient Census Run
At 00:00 every night, an automated background runner queries all active `InpatientAdmission` records and captures:
- Inpatient Room and Bed Occupancy Fees
- Daily Resident Medical Officer (RMO) Rounds
- Routine Ward Nursing Care Charges
Every charge event is tagged with an idempotent `source_reference` (e.g., `CENSUS-ADM412-BED10-20261026`), guaranteeing that if the job retries, duplicate charges are physically impossible.

### 6.3 Statutory GST Invoicing & AR Aging
- **Statutory GST Breakdown:** Compliant with Indian taxation laws, computing Central GST (CGST) and State GST (SGST) for intra-state care, or Integrated GST (IGST) for inter-state patients.
- **Accounts Receivable (AR) Aging:** Automatically aggregates outstanding patient balances into aging buckets: `0-30 Days`, `31-60 Days`, `61-90 Days`, and `90+ Days` (delinquent), incorporating tax liabilities into revenue forecasting.

---

## 7. Regulatory Gateways: ABDM (M1-M3) & NHCX

Avinya HMS connects natively to India's **Ayushman Bharat Digital Mission (ABDM)** and **National Health Claims Exchange (NHCX)**:

```mermaid
flowchart LR
    subgraph ABDM_STACK["ABDM & NHCX National Healthcare Stack"]
        M1["ABDM M1: ABHA Registration & OTP Profile Linking"]
        M2["ABDM M2: Consent Management (HIP/HIU Artifacts)"]
        M3["ABDM M3: Health Information Exchange (FHIR R4)"]
        NHCX["NHCX: Electronic Health Claims Exchange"]
    end

    subgraph AVINYA_ENGINE["Avinya HMS Native Connectors"]
        ABHA_LINK["backend/domains/abdm/router.py"]
        CONSENT_MGR["ABDMConsentArtefact Lifecycle"]
        FHIR_BUILDER["FHIR R4 DiagnosticReport & Encounter Bundles"]
        CLAIM_DISPATCH["NHCXClaimExchangeLog Adjudication Ledger"]
    end

    M1 <--> ABHA_LINK
    M2 <--> CONSENT_MGR
    M3 <--> FHIR_BUILDER
    NHCX <--> CLAIM_DISPATCH
```

1. **ABDM M1 (ABHA Profile):** Generates and verifies 14-digit Ayushman Bharat Health Accounts (ABHA) and `@abdm` addresses via mobile demographic OTP.
2. **ABDM M2 (Consent Management):** Receives and processes digital consent requests from other hospitals (HIU) or patients, validating cryptographic signatures before authorizing record release.
3. **ABDM M3 (FHIR R4 Bundles):** Packages clinical encounters, discharge summaries, laboratory reports, and diagnostic observations into internationally compliant **FHIR R4 JSON Bundles** for seamless inter-hospital data exchange.
4. **NHCX Gateway:** Dispatches standardized electronic claim bundles to insurers and logs real-time adjudication callbacks directly into hospital financial ledgers.

---

## 8. Hospital Quality Accreditations (NABH / JCI) & Adverse Incident CAPA

Accreditation bodies (NABH 5th Edition and JCI 7th Edition) require hospitals to measure and improve clinical outcomes continuously. Avinya HMS calculates these indicators dynamically:

### Key Hospital Quality Benchmarks Computed:
- **CAUTI Rate:** Catheter-Associated Urinary Tract Infections per 1,000 urinary catheter device days.
- **CLABSI Rate:** Central Line-Associated Bloodstream Infections per 1,000 central venous catheter days.
- **VAP Rate:** Ventilator-Associated Pneumonia cases per 1,000 mechanical ventilator days.
- **Surgical Site Infection (SSI) Rate:** Post-operative surgical site infections per 100 major surgical procedures.
- **Inpatient Fall Rate:** Patient fall incidents per 1,000 occupied bed days.
- **Medication Error Rate:** Bedside BCMA errors and adverse drug reactions per 1,000 administered doses.

### Adverse Incident Register & CAPA Workflow:
When an adverse event occurs (`NEAR_MISS`, `MINOR`, `MODERATE`, `MAJOR`, `SENTINEL`), quality officers follow a structured lifecycle:
`REPORTED` ➔ `UNDER_REVIEW` ➔ `CAPA_ASSIGNED` ➔ `RESOLVED` ➔ `CLOSED`
Major and sentinel events mandate documented **Root Cause Analysis (RCA)** (e.g., 5-Whys methodology) and a binding **Corrective and Preventive Action (CAPA)** plan with quality committee sign-off.

---

## 9. Operational Support Services: BMW, Blood Bank, Surgery, Workforce & AP

- **Biomedical Waste (BMW) Tracking:** Manages hazardous biomedical waste using statutory color-coded categorization (Yellow, Red, White, Blue). Every waste bag is barcoded, weighed at ward collection, and reconciled against authorized Common Bio-medical Waste Treatment Facility (CBWTF) pickup manifests.
- **Blood Bank & Transfusion Safety:** Tracks voluntary blood donors, mandatory viral serology screening (HIV, Hepatitis B, Hepatitis C, Syphilis, Malaria), blood component separation (PRBC, Platelets, FFP), and recipient cross-matching compatibility testing.
- **Operation Theatre (OT) & WHO Checklists:** OT scheduling, surgical team rostering, Pre-Anesthetic Checkup (PAC) clearance gates, and digital enforcement of the **World Health Organization (WHO) Surgical Safety Checklist** (Sign-In before induction, Time-Out before skin incision, Sign-Out before wound closure).
- **Workforce & Statutory Payroll:** Staff biometric attendance tracking, leave balances, and statutory deductions (Employee Provident Fund, Employee State Insurance, Professional Tax, TDS) with locked pay periods preventing retroactive tampering.
- **Accounts Payable & 3-Way Matching:** Purchase Orders (PO), Goods Receipt Notes (GRN), and Vendor Invoices reconciled via automated 3-way matching before payment voucher release.

---

*Proceed to [Chapter 4: Operator & Administrator Manual](04_OPERATOR_AND_ADMINISTRATOR_MANUAL.md) to learn how to administer the multi-tenant control plane, provision tenants, and manage backups.*
