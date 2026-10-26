from datetime import date
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from backend.core.database import get_db, resolve_tenant_context
from backend.core.date_ranges import resolve_record_date_window
from backend.core.dependencies import RequirePermission, user_has_permission, validate_pagination
from backend.domains.identity.models import User
from backend.domains.patients.models import Patient
from backend.domains.diagnostics.models import (
    DiagnosticOrder,
    DiagnosticTask,
    LabDeltaCheckConfig,
    OrderStatusEnum,
    ServiceBundleMapping,
    ServiceCatalog,
    ServiceCategory,
    TaskStatusEnum,
)
from backend.schemas.common import PaginatedResponse
from backend.domains.diagnostics.schemas import (
    DiagnosticOrderCreate,
    DiagnosticOrderResponse,
    DiagnosticOrderUpdate,
    DiagnosticTaskResponse,
    LabDeltaCheckConfigCreate,
    LabDeltaCheckConfigResponse,
    ServiceBundleMappingCreate,
    ServiceBundleMappingResponse,
    ServiceCatalogCreate,
    ServiceCatalogResponse,
    ServiceCatalogUpdate,
    ServiceCategoryCreate,
    ServiceCategoryResponse,
    ServiceCategoryUpdate,
    TaskResultEntry,
)
from backend.domains.diagnostics.services import explode_clinical_orders
from backend.domains.billing.services import (
    auto_bill_diagnostic_order,
    reverse_cancelled_diagnostic_order_billing,
)   
from backend.core.event_bus import publish_tenant_event
from backend.domains.privacy.runtime import evaluate_tenant_patient_data_uses

router = APIRouter(prefix="/api/diagnostics", tags=["Diagnostic Services & LIS"])


# -----------------------------------------------------------------------------
# Service Master Data Domain (Catalog & Bundles)
# -----------------------------------------------------------------------------
@router.post("/categories", response_model=ServiceCategoryResponse, status_code=status.HTTP_201_CREATED)
def create_service_category(
    payload: ServiceCategoryCreate,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("mdm:write")),
):
    existing = db.query(ServiceCategory).filter(ServiceCategory.code == payload.code).first()
    if existing:
        raise HTTPException(status_code=409, detail="Service category code already exists.")
    category = ServiceCategory(**payload.model_dump(), is_active=True)
    db.add(category)
    db.commit()
    db.refresh(category)
    return category


@router.get("/categories", response_model=PaginatedResponse[ServiceCategoryResponse])
def list_service_categories(
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("diagnostics:read", "mdm:read")),
):
    page, page_size = validate_pagination(page, page_size)
    query = db.query(ServiceCategory).filter(ServiceCategory.is_active == True).order_by(ServiceCategory.id.asc())
    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/categories/{category_id}", response_model=ServiceCategoryResponse)
def get_service_category(
    category_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("diagnostics:read", "mdm:read")),
):
    category = db.get(ServiceCategory, category_id)
    if not category or not category.is_active:
        raise HTTPException(status_code=404, detail="Active service category not found")
    return category


@router.put("/categories/{category_id}", response_model=ServiceCategoryResponse, status_code=200)
def update_service_category(
    category_id: int,
    payload: ServiceCategoryUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("mdm:write")),
):
    category = db.get(ServiceCategory, category_id)
    if not category:
        raise HTTPException(status_code=404, detail="Service category not found")
    if payload.name is not None:
        category.name = payload.name
    if payload.is_active is not None:
        category.is_active = payload.is_active
    db.commit()
    db.refresh(category)
    return category


@router.delete("/categories/{category_id}", status_code=200)
def deactivate_service_category(
    category_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("mdm:delete")),
):
    category = db.get(ServiceCategory, category_id)
    if not category:
        raise HTTPException(status_code=404, detail="Service category not found")
    category.is_active = False
    db.commit()
    return {"status": "success", "message": f"Category ID {category_id} deactivated."}


