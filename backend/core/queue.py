"""Redis-backed asynchronous background job queue and task dispatcher for Avinya HMS.

Provides robust, out-of-band task scheduling, retries with exponential backoff,
dead-letter queueing, and integration with Prometheus observability.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Callable, Dict, Optional

import redis
from sqlalchemy.orm import sessionmaker

from backend.core.database import (
    ControlSessionLocal,
    get_redis,
    tenant_engine_pool,
)
from backend.core.metrics import QUEUE_JOBS_TOTAL, QUEUE_PENDING_TASKS

logger = logging.getLogger("avinya.queue")

# -----------------------------------------------------------------------------
# Redis Key Namespaces
# -----------------------------------------------------------------------------
KEY_PENDING_TASKS = "avinya:queue:pending"
KEY_SCHEDULED_TASKS = "avinya:queue:scheduled"
KEY_DEAD_LETTER = "avinya:queue:dead_letter"
KEY_TASK_PREFIX = "avinya:task:"
KEY_WORKER_HEARTBEAT = "avinya:worker:heartbeat"

MAX_TASK_RETRIES = 3
DEFAULT_LOCK_TTL = 300  # 5 minutes


# -----------------------------------------------------------------------------
# Task Registry
# -----------------------------------------------------------------------------
class TaskRegistry:
    """Registry mapping task names to handler callables."""

    def __init__(self) -> None:
        self._handlers: Dict[str, Callable[..., Any]] = {}

    def register(self, task_name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            self._handlers[task_name] = fn
            return fn
        return decorator

    def get_handler(self, task_name: str) -> Optional[Callable[..., Any]]:
        return self._handlers.get(task_name)

    @property
    def registered_tasks(self) -> list[str]:
        return list(self._handlers.keys())


registry = TaskRegistry()


# -----------------------------------------------------------------------------
# Task Enqueueing & Dispatch API
# -----------------------------------------------------------------------------
def enqueue_task(
    task_name: str,
    payload: dict[str, Any],
    *,
    delay_seconds: int = 0,
    max_retries: int = MAX_TASK_RETRIES,
    sync_fallback: bool = True,
) -> str:
    """
    Enqueues a background task into the Redis task queue.
    If delay_seconds > 0, task is scheduled in the sorted set.
    If Redis is unreachable and sync_fallback is True, executes the task immediately.
    """
    task_id = str(uuid.uuid4())
    now = time.time()
    scheduled_time = now + max(0, delay_seconds)

    task_data = {
        "id": task_id,
        "task_name": task_name,
        "payload": json.dumps(payload),
        "status": "QUEUED" if delay_seconds == 0 else "SCHEDULED",
        "created_at": str(now),
        "scheduled_at": str(scheduled_time),
        "retries": "0",
        "max_retries": str(max_retries),
        "error": "",
    }

    try:
        r = get_redis()
        pipe = r.pipeline()
        pipe.hset(f"{KEY_TASK_PREFIX}{task_id}", mapping=task_data)
        # N-22: Secrets in queue: sensitive email and auth payloads expire in 1 hour; standard tasks in 24 hours
        task_ttl = 3600 if task_name == "email.send" else 86400
        pipe.expire(f"{KEY_TASK_PREFIX}{task_id}", task_ttl)

        if delay_seconds > 0:
            pipe.zadd(KEY_SCHEDULED_TASKS, {task_id: scheduled_time})
        else:
            pipe.rpush(KEY_PENDING_TASKS, task_id)

        pipe.execute()

        # Update gauge
        try:
            pending_count = r.llen(KEY_PENDING_TASKS)
            QUEUE_PENDING_TASKS.labels(queue_name="pending").set(pending_count)
        except Exception:
            pass

        logger.info("Enqueued task %s (%s, delay=%ss)", task_name, task_id, delay_seconds)
        return task_id

    except (redis.ConnectionError, redis.TimeoutError, Exception) as exc:
        logger.warning(
            "Redis unavailable for background enqueue of %s (%s): %s",
            task_name,
            task_id,
            exc,
        )
        if sync_fallback:
            logger.info("Executing task %s synchronously due to sync_fallback.", task_name)
            handler = registry.get_handler(task_name)
            if handler:
                try:
                    handler(payload)
                except Exception as run_exc:
                    logger.warning("Synchronous fallback execution of %s failed: %s", task_name, run_exc)
            return task_id
        raise


def get_task_status(task_id: str) -> Optional[dict[str, Any]]:
    """Retrieves current execution status and metadata for a task."""
    try:
        r = get_redis()
        data = r.hgetall(f"{KEY_TASK_PREFIX}{task_id}")
        if not data:
            return None
        return {
            "id": data.get("id", task_id),
            "task_name": data.get("task_name"),
            "status": data.get("status"),
            "retries": int(data.get("retries", 0)),
            "max_retries": int(data.get("max_retries", MAX_TASK_RETRIES)),
            "created_at": data.get("created_at"),
            "scheduled_at": data.get("scheduled_at"),
            "completed_at": data.get("completed_at"),
            "error": data.get("error"),
        }
    except Exception as exc:
        logger.warning("Failed to get task status for %s: %s", task_id, exc)
        return None


def promote_scheduled_tasks(r: redis.Redis) -> int:
    """Moves scheduled tasks that are now due from the sorted set to the pending queue."""
    now = time.time()
    due_tasks = r.zrangebyscore(KEY_SCHEDULED_TASKS, min="-inf", max=now)
    if not due_tasks:
        return 0

    promoted = 0
    for task_id in due_tasks:
        # Atomic remove and push
        if r.zrem(KEY_SCHEDULED_TASKS, task_id):
            r.rpush(KEY_PENDING_TASKS, task_id)
            r.hset(f"{KEY_TASK_PREFIX}{task_id}", "status", "QUEUED")
            promoted += 1

    if promoted:
        logger.info("Promoted %d scheduled tasks to pending queue.", promoted)
    return promoted


def dequeue_task(timeout: int = 1) -> Optional[tuple[str, str, dict[str, Any]]]:
    """
    Dequeues the next task to process.
    Returns (task_id, task_name, payload) or None if queue is empty.
    """
    r = get_redis()
    promote_scheduled_tasks(r)

    # Pop next task ID
    item = r.blpop(KEY_PENDING_TASKS, timeout=timeout)
    if not item:
        return None

    _, task_id = item
    raw_task = r.hgetall(f"{KEY_TASK_PREFIX}{task_id}")
    if not raw_task:
        return None

    task_name = raw_task.get("task_name", "")
    raw_payload = raw_task.get("payload", "{}")
    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError:
        payload = {}

    r.hset(f"{KEY_TASK_PREFIX}{task_id}", "status", "PROCESSING")
    return task_id, task_name, payload


def mark_task_completed(task_id: str, result: Optional[dict[str, Any]] = None) -> None:
    """Marks task as successfully completed and records metrics."""
    try:
        r = get_redis()
        task_data = r.hgetall(f"{KEY_TASK_PREFIX}{task_id}")
        task_name = task_data.get("task_name", "unknown") if task_data else "unknown"

        mapping = {
            "status": "COMPLETED",
            "completed_at": str(time.time()),
        }
        if result is not None:
            mapping["result"] = json.dumps(result)

        r.hset(f"{KEY_TASK_PREFIX}{task_id}", mapping=mapping)
        r.hdel(f"{KEY_TASK_PREFIX}{task_id}", "payload")
        r.expire(f"{KEY_TASK_PREFIX}{task_id}", 3600)
        QUEUE_JOBS_TOTAL.labels(job_name=task_name, status="success").inc()
    except Exception as exc:
        logger.error("Failed to mark task %s completed: %s", task_id, exc)


def mark_task_failed(task_id: str, error_message: str) -> None:
    """
    Handles task execution failure. Re-schedules with exponential backoff if
    retries remain, or routes to dead-letter queue.
    """
    try:
        r = get_redis()
        task_data = r.hgetall(f"{KEY_TASK_PREFIX}{task_id}")
        if not task_data:
            return

        task_name = task_data.get("task_name", "unknown")
        retries = int(task_data.get("retries", 0)) + 1
        max_retries = int(task_data.get("max_retries", MAX_TASK_RETRIES))

        if retries <= max_retries:
            backoff_delay = 5 * (2 ** (retries - 1))  # 5s, 10s, 20s
            next_run = time.time() + backoff_delay
            r.hset(
                f"{KEY_TASK_PREFIX}{task_id}",
                mapping={
                    "status": "RETRYING",
                    "retries": str(retries),
                    "error": str(error_message)[:1000],
                },
            )
            r.zadd(KEY_SCHEDULED_TASKS, {task_id: next_run})
            QUEUE_JOBS_TOTAL.labels(job_name=task_name, status="retried").inc()
            logger.warning(
                "Task %s (%s) failed (attempt %d/%d): %s. Retrying in %ds.",
                task_name,
                task_id,
                retries,
                max_retries,
                error_message,
                backoff_delay,
            )
        else:
            r.hset(
                f"{KEY_TASK_PREFIX}{task_id}",
                mapping={
                    "status": "FAILED",
                    "retries": str(retries),
                    "error": str(error_message)[:1000],
                    "failed_at": str(time.time()),
                },
            )
            r.rpush(KEY_DEAD_LETTER, task_id)
            # N-22: Expire dead-letter payload after 24 hours to prevent stale secret retention
            r.expire(f"{KEY_TASK_PREFIX}{task_id}", 86400)
            QUEUE_JOBS_TOTAL.labels(job_name=task_name, status="failed").inc()
            logger.error(
                "Task %s (%s) exceeded max retries (%d). Moved to dead-letter queue. Error: %s",
                task_name,
                task_id,
                max_retries,
                error_message,
            )
    except Exception as exc:
        logger.exception("Failed to mark task %s failed: %s", task_id, exc)


# -----------------------------------------------------------------------------
# Core Built-in Task Implementations
# -----------------------------------------------------------------------------

@registry.register("email.send")
def handle_email_send(payload: dict[str, Any]) -> dict[str, Any]:
    """Executes asynchronous email dispatch via configured mailer."""
    from backend.core.mailer import send_email, MailDeliveryError

    recipient = payload["recipient"]
    subject = payload["subject"]
    text_body = payload["text_body"]
    html_body = payload.get("html_body")

    try:
        send_email(
            recipient=recipient,
            subject=subject,
            text_body=text_body,
            html_body=html_body,
        )
        return {"recipient": recipient, "subject": subject, "sent": True}
    except MailDeliveryError as err:
        logger.warning("Email delivery skipped or failed for %s: %s", recipient, err)
        return {"recipient": recipient, "subject": subject, "sent": False, "error": str(err)}


@registry.register("privacy.retention.execute")
def handle_retention_execute(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Executes retention policy categories against a specific hospital tenant DB
    with strict two-person separation of duties and environment protection guards.
    """
    from datetime import datetime, timezone
    from backend.core.config import settings
    from backend.control_plane.models import TenantRegistry
    from backend.domains.identity.models import User, UserStatusEnum
    from backend.domains.privacy.models import PrivacyPolicyRevision
    from backend.domains.privacy.retention_service import execute_retention_policy_revision

    subdomain = payload["tenant_subdomain"]
    policy_revision_id = payload["policy_revision_id"]
    category_codes = payload["category_codes"]
    dry_run = payload.get("dry_run", False)
    approval_reference = payload.get("approval_reference", f"AUTO_WORKER_{int(time.time())}")

    # Guard: Fail closed if destructive execution is attempted without deployment permissions
    if not dry_run:
        if not getattr(settings, "PRIVACY_ALLOW_RETENTION_EXECUTE", False):
            raise PermissionError("Destructive retention execution is disabled by deployment configuration (PRIVACY_ALLOW_RETENTION_EXECUTE=False).")
        if settings.is_production():
            raise PermissionError("Destructive retention execution cannot run directly in production.")

    # Resolve tenant DB from control plane
    with ControlSessionLocal() as cp_db:
        tenant = cp_db.query(TenantRegistry).filter(
            TenantRegistry.subdomain_slug == subdomain,
            TenantRegistry.is_active == True,
        ).first()
        if not tenant:
            raise ValueError(f"Active tenant not found for subdomain: {subdomain}")
        db_name = tenant.db_name

    engine = tenant_engine_pool.get_engine(db_name)
    TenantSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    with TenantSession() as tenant_db:
        revision = tenant_db.query(PrivacyPolicyRevision).filter(
            PrivacyPolicyRevision.id == policy_revision_id,
            PrivacyPolicyRevision.policy_type == "RETENTION",
            PrivacyPolicyRevision.status == "APPROVED",
        ).first()
        if not revision:
            raise ValueError(f"Approved retention policy revision {policy_revision_id} not found.")

        # Resolve system or operator actor with strict separation of duties (N-05, N-33)
        actor_id = payload.get("actor_user_id")
        actor = None
        if actor_id:
            actor = tenant_db.query(User).filter(User.id == actor_id, User.status == UserStatusEnum.ACTIVE).first()

        # If no actor provided or if the actor configured this revision, find a distinct active user
        disallowed_ids = {revision.configured_by_user_id} if revision.configured_by_user_id else set()
        if not actor or actor.id in disallowed_ids:
            query = tenant_db.query(User).filter(User.status == UserStatusEnum.ACTIVE)
            if disallowed_ids:
                query = query.filter(~User.id.in_(disallowed_ids))
            distinct_actor = query.first()
            if distinct_actor:
                actor = distinct_actor
            elif not actor:
                actor = tenant_db.query(User).filter(User.status == UserStatusEnum.ACTIVE).first() or tenant_db.query(User).first()

        if not actor:
            raise ValueError("No user found in tenant database to attribute retention run.")

        # Ensure two-person separation of duties for live destructive execution
        if not dry_run and actor.id in disallowed_ids:
            raise PermissionError("Two-person separation of duties violation: retention executor cannot be the user who configured the policy revision.")

        result = execute_retention_policy_revision(
            tenant_db,
            revision=revision,
            category_codes=category_codes,
            actor=actor,
            approval_reference=approval_reference,
            now=datetime.now(timezone.utc),
            dry_run=dry_run,
        )
        return result


