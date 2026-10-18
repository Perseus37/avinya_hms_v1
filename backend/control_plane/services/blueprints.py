from typing import Any, Dict
from sqlalchemy.orm import Session
from backend.domains.facility.models import Bed, BedStatusEnum, BedTypeEnum, Floor, PhysicalRoom, RoomTypeEnum, Ward, WardTypeEnum
from backend.domains.identity.models import Department
from backend.domains.surgery.models import OTTheatreTypeEnum, OperationTheatre

BLUEPRINTS: Dict[str, Dict[str, Any]] = {
    "TIER_1_QUATERNARY_500_BED": {
        "name": "500-Bed Tertiary & Quaternary Flagship",
        "description": "Multi-floor medical center with ICU, Emergency, Modular OTs, and Deluxe suites.",
        "floors": [
            {
                "floor_number": 1,
                "name": "Ground Floor - Critical & Emergency",
                "wards": [
                    {
                        "code": "W_ER",
                        "name": "Emergency & Trauma Bay",
                        "ward_type": WardTypeEnum.EMERGENCY,
                        "dept_code": "DEPT_EMERGENCY",
                        "rooms": [
                            {
                                "number": "ER-BAY",
                                "type": RoomTypeEnum.DORMITORY,
                                "beds": 10,
                                "rate": 200000,
                                "b_type": BedTypeEnum.STANDARD,
                            }
                        ],
                    },
                    {
                        "code": "W_MICU",
                        "name": "Medical Intensive Care Unit",
                        "ward_type": WardTypeEnum.ICU,
                        "dept_code": "DEPT_GEN_MED",
                        "rooms": [
                            {
                                "number": "ICU-101",
                                "type": RoomTypeEnum.DORMITORY,
                                "beds": 8,
                                "rate": 800000,
                                "b_type": BedTypeEnum.ICU,
                            }
                        ],
                    },
                ],
            },
            {
                "floor_number": 2,
                "name": "Second Floor - Surgical & Inpatient",
                "wards": [
                    {
                        "code": "W_GEN_SURG",
                        "name": "General Surgical Ward",
                        "ward_type": WardTypeEnum.GENERAL,
                        "dept_code": "DEPT_GEN_SURG",
                        "rooms": [
                            {
                                "number": "201",
                                "type": RoomTypeEnum.DOUBLE,
                                "beds": 2,
                                "rate": 150000,
                                "b_type": BedTypeEnum.STANDARD,
                            },
                            {
                                "number": "202",
                                "type": RoomTypeEnum.DOUBLE,
                                "beds": 2,
                                "rate": 150000,
                                "b_type": BedTypeEnum.STANDARD,
                            },
                        ],
                    }
                ],
            },
        ],
        "theatres": [
            {
                "code": "OT_MAIN_1",
                "name": "Modular OT 1 (Cardiac/Neuro)",
                "theatre_type": OTTheatreTypeEnum.MODULAR_MAJOR,
                "hourly_rate_paise": 600000,
            },
            {
                "code": "OT_EMERGENCY",
                "name": "Trauma Emergency OT",
                "theatre_type": OTTheatreTypeEnum.ORTHO_TRAUMA,
                "hourly_rate_paise": 450000,
            },
        ],
    },
    "BOUTIQUE_MATERNITY_NICU": {
        "name": "Boutique Maternity, Fertility & NICU Center",
        "description": "Specialized women and child facility with Labor Suites and Level III NICU.",
        "floors": [
            {
                "floor_number": 1,
                "name": "First Floor - LDR & Inpatient Suites",
                "wards": [
                    {
                        "code": "W_LDR",
                        "name": "Labor Delivery Recovery (LDR)",
                        "ward_type": WardTypeEnum.PRIVATE,
                        "dept_code": "DEPT_OBGYN",
                        "rooms": [
                            {
                                "number": "SUITE-1",
                                "type": RoomTypeEnum.DELUXE,
                                "beds": 1,
                                "rate": 500000,
                                "b_type": BedTypeEnum.MOTORIZED,
                            }
                        ],
                    },
                    {
                        "code": "W_NICU",
                        "name": "Neonatal ICU",
                        "ward_type": WardTypeEnum.NICU,
                        "dept_code": "DEPT_PEDIATRICS",
                        "rooms": [
                            {
                                "number": "NICU-POD",
                                "type": RoomTypeEnum.DORMITORY,
                                "beds": 6,
                                "rate": 700000,
                                "b_type": BedTypeEnum.PEDIATRIC,
                            }
                        ],
                    },
                ],
            }
        ],
        "theatres": [
            {
                "code": "OT_OBGYN_1",
                "name": "Dedicated Obstetric Theatre",
                "theatre_type": OTTheatreTypeEnum.MINOR,
                "hourly_rate_paise": 350000,
            }
        ],
    },
    "TRAUMA_EMERGENCY_HUB": {
        "name": "Acute Trauma & Emergency Response Hospital",
        "description": "High-velocity resuscitation bays, orthopedics, and emergency blood storage.",
        "floors": [
            {
                "floor_number": 1,
                "name": "Ground Level - Trauma Center",
                "wards": [
                    {
                        "code": "W_RESUS",
                        "name": "Red Resuscitation Bay",
                        "ward_type": WardTypeEnum.EMERGENCY,
                        "dept_code": "DEPT_EMERGENCY",
                        "rooms": [
                            {
                                "number": "RED-BAY",
                                "type": RoomTypeEnum.DORMITORY,
                                "beds": 6,
                                "rate": 400000,
                                "b_type": BedTypeEnum.ICU,
                            }
                        ],
                    }
                ],
            }
        ],
        "theatres": [
            {
                "code": "OT_TRAUMA",
                "name": "24/7 Ortho-Trauma Suite",
                "theatre_type": OTTheatreTypeEnum.ORTHO_TRAUMA,
                "hourly_rate_paise": 500000,
            }
        ],
    },
    "POLYCLINIC_DAYCARE_OPD": {
        "name": "High-Throughput Polyclinic & Daycare Center",
        "description": "Outpatient consulting, short-stay daycare recliners, and diagnostic labs.",
        "floors": [
            {
                "floor_number": 1,
                "name": "Daycare Floor",
                "wards": [
                    {
                        "code": "W_DAYCARE",
                        "name": "Daycare Observation Ward",
                        "ward_type": WardTypeEnum.GENERAL,
                        "dept_code": "DEPT_GEN_MED",
                        "rooms": [
                            {
                                "number": "DC-101",
                                "type": RoomTypeEnum.DORMITORY,
                                "beds": 4,
                                "rate": 80000,
                                "b_type": BedTypeEnum.STANDARD,
                            }
                        ],
                    }
                ],
            }
        ],
        "theatres": [],
    },
    "BLANK_CANVAS": {
        "name": "Blank Canvas (100% Custom Hospital Admin Setup)",
        "description": "Seeds RBAC roles, departments, and templates with zero pre-built floors or beds.",
        "floors": [],
        "theatres": [],
    },
}


