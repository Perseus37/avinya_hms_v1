"""Prometheus observability and telemetry metrics for Avinya HMS.

Provides standardized HTTP request latency histograms, status counters,
background job metrics, and path normalization to prevent high-cardinality label explosions.
"""

import re
from typing import Optional
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    Info,
    REGISTRY,
    generate_latest,
)

from backend.core.config import settings

# -----------------------------------------------------------------------------
# Metric Definitions (Registered on Default Prometheus REGISTRY)
# -----------------------------------------------------------------------------

HTTP_REQUESTS_TOTAL = Counter(
    "avinya_http_requests_total",
    "Total count of HTTP requests processed by Avinya HMS.",
    ["method", "endpoint", "status"],
)

HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "avinya_http_request_duration_seconds",
    "HTTP request latency in seconds.",
    ["method", "endpoint"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

QUEUE_JOBS_TOTAL = Counter(
    "avinya_queue_jobs_total",
    "Total count of background worker queue jobs executed.",
    ["job_name", "status"],
)

QUEUE_PENDING_TASKS = Gauge(
    "avinya_queue_pending_tasks",
    "Number of background tasks currently queued or scheduled.",
    ["queue_name"],
)

APP_INFO = Info(
    "avinya_app_info",
    "Avinya HMS application and environment metadata.",
)

# Initialize application metadata
APP_INFO.info({
    "version": settings.VERSION,
    "project": settings.PROJECT_NAME,
    "environment": settings.ENVIRONMENT,
})


# -----------------------------------------------------------------------------
# Cardinality Protection: Route Path Normalizer
# -----------------------------------------------------------------------------
_UUID_REGEX = re.compile(r"/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}(?=/|$)")
_NUMERIC_ID_REGEX = re.compile(r"/\d+(?=/|$)")
_HEX_TOKEN_REGEX = re.compile(r"/[0-9a-fA-F]{32,64}(?=/|$)")


def normalize_metric_path(path: str) -> str:
    """
    Collapses dynamic URL path segments (UUIDs, integer entity IDs, hex hashes)
    into static placeholders to ensure Prometheus label cardinality remains bounded.
    """
    if not path:
        return "/"
    # Strip query parameters if any
    clean_path = path.split("?")[0]
    clean_path = _UUID_REGEX.sub("/{uuid}", clean_path)
    clean_path = _HEX_TOKEN_REGEX.sub("/{hash}", clean_path)
    clean_path = _NUMERIC_ID_REGEX.sub("/{id}", clean_path)
    return clean_path


def get_prometheus_metrics_bytes() -> bytes:
    """Renders latest metrics in Prometheus text exposition format."""
    return generate_latest(REGISTRY)