@router.post("/catalog", response_model=ServiceCatalogResponse, status_code=status.HTTP_201_CREATED)
def create_service_catalog_item(
    payload: ServiceCatalogCreate,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("mdm:write")),
):
    existing = db.query(ServiceCatalog).filter(ServiceCatalog.code == payload.code).first()
    if existing:
        raise HTTPException(status_code=409, detail="Service catalog code already exists.")
    item = ServiceCatalog(**payload.model_dump(), is_active=True)
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.get("/catalog", response_model=PaginatedResponse[ServiceCatalogResponse])
def list_service_catalog(
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("diagnostics:read", "mdm:read", "billing:read")),
):
    page, page_size = validate_pagination(page, page_size)
    query = db.query(ServiceCatalog).filter(ServiceCatalog.is_active == True).order_by(ServiceCatalog.id.asc())
    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/catalog/{item_id}", response_model=ServiceCatalogResponse)
def get_service_catalog_item(
    item_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("diagnostics:read", "mdm:read", "billing:read")),
):
    item = db.get(ServiceCatalog, item_id)
    if not item or not item.is_active:
        raise HTTPException(status_code=404, detail="Active service catalog item not found")
    return item


@router.put("/catalog/{item_id}", response_model=ServiceCatalogResponse, status_code=200)
def update_service_catalog_item(
    item_id: int,
    payload: ServiceCatalogUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("mdm:write")),
):
    item = db.get(ServiceCatalog, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Service catalog item not found")
    if payload.name is not None:
        item.name = payload.name
    if payload.base_price_paise is not None:
        item.base_price_paise = payload.base_price_paise
    if payload.is_active is not None:
        item.is_active = payload.is_active
    db.commit()
    db.refresh(item)
    return item


@router.delete("/catalog/{item_id}", status_code=200)
def deactivate_service_catalog_item(
    item_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("mdm:delete")),
):
    item = db.get(ServiceCatalog, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Service catalog item not found")
    item.is_active = False
    db.commit()
    return {"status": "success", "message": f"Service Catalog ID {item_id} deactivated."}


@router.post("/catalog/bundle", status_code=status.HTTP_201_CREATED)
def map_bundle_child(
    payload: ServiceBundleMappingCreate,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("mdm:write")),
):
    if payload.parent_service_id == payload.child_service_id:
        raise HTTPException(status_code=400, detail="A bundle cannot reference itself as a child service.")
    if payload.quantity < 1:
        raise HTTPException(status_code=400, detail="Bundle child quantity must be at least 1.")

    parent = db.get(ServiceCatalog, payload.parent_service_id)
    if not parent or not parent.is_active or not parent.is_order_set:
        raise HTTPException(status_code=400, detail="Parent service is invalid, inactive, or not marked as an order set.")

    child = db.get(ServiceCatalog, payload.child_service_id)
    if not child or not child.is_active:
        raise HTTPException(status_code=400, detail="Child service is invalid or inactive.")

    existing = (
        db.query(ServiceBundleMapping)
        .filter(
            ServiceBundleMapping.parent_service_id == payload.parent_service_id,
            ServiceBundleMapping.child_service_id == payload.child_service_id,
        )
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="Child service is already mapped to this bundle.")

    mapping = ServiceBundleMapping(**payload.model_dump())
    db.add(mapping)
    db.commit()
    return {"status": "success", "message": "Service successfully mapped to bundle."}


@router.get("/catalog/{parent_id}/bundles", response_model=list[ServiceBundleMappingResponse])
def get_bundle_mappings(
    parent_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("diagnostics:read", "mdm:read")),
):
    parent = db.get(ServiceCatalog, parent_id)
    if not parent or not parent.is_order_set:
        raise HTTPException(status_code=404, detail="Parent package not found or is not an order set.")

    stmt = (
        select(ServiceBundleMapping)
        .options(joinedload(ServiceBundleMapping.child_service))
        .where(ServiceBundleMapping.parent_service_id == parent_id)
    )
    return db.execute(stmt).scalars().all()