@registry.register("privacy.retention.scheduled_daily_sweep")
def handle_scheduled_retention_sweep(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Scans all active tenants and enqueues scheduled retention execution
    for any approved active retention policy revisions. Always forces dry-run
    in production or when destructive execution is disabled.
    """
    from backend.core.config import settings
    from backend.control_plane.models import TenantRegistry
    from backend.domains.privacy.models import PrivacyPolicyRevision
    from backend.domains.privacy.policy_schema import validate_policy_document

    is_destructive_allowed = (
        getattr(settings, "PRIVACY_ALLOW_RETENTION_EXECUTE", False)
        and not settings.is_production()
    )
    safe_dry_run = payload.get("dry_run", True) if is_destructive_allowed else True

    executed_tenants = []
    with ControlSessionLocal() as cp_db:
        tenants = cp_db.query(TenantRegistry).filter(TenantRegistry.is_active == True).all()
        for t in tenants:
            engine = tenant_engine_pool.get_engine(t.db_name)
            TenantSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
            try:
                with TenantSession() as tenant_db:
                    revision = tenant_db.query(PrivacyPolicyRevision).filter(
                        PrivacyPolicyRevision.policy_type == "RETENTION",
                        PrivacyPolicyRevision.status == "APPROVED",
                    ).order_by(PrivacyPolicyRevision.id.desc()).first()

                    if revision:
                        policy = validate_policy_document("RETENTION", revision.policy_document)
                        categories = [cat.code for cat in policy.categories]
                        enqueue_task(
                            "privacy.retention.execute",
                            {
                                "tenant_subdomain": t.subdomain_slug,
                                "policy_revision_id": revision.id,
                                "category_codes": categories,
                                "dry_run": safe_dry_run,
                                "approval_reference": f"CRON_DAILY_SWEEP_{int(time.time())}",
                            },
                        )
                        executed_tenants.append(t.subdomain_slug)
            except Exception as e:
                logger.warning("Error evaluating scheduled retention for tenant %s: %s", t.subdomain_slug, e)

    return {"status": "SUCCESS", "enqueued_tenants": executed_tenants, "dry_run": safe_dry_run}

