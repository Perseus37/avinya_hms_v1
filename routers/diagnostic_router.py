from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from database import get_db
from dependencies import RequirePermission, get_current_user
from models import (
    User, ServiceCategory, ServiceCatalog, ServiceBundleMapping, 
    DiagnosticOrder, DiagnosticTask, TaskStatusEnum, OrderStatusEnum
)
from schemas import (
    PaginatedResponse, ServiceCategoryCreate, ServiceCategoryResponse,
    ServiceCatalogCreate, ServiceCatalogResponse, ServiceBundleMappingCreate,
    DiagnosticOrderCreate, DiagnosticOrderResponse, TaskResultEntry, DiagnosticTaskResponse,
    ServiceCategoryUpdate, ServiceCatalogUpdate,ServiceBundleMappingResponse,DiagnosticOrderUpdate
)
from services.explosion_engine import explode_clinical_orders
from typing import Optional, List
router = APIRouter(prefix="/api/diagnostics", tags=["Diagnostic Services & LIS"])

# -----------------------------------------------------------------------------
# Service Master Data Domain (Catalog & Bundles)
# -----------------------------------------------------------------------------

@router.post("/categories", response_model=ServiceCategoryResponse, status_code=status.HTTP_201_CREATED)
def create_service_category(payload: ServiceCategoryCreate, db: Session = Depends(get_db), _: bool = Depends(RequirePermission("manage_clinical_mdm"))):
    category = ServiceCategory(**payload.model_dump(), is_active=True)
    db.add(category)
    db.commit()
    db.refresh(category)
    return category

@router.post("/catalog", response_model=ServiceCatalogResponse, status_code=status.HTTP_201_CREATED)
def create_service_catalog_item(payload: ServiceCatalogCreate, db: Session = Depends(get_db), _: bool = Depends(RequirePermission("manage_clinical_mdm"))):
    item = ServiceCatalog(**payload.model_dump(), is_active=True)
    db.add(item)
    db.commit()
    db.refresh(item)
    return item

@router.post("/catalog/bundle", status_code=status.HTTP_201_CREATED)
def map_bundle_child(payload: ServiceBundleMappingCreate, db: Session = Depends(get_db), _: bool = Depends(RequirePermission("manage_clinical_mdm"))):
    parent = db.get(ServiceCatalog, payload.parent_service_id)
    if not parent or not parent.is_order_set:
        raise HTTPException(status_code=400, detail="Parent service is invalid or not marked as an order set.")
        
    mapping = ServiceBundleMapping(**payload.model_dump())
    db.add(mapping)
    db.commit()
    return {"status": "success", "message": "Service successfully mapped to bundle."}

@router.get("/catalog", response_model=PaginatedResponse[ServiceCatalogResponse])
def list_service_catalog(page: int = 1, page_size: int = 50, db: Session = Depends(get_db), _: bool = Depends(get_current_user)):
    query = db.query(ServiceCatalog).filter(ServiceCatalog.is_active == True)
    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    return {"items": items, "total": total, "page": page, "page_size": page_size}


# -----------------------------------------------------------------------------
# Diagnostic CPOE & Execution Queues
# -----------------------------------------------------------------------------

