from datetime import date
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.core.database import get_db, resolve_tenant_context
from backend.core.dependencies import RequirePermission
from backend.domains.clinical.models import DiagnosisTypeEnum, EncounterDiagnosis, EncounterVital
from backend.domains.encounters.models import EncounterStatusEnum, EncounterTypeEnum, PatientEncounter
from backend.domains.identity.models import GenderEnum, User
from backend.domains.patients.models import DiagnosisMaster, Patient
from backend.domains.privacy.runtime import (
    calculate_patient_age,
    commit_without_expiring,
    evaluate_tenant_data_use,
    evaluate_tenant_data_use_for_patients,
)

router = APIRouter(prefix="/api/fhir/R4", tags=["FHIR R4"])


@router.get("/metadata")
def capability_statement():
    return JSONResponse(
        media_type="application/fhir+json",
        content={
            "resourceType": "CapabilityStatement",
            "status": "active",
            "date": date.today().isoformat(),
            "kind": "instance",
            "fhirVersion": "4.0.1",
            "format": ["application/fhir+json"],
            "rest": [
                {
                    "mode": "server",
                    "resource": [
                        {
                            "type": "Patient",
                            "profile": "http://hl7.org/fhir/StructureDefinition/Patient",
                            "interaction": [
                                {"code": "read"},
                                {"code": "search-type"},
                            ],
                            "searchParam": [
                                {"name": "_id", "type": "token"},
                                {"name": "identifier", "type": "token"},
                                {"name": "name", "type": "string"},
                                {"name": "birthdate", "type": "date"},
                                {"name": "gender", "type": "token"},
                            ],
                        },
                        {
                            "type": "Encounter",
                            "profile": "http://hl7.org/fhir/StructureDefinition/Encounter",
                            "interaction": [
                                {"code": "read"},
                                {"code": "search-type"},
                            ],
                            "searchParam": [
                                {"name": "_id", "type": "token"},
                                {"name": "patient", "type": "reference"},
                                {"name": "status", "type": "token"},
                                {"name": "date", "type": "date"},
                            ],
                        },
                        {
                            "type": "Observation",
                            "profile": "http://hl7.org/fhir/StructureDefinition/Observation",
                            "interaction": [
                                {"code": "read"},
                                {"code": "search-type"},
                            ],
                            "searchParam": [
                                {"name": "_id", "type": "token"},
                                {"name": "patient", "type": "reference"},
                                {"name": "encounter", "type": "reference"},
                                {"name": "date", "type": "date"},
                            ],
                        },
                        {
                            "type": "Condition",
                            "profile": "http://hl7.org/fhir/StructureDefinition/Condition",
                            "interaction": [
                                {"code": "read"},
                                {"code": "search-type"},
                            ],
                            "searchParam": [
                                {"name": "_id", "type": "token"},
                                {"name": "patient", "type": "reference"},
                                {"name": "encounter", "type": "reference"},
                                {"name": "code", "type": "token"},
                            ],
                        },
                    ],
                }
            ],
        },
    )


# -----------------------------------------------------------------------------
# FHIR Patient Helpers & Endpoints
# -----------------------------------------------------------------------------

def _patient_resource(patient: Patient, tenant_slug: str) -> dict:
    gender = {
        GenderEnum.MALE: "male",
        GenderEnum.FEMALE: "female",
        GenderEnum.OTHER: "other",
    }.get(patient.gender, "unknown")
    identifiers = [
        {
            "use": "usual",
            "system": "urn:avinya:hms:mrn",
            "value": patient.mrn,
        }
    ]
    if patient.abha_number:
        identifiers.append(
            {
                "system": "urn:avinya:hms:abha",
                "value": patient.abha_number,
            }
        )
    telecom = []
    if patient.phone_number:
        telecom.append({"system": "phone", "value": patient.phone_number, "use": "mobile"})
    if patient.email:
        telecom.append({"system": "email", "value": patient.email})
    name = {
        "use": "official",
        "text": patient.full_name,
    }
    name_parts = patient.full_name.strip().split()
    if name_parts:
        if len(name_parts) > 1:
            name["given"] = name_parts[:-1]
            name["family"] = name_parts[-1]
        else:
            name["given"] = name_parts

    resource = {
        "resourceType": "Patient",
        "id": str(patient.id),
        "active": patient.is_active,
        "identifier": identifiers,
        "name": [name],
        "gender": gender,
        "birthDate": patient.date_of_birth.date().isoformat(),
        "meta": {
            "profile": ["http://hl7.org/fhir/StructureDefinition/Patient"],
            "tag": [{"system": "urn:avinya:hms:tenant", "code": tenant_slug}],
        },
    }
    if telecom:
        resource["telecom"] = telecom
    if any((patient.address, patient.city, patient.district, patient.state, patient.pincode, patient.village)):
        address = {"use": "home", "type": "physical"}
        lines = [value for value in (patient.address, patient.village) if value]
        if lines:
            address["line"] = lines
        for field in ("city", "district", "state", "pincode"):
            value = getattr(patient, field)
            if value:
                address["postalCode" if field == "pincode" else field] = value
        resource["address"] = [address]
    return resource


