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
from backend.domains.identity.models import GenderEnum, User
from backend.domains.patients.models import Patient
from backend.domains.privacy.runtime import (
    calculate_patient_age,
    evaluate_tenant_data_use,
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
                        }
                    ],
                }
            ],
        },
    )


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
    for patient in patients:
        evaluate_tenant_data_use(
            db,
            data_use_point="fhir.patient.search",
            patient_id=patient.id,
            actor_user_id=actor.id,
            patient_age=calculate_patient_age(patient.date_of_birth),
            commit_audit=False,
        )
    db.commit()
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
        "total": total,
        "link": links,
        "entry": [
            {
                "fullUrl": f"{settings.tenant_url(tenant.subdomain_slug)}{request.url.path}/{patient.id}",
                "resource": _patient_resource(patient, tenant.subdomain_slug),
                "search": {"mode": "match"},
            }
            for patient in patients
        ],
    }