@router.delete("/catalog/bundle/{parent_id}/{child_id}", status_code=200)
def remove_bundle_mapping(
    parent_id: int,
    child_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("mdm:delete")),
):
    stmt = select(ServiceBundleMapping).where(
        ServiceBundleMapping.parent_service_id == parent_id,
        ServiceBundleMapping.child_service_id == child_id,
    )
    mapping = db.execute(stmt).scalar_one_or_none()
    if not mapping:
        raise HTTPException(status_code=404, detail="Bundle mapping not found.")

    db.delete(mapping)
    db.commit()
    return {"status": "success", "message": "Service successfully removed from bundle."}


# -----------------------------------------------------------------------------
# Diagnostic CPOE & Execution Queues
# -----------------------------------------------------------------------------
@router.post("/orders", response_model=DiagnosticOrderResponse, status_code=status.HTTP_201_CREATED)
def place_diagnostic_order(
    payload: DiagnosticOrderCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("diagnostics:write")),
):
    patient = db.get(Patient, payload.patient_id)
    if not patient or not patient.is_active:
        raise HTTPException(status_code=404, detail="Active patient not found.")

    executable_tasks_dict = explode_clinical_orders(db, payload.ordered_service_ids)

    order = DiagnosticOrder(
        patient_id=payload.patient_id,
        ordering_doctor_id=current_user.id,
        clinical_notes=payload.clinical_notes,
        status=OrderStatusEnum.ACTIVE,
    )
    db.add(order)
    db.flush()

    tasks = []
    for service_id, quantity in executable_tasks_dict.items():
        for _ in range(quantity):
            tasks.append(
                DiagnosticTask(
                    order_id=order.id,
                    service_id=service_id,
                    status=TaskStatusEnum.PENDING,
                )
            )

    db.add_all(tasks)
    auto_bill_diagnostic_order(db, order, payload.ordered_service_ids)
    db.commit()
    db.refresh(order)
    return order


@router.get(
    "/orders",
    response_model=PaginatedResponse[DiagnosticOrderResponse],
    openapi_extra={"x-privacy-data-use-point": "diagnostics.orders.search"},
)
def list_diagnostic_orders(
    patient_id: Optional[int] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db),
    actor: User = Depends(RequirePermission("diagnostics:read")),
):
    page, page_size = validate_pagination(page, page_size)
    start_at, end_at = resolve_record_date_window(start_date, end_date)
    query = db.query(DiagnosticOrder).options(joinedload(DiagnosticOrder.tasks))
    if patient_id:
        query = query.filter(DiagnosticOrder.patient_id == patient_id)
    if start_at is not None and end_at is not None:
        query = query.filter(
            DiagnosticOrder.created_at >= start_at,
            DiagnosticOrder.created_at < end_at,
        )
    query = query.order_by(DiagnosticOrder.id.desc())

    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    decisions = evaluate_tenant_patient_data_uses(
        db,
        data_use_point="diagnostics.orders.search",
        patient_ids=[item.patient_id for item in items],
        actor_user_id=actor.id,
    )
    if any(not decision.allowed for decision in decisions):
        raise HTTPException(status_code=403, detail="Privacy policy does not permit one or more patient disclosures.")
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get(
    "/orders/{order_id}",
    response_model=DiagnosticOrderResponse,
    openapi_extra={"x-privacy-data-use-point": "diagnostics.orders.read"},
)
def get_diagnostic_order(
    order_id: int,
    db: Session = Depends(get_db),
    actor: User = Depends(RequirePermission("diagnostics:read")),
):
    order = (
        db.query(DiagnosticOrder)
        .options(joinedload(DiagnosticOrder.tasks))
        .filter(DiagnosticOrder.id == order_id)
        .first()
    )
    if not order:
        raise HTTPException(status_code=404, detail="Diagnostic order not found.")
    decisions = evaluate_tenant_patient_data_uses(
        db,
        data_use_point="diagnostics.orders.read",
        patient_ids=[order.patient_id],
        actor_user_id=actor.id,
    )
    if any(not decision.allowed for decision in decisions):
        raise HTTPException(status_code=403, detail="Privacy policy does not permit this patient disclosure.")
    return order


