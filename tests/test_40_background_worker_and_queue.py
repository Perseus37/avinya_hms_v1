"""Tests for Redis-backed background job queue and worker architecture (M-01 & M-02)."""

import json
import time
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from backend.core.queue import (
    KEY_DEAD_LETTER,
    KEY_PENDING_TASKS,
    KEY_SCHEDULED_TASKS,
    KEY_TASK_PREFIX,
    dequeue_task,
    enqueue_task,
    get_task_status,
    mark_task_completed,
    mark_task_failed,
    promote_scheduled_tasks,
    registry,
)
from backend.worker import run_worker_loop


class FakeRedis:
    """In-memory Redis mock for queue and task testing."""

    def __init__(self):
        self._hashes = {}
        self._lists = {}
        self._zsets = {}
        self._strings = {}

    def pipeline(self):
        return self

    def hset(self, key, *args, mapping=None, **kwargs):
        if key not in self._hashes:
            self._hashes[key] = {}
        if mapping:
            self._hashes[key].update(mapping)
        if len(args) == 2:
            self._hashes[key][args[0]] = args[1]
        elif len(args) == 1 and isinstance(args[0], dict):
            self._hashes[key].update(args[0])
        if kwargs:
            self._hashes[key].update(kwargs)
        return self

    def hgetall(self, key):
        return dict(self._hashes.get(key, {}))

    def expire(self, key, seconds):
        return self

    def rpush(self, key, *values):
        if key not in self._lists:
            self._lists[key] = []
        self._lists[key].extend(values)
        return len(self._lists[key])

    def blpop(self, key, timeout=0):
        items = self._lists.get(key, [])
        if items:
            val = items.pop(0)
            return (key, val)
        return None

    def llen(self, key):
        return len(self._lists.get(key, []))

    def zadd(self, key, mapping):
        if key not in self._zsets:
            self._zsets[key] = {}
        self._zsets[key].update(mapping)
        return len(mapping)

    def zrangebyscore(self, key, min, max):
        z = self._zsets.get(key, {})
        max_val = float("inf") if max == "+inf" else float(max)
        min_val = float("-inf") if min == "-inf" else float(min)
        return [k for k, score in z.items() if min_val <= score <= max_val]

    def zrem(self, key, *members):
        z = self._zsets.get(key, {})
        removed = 0
        for m in members:
            if m in z:
                del z[m]
                removed += 1
        return removed

    def set(self, key, value, ex=None):
        self._strings[key] = value
        return True

    def execute(self):
        return True


@pytest.fixture
def fake_redis(monkeypatch):
    r = FakeRedis()
    monkeypatch.setattr("backend.core.queue.get_redis", lambda: r)
    monkeypatch.setattr("backend.worker.get_redis", lambda: r)
    return r


def test_enqueue_immediate_task_and_status(fake_redis):
    payload = {"recipient": "doctor@apollo.test", "subject": "Test alert", "text_body": "Hello"}
    task_id = enqueue_task("email.send", payload, delay_seconds=0, sync_fallback=False)

    assert task_id is not None
    assert task_id in fake_redis._lists.get(KEY_PENDING_TASKS, [])

    status = get_task_status(task_id)
    assert status is not None
    assert status["status"] == "QUEUED"
    assert status["task_name"] == "email.send"
    assert status["retries"] == 0


def test_enqueue_scheduled_task_and_promotion(fake_redis):
    payload = {"data": 123}
    task_id = enqueue_task("sample.task", payload, delay_seconds=10, sync_fallback=False)

    assert task_id in fake_redis._zsets.get(KEY_SCHEDULED_TASKS, {})
    assert task_id not in fake_redis._lists.get(KEY_PENDING_TASKS, [])

    # Promote with time in future
    future_time = time.time() + 20
    with patch("time.time", return_value=future_time):
        promoted = promote_scheduled_tasks(fake_redis)
        assert promoted == 1
        assert task_id in fake_redis._lists.get(KEY_PENDING_TASKS, [])
        assert task_id not in fake_redis._zsets.get(KEY_SCHEDULED_TASKS, {})


def test_dequeue_and_completion_lifecycle(fake_redis):
    payload = {"key": "val"}
    task_id = enqueue_task("email.send", payload, delay_seconds=0, sync_fallback=False)

    dequeued = dequeue_task(timeout=0)
    assert dequeued is not None
    dq_id, dq_name, dq_payload = dequeued
    assert dq_id == task_id
    assert dq_name == "email.send"
    assert dq_payload == payload

    # Complete task
    mark_task_completed(task_id, {"status": "DELIVERED"})
    status = get_task_status(task_id)
    assert status["status"] == "COMPLETED"
    assert status["completed_at"] is not None


def test_task_failure_and_dead_letter_routing(fake_redis):
    task_id = enqueue_task("failing.task", {}, max_retries=2, sync_fallback=False)

    # 1st failure -> retry 1
    mark_task_failed(task_id, "Connection timeout 1")
    status = get_task_status(task_id)
    assert status["status"] == "RETRYING"
    assert status["retries"] == 1
    assert task_id in fake_redis._zsets.get(KEY_SCHEDULED_TASKS, {})

    # 2nd failure -> retry 2
    mark_task_failed(task_id, "Connection timeout 2")
    status = get_task_status(task_id)
    assert status["status"] == "RETRYING"
    assert status["retries"] == 2

    # 3rd failure -> dead-letter queue
    mark_task_failed(task_id, "Fatal unrecoverable error")
    status = get_task_status(task_id)
    assert status["status"] == "FAILED"
    assert task_id in fake_redis._lists.get(KEY_DEAD_LETTER, [])


def test_worker_burst_execution(fake_redis, monkeypatch):
    executed = []

    @registry.register("test.burst_task")
    def sample_burst(payload):
        executed.append(payload["msg"])
        return {"processed": True}

    enqueue_task("test.burst_task", {"msg": "job 1"}, sync_fallback=False)
    enqueue_task("test.burst_task", {"msg": "job 2"}, sync_fallback=False)

    exit_code = run_worker_loop(burst=True, poll_interval=0.01, enable_periodic=False)
    assert exit_code == 0
    assert executed == ["job 1", "job 2"]

