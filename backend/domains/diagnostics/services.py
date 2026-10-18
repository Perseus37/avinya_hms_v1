from sqlalchemy.orm import Session
from sqlalchemy import select
from fastapi import HTTPException
from backend.domains.diagnostics.models import ServiceCatalog, ServiceBundleMapping

def explode_clinical_orders(session: Session, ordered_service_ids: list[int]) -> dict[int, int]:
    """
    Takes a list of requested service IDs and resolves them into atomic, executable tasks.
    Automatically explodes order sets (packages) into their constituent child services.
    
    Returns:
        dict: Mapping of {executable_service_id: total_quantity}
    """
    # Dictionary to accumulate quantities of atomic executable tasks
    executable_tasks_quantities = {}
    
    # Queue stores tuples of (service_id, requested_quantity, traversal_path)
    # Traversal path prevents circular dependencies (e.g., A -> B -> A)
    queue = [(sid, 1, {sid}) for sid in ordered_service_ids]
    
    while queue:
        current_id, current_qty, path = queue.pop(0)
        
        service = session.get(ServiceCatalog, current_id)
        if not service or not service.is_active:
            raise HTTPException(status_code=400, detail=f"Service ID {current_id} is invalid or inactive.")
            
        if not service.is_order_set:
            # Atomic executable test (e.g., individual CBC or X-Ray)
            # H4 FIX: Aggregate quantities instead of creating duplicate rows
            if current_id in executable_tasks_quantities:
                executable_tasks_quantities[current_id] += current_qty
            else:
                executable_tasks_quantities[current_id] = current_qty
        else:
            # Explode package: Fetch all mapped child services
            stmt = select(ServiceBundleMapping).where(ServiceBundleMapping.parent_service_id == current_id)
            mappings = session.execute(stmt).scalars().all()
            
            if not mappings:
                raise HTTPException(status_code=400, detail=f"Order set ID {current_id} contains no mapped child services.")
                
            for mapping in mappings:
                child_id = mapping.child_service_id
                
                # H4 FIX: Circular bundle protection
                if child_id in path:
                    raise HTTPException(status_code=400, detail=f"Circular bundle dependency detected at Service ID {child_id}.")
                    
                # Calculate true quantity: Parent requested qty * Child mapping qty
                child_qty = current_qty * mapping.quantity
                new_path = path.copy()
                new_path.add(child_id)
                
                queue.append((child_id, child_qty, new_path))
                
    if not executable_tasks_quantities:
        raise HTTPException(status_code=400, detail="Order resolution resulted in zero executable tasks.")
        
    return executable_tasks_quantities