@router.put("/orders/{order_id}", response_model=DiagnosticOrderResponse)
def update_diagnostic_order(
    order_id: int,
    payload: DiagnosticOrderUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("diagnostics:write")),
):
    order = db.get(DiagnosticOrder, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Diagnostic order not found.")
    if order.status in [OrderStatusEnum.COMPLETED, OrderStatusEnum.CANCELLED]:
        raise HTTPException(status_code=400, detail="Cannot modify a completed or cancelled diagnostic order.")

    if payload.clinical_notes is not None:
        order.clinical_notes = payload.clinical_notes
    if payload.status is not None:
        order.status = payload.status
    db.commit()
    db.refresh(order)
    return order


@router.delete("/orders/{order_id}", status_code=200)
def cancel_diagnostic_order(
    order_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("diagnostics:verify", "diagnostics:write")),
):
    order = db.query(DiagnosticOrder).options(joinedload(DiagnosticOrder.tasks)).filter(DiagnosticOrder.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Diagnostic order not found.")
    if order.status in [OrderStatusEnum.COMPLETED, OrderStatusEnum.CANCELLED]:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel a diagnostic order in '{order.status.value}' state.",
        )

    order.status = OrderStatusEnum.CANCELLED
    for task in order.tasks:
        if task.status in [TaskStatusEnum.PENDING, TaskStatusEnum.SAMPLE_COLLECTED, TaskStatusEnum.IN_PROGRESS]:
            task.status = TaskStatusEnum.CANCELLED

    reverse_cancelled_diagnostic_order_billing(db, order)
    db.commit()
    return {"status": "success", "message": f"Order {order_id} and associated pending tasks cancelled."}

