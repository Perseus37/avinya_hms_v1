import csv
import io
import json
from typing import Any, Dict, List, Literal, Optional
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from database import get_db
from dependencies import RequirePermission
from models import (
    Bed,
    BedStatusEnum,
    BedTypeEnum,
    Department,
    FederationPolicyEnum,
    Floor,
    HospitalPolicySetting,
    MedicationMaster,
    OTTheatreTypeEnum,
    OperationTheatre,
    PhysicalRoom,
    RoomTypeEnum,
    ServiceCatalog,
    User,
    Ward,
    WardTypeEnum,
)

router = APIRouter(prefix="/api/admin", tags=["Tenant Administration Operations"])


def get_or_create_policy(db: Session) -> HospitalPolicySetting:
    """Returns the current hospital's operational & federation policy settings."""
    setting = db.execute(select(HospitalPolicySetting)).scalars().first()
    if not setting:
        setting = HospitalPolicySetting()
        db.add(setting)
        db.flush()
    return setting


# =====================================================================
# FEATURE 4: BULK SPATIAL INFRASTRUCTURE GENERATOR
# =====================================================================

class BedCountSpec(BaseModel):
    prefix: str = "BED"
    start_number: int = 1
    count: int = Field(ge=1, le=50)
    bed_type: BedTypeEnum = BedTypeEnum.STANDARD
    base_daily_rate_paise: int = Field(ge=0)


class RoomBatchSpec(BaseModel):
    room_number_prefix: str = "10"
    rooms_count: int = Field(ge=1, le=50)
    room_type: RoomTypeEnum = RoomTypeEnum.DOUBLE
    beds: BedCountSpec


class WardBatchSpec(BaseModel):
    code: str = Field(min_length=2, max_length=20)
    name: str = Field(min_length=2, max_length=100)
    ward_type: WardTypeEnum = WardTypeEnum.GENERAL
    department_code: str
    rooms: List[RoomBatchSpec]


class FloorBatchSpec(BaseModel):
    floor_number: int
    name: str
    wards: List[WardBatchSpec]


class OTBatchSpec(BaseModel):
    code: str
    name: str
    theatre_type: OTTheatreTypeEnum = OTTheatreTypeEnum.MODULAR_MAJOR
    hourly_rate_paise: int = 500000


class BulkSpatialMatrixRequest(BaseModel):
    floors: List[FloorBatchSpec]
    operation_theatres: List[OTBatchSpec] = []


@router.post("/spatial-matrix", status_code=status.HTTP_201_CREATED)
def generate_hospital_spatial_matrix(
    payload: BulkSpatialMatrixRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("wards:write", "iam:write")),
):
    """Hospital Admin Tool: Atomic bulk generation of floors, wards, rooms, beds, and theatres."""
    created = {"floors": 0, "wards": 0, "rooms": 0, "beds": 0, "theatres": 0}
    depts = {d.code: d for d in db.query(Department).all()}

    for f_spec in payload.floors:
        floor = Floor(
            floor_number=f_spec.floor_number,
            name=f_spec.name,
            is_active=True,
            created_by_id=current_user.id,
        )
        db.add(floor)
        db.flush()
        created["floors"] += 1

        for w_spec in f_spec.wards:
            dept = depts.get(w_spec.department_code)
            if not dept:
                raise HTTPException(status_code=400, detail=f"Department code '{w_spec.department_code}' not found.")

            ward = Ward(
                floor_id=floor.id,
                department_id=dept.id,
                code=w_spec.code,
                name=w_spec.name,
                ward_type=w_spec.ward_type,
                is_active=True,
                created_by_id=current_user.id,
            )
            db.add(ward)
            db.flush()
            created["wards"] += 1

            for r_spec in w_spec.rooms:
                for r_idx in range(1, r_spec.rooms_count + 1):
                    room_no = f"{r_spec.room_number_prefix}{r_idx:02d}"
                    room = PhysicalRoom(
                        ward_id=ward.id,
                        room_number=room_no,
                        room_type=r_spec.room_type,
                        bed_capacity=r_spec.beds.count,
                        is_active=True,
                        created_by_id=current_user.id,
                    )
                    db.add(room)
                    db.flush()
                    created["rooms"] += 1

                    for b_num in range(r_spec.beds.start_number, r_spec.beds.start_number + r_spec.beds.count):
                        bed = Bed(
                            room_id=room.id,
                            bed_number=f"{room_no}-{r_spec.beds.prefix}-{b_num:02d}",
                            bed_type=r_spec.beds.bed_type,
                            status=BedStatusEnum.AVAILABLE,
                            base_daily_rate_paise=r_spec.beds.base_daily_rate_paise,
                            created_by_id=current_user.id,
                        )
                        db.add(bed)
                        created["beds"] += 1

    for ot_spec in payload.operation_theatres:
        ot = OperationTheatre(
            code=ot_spec.code,
            name=ot_spec.name,
            theatre_type=ot_spec.theatre_type,
            hourly_rate_paise=ot_spec.hourly_rate_paise,
            is_active=True,
            created_by_id=current_user.id,
        )
        db.add(ot)
        created["theatres"] += 1

    db.commit()
    return {"status": "success", "summary": created}


# =====================================================================
# FEATURE 5: HOSPITAL POLICY & FEDERATION SOVEREIGNTY CONFIGURATOR
# =====================================================================

