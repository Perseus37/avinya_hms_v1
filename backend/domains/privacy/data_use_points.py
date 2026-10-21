"""Data-use points that may consult a hospital's consent policy."""

from dataclasses import dataclass


@dataclass(frozen=True)
class DataUsePoint:
    code: str
    description: str
    route: str
    action: str = "disclose"


DATA_USE_POINTS = {
    point.code: point
    for point in (
        DataUsePoint(
            "fhir.patient.read",
            "Read one patient through FHIR R4",
            "GET /api/fhir/R4/Patient/{patient_id}",
        ),
        DataUsePoint(
            "fhir.patient.search",
            "Search patients through FHIR R4",
            "GET /api/fhir/R4/Patient",
        ),
        DataUsePoint(
            "fhir.encounter.read",
            "Read one encounter through FHIR R4",
            "GET /api/fhir/R4/Encounter/{encounter_id}",
        ),
        DataUsePoint(
            "fhir.encounter.search",
            "Search encounters through FHIR R4",
            "GET /api/fhir/R4/Encounter",
        ),
        DataUsePoint(
            "fhir.observation.read",
            "Read one observation through FHIR R4",
            "GET /api/fhir/R4/Observation/{observation_id}",
        ),
        DataUsePoint(
            "fhir.observation.search",
            "Search observations through FHIR R4",
            "GET /api/fhir/R4/Observation",
        ),
        DataUsePoint(
            "fhir.condition.read",
            "Read one condition through FHIR R4",
            "GET /api/fhir/R4/Condition/{condition_id}",
        ),
        DataUsePoint(
            "fhir.condition.search",
            "Search conditions through FHIR R4",
            "GET /api/fhir/R4/Condition",
        ),
        DataUsePoint(
            "patients.mpi.search",
            "Search or list records in the tenant patient index",
            "GET /api/patients",
            action="use",
        ),
        DataUsePoint(
            "patients.mpi.read",
            "Read one patient record from the tenant patient index",
            "GET /api/patients/{patient_id}",
            action="use",
        ),
        DataUsePoint(
            "patients.allergies.read",
            "Read active allergy records for a patient",
            "GET /api/patients/{patient_id}/allergies",
            action="use",
        ),
        DataUsePoint(
            "clinical.encounter.vitals.read",
            "Read vital observations for a patient encounter",
            "GET /api/clinical/encounters/{encounter_id}/vitals",
            action="use",
        ),
        DataUsePoint(
            "clinical.encounter.notes.read",
            "Read clinical notes for a patient encounter",
            "GET /api/clinical/encounters/{encounter_id}/notes",
            action="use",
        ),
        DataUsePoint(
            "clinical.encounter.diagnoses.read",
            "Read diagnoses for a patient encounter",
            "GET /api/clinical/encounters/{encounter_id}/diagnoses",
            action="use",
        ),
        DataUsePoint(
            "clinical.patient.comorbidities.read",
            "Read a patient's active comorbidities",
            "GET /api/clinical/patients/{patient_id}/comorbidities",
            action="use",
        ),
        DataUsePoint(
            "clinical.encounter.physical_exams.read",
            "Read physical examination findings for a patient encounter",
            "GET /api/clinical/encounters/{encounter_id}/physical-exams",
            action="use",
        ),
        DataUsePoint(
            "adt.encounters.active.read",
            "Read the active patient encounter list",
            "GET /api/adt/encounters/active",
            action="use",
        ),
        DataUsePoint(
            "adt.encounters.read",
            "Read one patient encounter",
            "GET /api/adt/encounters/{encounter_id}",
            action="use",
        ),
        DataUsePoint(
            "adt.admissions.search",
            "Search inpatient admissions",
            "GET /api/adt/admissions",
            action="use",
        ),
        DataUsePoint(
            "adt.admissions.read",
            "Read one inpatient admission",
            "GET /api/adt/admissions/{admission_id}",
            action="use",
        ),
        DataUsePoint(
            "adt.admission.transfers.read",
            "Read transfer history for an inpatient admission",
            "GET /api/adt/admissions/{admission_id}/transfers",
            action="use",
        ),
        DataUsePoint(
            "adt.discharge_summary.print",
            "Render a patient's inpatient discharge summary",
            "GET /api/adt/admissions/{admission_id}/discharge-summary/print",
            action="use",
        ),
        DataUsePoint(
            "nursing.emar.read",
            "Read medication administration records for an encounter",
            "GET /api/nursing/encounters/{encounter_id}/emar",
            action="use",
        ),
        DataUsePoint(
            "nursing.fluid_balance.read",
            "Read fluid balance records for an encounter",
            "GET /api/nursing/encounters/{encounter_id}/fluid-balance",
            action="use",
        ),
        DataUsePoint(
            "er.triage_queue.read",
            "Read active emergency triage records",
            "GET /api/er/triage-queue",
            action="use",
        ),
        DataUsePoint(
            "registry.births.read",
            "Read statutory birth records",
            "GET /api/registries/births",
            action="use",
        ),
        DataUsePoint(
            "registry.births.print",
            "Render a statutory birth certificate",
            "GET /api/registries/births/{birth_id}/print",
            action="use",
        ),
        DataUsePoint(
            "registry.deaths.print",
            "Render a statutory death certificate",
            "GET /api/registries/deaths/{death_id}/print",
            action="use",
        ),
        DataUsePoint(
            "registry.mlc.print",
            "Render a medico-legal case report",
            "GET /api/er/mlc/{mlc_id}/print",
            action="use",
        ),
        DataUsePoint(
            "opd.appointments.search",
            "Search outpatient appointments",
            "GET /api/opd/appointments",
            action="use",
        ),
        DataUsePoint(
            "opd.appointments.read",
            "Read one outpatient appointment",
            "GET /api/opd/appointments/{appointment_id}",
            action="use",
        ),
        DataUsePoint(
            "opd.appointment.print",
            "Render an outpatient appointment case sheet",
            "GET /api/opd/appointments/{appointment_id}/print",
            action="use",
        ),
        DataUsePoint(
            "opd.appointment.history.read",
            "Read status history for a patient appointment",
            "GET /api/opd/appointments/{appointment_id}/status-history",
            action="use",
        ),
        DataUsePoint(
            "opd.waitlist.search",
            "Search patient appointment waitlist entries",
            "GET /api/opd/appointment-waitlist",
            action="use",
        ),
        DataUsePoint(
            "opd.waitlist.history.read",
            "Read status history for a patient waitlist entry",
            "GET /api/opd/appointment-waitlist/{entry_id}/history",
            action="use",
        ),
        DataUsePoint(
            "opd.service_demand.search",
            "Search patient-linked service demand requests",
            "GET /api/opd/service-demand",
            action="use",
        ),
        DataUsePoint(
            "opd.service_demand.history.read",
            "Read history for a patient-linked service demand request",
            "GET /api/opd/service-demand/{demand_id}/history",
            action="use",
        ),
        DataUsePoint(
            "billing.invoices.search",
            "Search patient invoices and ledger details",
            "GET /api/billing/invoices",
            action="use",
        ),
        DataUsePoint(
            "billing.invoices.read",
            "Read a patient invoice and line items",
            "GET /api/billing/invoices/{invoice_id}",
            action="use",
        ),
        DataUsePoint(
            "billing.payments.read",
            "Read payment records for a patient invoice",
            "GET /api/billing/invoices/{invoice_id}/payments",
            action="use",
        ),
        DataUsePoint(
            "billing.deposits.read",
            "Read a patient's advance deposits",
            "GET /api/billing/patients/{patient_id}/deposits",
            action="use",
        ),
        DataUsePoint(
            "billing.refunds.read",
            "Read a patient's refund vouchers",
            "GET /api/billing/patients/{patient_id}/refunds",
            action="use",
        ),
        DataUsePoint(
            "billing.invoices.print",
            "Render a patient invoice and receipt",
            "GET /api/billing/invoices/{invoice_id}/print",
            action="use",
        ),
        DataUsePoint(
            "billing.invoices.pdf",
            "Export a patient invoice as PDF",
            "GET /api/billing/invoices/{invoice_id}/pdf",
            action="use",
        ),
        DataUsePoint(
            "pharmacy.prescription.print",
            "Render a patient's prescription",
            "GET /api/pharmacy/prescriptions/{prescription_id}/print",
            action="use",
        ),
        DataUsePoint(
            "diagnostics.orders.print",
            "Render a verified patient diagnostic report",
            "GET /api/diagnostics/orders/{order_id}/print",
            action="use",
        ),
        DataUsePoint(
            "insurance.patient.policies.read",
            "Read insurance policies associated with a patient",
            "GET /api/insurance/patients/{patient_id}/policies",
            action="use",
        ),
        DataUsePoint(
            "insurance.preauth.pdf",
            "Export a patient's insurance pre-authorization document",
            "GET /api/insurance/preauths/{preauth_id}/documents.pdf",
            action="use",
        ),
        DataUsePoint(
            "insurance.claim.pdf",
            "Export a patient's insurance claim document",
            "GET /api/insurance/claims/{claim_id}/documents.pdf",
            action="use",
        ),
        DataUsePoint(
            "ambulance.dispatches.read",
            "Read ambulance dispatches linked to patients",
            "GET /api/ambulance/dispatches",
            action="use",
        ),
        DataUsePoint(
            "ambulance.dispatch.history.read",
            "Read history for a patient ambulance dispatch",
            "GET /api/ambulance/dispatches/{dispatch_id}/history",
            action="use",
        ),
        DataUsePoint(
            "dietary.orders.read",
            "Read patient dietary orders",
            "GET /api/dietary/orders",
            action="use",
        ),
        DataUsePoint(
            "dietary.order.history.read",
            "Read history for a patient dietary order",
            "GET /api/dietary/orders/{order_id}/history",
            action="use",
        ),
        DataUsePoint(
            "mortuary.cases.read",
            "Read mortuary cases linked to patients",
            "GET /api/mortuary/cases",
            action="use",
        ),
        DataUsePoint(
            "mortuary.case.history.read",
            "Read history for a patient mortuary case",
            "GET /api/mortuary/cases/{case_id}/history",
            action="use",
        ),
        DataUsePoint(
            "break_glass.patient.read",
            "Read a patient record under an active emergency-access grant",
            "GET /api/break-glass/grants/{grant_id}/patient",
            action="use",
        ),
        DataUsePoint(
            "break_glass.reviews.read",
            "Read patient identifiers and reasons in the emergency-access review queue",
            "GET /api/break-glass/reviews/pending",
            action="use",
        ),
        DataUsePoint(
            "ot.surgery.print",
            "Render a completed patient's operative note",
            "GET /api/ot/surgeries/{surgery_id}/print",
            action="use",
        ),
        DataUsePoint(
            "pharmacy.prescriptions.search",
            "Search patient prescriptions",
            "GET /api/pharmacy/prescriptions",
            action="use",
        ),
        DataUsePoint(
            "pharmacy.prescriptions.read",
            "Read one patient prescription",
            "GET /api/pharmacy/prescriptions/{prescription_id}",
            action="use",
        ),
        DataUsePoint(
            "diagnostics.orders.search",
            "Search patient diagnostic orders",
            "GET /api/diagnostics/orders",
            action="use",
        ),
        DataUsePoint(
            "diagnostics.orders.read",
            "Read a patient diagnostic order",
            "GET /api/diagnostics/orders/{order_id}",
            action="use",
        ),
        DataUsePoint(
            "diagnostics.order.tasks.read",
            "Read diagnostic tasks for a patient order",
            "GET /api/diagnostics/orders/{order_id}/tasks",
            action="use",
        ),
        DataUsePoint(
            "diagnostics.tasks.pending.read",
            "Read pending diagnostic tasks and patient information",
            "GET /api/diagnostics/tasks/pending",
            action="use",
        ),
        DataUsePoint(
            "diagnostics.tasks.panic.read",
            "Read critical diagnostic alerts and patient information",
            "GET /api/diagnostics/tasks/panic-alerts",
            action="use",
        ),
        DataUsePoint(
            "diagnostics.study.read",
            "Read radiology study metadata for a patient diagnostic task",
            "GET /api/diagnostics/tasks/{task_id}/dicom-study",
            action="use",
        ),
        DataUsePoint(
            "federation.patient.discover",
            "Disclose matching patient data to a federated caller",
            "GET /api/federation/patients/discover",
        ),
        DataUsePoint(
            "federation.patient.publish_pointer",
            "Publish a patient pointer to the discovery index",
            "POST /api/federation/patients/{patient_id}/publish-pointer",
        ),
    )
}


def validate_data_use_point_registry(policy_data_use_points: dict[str, str]) -> list[str]:
    """Return configured policy points that have no implementation registry entry."""
    return sorted(set(policy_data_use_points) - set(DATA_USE_POINTS))