@router.post("/orders", response_model=DiagnosticOrderResponse, status_code=status.HTTP_201_CREATED)
def place_diagnostic_order(payload: DiagnosticOrderCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Places a clinical order and executes the Order Explosion Engine to generate department tasks."""
    # 1. Explode ordered IDs into executable tasks
    executable_service_ids = explode_clinical_orders(db, payload.ordered_service_ids)
    
    # 2. Create the Parent Order
    order = DiagnosticOrder(
        patient_id=payload.patient_id,
        ordering_doctor_id=current_user.id,
        clinical_notes=payload.clinical_notes,
        status=OrderStatusEnum.ACTIVE
    )
    db.add(order)
    db.flush() # Secure the order.id for child task assignment
    
    # 3. Generate Department Tasks
    tasks = []
    for service_id in executable_service_ids:
        task = DiagnosticTask(
            order_id=order.id,
            service_id=service_id,
            status=TaskStatusEnum.PENDING
        )
        tasks.append(task)
        
    db.add_all(tasks)
    db.commit()
    db.refresh(order)
    return order

@router.get("/tasks/pending", response_model=PaginatedResponse[DiagnosticTaskResponse])
def get_department_task_queue(page: int = 1, page_size: int = 50, db: Session = Depends(get_db), _: bool = Depends(get_current_user)):
    """Retrieves pending diagnostic execution tickets for lab/radiology departments."""
    query = db.query(DiagnosticTask).filter(DiagnosticTask.status == TaskStatusEnum.PENDING)
    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    return {"items": items, "total": total, "page": page, "page_size": page_size}

@router.patch("/tasks/{task_id}/result", status_code=200)
def submit_task_result(task_id: int, payload: TaskResultEntry, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Logs results for a task and advances its lifecycle state."""
    task = db.get(DiagnosticTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")
        
    task.result_value = payload.result_value
    task.result_notes = payload.result_notes
    task.status = payload.status
    
    if payload.status in [TaskStatusEnum.RESULT_ENTERED, TaskStatusEnum.VERIFIED]:
        task.performed_by_id = current_user.id
        
    if payload.status == TaskStatusEnum.VERIFIED:
        task.verified_by_id = current_user.id

    db.commit()
    return {"status": "success", "message": f"Task {task_id} results recorded and status advanced to {payload.status}."}

from schemas import ServiceCategoryUpdate, ServiceCatalogUpdate

# --- Service Category Missing Reads ---

@router.get("/categories", response_model=PaginatedResponse[ServiceCategoryResponse])
def list_service_categories(page: int = 1, page_size: int = 50, db: Session = Depends(get_db), _: bool = Depends(get_current_user)):
    query = db.query(ServiceCategory).filter(ServiceCategory.is_active == True)
    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    return {"items": items, "total": total, "page": page, "page_size": page_size}

@router.get("/categories/{category_id}", response_model=ServiceCategoryResponse)
def get_service_category(category_id: int, db: Session = Depends(get_db), _: bool = Depends(get_current_user)):
    category = db.get(ServiceCategory, category_id)
    if not category:
        raise HTTPException(status_code=404, detail="Service category not found")
    return category


# --- Service Catalog Missing Reads ---

@router.get("/catalog/{item_id}", response_model=ServiceCatalogResponse)
def get_service_catalog_item(item_id: int, db: Session = Depends(get_db), _: bool = Depends(get_current_user)):
    item = db.get(ServiceCatalog, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Service catalog item not found")
    return item


# --- Service Bundle Missing Reads & Deletes ---

@router.get("/catalog/{parent_id}/bundles", response_model=list[ServiceBundleMappingResponse])
def get_bundle_mappings(parent_id: int, db: Session = Depends(get_db), _: bool = Depends(get_current_user)):
    """Retrieves all child services mapped to a parent order set/package."""
    parent = db.get(ServiceCatalog, parent_id)
    if not parent or not parent.is_order_set:
        raise HTTPException(status_code=404, detail="Parent package not found or is not an order set.")
    
    stmt = select(ServiceBundleMapping).where(ServiceBundleMapping.parent_service_id == parent_id)
    return db.execute(stmt).scalars().all()

@router.delete("/catalog/bundle/{parent_id}/{child_id}", status_code=200)
def remove_bundle_mapping(parent_id: int, child_id: int, db: Session = Depends(get_db), _: bool = Depends(RequirePermission("manage_clinical_mdm"))):
    """Removes a specific test/service from a bundled package."""
    stmt = select(ServiceBundleMapping).where(
        ServiceBundleMapping.parent_service_id == parent_id,
        ServiceBundleMapping.child_service_id == child_id
    )
    mapping = db.execute(stmt).scalar_one_or_none()
    if not mapping:
        raise HTTPException(status_code=404, detail="Bundle mapping not found.")
        
    db.delete(mapping)
    db.commit()
    return {"status": "success", "message": "Service successfully removed from bundle."}

@router.put("/categories/{category_id}", response_model=ServiceCategoryResponse, status_code=200)
def update_service_category(category_id: int, payload: ServiceCategoryUpdate, db: Session = Depends(get_db), _: bool = Depends(RequirePermission("manage_clinical_mdm"))):
    """Updates category details such as department name adjustments."""
    category = db.get(ServiceCategory, category_id)
    if not category:
        raise HTTPException(status_code=404, detail="Service category not found")
        
    if payload.name is not None: category.name = payload.name
    if payload.is_active is not None: category.is_active = payload.is_active
    db.commit()
    db.refresh(category)
    return category

@router.delete("/categories/{category_id}", status_code=200)
def deactivate_service_category(category_id: int, db: Session = Depends(get_db), _: bool = Depends(RequirePermission("manage_clinical_mdm"))):
    """Soft-deletes a category. Note: Active child catalog items should be handled by application logic."""
    category = db.get(ServiceCategory, category_id)
    if not category:
        raise HTTPException(status_code=404, detail="Service category not found")
        
    category.is_active = False
    db.commit()
    return {"status": "success", "message": f"Category ID {category_id} deactivated."}

@router.put("/catalog/{item_id}", response_model=ServiceCatalogResponse, status_code=200)
def update_service_catalog_item(item_id: int, payload: ServiceCatalogUpdate, db: Session = Depends(get_db), _: bool = Depends(RequirePermission("manage_clinical_mdm"))):
    """Updates core service catalog metadata including pricing."""
    item = db.get(ServiceCatalog, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Service catalog item not found")
        
    if payload.name is not None: item.name = payload.name
    if payload.base_price_paise is not None: item.base_price_paise = payload.base_price_paise
    if payload.is_active is not None: item.is_active = payload.is_active
    db.commit()
    db.refresh(item)
    return item

@router.delete("/catalog/{item_id}", status_code=200)
def deactivate_service_catalog_item(item_id: int, db: Session = Depends(get_db), _: bool = Depends(RequirePermission("manage_clinical_mdm"))):
    """Soft-deletes a master catalog item, removing it from future order selection."""
    item = db.get(ServiceCatalog, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Service catalog item not found")
        
    item.is_active = False
    db.commit()
    return {"status": "success", "message": f"Service Catalog ID {item_id} deactivated."}

from sqlalchemy.orm import joinedload

# --- Diagnostic Order Missing CRUD ---

@router.get("/orders", response_model=PaginatedResponse[DiagnosticOrderResponse])
def list_diagnostic_orders(patient_id: Optional[int] = None, page: int = 1, page_size: int = 50, db: Session = Depends(get_db), _: bool = Depends(get_current_user)):
    """Lists diagnostic orders, optionally filtered by a specific patient."""
    query = db.query(DiagnosticOrder).options(joinedload(DiagnosticOrder.tasks))
    if patient_id:
        query = query.filter(DiagnosticOrder.patient_id == patient_id)
        
    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    return {"items": items, "total": total, "page": page, "page_size": page_size}

@router.get("/orders/{order_id}", response_model=DiagnosticOrderResponse)
def get_diagnostic_order(order_id: int, db: Session = Depends(get_db), _: bool = Depends(get_current_user)):
    order = db.query(DiagnosticOrder).options(joinedload(DiagnosticOrder.tasks)).filter(DiagnosticOrder.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Diagnostic order not found.")
    return order

@router.put("/orders/{order_id}", response_model=DiagnosticOrderResponse)
def update_diagnostic_order(order_id: int, payload: DiagnosticOrderUpdate, db: Session = Depends(get_db), _: bool = Depends(RequirePermission("write_cpoe_orders"))):
    """Updates clinical notes or forces a status change on an order."""
    order = db.get(DiagnosticOrder, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Diagnostic order not found.")
        
    if payload.clinical_notes is not None: order.clinical_notes = payload.clinical_notes
    if payload.status is not None: order.status = payload.status
    db.commit()
    db.refresh(order)
    return order

@router.delete("/orders/{order_id}", status_code=200)
def cancel_diagnostic_order(order_id: int, db: Session = Depends(get_db), _: bool = Depends(RequirePermission("write_cpoe_orders"))):
    """Cancels an order and cascades the cancellation status down to pending tasks."""
    order = db.get(DiagnosticOrder, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Diagnostic order not found.")
        
    order.status = OrderStatusEnum.CANCELLED
    
    # Cascade cancellation to child tasks that haven't been processed yet
    for task in order.tasks:
        if task.status in [TaskStatusEnum.PENDING, TaskStatusEnum.IN_PROGRESS]:
            task.status = TaskStatusEnum.CANCELLED
            
    db.commit()
    return {"status": "success", "message": f"Order {order_id} and associated pending tasks cancelled."}


# --- Diagnostic Task Missing Reads ---

@router.get("/orders/{order_id}/tasks", response_model=PaginatedResponse[DiagnosticTaskResponse])
def list_order_tasks(order_id: int, page: int = 1, page_size: int = 50, db: Session = Depends(get_db), _: bool = Depends(get_current_user)):
    """Retrieves all department-level execution tickets for a specific parent order."""
    query = db.query(DiagnosticTask).filter(DiagnosticTask.order_id == order_id)
    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    return {"items": items, "total": total, "page": page, "page_size": page_size}