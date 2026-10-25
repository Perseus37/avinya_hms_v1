"""Bio-Medical Waste (BMW) Management APIs under Indian BMW Rules 2016 (M-09).

Implements barcoded, colour-coded waste bag tracking (Yellow, Red, White, Blue),
daily weigh-ins, central storage transitions, CBWTF handover manifests, and
State/Central Pollution Control Board (SPCB/CPCB) compliance reporting.
"""

from datetime import date, datetime, time, timezone
import secrets
from typing import Any, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.core.dependencies import RequirePermission
from backend.domains.identity.models import User
from backend.domains.service_ops.models import (
    BiomedicalWasteBag,
    BiomedicalWasteManifest,
    BMWBagStatusEnum,
    BMWColorCategoryEnum,
)

bmw_router = APIRouter(prefix="/api/biomedical-waste", tags=["Bio-Medical Waste Management"])


def _generate_barcode(color: str) -> str:
    color_prefix = color[:3].upper()
    ts = datetime.now(timezone.utc).strftime("%Y%m%d")
    rnd = secrets.token_hex(3).upper()
    return f"BMW-{color_prefix}-{ts}-{rnd}"


def _generate_manifest_number() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d")
    rnd = secrets.token_hex(3).upper()
    return f"BMW-MAN-{ts}-{rnd}"


# -----------------------------------------------------------------------------
# Schemas
# -----------------------------------------------------------------------------
class BMWBagCreate(BaseModel):
    barcode: Optional[str] = Field(default=None, max_length=64)
    color_category: Literal["YELLOW", "RED", "WHITE", "BLUE"]
    weight_grams: int = Field(gt=0, le=100_000)  # Up to 100 kg
    source_department: str = Field(min_length=2, max_length=100)
    waste_description: Optional[str] = Field(default=None, max_length=500)

    @field_validator("source_department")
    @classmethod
    def normalize_dept(cls, value: str) -> str:
        value = value.strip().upper()
        if not value:
            raise ValueError("source_department cannot be blank.")
        return value


class BMWBagResponse(BaseModel):
    id: int
    barcode: str
    color_category: str
    weight_grams: int
    weight_kg: float
    source_department: str
    status: str
    manifest_id: Optional[int]
    waste_description: Optional[str]
    logged_by_user_id: int
    logged_at: datetime
    stored_at: Optional[datetime]
    handed_over_at: Optional[datetime]

    @classmethod
    def from_orm_model(cls, bag: BiomedicalWasteBag) -> "BMWBagResponse":
        return cls(
            id=bag.id,
            barcode=bag.barcode,
            color_category=bag.color_category,
            weight_grams=bag.weight_grams,
            weight_kg=round(bag.weight_grams / 1000.0, 3),
            source_department=bag.source_department,
            status=bag.status,
            manifest_id=bag.manifest_id,
            waste_description=bag.waste_description,
            logged_by_user_id=bag.logged_by_user_id,
            logged_at=bag.logged_at,
            stored_at=bag.stored_at,
            handed_over_at=bag.handed_over_at,
        )


class BMWManifestCreate(BaseModel):
    cbwtf_operator_name: str = Field(min_length=2, max_length=150)
    cbwtf_vehicle_number: str = Field(min_length=3, max_length=50)
    cbwtf_driver_name: str = Field(min_length=2, max_length=100)
    bag_identifiers: List[str] = Field(min_length=1)  # list of barcodes or str(id)
    notes: Optional[str] = Field(default=None, max_length=1000)


class BMWManifestResponse(BaseModel):
    id: int
    manifest_number: str
    cbwtf_operator_name: str
    cbwtf_vehicle_number: str
    cbwtf_driver_name: str
    total_bags_count: int
    total_weight_grams: int
    total_weight_kg: float
    yellow_weight_grams: int
    red_weight_grams: int
    white_weight_grams: int
    blue_weight_grams: int
    supervisor_user_id: int
    pickup_at: datetime
    notes: Optional[str]


