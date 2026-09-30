from sqlalchemy.orm import Session
from sqlalchemy import select
from fastapi import HTTPException
from models import ServiceCatalog, ServiceBundleMapping

def explode_clinical_orders(session: Session, ordered_service_ids: list[int]) -> list[int]:
    """
    Takes a list of requested service IDs and resolves them into atomic, executable tasks.
    Automatically explodes order sets (packages) into their constituent child services.
    """
    executable_service_ids = []
    queue = list(ordered_service_ids)
    visited_bundles = set()

    while queue:
        current_id = queue.pop(0)

        # Protection against circular package dependencies
        if current_id in visited_bundles:
            continue
        
        service = session.get(ServiceCatalog, current_id)
        if not service or not service.is_active:
            raise HTTPException(status_code=400, detail=f"Service ID {current_id} is invalid or inactive.")

        if not service.is_order_set:
            # Atomic executable test (e.g., individual CBC or X-Ray)
            executable_service_ids.append(current_id)
        else:
            # Explode package: Fetch all mapped child services
            visited_bundles.add(current_id)
            stmt = select(ServiceBundleMapping.child_service_id).where(ServiceBundleMapping.parent_service_id == current_id)
            children = session.execute(stmt).scalars().all()
            
            if not children:
                raise HTTPException(status_code=400, detail=f"Order set ID {current_id} contains no mapped child services.")
            
            # Queue children for resolution (in case a package contains a sub-package)
            queue.extend(children)

    if not executable_service_ids:
        raise HTTPException(status_code=400, detail="Order resolution resulted in zero executable tasks.")
        
    return executable_service_ids