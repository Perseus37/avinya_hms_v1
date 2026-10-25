"""Production background worker process for Avinya HMS.

Consumes tasks from the Redis queue, executes registered handlers,
handles retries and dead-letter routing, and triggers periodic maintenance jobs.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from typing import Any

from backend.core.database import get_redis
from backend.core.queue import (
    KEY_WORKER_HEARTBEAT,
    dequeue_task,
    enqueue_task,
    mark_task_completed,
    mark_task_failed,
    registry,
)

logger = logging.getLogger("avinya.worker")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | [Worker] | %(name)s | %(message)s",
)

_RUNNING = True


def _signal_handler(signum: int, frame: Any) -> None:
    global _RUNNING
    logger.info("Termination signal %d intercepted. Gracefully shutting down worker...", signum)
    _RUNNING = False


def update_worker_heartbeat() -> None:
    """Updates worker heartbeat key in Redis with a 60-second TTL."""
    try:
        r = get_redis()
        r.set(KEY_WORKER_HEARTBEAT, str(time.time()), ex=60)
    except Exception:
        pass


def run_worker_loop(
    *,
    burst: bool = False,
    poll_interval: float = 1.0,
    periodic_interval: float = 3600.0,
    enable_periodic: bool = True,
) -> int:
    """
    Runs the main worker loop.
    In burst mode, drains all currently pending/scheduled tasks and exits.
    """
    global _RUNNING
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    logger.info(
        "Avinya HMS Background Worker initialized. Registered tasks: %s. Mode: %s",
        registry.registered_tasks,
        "BURST" if burst else "CONTINUOUS",
    )

    last_periodic_check = time.time()
    processed_count = 0

    while _RUNNING:
        update_worker_heartbeat()

        # Handle periodic maintenance tasks
        now = time.time()
        if enable_periodic and (now - last_periodic_check >= periodic_interval):
            last_periodic_check = now
            logger.info("Triggering periodic scheduled retention check.")
            try:
                enqueue_task("privacy.retention.scheduled_daily_sweep", {}, sync_fallback=False)
            except Exception as e:
                logger.warning("Failed to enqueue periodic retention sweep: %s", e)

        # Dequeue next task
        dequeued = dequeue_task(timeout=int(poll_interval) if not burst else 0)
        if not dequeued:
            if burst:
                logger.info("Burst mode completed: Queue drained (%d tasks processed).", processed_count)
                break
            time.sleep(poll_interval)
            continue

        task_id, task_name, payload = dequeued
        logger.info("Processing task %s (%s)", task_name, task_id)
        start_time = time.time()

        handler = registry.get_handler(task_name)
        if not handler:
            err_msg = f"No registered handler found for task '{task_name}'"
            logger.error(err_msg)
            mark_task_failed(task_id, err_msg)
            continue

        try:
            result = handler(payload)
            elapsed = time.time() - start_time
            logger.info("Task %s (%s) completed in %.2fs", task_name, task_id, elapsed)
            mark_task_completed(task_id, result if isinstance(result, dict) else {"result": str(result)})
            processed_count += 1
        except Exception as exc:
            elapsed = time.time() - start_time
            logger.exception("Task %s (%s) failed after %.2fs: %s", task_name, task_id, elapsed, exc)
            mark_task_failed(task_id, str(exc))

    logger.info("Worker process exited cleanly. Total tasks handled: %d", processed_count)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Avinya HMS Background Queue Worker")
    parser.add_argument("--burst", action="store_true", help="Drain all pending tasks and terminate immediately.")
    parser.add_argument("--interval", type=float, default=1.0, help="Poll interval in seconds.")
    parser.add_argument("--periodic-interval", type=float, default=3600.0, help="Seconds between scheduled sweeps.")
    parser.add_argument("--disable-periodic", action="store_true", help="Disable periodic cron maintenance sweeps.")
    args = parser.parse_args()

    sys.exit(
        run_worker_loop(
            burst=args.burst,
            poll_interval=args.interval,
            periodic_interval=args.periodic_interval,
            enable_periodic=not args.disable_periodic,
        )
    )


if __name__ == "__main__":
    main()

