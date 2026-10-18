import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict

from backend.core.database import get_redis

logger = logging.getLogger("avinya.event_bus")


def publish_tenant_event(
    tenant_slug: str,
    stream: str,
    event_type: str,
    payload: Dict[str, Any],
) -> None:
    """Publish a structured JSON message to a tenant-namespaced Redis channel."""
    channel = f"tenant:{tenant_slug.strip().lower()}:{stream.strip().lower()}"
    message = {
        "event_type": event_type,
        "tenant_slug": tenant_slug,
        "stream": stream,
        "payload": payload,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    try:
        redis_client = get_redis()
        redis_client.publish(channel, json.dumps(message))
    except Exception as exc:
        logger.warning(
            "Failed to publish event to Redis channel '%s': %s", channel, str(exc)
        )