@router.get(
    "/Patient/{patient_id}",
    openapi_extra={"x-privacy-data-use-point": "fhir.patient.read"},
)
def read_patient(
    patient_id: int,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    actor: User = Depends(RequirePermission("patients:read")),
):
    patient = db.get(Patient, patient_id)
    if not patient or not patient.is_active:
        return JSONResponse(
            status_code=404,
            media_type="application/fhir+json",
            content={
                "resourceType": "OperationOutcome",
                "issue": [
                    {
                        "severity": "error",
                        "code": "not-found",
                        "diagnostics": "FHIR Patient not found.",
                    }
                ],
            },
        )
    decision = evaluate_tenant_data_use(
        db,
        data_use_point="fhir.patient.read",
        patient_id=patient.id,
        actor_user_id=actor.id,
        patient_age=calculate_patient_age(patient.date_of_birth),
    )
    if not decision.allowed:
        return JSONResponse(
            status_code=403,
            media_type="application/fhir+json",
            content={
                "resourceType": "OperationOutcome",
                "issue": [
                    {
                        "severity": "error",
                        "code": "forbidden",
                        "diagnostics": "Privacy policy does not permit this disclosure.",
                    }
                ],
            },
        )
    response.media_type = "application/fhir+json"
    tenant = resolve_tenant_context(request)
    return _patient_resource(patient, tenant.subdomain_slug)


@router.get(
    "/Patient",
    openapi_extra={"x-privacy-data-use-point": "fhir.patient.search"},
)
def search_patients(
    request: Request,
    response: Response,
    patient_id: Optional[str] = Query(default=None, alias="_id", pattern=r"^[0-9]+$"),
    identifier: Optional[str] = Query(default=None, max_length=150),
    name: Optional[str] = Query(default=None, min_length=1, max_length=100),
    birthdate: Optional[date] = None,
    gender: Optional[str] = Query(
        default=None, pattern=r"^(male|female|other|unknown)$"
    ),
    count: int = Query(default=20, alias="_count", ge=1, le=100),
    offset: int = Query(default=0, alias="_offset", ge=0),
    db: Session = Depends(get_db),
    actor: User = Depends(RequirePermission("patients:read")),
):
    statement = select(Patient).where(Patient.is_active.is_(True))
    if patient_id:
        statement = statement.where(Patient.id == int(patient_id))
    if identifier:
        if "|" in identifier:
            system, value = identifier.split("|", maxsplit=1)
            if system == "urn:avinya:hms:mrn":
                statement = statement.where(Patient.mrn == value)
            elif system == "urn:avinya:hms:abha":
                statement = statement.where(Patient.abha_number == value)
            else:
                statement = statement.where(False)
        else:
            statement = statement.where(
                or_(Patient.mrn == identifier, Patient.abha_number == identifier)
            )
    if name:
        escaped_name = name.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        statement = statement.where(
            Patient.full_name.ilike(f"%{escaped_name}%", escape="\\")
        )
    if birthdate:
        statement = statement.where(func.date(Patient.date_of_birth) == birthdate.isoformat())
    if gender:
        gender_value = {
            "male": GenderEnum.MALE,
            "female": GenderEnum.FEMALE,
            "other": GenderEnum.OTHER,
        }.get(gender)
        if gender_value is None:
            statement = statement.where(False)
        else:
            statement = statement.where(Patient.gender == gender_value)

    total = db.execute(
        select(func.count()).select_from(statement.order_by(None).subquery())
    ).scalar_one()
    patients = db.execute(
        statement.order_by(Patient.id).offset(offset).limit(count)
    ).scalars().all()
    decisions = evaluate_tenant_data_use_for_patients(
        db,
        data_use_point="fhir.patient.search",
        patient_ages={
            patient.id: calculate_patient_age(patient.date_of_birth)
            for patient in patients
        },
        actor_user_id=actor.id,
        commit_audit=False,
    )
    commit_without_expiring(db)
    allowed_patients = [
        patient
        for patient in patients
        if decisions.get(patient.id) is None or decisions[patient.id].allowed
    ]
    adjusted_total = max(0, total - (len(patients) - len(allowed_patients)))
    tenant = resolve_tenant_context(request)
    current_url = f"{settings.tenant_url(tenant.subdomain_slug)}{request.url.path}"
    query_items = list(request.query_params.multi_items())
    links = [
        {
            "relation": "self",
            "url": f"{current_url}?{urlencode(query_items)}" if query_items else current_url,
        }
    ]
    if offset + len(patients) < total:
        next_offset = offset + count
        next_query = [
            (key, value) for key, value in query_items if key != "_offset"
        ]
        next_query.append(("_offset", str(next_offset)))
        links.append(
            {
                "relation": "next",
                "url": f"{current_url}?{urlencode(next_query)}",
            }
        )
    response.media_type = "application/fhir+json"
    return {
        "resourceType": "Bundle",
        "type": "searchset",
        "total": adjusted_total,
        "link": links,
        "entry": [
            {
                "fullUrl": f"{settings.tenant_url(tenant.subdomain_slug)}{request.url.path}/{patient.id}",
                "resource": _patient_resource(patient, tenant.subdomain_slug),
                "search": {"mode": "match"},
            }
            for patient in allowed_patients
        ],
    }