class HospitalPolicyUpdate(BaseModel):
    max_failed_login_attempts: Optional[int] = Field(default=None, ge=3, le=10)
    session_timeout_minutes: Optional[int] = Field(default=None, ge=5, le=480)
    default_consultation_duration_minutes: Optional[int] = Field(default=None, ge=5, le=60)
    allow_overbooking: Optional[bool] = None
    enforce_financial_discharge_gate: Optional[bool] = None
    allow_unfinalized_interim_payments: Optional[bool] = None
    fefo_expiry_warning_days: Optional[int] = Field(default=None, ge=7, le=180)
    low_stock_threshold_units: Optional[int] = Field(default=None, ge=1, le=500)
    o_negative_critical_threshold_units: Optional[int] = Field(default=None, ge=1, le=20)
    federation_sharing_mode: Optional[FederationPolicyEnum] = None
    federation_share_demographics: Optional[bool] = None
    federation_share_allergies: Optional[bool] = None


@router.get("/policies")
def get_hospital_policies(
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("iam:read")),
):
    setting = get_or_create_policy(db)
    db.commit()
    db.refresh(setting)
    return setting


@router.put("/policies")
def update_hospital_policies(
    payload: HospitalPolicyUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("iam:write")),
):
    setting = get_or_create_policy(db)
    for field, val in payload.model_dump(exclude_unset=True).items():
        setattr(setting, field, val)

    setting.updated_by_id = current_user.id
    db.commit()
    db.refresh(setting)
    return setting


# =====================================================================
# FEATURE 6: MASTER DATA BULK IMPORTER (JSON & CSV)
# =====================================================================

class BulkRowDetail(BaseModel):
    row_index: int
    identifier: str
    status: Literal["CREATED", "UPDATED", "SKIPPED", "ERROR"]
    message: str


class BulkImportSummaryResponse(BaseModel):
    entity: str
    total_processed: int
    success_count: int
    error_count: int
    details: List[BulkRowDetail]


def _parse_file_rows(filename: str, content: bytes) -> List[Dict[str, Any]]:
    if filename.endswith(".json"):
        return json.loads(content.decode("utf-8"))
    stream = io.StringIO(content.decode("utf-8-sig"))
    return list(csv.DictReader(stream))


@router.post("/bulk-import/service-catalog", response_model=BulkImportSummaryResponse)
async def bulk_import_services(
    file: UploadFile = File(...),
    upsert: bool = Query(default=True),
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("mdm:write", "billing:write")),
):
    """Imports service catalog price lists from a CSV or JSON file."""
    rows = _parse_file_rows(file.filename or "data.csv", await file.read())
    details: List[BulkRowDetail] = []
    success = 0
    errors = 0

    for idx, r in enumerate(rows, start=1):
        code = str(r.get("code", "")).strip().upper()
        name = str(r.get("name", "")).strip()
        price = int(r.get("base_price_paise", 0))

        if not code or not name:
            details.append(BulkRowDetail(row_index=idx, identifier=code, status="ERROR", message="Missing code or name"))
            errors += 1
            continue

        existing = db.execute(select(ServiceCatalog).where(ServiceCatalog.code == code)).scalar_one_or_none()
        if existing:
            if upsert:
                existing.name = name
                existing.base_price_paise = price
                existing.updated_by_id = current_user.id
                details.append(BulkRowDetail(row_index=idx, identifier=code, status="UPDATED", message="Updated existing item"))
                success += 1
            else:
                details.append(BulkRowDetail(row_index=idx, identifier=code, status="SKIPPED", message="Item already exists"))
        else:
            item = ServiceCatalog(
                category_id=int(r.get("category_id", 1)),
                code=code,
                name=name,
                service_type=r.get("service_type", "PROCEDURE"),
                base_price_paise=price,
                is_order_set=str(r.get("is_order_set", "false")).lower() in ("true", "1"),
                is_active=True,
                created_by_id=current_user.id,
            )
            db.add(item)
            details.append(BulkRowDetail(row_index=idx, identifier=code, status="CREATED", message="Created new catalog item"))
            success += 1

    db.commit()
    return BulkImportSummaryResponse(
        entity="service_catalog", total_processed=len(rows), success_count=success, error_count=errors, details=details
    )


@router.post("/bulk-import/medications", response_model=BulkImportSummaryResponse)
async def bulk_import_medications(
    file: UploadFile = File(...),
    upsert: bool = Query(default=True),
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("pharmacy:write", "mdm:write")),
):
    """Imports pharmacy formulary medications from a CSV or JSON file."""
    rows = _parse_file_rows(file.filename or "meds.csv", await file.read())
    details: List[BulkRowDetail] = []
    success = 0
    errors = 0

    for idx, r in enumerate(rows, start=1):
        code = str(r.get("code", "")).strip().upper()
        brand = str(r.get("brand_name", "")).strip()

        if not code or not brand:
            details.append(BulkRowDetail(row_index=idx, identifier=code, status="ERROR", message="Missing code or brand_name"))
            errors += 1
            continue

        existing = db.execute(select(MedicationMaster).where(MedicationMaster.code == code)).scalar_one_or_none()
        if existing:
            if upsert:
                existing.brand_name = brand
                existing.generic_name = r.get("generic_name", existing.generic_name)
                existing.strength = r.get("strength", existing.strength)
                existing.updated_by_id = current_user.id
                details.append(BulkRowDetail(row_index=idx, identifier=code, status="UPDATED", message="Updated formulary item"))
                success += 1
            else:
                details.append(BulkRowDetail(row_index=idx, identifier=code, status="SKIPPED", message="Medication already exists"))
        else:
            med = MedicationMaster(
                code=code,
                brand_name=brand,
                generic_name=r.get("generic_name", "Generic"),
                form=r.get("form", "TABLET"),
                strength=r.get("strength", "Standard"),
                is_active=True,
                created_by_id=current_user.id,
            )
            db.add(med)
            details.append(BulkRowDetail(row_index=idx, identifier=code, status="CREATED", message="Created formulary item"))
            success += 1

    db.commit()
    return BulkImportSummaryResponse(
        entity="medications", total_processed=len(rows), success_count=success, error_count=errors, details=details
    )