# -----------------------------------------------------------------------------
# Endpoints
# -----------------------------------------------------------------------------
@bmw_router.post("/bags", response_model=BMWBagResponse, status_code=status.HTTP_201_CREATED)
def register_waste_bag(
    payload: BMWBagCreate,
    db: Session = Depends(get_db),
    actor: User = Depends(RequirePermission("service_ops:write")),
):
    """Registers a barcoded colour-coded bio-medical waste bag with daily weigh-in."""
    barcode = payload.barcode.strip() if payload.barcode else _generate_barcode(payload.color_category)

    # Validate barcode uniqueness
    existing = db.execute(
        select(BiomedicalWasteBag.id).where(BiomedicalWasteBag.barcode == barcode)
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Biomedical waste bag with barcode '{barcode}' is already registered.",
        )

    bag = BiomedicalWasteBag(
        barcode=barcode,
        color_category=payload.color_category,
        weight_grams=payload.weight_grams,
        source_department=payload.source_department,
        status="LOGGED",
        waste_description=payload.waste_description,
        logged_by_user_id=actor.id,
        created_by_id=actor.id,
        updated_by_id=actor.id,
    )
    db.add(bag)
    db.commit()
    db.refresh(bag)
    return BMWBagResponse.from_orm_model(bag)


@bmw_router.get("/bags", response_model=List[BMWBagResponse])
def list_waste_bags(
    color_category: Optional[Literal["YELLOW", "RED", "WHITE", "BLUE"]] = None,
    bag_status: Optional[Literal["LOGGED", "STORED", "HANDED_OVER", "DISPOSED"]] = Query(
        default=None, alias="status"
    ),
    source_department: Optional[str] = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("service_ops:read")),
):
    """Lists waste bags with optional filtering by colour category, status, and department."""
    stmt = select(BiomedicalWasteBag).order_by(BiomedicalWasteBag.id.desc())
    if color_category:
        stmt = stmt.where(BiomedicalWasteBag.color_category == color_category)
    if bag_status:
        stmt = stmt.where(BiomedicalWasteBag.status == bag_status)
    if source_department:
        stmt = stmt.where(BiomedicalWasteBag.source_department == source_department.strip().upper())

    bags = db.execute(stmt.offset(offset).limit(limit)).scalars().all()
    return [BMWBagResponse.from_orm_model(b) for b in bags]


@bmw_router.patch("/bags/{barcode}/store", response_model=BMWBagResponse)
def move_bag_to_storage(
    barcode: str,
    db: Session = Depends(get_db),
    actor: User = Depends(RequirePermission("service_ops:write")),
):
    """Moves a logged waste bag to the hospital's central bio-medical waste storage room."""
    bag = db.execute(
        select(BiomedicalWasteBag).where(BiomedicalWasteBag.barcode == barcode.strip())
    ).scalar_one_or_none()
    if not bag:
        raise HTTPException(status_code=404, detail="Biomedical waste bag not found.")
    if bag.status == "HANDED_OVER":
        raise HTTPException(status_code=409, detail="Bag has already been handed over to CBWTF.")

    bag.status = "STORED"
    bag.stored_at = datetime.now(timezone.utc)
    bag.updated_by_id = actor.id
    db.commit()
    db.refresh(bag)
    return BMWBagResponse.from_orm_model(bag)


