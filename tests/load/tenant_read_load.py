import argparse
import asyncio
import csv
import json
import math
import random
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

import httpx


@dataclass(frozen=True)
class TestUser:
    tenant: str
    base_url: str
    email: str
    password: str


def load_test_users(
    path: Path, users_per_tenant: int, expected_tenants: int | None = None
) -> list[TestUser]:
    users_by_tenant: dict[str, list[TestUser]] = defaultdict(list)
    with path.open(newline="", encoding="utf-8-sig") as input_file:
        reader = csv.DictReader(input_file)
        expected_columns = {"tenant", "base_url", "email", "password"}
        if not reader.fieldnames or not expected_columns <= set(reader.fieldnames):
            raise ValueError(
                "CSV must contain tenant,base_url,email,password columns."
            )
        for line_number, row in enumerate(reader, start=2):
            tenant = (row.get("tenant") or "").strip()
            base_url = (row.get("base_url") or "").strip().rstrip("/")
            email = (row.get("email") or "").strip()
            password = row.get("password") or ""
            if not tenant or not base_url.startswith(("https://", "http://")):
                raise ValueError(f"CSV row {line_number} has an invalid tenant or URL.")
            if not email or not password:
                raise ValueError(f"CSV row {line_number} has empty credentials.")
            users_by_tenant[tenant].append(TestUser(tenant, base_url, email, password))

    if not users_by_tenant:
        raise ValueError("CSV contains no load-test users.")
    if expected_tenants is not None and len(users_by_tenant) != expected_tenants:
        raise ValueError(
            f"Expected {expected_tenants} tenants in the load profile; "
            f"found {len(users_by_tenant)}."
        )
    under_provisioned = sorted(
        tenant
        for tenant, users in users_by_tenant.items()
        if len(users) < users_per_tenant
    )
    if under_provisioned:
        raise ValueError(
            "Each tenant needs at least "
            f"{users_per_tenant} test accounts; insufficient: "
            + ", ".join(under_provisioned)
        )
    return [
        user
        for tenant in sorted(users_by_tenant)
        for user in users_by_tenant[tenant][:users_per_tenant]
    ]


def percentile(values: list[float], percent: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(percent * len(ordered)) - 1)
    return ordered[index]


async def run_load(args: argparse.Namespace) -> dict:
    users = load_test_users(
        args.users_file,
        args.users_per_tenant,
        expected_tenants=args.expected_tenants,
    )
    request_latencies: list[float] = []
    latencies_by_path: dict[str, list[float]] = defaultdict(list)
    request_statuses: Counter[str] = Counter()
    request_counts: Counter[str] = Counter()
    tenant_counts: Counter[str] = Counter()
    lock = asyncio.Lock()
    started = time.monotonic()
    end_time = started + args.ramp_seconds + args.duration_seconds

    async def record(path: str, tenant: str, status_code: int, latency: float):
        async with lock:
            request_latencies.append(latency)
            latencies_by_path[path].append(latency)
            request_statuses[str(status_code)] += 1
            request_counts[path] += 1
            tenant_counts[tenant] += 1

    async def simulate_user(index: int, user: TestUser):
        delay = args.ramp_seconds * index / max(1, len(users) - 1)
        await asyncio.sleep(delay)
        async with httpx.AsyncClient(
            base_url=user.base_url,
            timeout=args.timeout_seconds,
            follow_redirects=False,
        ) as client:
            login_started = time.monotonic()
            try:
                response = await client.post(
                    "/api/auth/login",
                    json={"email": user.email, "password": user.password},
                )
            except httpx.HTTPError:
                await record("login", user.tenant, 0, time.monotonic() - login_started)
                return
            await record("login", user.tenant, response.status_code, time.monotonic() - login_started)
            if response.status_code != 200:
                return
            token = response.json().get("access_token")
            if not token:
                await record("login_token_missing", user.tenant, 0, 0)
                return

            headers = {"Authorization": f"Bearer {token}"}
            while time.monotonic() < end_time:
                path = (
                    "/health/ready"
                    if random.random() < args.health_check_fraction
                    else "/api/auth/me"
                )
                request_started = time.monotonic()
                try:
                    response = await client.get(path, headers=headers if path.startswith("/api/") else None)
                    status_code = response.status_code
                except httpx.HTTPError:
                    status_code = 0
                await record(path, user.tenant, status_code, time.monotonic() - request_started)
                await asyncio.sleep(random.expovariate(1 / args.request_interval_seconds))

    await asyncio.gather(
        *(simulate_user(index, user) for index, user in enumerate(users))
    )
    duration = time.monotonic() - started
    successful = sum(
        count for code, count in request_statuses.items()
        if code.isdigit() and 200 <= int(code) < 300
    )
    total = sum(request_statuses.values())
    return {
        "users": len(users),
        "users_per_tenant": args.users_per_tenant,
        "tenant_count": len({user.tenant for user in users}),
        "duration_seconds": round(duration, 2),
        "total_requests": total,
        "successful_requests": successful,
        "failure_rate": round((total - successful) / total, 5) if total else 1.0,
        "requests_per_second": round(total / duration, 2) if duration else 0,
        "latency_ms": {
            "p50": round(percentile(request_latencies, 0.50) * 1000, 2) if request_latencies else None,
            "p95": round(percentile(request_latencies, 0.95) * 1000, 2) if request_latencies else None,
            "p99": round(percentile(request_latencies, 0.99) * 1000, 2) if request_latencies else None,
            "max": round(max(request_latencies) * 1000, 2) if request_latencies else None,
        },
        "latency_by_path_ms": {
            path: {
                "p50": round(percentile(values, 0.50) * 1000, 2),
                "p95": round(percentile(values, 0.95) * 1000, 2),
                "p99": round(percentile(values, 0.99) * 1000, 2),
                "max": round(max(values) * 1000, 2),
            }
            for path, values in latencies_by_path.items()
        },
        "status_codes": dict(request_statuses),
        "requests_by_path": dict(request_counts),
        "requests_by_tenant": dict(tenant_counts),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only, authenticated tenant load profile for synthetic staging data."
    )
    parser.add_argument("--users-file", type=Path, required=True)
    parser.add_argument("--expected-tenants", type=int, default=10)
    parser.add_argument("--users-per-tenant", type=int, default=20)
    parser.add_argument("--duration-seconds", type=int, default=1800)
    parser.add_argument("--ramp-seconds", type=int, default=600)
    parser.add_argument("--request-interval-seconds", type=float, default=5.0)
    parser.add_argument("--health-check-fraction", type=float, default=0.1)
    parser.add_argument("--timeout-seconds", type=float, default=10)
    parser.add_argument("--report-file", type=Path)
    args = parser.parse_args()
    if args.users_per_tenant < 1 or args.duration_seconds < 1:
        parser.error("user count and duration must be positive.")
    if args.expected_tenants < 1:
        parser.error("expected tenant count must be positive.")
    if args.ramp_seconds < 0 or args.request_interval_seconds <= 0:
        parser.error("ramp must be non-negative and request interval positive.")
    if not 0 <= args.health_check_fraction <= 1:
        parser.error("health-check fraction must be between zero and one.")
    return args


def main() -> int:
    args = parse_args()
    report = asyncio.run(run_load(args))
    serialized = json.dumps(report, indent=2)
    print(serialized)
    if args.report_file:
        args.report_file.write_text(serialized + "\n", encoding="utf-8")
    return 1 if report["failure_rate"] > 0.01 else 0


if __name__ == "__main__":
    raise SystemExit(main())