def apply_hospital_blueprint(db: Session, blueprint_key: str, admin_user_id: int) -> Dict[str, int]:
    """Generates the initial spatial setup directly in the new hospital database."""
    blueprint = BLUEPRINTS.get(blueprint_key, BLUEPRINTS["TIER_1_QUATERNARY_500_BED"])
    summary = {"floors": 0, "wards": 0, "rooms": 0, "beds": 0, "theatres": 0}

    depts = {d.code: d for d in db.query(Department).all()}
    fallback_dept = next(iter(depts.values()), None)

    for f_spec in blueprint["floors"]:
        floor = Floor(floor_number=f_spec["floor_number"], name=f_spec["name"], is_active=True, created_by_id=admin_user_id)
        db.add(floor)
        db.flush()
        summary["floors"] += 1

        for w_spec in f_spec["wards"]:
            dept = depts.get(w_spec["dept_code"], fallback_dept)
            dept_id = dept.id if dept else 1

            ward = Ward(
                floor_id=floor.id,
                department_id=dept_id,
                code=w_spec["code"],
                name=w_spec["name"],
                ward_type=w_spec["ward_type"],
                is_active=True,
                created_by_id=admin_user_id,
            )
            db.add(ward)
            db.flush()
            summary["wards"] += 1

            for r_spec in w_spec["rooms"]:
                room = PhysicalRoom(
                    ward_id=ward.id,
                    room_number=r_spec["number"],
                    room_type=r_spec["type"],
                    bed_capacity=r_spec["beds"],
                    is_active=True,
                    created_by_id=admin_user_id,
                )
                db.add(room)
                db.flush()
                summary["rooms"] += 1

                for b_idx in range(1, r_spec["beds"] + 1):
                    bed = Bed(
                        room_id=room.id,
                        bed_number=f"{r_spec['number']}-{b_idx:02d}",
                        bed_type=r_spec["b_type"],
                        status=BedStatusEnum.AVAILABLE,
                        base_daily_rate_paise=r_spec["rate"],
                        created_by_id=admin_user_id,
                    )
                    db.add(bed)
                    summary["beds"] += 1

    for ot_spec in blueprint.get("theatres", []):
        ot = OperationTheatre(
            code=ot_spec["code"],
            name=ot_spec["name"],
            theatre_type=ot_spec["theatre_type"],
            hourly_rate_paise=ot_spec["hourly_rate_paise"],
            is_active=True,
            created_by_id=admin_user_id,
        )
        db.add(ot)
        summary["theatres"] += 1

    db.commit()
    return summary