# -----------------------------------------------------------------------------
# FHIR Encounter Helpers & Endpoints
# -----------------------------------------------------------------------------

def _encounter_resource(encounter: PatientEncounter, tenant_slug: str) -> dict:
    status_map = {
        EncounterStatusEnum.PLANNED: "planned",
        EncounterStatusEnum.ACTIVE: "in-progress",
        EncounterStatusEnum.DISCHARGED: "finished",
        EncounterStatusEnum.CANCELLED: "cancelled",
    }
    class_map = {
        EncounterTypeEnum.OPD: {
            "system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
            "code": "AMB",
            "display": "ambulatory",
        },
        EncounterTypeEnum.IPD: {
            "system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
            "code": "IMP",
            "display": "inpatient encounter",
        },
        EncounterTypeEnum.ER: {
            "system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
            "code": "EMER",
            "display": "emergency",
        },
    }
    res = {
        "resourceType": "Encounter",
        "id": str(encounter.id),
        "status": status_map.get(encounter.status, "unknown"),
        "class": class_map.get(
            encounter.encounter_type,
            {
                "system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
                "code": "AMB",
                "display": "ambulatory",
            },
        ),
        "subject": {
            "reference": f"Patient/{encounter.patient_id}",
        },
        "participant": [
            {
                "individual": {
                    "reference": f"Practitioner/{encounter.primary_doctor_id}",
                }
            }
        ],
        "period": {
            "start": encounter.start_time.isoformat(),
        },
        "serviceType": {
            "coding": [
                {
                    "system": "urn:avinya:hms:department",
                    "code": str(encounter.department_id),
                }
            ]
        },
        "meta": {
            "profile": ["http://hl7.org/fhir/StructureDefinition/Encounter"],
            "tag": [{"system": "urn:avinya:hms:tenant", "code": tenant_slug}],
        },
    }
    if encounter.end_time:
        res["period"]["end"] = encounter.end_time.isoformat()
    if encounter.chief_complaint_notes:
        res["reasonCode"] = [{"text": encounter.chief_complaint_notes}]
    return res


@router.get(
    "/Encounter/{encounter_id}",
    openapi_extra={"x-privacy-data-use-point": "fhir.encounter.read"},
)
def read_encounter(
    encounter_id: int,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    actor: User = Depends(RequirePermission("adt:read")),
):
    encounter = db.get(PatientEncounter, encounter_id)
    if not encounter:
        return JSONResponse(
            status_code=404,
            media_type="application/fhir+json",
            content={
                "resourceType": "OperationOutcome",
                "issue": [
                    {
                        "severity": "error",
                        "code": "not-found",
                        "diagnostics": "FHIR Encounter not found.",
                    }
                ],
            },
        )
    patient = db.get(Patient, encounter.patient_id)
    if not patient or not patient.is_active:
        return JSONResponse(
            status_code=404,
            media_type="application/fhir+json",
            content={
                "resourceType": "OperationOutcome",
                "issue": [
                    {
                        "severity": "error",
                        "code": "not-found",
                        "diagnostics": "FHIR Patient for Encounter not found.",
                    }
                ],
            },
        )
    decision = evaluate_tenant_data_use(
        db,
        data_use_point="fhir.encounter.read",
        patient_id=patient.id,
        actor_user_id=actor.id,
        patient_age=calculate_patient_age(patient.date_of_birth),
    )
    if not decision.allowed:
        return JSONResponse(
            status_code=403,
            media_type="application/fhir+json",
            content={
                "resourceType": "OperationOutcome",
                "issue": [
                    {
                        "severity": "error",
                        "code": "forbidden",
                        "diagnostics": "Privacy policy does not permit this disclosure.",
                    }
                ],
            },
        )
    response.media_type = "application/fhir+json"
    tenant = resolve_tenant_context(request)
    return _encounter_resource(encounter, tenant.subdomain_slug)