@bmw_router.post("/manifests", response_model=BMWManifestResponse, status_code=status.HTTP_201_CREATED)
def create_cbwtf_handover_manifest(
    payload: BMWManifestCreate,
    db: Session = Depends(get_db),
    actor: User = Depends(RequirePermission("service_ops:write")),
):
    """
    Creates a formal handover manifest for the Common Bio-medical Waste Treatment Facility (CBWTF).
    Transitions all included bags to 'HANDED_OVER' status and audits aggregate weights per colour code.
    """
    identifiers = [b.strip() for b in payload.bag_identifiers if b.strip()]
    if not identifiers:
        raise HTTPException(status_code=422, detail="At least one bag identifier is required.")

    # Find matching bags by barcode or id
    bags = db.execute(
        select(BiomedicalWasteBag).where(
            BiomedicalWasteBag.barcode.in_(identifiers)
            | BiomedicalWasteBag.id.in_([int(i) for i in identifiers if i.isdigit()])
        )
    ).scalars().all()

    if len(bags) != len(set(identifiers)):
        found_barcodes = {b.barcode for b in bags}.union({str(b.id) for b in bags})
        missing = set(identifiers) - found_barcodes
        raise HTTPException(
            status_code=404,
            detail=f"One or more waste bags could not be found: {list(missing)[:5]}",
        )

    for b in bags:
        if b.status == "HANDED_OVER":
            raise HTTPException(
                status_code=409,
                detail=f"Waste bag '{b.barcode}' has already been handed over on manifest {b.manifest_id}.",
            )

    now = datetime.now(timezone.utc)
    manifest_num = _generate_manifest_number()

    # Calculate category weight totals
    yellow_wt = sum(b.weight_grams for b in bags if b.color_category == "YELLOW")
    red_wt = sum(b.weight_grams for b in bags if b.color_category == "RED")
    white_wt = sum(b.weight_grams for b in bags if b.color_category == "WHITE")
    blue_wt = sum(b.weight_grams for b in bags if b.color_category == "BLUE")
    total_wt = yellow_wt + red_wt + white_wt + blue_wt

    manifest = BiomedicalWasteManifest(
        manifest_number=manifest_num,
        cbwtf_operator_name=payload.cbwtf_operator_name.strip(),
        cbwtf_vehicle_number=payload.cbwtf_vehicle_number.strip().upper(),
        cbwtf_driver_name=payload.cbwtf_driver_name.strip(),
        total_bags_count=len(bags),
        total_weight_grams=total_wt,
        yellow_weight_grams=yellow_wt,
        red_weight_grams=red_wt,
        white_weight_grams=white_wt,
        blue_weight_grams=blue_wt,
        supervisor_user_id=actor.id,
        pickup_at=now,
        notes=payload.notes,
        created_by_id=actor.id,
        updated_by_id=actor.id,
    )
    db.add(manifest)
    db.flush()

    for b in bags:
        b.status = "HANDED_OVER"
        b.manifest_id = manifest.id
        b.handed_over_at = now
        b.updated_by_id = actor.id

    db.commit()
    db.refresh(manifest)

    return BMWManifestResponse(
        id=manifest.id,
        manifest_number=manifest.manifest_number,
        cbwtf_operator_name=manifest.cbwtf_operator_name,
        cbwtf_vehicle_number=manifest.cbwtf_vehicle_number,
        cbwtf_driver_name=manifest.cbwtf_driver_name,
        total_bags_count=manifest.total_bags_count,
        total_weight_grams=manifest.total_weight_grams,
        total_weight_kg=round(manifest.total_weight_grams / 1000.0, 3),
        yellow_weight_grams=manifest.yellow_weight_grams,
        red_weight_grams=manifest.red_weight_grams,
        white_weight_grams=manifest.white_weight_grams,
        blue_weight_grams=manifest.blue_weight_grams,
        supervisor_user_id=manifest.supervisor_user_id,
        pickup_at=manifest.pickup_at,
        notes=manifest.notes,
    )


@bmw_router.get("/manifests", response_model=List[BMWManifestResponse])
def list_manifests(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("service_ops:read")),
):
    """Lists bio-medical waste handover manifests."""
    manifests = db.execute(
        select(BiomedicalWasteManifest)
        .order_by(BiomedicalWasteManifest.id.desc())
        .offset(offset)
        .limit(limit)
    ).scalars().all()

    return [
        BMWManifestResponse(
            id=m.id,
            manifest_number=m.manifest_number,
            cbwtf_operator_name=m.cbwtf_operator_name,
            cbwtf_vehicle_number=m.cbwtf_vehicle_number,
            cbwtf_driver_name=m.cbwtf_driver_name,
            total_bags_count=m.total_bags_count,
            total_weight_grams=m.total_weight_grams,
            total_weight_kg=round(m.total_weight_grams / 1000.0, 3),
            yellow_weight_grams=m.yellow_weight_grams,
            red_weight_grams=m.red_weight_grams,
            white_weight_grams=m.white_weight_grams,
            blue_weight_grams=m.blue_weight_grams,
            supervisor_user_id=m.supervisor_user_id,
            pickup_at=m.pickup_at,
            notes=m.notes,
        )
        for m in manifests
    ]


