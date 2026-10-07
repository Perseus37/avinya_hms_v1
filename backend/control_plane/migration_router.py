import csv
import io
import json
import logging
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from control_models import ControlPlaneAuditLog, TenantRegistry
from database import get_control_db, tenant_engine_pool
from control_plane.fleet_ops_router import _verify_vendor_key
from models import Patient, GenderEnum

logger = logging.getLogger("avinya.migration")
router = APIRouter(prefix="/api/migration", tags=["Control Plane Visual ETL Studio"])


@router.post("/{slug}/analyze-csv")
async def analyze_legacy_csv(
    slug: str,
    master_setup_key: str = Form(...),
    file: UploadFile = File(...),
    control_db: Session = Depends(get_control_db),
):
    """Parses the CSV headers to dynamically generate the Source Nodes in React Flow."""
    _verify_vendor_key(master_setup_key)
    
    tenant = control_db.execute(select(TenantRegistry).where(TenantRegistry.subdomain_slug == slug)).scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found.")

    content = await file.read()
    stream = io.StringIO(content.decode("utf-8-sig", errors="ignore"))
    reader = csv.reader(stream)
    
    try:
        headers = next(reader)
    except StopIteration:
        raise HTTPException(status_code=400, detail="CSV file is empty.")

    # Return headers to the frontend so it can render the draggable nodes
    return {
        "status": "success",
        "subdomain_slug": slug,
        "source_columns": [h.strip() for h in headers if h.strip()]
    }


@router.post("/{slug}/execute-node-mapping")
async def execute_visual_node_migration(
    slug: str,
    master_setup_key: str = Form(...),
    entity_type: str = Form(...),
    node_mapping_json: str = Form(...),
    file: UploadFile = File(...),
    control_db: Session = Depends(get_control_db),
):
    """
    Receives the compiled visual wire mappings from React Flow, transforms the CSV rows, 
    and bulk-inserts them securely into the target tenant's database.
    """
    _verify_vendor_key(master_setup_key)
    
    tenant = control_db.execute(select(TenantRegistry).where(TenantRegistry.subdomain_slug == slug)).scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found.")

    try:
        mapping = json.loads(node_mapping_json)  # e.g. {"Legacy_Name_Col": "full_name"}
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid node mapping JSON.")

    content = await file.read()
    stream = io.StringIO(content.decode("utf-8-sig", errors="ignore"))
    reader = csv.DictReader(stream)

    target_engine = tenant_engine_pool.get_engine(tenant.db_name)
    TSession = sessionmaker(bind=target_engine, autocommit=False, autoflush=False)
    
    success_count = 0
    errors = []

    with TSession() as target_db:
        # Optimization: Use bulk inserts for massive files
        batch_objects = []
        
        for row_idx, row in enumerate(reader, start=2): # Start at 2 to account for header
            try:
                if entity_type == "PATIENTS":
                    import uuid
                    from datetime import datetime, timezone
                    
                    # Extract based on React Flow wires
                    mrn_val = row.get(mapping.get("mrn", ""))
                    full_name = row.get(mapping.get("full_name", ""))
                    dob_str = row.get(mapping.get("date_of_birth", ""))
                    phone = row.get(mapping.get("phone_number", ""))
                    raw_gender = str(row.get(mapping.get("gender", ""), "")).strip().upper()
                    
                    if not full_name:
                        raise ValueError("Missing required mapped field: full_name")

                    # Safe DOB Parsing (Fall back to 1900 if dirty/blank data)
                    dob = None
                    if dob_str:
                        try:
                            clean_date = str(dob_str).split("T")[0].split(" ")[0]
                            dob = datetime.fromisoformat(clean_date).replace(tzinfo=timezone.utc)
                        except ValueError:
                            pass
                    if not dob:
                        dob = datetime(1900, 1, 1, tzinfo=timezone.utc)

                    # Transform dirty legacy gender data
                    gender_val = GenderEnum.OTHER
                    if raw_gender in ("M", "MALE", "1"):
                        gender_val = GenderEnum.MALE
                    elif raw_gender in ("F", "FEMALE", "2"):
                        gender_val = GenderEnum.FEMALE

                    batch_objects.append(
                        Patient(
                            mrn=mrn_val if mrn_val else f"MIG-{uuid.uuid4().hex[:8].upper()}",
                            full_name=full_name,
                            date_of_birth=dob,
                            phone_number=phone[:15] if phone else None,
                            gender=gender_val,
                            is_active=True,
                            allow_network_sharing=True,
                            created_by_id=1 # Default Admin ID
                        )
                    )
                else:
                    raise ValueError(f"Unsupported entity type: {entity_type}")

                success_count += 1
                
                # Flush every 1000 records to prevent memory spikes
                if len(batch_objects) >= 1000:
                    target_db.bulk_save_objects(batch_objects)
                    batch_objects.clear()

            except Exception as e:
                errors.append(f"Row {row_idx} failed: {str(e)}")

        # Insert remaining
        if batch_objects:
            target_db.bulk_save_objects(batch_objects)
        
        target_db.commit()

    # Log the migration in the immutable audit vault
    control_db.add(ControlPlaneAuditLog(
        action="ETL_VISUAL_MIGRATION",
        target_slug=slug,
        details=f"Migrated {success_count} {entity_type} records. Errors: {len(errors)}"
    ))
    control_db.commit()

    return {
        "status": "success",
        "records_migrated": success_count,
        "error_count": len(errors),
        "errors": errors[:50] # Return top 50 errors to UI
    }