@router.get(
    "/Encounter",
    openapi_extra={"x-privacy-data-use-point": "fhir.encounter.search"},
)
def search_encounters(
    request: Request,
    response: Response,
    encounter_id: Optional[str] = Query(default=None, alias="_id", pattern=r"^[0-9]+$"),
    patient: Optional[str] = Query(default=None),
    subject: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    date_val: Optional[date] = Query(default=None, alias="date"),
    count: int = Query(default=20, alias="_count", ge=1, le=100),
    offset: int = Query(default=0, alias="_offset", ge=0),
    db: Session = Depends(get_db),
    actor: User = Depends(RequirePermission("adt:read")),
):
    statement = (
        select(PatientEncounter)
        .join(Patient, Patient.id == PatientEncounter.patient_id)
        .where(Patient.is_active.is_(True))
    )
    if encounter_id:
        statement = statement.where(PatientEncounter.id == int(encounter_id))
    patient_ref = patient or subject
    if patient_ref:
        p_id = patient_ref.replace("Patient/", "")
        if p_id.isdigit():
            statement = statement.where(PatientEncounter.patient_id == int(p_id))
        else:
            statement = statement.where(False)
    if status:
        status_reverse = {
            "planned": EncounterStatusEnum.PLANNED,
            "in-progress": EncounterStatusEnum.ACTIVE,
            "finished": EncounterStatusEnum.DISCHARGED,
            "cancelled": EncounterStatusEnum.CANCELLED,
        }
        st_enum = status_reverse.get(status)
        if st_enum:
            statement = statement.where(PatientEncounter.status == st_enum)
        else:
            statement = statement.where(False)
    if date_val:
        statement = statement.where(func.date(PatientEncounter.start_time) == date_val.isoformat())

    total = db.execute(
        select(func.count()).select_from(statement.order_by(None).subquery())
    ).scalar_one()
    encounters = db.execute(
        statement.order_by(PatientEncounter.id.desc()).offset(offset).limit(count)
    ).scalars().all()

    patient_ids = {enc.patient_id for enc in encounters}
    patients = (
        db.execute(select(Patient).where(Patient.id.in_(patient_ids))).scalars().all()
        if patient_ids
        else []
    )
    patient_ages = {p.id: calculate_patient_age(p.date_of_birth) for p in patients}
    decisions = evaluate_tenant_data_use_for_patients(
        db,
        data_use_point="fhir.encounter.search",
        patient_ages=patient_ages,
        actor_user_id=actor.id,
        commit_audit=False,
    )
    commit_without_expiring(db)
    allowed_encounters = [
        enc
        for enc in encounters
        if decisions.get(enc.patient_id) is None or decisions[enc.patient_id].allowed
    ]
    adjusted_total = max(0, total - (len(encounters) - len(allowed_encounters)))

    tenant = resolve_tenant_context(request)
    current_url = f"{settings.tenant_url(tenant.subdomain_slug)}{request.url.path}"
    query_items = list(request.query_params.multi_items())
    links = [
        {
            "relation": "self",
            "url": f"{current_url}?{urlencode(query_items)}" if query_items else current_url,
        }
    ]
    if offset + len(encounters) < total:
        next_offset = offset + count
        next_query = [(k, v) for k, v in query_items if k != "_offset"]
        next_query.append(("_offset", str(next_offset)))
        links.append(
            {
                "relation": "next",
                "url": f"{current_url}?{urlencode(next_query)}",
            }
        )
    response.media_type = "application/fhir+json"
    return {
        "resourceType": "Bundle",
        "type": "searchset",
        "total": adjusted_total,
        "link": links,
        "entry": [
            {
                "fullUrl": f"{settings.tenant_url(tenant.subdomain_slug)}{request.url.path}/{enc.id}",
                "resource": _encounter_resource(enc, tenant.subdomain_slug),
                "search": {"mode": "match"},
            }
            for enc in allowed_encounters
        ],
    }


# -----------------------------------------------------------------------------
# FHIR Observation (Vitals) Helpers & Endpoints
# -----------------------------------------------------------------------------

