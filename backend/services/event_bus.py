import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict
from config import settings
from database import get_redis

logger = logging.getLogger("avinya.event_bus")


def publish_tenant_event(
    tenant_slug: str,
    stream: str,
    event_type: str,
    payload: Dict[str, Any],
) -> None:
    """Publishes a structured JSON message to a tenant-namespaced Redis Pub/Sub channel."""
    channel = f"tenant:{tenant_slug.strip().lower()}:{stream.strip().lower()}"
    message = {
        "event_type": event_type,
        "tenant_slug": tenant_slug,
        "stream": stream,
        "payload": payload,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    try:
        r = get_redis()
        r.publish(channel, json.dumps(message))
    except Exception as exc:
        logger.warning("Failed to publish event to Redis channel '%s': %s", channel, str(exc))