@bmw_router.get("/manifests/{manifest_id}")
def get_manifest_detail(
    manifest_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("service_ops:read")),
):
    """Retrieves manifest details and all associated barcoded bags."""
    manifest = db.get(BiomedicalWasteManifest, manifest_id)
    if not manifest:
        raise HTTPException(status_code=404, detail="Biomedical waste manifest not found.")

    bags = db.execute(
        select(BiomedicalWasteBag).where(BiomedicalWasteBag.manifest_id == manifest.id)
    ).scalars().all()

    return {
        "manifest": BMWManifestResponse(
            id=manifest.id,
            manifest_number=manifest.manifest_number,
            cbwtf_operator_name=manifest.cbwtf_operator_name,
            cbwtf_vehicle_number=manifest.cbwtf_vehicle_number,
            cbwtf_driver_name=manifest.cbwtf_driver_name,
            total_bags_count=manifest.total_bags_count,
            total_weight_grams=manifest.total_weight_grams,
            total_weight_kg=round(manifest.total_weight_grams / 1000.0, 3),
            yellow_weight_grams=manifest.yellow_weight_grams,
            red_weight_grams=manifest.red_weight_grams,
            white_weight_grams=manifest.white_weight_grams,
            blue_weight_grams=manifest.blue_weight_grams,
            supervisor_user_id=manifest.supervisor_user_id,
            pickup_at=manifest.pickup_at,
            notes=manifest.notes,
        ),
        "bags": [BMWBagResponse.from_orm_model(b) for b in bags],
    }


@bmw_router.get("/compliance/summary")
def get_bmw_compliance_summary(
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("service_ops:read")),
):
    """
    Computes State / Central Pollution Control Board (SPCB / CPCB) annual/monthly compliance summary.
    Aggregates waste weight and bag counts by colour category and source department.
    """
    stmt = select(BiomedicalWasteBag)
    if from_date:
        stmt = stmt.where(BiomedicalWasteBag.logged_at >= datetime.combine(from_date, time.min, tzinfo=timezone.utc))
    if to_date:
        stmt = stmt.where(BiomedicalWasteBag.logged_at <= datetime.combine(to_date, time.max, tzinfo=timezone.utc))

    bags = db.execute(stmt).scalars().all()

    categories = {"YELLOW": 0, "RED": 0, "WHITE": 0, "BLUE": 0}
    category_counts = {"YELLOW": 0, "RED": 0, "WHITE": 0, "BLUE": 0}
    dept_weights: dict[str, int] = {}
    handed_over_weight = 0

    for b in bags:
        wt = b.weight_grams
        categories[b.color_category] = categories.get(b.color_category, 0) + wt
        category_counts[b.color_category] = category_counts.get(b.color_category, 0) + 1
        dept_weights[b.source_department] = dept_weights.get(b.source_department, 0) + wt
        if b.status == "HANDED_OVER":
            handed_over_weight += wt

    total_weight = sum(categories.values())

    return {
        "period": {
            "from_date": from_date.isoformat() if from_date else "ALL",
            "to_date": to_date.isoformat() if to_date else "ALL",
        },
        "total_bags_generated": len(bags),
        "total_weight_grams": total_weight,
        "total_weight_kg": round(total_weight / 1000.0, 3),
        "total_handed_over_grams": handed_over_weight,
        "total_handed_over_kg": round(handed_over_weight / 1000.0, 3),
        "by_category": {
            cat: {
                "weight_grams": wt,
                "weight_kg": round(wt / 1000.0, 3),
                "bag_count": category_counts[cat],
                "percentage_of_total": round((wt / total_weight * 100.0), 2) if total_weight > 0 else 0.0,
            }
            for cat, wt in categories.items()
        },
        "by_department": {
            dept: {
                "weight_grams": wt,
                "weight_kg": round(wt / 1000.0, 3),
            }
            for dept, wt in dept_weights.items()
        },
        "statutory_rules": "Bio-Medical Waste Management Rules 2016 (CPCB/SPCB Compliance)",
    }