def _observation_resource(vital: EncounterVital, patient_id: int, tenant_slug: str) -> dict:
    components = []
    if vital.temperature_f is not None:
        components.append(
            {
                "code": {
                    "coding": [
                        {
                            "system": "http://loinc.org",
                            "code": "8310-5",
                            "display": "Body temperature",
                        }
                    ]
                },
                "valueQuantity": {
                    "value": vital.temperature_f,
                    "unit": "[degF]",
                    "system": "http://unitsofmeasure.org",
                    "code": "[degF]",
                },
            }
        )
    if vital.pulse_bpm is not None:
        components.append(
            {
                "code": {
                    "coding": [
                        {
                            "system": "http://loinc.org",
                            "code": "8867-4",
                            "display": "Heart rate",
                        }
                    ]
                },
                "valueQuantity": {
                    "value": vital.pulse_bpm,
                    "unit": "/min",
                    "system": "http://unitsofmeasure.org",
                    "code": "/min",
                },
            }
        )
    if vital.bp_systolic is not None:
        components.append(
            {
                "code": {
                    "coding": [
                        {
                            "system": "http://loinc.org",
                            "code": "8480-6",
                            "display": "Systolic blood pressure",
                        }
                    ]
                },
                "valueQuantity": {
                    "value": vital.bp_systolic,
                    "unit": "mm[Hg]",
                    "system": "http://unitsofmeasure.org",
                    "code": "mm[Hg]",
                },
            }
        )
    if vital.bp_diastolic is not None:
        components.append(
            {
                "code": {
                    "coding": [
                        {
                            "system": "http://loinc.org",
                            "code": "8462-4",
                            "display": "Diastolic blood pressure",
                        }
                    ]
                },
                "valueQuantity": {
                    "value": vital.bp_diastolic,
                    "unit": "mm[Hg]",
                    "system": "http://unitsofmeasure.org",
                    "code": "mm[Hg]",
                },
            }
        )
    if vital.respiratory_rate is not None:
        components.append(
            {
                "code": {
                    "coding": [
                        {
                            "system": "http://loinc.org",
                            "code": "9279-1",
                            "display": "Respiratory rate",
                        }
                    ]
                },
                "valueQuantity": {
                    "value": vital.respiratory_rate,
                    "unit": "/min",
                    "system": "http://unitsofmeasure.org",
                    "code": "/min",
                },
            }
        )
    if vital.spo2_percent is not None:
        components.append(
            {
                "code": {
                    "coding": [
                        {
                            "system": "http://loinc.org",
                            "code": "2708-6",
                            "display": "Oxygen saturation in Arterial blood",
                        }
                    ]
                },
                "valueQuantity": {
                    "value": vital.spo2_percent,
                    "unit": "%",
                    "system": "http://unitsofmeasure.org",
                    "code": "%",
                },
            }
        )
    if vital.weight_kg is not None:
        components.append(
            {
                "code": {
                    "coding": [
                        {
                            "system": "http://loinc.org",
                            "code": "29463-7",
                            "display": "Body weight",
                        }
                    ]
                },
                "valueQuantity": {
                    "value": vital.weight_kg,
                    "unit": "kg",
                    "system": "http://unitsofmeasure.org",
                    "code": "kg",
                },
            }
        )
    if vital.height_cm is not None:
        components.append(
            {
                "code": {
                    "coding": [
                        {
                            "system": "http://loinc.org",
                            "code": "8302-2",
                            "display": "Body height",
                        }
                    ]
                },
                "valueQuantity": {
                    "value": vital.height_cm,
                    "unit": "cm",
                    "system": "http://unitsofmeasure.org",
                    "code": "cm",
                },
            }
        )
    if vital.bmi is not None:
        components.append(
            {
                "code": {
                    "coding": [
                        {
                            "system": "http://loinc.org",
                            "code": "39156-5",
                            "display": "Body mass index (BMI) [Ratio]",
                        }
                    ]
                },
                "valueQuantity": {
                    "value": vital.bmi,
                    "unit": "kg/m2",
                    "system": "http://unitsofmeasure.org",
                    "code": "kg/m2",
                },
            }
        )

    res = {
        "resourceType": "Observation",
        "id": str(vital.id),
        "status": "final",
        "category": [
            {
                "coding": [
                    {
                        "system": "http://terminology.hl7.org/CodeSystem/observation-category",
                        "code": "vital-signs",
                        "display": "Vital Signs",
                    }
                ]
            }
        ],
        "code": {
            "coding": [
                {
                    "system": "http://loinc.org",
                    "code": "85353-8",
                    "display": "Vital signs, weight, height, head circumference, oxygen saturation & BMI panel",
                }
            ],
            "text": "Vital Signs Panel",
        },
        "subject": {
            "reference": f"Patient/{patient_id}",
        },
        "encounter": {
            "reference": f"Encounter/{vital.encounter_id}",
        },
        "effectiveDateTime": vital.created_at.isoformat(),
        "component": components,
        "meta": {
            "profile": ["http://hl7.org/fhir/StructureDefinition/Observation"],
            "tag": [{"system": "urn:avinya:hms:tenant", "code": tenant_slug}],
        },
    }
    if vital.notes:
        res["note"] = [{"text": vital.notes}]
    return res