@router.get(
    "/orders/{order_id}/tasks",
    response_model=PaginatedResponse[DiagnosticTaskResponse],
    openapi_extra={"x-privacy-data-use-point": "diagnostics.order.tasks.read"},
)
def list_order_tasks(
    order_id: int,
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db),
    actor: User = Depends(RequirePermission("diagnostics:read")),
):
    page, page_size = validate_pagination(page, page_size)
    query = db.query(DiagnosticTask).filter(DiagnosticTask.order_id == order_id).order_by(DiagnosticTask.id.asc())
    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    order = db.get(DiagnosticOrder, order_id)
    if order is not None:
        decisions = evaluate_tenant_patient_data_uses(
            db,
            data_use_point="diagnostics.order.tasks.read",
            patient_ids=[order.patient_id],
            actor_user_id=actor.id,
        )
        if any(not decision.allowed for decision in decisions):
            raise HTTPException(status_code=403, detail="Privacy policy does not permit this patient disclosure.")
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get(
    "/tasks/pending",
    response_model=PaginatedResponse[DiagnosticTaskResponse],
    openapi_extra={"x-privacy-data-use-point": "diagnostics.tasks.pending.read"},
)
def get_department_task_queue(
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db),
    actor: User = Depends(RequirePermission("diagnostics:read")),
):
    page, page_size = validate_pagination(page, page_size)
    query = (
        db.query(DiagnosticTask)
        .filter(DiagnosticTask.status == TaskStatusEnum.PENDING)
        .order_by(DiagnosticTask.id.asc())
    )
    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    patient_ids = [task.order.patient_id for task in items if task.order is not None]
    decisions = evaluate_tenant_patient_data_uses(
        db,
        data_use_point="diagnostics.tasks.pending.read",
        patient_ids=patient_ids,
        actor_user_id=actor.id,
    )
    if any(not decision.allowed for decision in decisions):
        raise HTTPException(status_code=403, detail="Privacy policy does not permit one or more patient disclosures.")
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.patch("/tasks/{task_id}/result", status_code=200)
def submit_task_result(
    task_id: int,
    payload: TaskResultEntry,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("diagnostics:write", "diagnostics:verify")),
):
    """H8 FIX: Enforces task lifecycle, checks action-specific permission, and prevents self-verification."""
    
    task = (
        db.query(DiagnosticTask)
        .options(
            joinedload(DiagnosticTask.service),
            joinedload(DiagnosticTask.order).joinedload(DiagnosticOrder.tasks),
        )
        .filter(DiagnosticTask.id == task_id)
        .first()
    )
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")
    if not task.order or task.order.status == OrderStatusEnum.CANCELLED:
        raise HTTPException(status_code=400, detail="Cannot submit results for a cancelled order.")
    if task.status in [TaskStatusEnum.VERIFIED, TaskStatusEnum.CANCELLED]:
        raise HTTPException(
            status_code=409,
            detail=f"Task is already {task.status.value} and is locked from further edits.",
        )

    valid_transitions = {
        TaskStatusEnum.PENDING: [
            TaskStatusEnum.SAMPLE_COLLECTED,
            TaskStatusEnum.SAMPLE_REJECTED,
            TaskStatusEnum.IN_PROGRESS,
            TaskStatusEnum.RESULT_ENTERED,
        ],
        TaskStatusEnum.SAMPLE_COLLECTED: [
            TaskStatusEnum.SAMPLE_REJECTED,
            TaskStatusEnum.IN_PROGRESS,
            TaskStatusEnum.RESULT_ENTERED,
        ],
        TaskStatusEnum.SAMPLE_REJECTED: [
            TaskStatusEnum.SAMPLE_COLLECTED,
            TaskStatusEnum.IN_PROGRESS,
        ],
        TaskStatusEnum.IN_PROGRESS: [
            TaskStatusEnum.SAMPLE_REJECTED,
            TaskStatusEnum.RESULT_ENTERED,
        ],
        TaskStatusEnum.RESULT_ENTERED: [
            TaskStatusEnum.RESULT_ENTERED,
            TaskStatusEnum.VERIFIED,
        ],
    }

    if payload.status not in valid_transitions.get(task.status, []):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid lifecycle transition from {task.status.value} to {payload.status.value}.",
        )

    if payload.status == TaskStatusEnum.VERIFIED:
        if not user_has_permission(db, current_user, "diagnostics:verify"):
            raise HTTPException(status_code=403, detail="Access denied: 'diagnostics:verify' permission required to verify results.")
        if task.performed_by_id is None:
            raise HTTPException(status_code=400, detail="Task must have entered results before verification.")
        if task.performed_by_id == current_user.id and not current_user.is_superuser:
            raise HTTPException(
                status_code=403,
                detail="Separation of duties violation: The user who performed the test cannot verify it.",
            )
        task.verified_by_id = current_user.id
        task.status = TaskStatusEnum.VERIFIED
    else:
        if not user_has_permission(db, current_user, "diagnostics:write"):
            raise HTTPException(status_code=403, detail="Access denied: 'diagnostics:write' permission required to enter results.")
        if payload.status == TaskStatusEnum.SAMPLE_REJECTED:
            if not payload.rejection_reason or len(payload.rejection_reason.strip()) < 3:
                raise HTTPException(
                    status_code=400,
                    detail="A valid rejection_reason is mandatory when marking a sample as SAMPLE_REJECTED.",
                )
            task.rejection_reason = payload.rejection_reason
            # CLIN-002: Wipe previous results and panic flags if a sample is rejected
            task.result_value = None
            task.result_notes = None
            task.is_critical_panic = False
        elif payload.status == TaskStatusEnum.RESULT_ENTERED:
            if not payload.result_value:
                raise HTTPException(status_code=400, detail="result_value is required when status is RESULT_ENTERED.")
            task.result_value = payload.result_value
            task.result_notes = payload.result_notes
            task.is_critical_panic = payload.is_critical_panic
            task.performed_by_id = current_user.id

            # Evaluate LIS Delta Check against patient history
            try:
                from backend.domains.clinical.services import evaluate_lab_delta_check
                delta_eval = evaluate_lab_delta_check(
                    session=db,
                    patient_id=task.order.patient_id,
                    service_id=task.service_id,
                    current_value_str=payload.result_value,
                    current_task_id=task.id,
                )
                task.delta_check_flag = delta_eval.get("delta_flag")
                task.previous_result_value = delta_eval.get("previous_value")
                task.delta_percentage = delta_eval.get("percentage_delta")
                if delta_eval.get("delta_flag") == "DELTA_EXCEEDED":
                    delta_note = delta_eval.get("detail", "")
                    task.result_notes = f"{task.result_notes or ''} [{delta_note}]".strip()
            except Exception:
                pass

        task.status = payload.status

    all_tasks = task.order.tasks
    if all(t.status == TaskStatusEnum.VERIFIED for t in all_tasks):
        task.order.status = OrderStatusEnum.COMPLETED
    elif any(t.status in [TaskStatusEnum.RESULT_ENTERED, TaskStatusEnum.VERIFIED] for t in all_tasks):
        task.order.status = OrderStatusEnum.PARTIAL

    db.commit()

    if payload.status == TaskStatusEnum.RESULT_ENTERED and payload.is_critical_panic:
        tenant = resolve_tenant_context(request)
        publish_tenant_event(
            tenant_slug=tenant.subdomain_slug,
            stream="panic_labs",
            event_type="CRITICAL_PANIC_VALUE_ALERT",
            payload={
                "task_id": task.id,
                "order_id": task.order_id,
                "service_code": task.service.code,
                "service_name": task.service.name,
                "result_value": payload.result_value,
                "result_notes": payload.result_notes,
                "performed_by_id": current_user.id,
            },
        )

    return {
        "status": "success",
        "message": f"Task {task_id} status advanced to {task.status.value}.",
        "task_id": task.id,
        "delta_check_flag": task.delta_check_flag,
        "previous_result_value": task.previous_result_value,
        "delta_percentage": task.delta_percentage,
        "result_notes": task.result_notes,
    }