@router.get(
    "/Observation/{observation_id}",
    openapi_extra={"x-privacy-data-use-point": "fhir.observation.read"},
)
def read_observation(
    observation_id: int,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    actor: User = Depends(RequirePermission("adt:read")),
):
    vital = db.get(EncounterVital, observation_id)
    if not vital:
        return JSONResponse(
            status_code=404,
            media_type="application/fhir+json",
            content={
                "resourceType": "OperationOutcome",
                "issue": [
                    {
                        "severity": "error",
                        "code": "not-found",
                        "diagnostics": "FHIR Observation not found.",
                    }
                ],
            },
        )
    encounter = db.get(PatientEncounter, vital.encounter_id)
    if not encounter:
        return JSONResponse(
            status_code=404,
            media_type="application/fhir+json",
            content={
                "resourceType": "OperationOutcome",
                "issue": [
                    {
                        "severity": "error",
                        "code": "not-found",
                        "diagnostics": "Encounter for Observation not found.",
                    }
                ],
            },
        )
    patient = db.get(Patient, encounter.patient_id)
    if not patient or not patient.is_active:
        return JSONResponse(
            status_code=404,
            media_type="application/fhir+json",
            content={
                "resourceType": "OperationOutcome",
                "issue": [
                    {
                        "severity": "error",
                        "code": "not-found",
                        "diagnostics": "Patient for Observation not found.",
                    }
                ],
            },
        )
    decision = evaluate_tenant_data_use(
        db,
        data_use_point="fhir.observation.read",
        patient_id=patient.id,
        actor_user_id=actor.id,
        patient_age=calculate_patient_age(patient.date_of_birth),
    )
    if not decision.allowed:
        return JSONResponse(
            status_code=403,
            media_type="application/fhir+json",
            content={
                "resourceType": "OperationOutcome",
                "issue": [
                    {
                        "severity": "error",
                        "code": "forbidden",
                        "diagnostics": "Privacy policy does not permit this disclosure.",
                    }
                ],
            },
        )
    response.media_type = "application/fhir+json"
    tenant = resolve_tenant_context(request)
    return _observation_resource(vital, patient.id, tenant.subdomain_slug)


@router.get(
    "/Observation",
    openapi_extra={"x-privacy-data-use-point": "fhir.observation.search"},
)
def search_observations(
    request: Request,
    response: Response,
    observation_id: Optional[str] = Query(default=None, alias="_id", pattern=r"^[0-9]+$"),
    patient: Optional[str] = Query(default=None),
    subject: Optional[str] = Query(default=None),
    encounter: Optional[str] = Query(default=None),
    date_val: Optional[date] = Query(default=None, alias="date"),
    count: int = Query(default=20, alias="_count", ge=1, le=100),
    offset: int = Query(default=0, alias="_offset", ge=0),
    db: Session = Depends(get_db),
    actor: User = Depends(RequirePermission("adt:read")),
):
    statement = (
        select(EncounterVital, PatientEncounter.patient_id)
        .join(PatientEncounter, PatientEncounter.id == EncounterVital.encounter_id)
        .join(Patient, Patient.id == PatientEncounter.patient_id)
        .where(Patient.is_active.is_(True))
    )
    if observation_id:
        statement = statement.where(EncounterVital.id == int(observation_id))
    patient_ref = patient or subject
    if patient_ref:
        p_id = patient_ref.replace("Patient/", "")
        if p_id.isdigit():
            statement = statement.where(PatientEncounter.patient_id == int(p_id))
        else:
            statement = statement.where(False)
    if encounter:
        enc_id = encounter.replace("Encounter/", "")
        if enc_id.isdigit():
            statement = statement.where(EncounterVital.encounter_id == int(enc_id))
        else:
            statement = statement.where(False)
    if date_val:
        statement = statement.where(func.date(EncounterVital.created_at) == date_val.isoformat())

    total = db.execute(
        select(func.count()).select_from(statement.order_by(None).subquery())
    ).scalar_one()
    rows = db.execute(
        statement.order_by(EncounterVital.id.desc()).offset(offset).limit(count)
    ).all()

    patient_ids = {row[1] for row in rows}
    patients = (
        db.execute(select(Patient).where(Patient.id.in_(patient_ids))).scalars().all()
        if patient_ids
        else []
    )
    patient_ages = {p.id: calculate_patient_age(p.date_of_birth) for p in patients}
    decisions = evaluate_tenant_data_use_for_patients(
        db,
        data_use_point="fhir.observation.search",
        patient_ages=patient_ages,
        actor_user_id=actor.id,
        commit_audit=False,
    )
    commit_without_expiring(db)
    allowed_rows = [
        row
        for row in rows
        if decisions.get(row[1]) is None or decisions[row[1]].allowed
    ]
    adjusted_total = max(0, total - (len(rows) - len(allowed_rows)))

    tenant = resolve_tenant_context(request)
    current_url = f"{settings.tenant_url(tenant.subdomain_slug)}{request.url.path}"
    query_items = list(request.query_params.multi_items())
    links = [
        {
            "relation": "self",
            "url": f"{current_url}?{urlencode(query_items)}" if query_items else current_url,
        }
    ]
    if offset + len(rows) < total:
        next_offset = offset + count
        next_query = [(k, v) for k, v in query_items if k != "_offset"]
        next_query.append(("_offset", str(next_offset)))
        links.append(
            {
                "relation": "next",
                "url": f"{current_url}?{urlencode(next_query)}",
            }
        )
    response.media_type = "application/fhir+json"
    return {
        "resourceType": "Bundle",
        "type": "searchset",
        "total": adjusted_total,
        "link": links,
        "entry": [
            {
                "fullUrl": f"{settings.tenant_url(tenant.subdomain_slug)}{request.url.path}/{vital.id}",
                "resource": _observation_resource(vital, pat_id, tenant.subdomain_slug),
                "search": {"mode": "match"},
            }
            for vital, pat_id in allowed_rows
        ],
    }


# -----------------------------------------------------------------------------
# FHIR Condition (Diagnoses) Helpers & Endpoints
# -----------------------------------------------------------------------------

def _condition_resource(
    diagnosis: EncounterDiagnosis, diag_master: DiagnosisMaster, patient_id: int, tenant_slug: str
) -> dict:
    is_provisional = diagnosis.diagnosis_type == DiagnosisTypeEnum.PROVISIONAL
    res = {
        "resourceType": "Condition",
        "id": str(diagnosis.id),
        "clinicalStatus": {
            "coding": [
                {
                    "system": "http://terminology.hl7.org/CodeSystem/condition-clinical",
                    "code": "active",
                    "display": "Active",
                }
            ]
        },
        "verificationStatus": {
            "coding": [
                {
                    "system": "http://terminology.hl7.org/CodeSystem/condition-ver-status",
                    "code": "provisional" if is_provisional else "confirmed",
                    "display": "Provisional" if is_provisional else "Confirmed",
                }
            ]
        },
        "category": [
            {
                "coding": [
                    {
                        "system": "http://terminology.hl7.org/CodeSystem/condition-category",
                        "code": "encounter-diagnosis",
                        "display": "Encounter Diagnosis",
                    }
                ]
            }
        ],
        "code": {
            "coding": [
                {
                    "system": "http://hl7.org/fhir/sid/icd-10",
                    "code": diag_master.icd10_code,
                    "display": diag_master.short_description,
                }
            ],
            "text": diag_master.full_description,
        },
        "subject": {
            "reference": f"Patient/{patient_id}",
        },
        "encounter": {
            "reference": f"Encounter/{diagnosis.encounter_id}",
        },
        "recordedDate": diagnosis.created_at.date().isoformat(),
        "meta": {
            "profile": ["http://hl7.org/fhir/StructureDefinition/Condition"],
            "tag": [{"system": "urn:avinya:hms:tenant", "code": tenant_slug}],
        },
    }
    if diagnosis.clinical_remarks:
        res["note"] = [{"text": diagnosis.clinical_remarks}]
    return res


@router.get(
    "/Condition/{condition_id}",
    openapi_extra={"x-privacy-data-use-point": "fhir.condition.read"},
)
def read_condition(
    condition_id: int,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    actor: User = Depends(RequirePermission("adt:read")),
):
    diagnosis = db.get(EncounterDiagnosis, condition_id)
    if not diagnosis:
        return JSONResponse(
            status_code=404,
            media_type="application/fhir+json",
            content={
                "resourceType": "OperationOutcome",
                "issue": [
                    {
                        "severity": "error",
                        "code": "not-found",
                        "diagnostics": "FHIR Condition not found.",
                    }
                ],
            },
        )
    encounter = db.get(PatientEncounter, diagnosis.encounter_id)
    if not encounter:
        return JSONResponse(
            status_code=404,
            media_type="application/fhir+json",
            content={
                "resourceType": "OperationOutcome",
                "issue": [
                    {
                        "severity": "error",
                        "code": "not-found",
                        "diagnostics": "Encounter for Condition not found.",
                    }
                ],
            },
        )
    patient = db.get(Patient, encounter.patient_id)
    if not patient or not patient.is_active:
        return JSONResponse(
            status_code=404,
            media_type="application/fhir+json",
            content={
                "resourceType": "OperationOutcome",
                "issue": [
                    {
                        "severity": "error",
                        "code": "not-found",
                        "diagnostics": "Patient for Condition not found.",
                    }
                ],
            },
        )
    decision = evaluate_tenant_data_use(
        db,
        data_use_point="fhir.condition.read",
        patient_id=patient.id,
        actor_user_id=actor.id,
        patient_age=calculate_patient_age(patient.date_of_birth),
    )
    if not decision.allowed:
        return JSONResponse(
            status_code=403,
            media_type="application/fhir+json",
            content={
                "resourceType": "OperationOutcome",
                "issue": [
                    {
                        "severity": "error",
                        "code": "forbidden",
                        "diagnostics": "Privacy policy does not permit this disclosure.",
                    }
                ],
            },
        )
    diag_master = db.get(DiagnosisMaster, diagnosis.diagnosis_id)
    response.media_type = "application/fhir+json"
    tenant = resolve_tenant_context(request)
    return _condition_resource(diagnosis, diag_master, patient.id, tenant.subdomain_slug)