@router.get(
    "/tasks/panic-alerts",
    response_model=list[DiagnosticTaskResponse],
    openapi_extra={"x-privacy-data-use-point": "diagnostics.tasks.panic.read"},
)
def list_critical_panic_lab_alerts(
    db: Session = Depends(get_db),
    actor: User = Depends(RequirePermission("diagnostics:read", "opd:read", "adt:read")),
):
    """Returns all diagnostic tasks flagged with critical life-threatening panic values."""
    stmt = (
        select(DiagnosticTask)
        .where(DiagnosticTask.is_critical_panic == True)
        .order_by(DiagnosticTask.id.desc())
    )
    tasks = list(db.execute(stmt).scalars().all())
    decisions = evaluate_tenant_patient_data_uses(
        db,
        data_use_point="diagnostics.tasks.panic.read",
        patient_ids=[task.order.patient_id for task in tasks if task.order is not None],
        actor_user_id=actor.id,
    )
    if any(not decision.allowed for decision in decisions):
        raise HTTPException(status_code=403, detail="Privacy policy does not permit one or more patient disclosures.")
    return tasks


# -----------------------------------------------------------------------------
# Enterprise LIS Delta Check Configurations
# -----------------------------------------------------------------------------

@router.post("/delta-configs", response_model=LabDeltaCheckConfigResponse, status_code=status.HTTP_201_CREATED)
def create_or_update_delta_config(
    payload: LabDeltaCheckConfigCreate,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("mdm:write", "diagnostics:write")),
):
    """Configures laboratory delta check thresholds for acute result shift detection."""
    service = db.get(ServiceCatalog, payload.service_id)
    if not service or not service.is_active:
        raise HTTPException(status_code=404, detail="Active service not found in catalog.")

    existing = db.execute(
        select(LabDeltaCheckConfig).where(LabDeltaCheckConfig.service_id == payload.service_id)
    ).scalar_one_or_none()
    if existing:
        existing.parameter_name = payload.parameter_name
        existing.max_absolute_delta = payload.max_absolute_delta
        existing.max_percentage_delta = payload.max_percentage_delta
        existing.lookback_hours = payload.lookback_hours
        existing.severity = payload.severity
        cfg = existing
    else:
        cfg = LabDeltaCheckConfig(**payload.model_dump())
        db.add(cfg)
    db.commit()
    db.refresh(cfg)
    return cfg


@router.get("/delta-configs", response_model=List[LabDeltaCheckConfigResponse])
def list_delta_configs(
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("diagnostics:read", "mdm:read")),
):
    """Lists all configured laboratory delta check thresholds."""
    return list(db.execute(select(LabDeltaCheckConfig).order_by(LabDeltaCheckConfig.id.asc())).scalars().all())