@router.get(
    "/Condition",
    openapi_extra={"x-privacy-data-use-point": "fhir.condition.search"},
)
def search_conditions(
    request: Request,
    response: Response,
    condition_id: Optional[str] = Query(default=None, alias="_id", pattern=r"^[0-9]+$"),
    patient: Optional[str] = Query(default=None),
    subject: Optional[str] = Query(default=None),
    encounter: Optional[str] = Query(default=None),
    code: Optional[str] = Query(default=None),
    count: int = Query(default=20, alias="_count", ge=1, le=100),
    offset: int = Query(default=0, alias="_offset", ge=0),
    db: Session = Depends(get_db),
    actor: User = Depends(RequirePermission("adt:read")),
):
    statement = (
        select(EncounterDiagnosis, DiagnosisMaster, PatientEncounter.patient_id)
        .join(DiagnosisMaster, DiagnosisMaster.id == EncounterDiagnosis.diagnosis_id)
        .join(PatientEncounter, PatientEncounter.id == EncounterDiagnosis.encounter_id)
        .join(Patient, Patient.id == PatientEncounter.patient_id)
        .where(Patient.is_active.is_(True))
    )
    if condition_id:
        statement = statement.where(EncounterDiagnosis.id == int(condition_id))
    patient_ref = patient or subject
    if patient_ref:
        p_id = patient_ref.replace("Patient/", "")
        if p_id.isdigit():
            statement = statement.where(PatientEncounter.patient_id == int(p_id))
        else:
            statement = statement.where(False)
    if encounter:
        enc_id = encounter.replace("Encounter/", "")
        if enc_id.isdigit():
            statement = statement.where(EncounterDiagnosis.encounter_id == int(enc_id))
        else:
            statement = statement.where(False)
    if code:
        statement = statement.where(DiagnosisMaster.icd10_code == code)

    total = db.execute(
        select(func.count()).select_from(statement.order_by(None).subquery())
    ).scalar_one()
    rows = db.execute(
        statement.order_by(EncounterDiagnosis.id.desc()).offset(offset).limit(count)
    ).all()

    patient_ids = {row[2] for row in rows}
    patients = (
        db.execute(select(Patient).where(Patient.id.in_(patient_ids))).scalars().all()
        if patient_ids
        else []
    )
    patient_ages = {p.id: calculate_patient_age(p.date_of_birth) for p in patients}
    decisions = evaluate_tenant_data_use_for_patients(
        db,
        data_use_point="fhir.condition.search",
        patient_ages=patient_ages,
        actor_user_id=actor.id,
        commit_audit=False,
    )
    commit_without_expiring(db)
    allowed_rows = [
        row
        for row in rows
        if decisions.get(row[2]) is None or decisions[row[2]].allowed
    ]
    adjusted_total = max(0, total - (len(rows) - len(allowed_rows)))

    tenant = resolve_tenant_context(request)
    current_url = f"{settings.tenant_url(tenant.subdomain_slug)}{request.url.path}"
    query_items = list(request.query_params.multi_items())
    links = [
        {
            "relation": "self",
            "url": f"{current_url}?{urlencode(query_items)}" if query_items else current_url,
        }
    ]
    if offset + len(rows) < total:
        next_offset = offset + count
        next_query = [(k, v) for k, v in query_items if k != "_offset"]
        next_query.append(("_offset", str(next_offset)))
        links.append(
            {
                "relation": "next",
                "url": f"{current_url}?{urlencode(next_query)}",
            }
        )
    response.media_type = "application/fhir+json"
    return {
        "resourceType": "Bundle",
        "type": "searchset",
        "total": adjusted_total,
        "link": links,
        "entry": [
            {
                "fullUrl": f"{settings.tenant_url(tenant.subdomain_slug)}{request.url.path}/{diag.id}",
                "resource": _condition_resource(diag, d_master, pat_id, tenant.subdomain_slug),
                "search": {"mode": "match"},
            }
            for diag, d_master, pat_id in allowed_rows
        ],
    }
