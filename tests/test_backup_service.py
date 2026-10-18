from pathlib import Path

from sqlalchemy.exc import OperationalError

from backend.scripts import backup_service


def test_backup_daemon_retries_database_recovery_then_resumes_schedule(
    monkeypatch, tmp_path: Path
):
    calls = []
    delays = []
    monkeypatch.setattr(backup_service, "_running", True)

    def run_cycle(output_dir):
        calls.append(output_dir)
        if len(calls) == 1:
            raise OperationalError("SELECT 1", {}, RuntimeError("database recovering"))

    def wait(seconds):
        delays.append(seconds)
        if len(delays) == 2:
            monkeypatch.setattr(backup_service, "_running", False)

    monkeypatch.setattr(backup_service, "run_backup_cycle", run_cycle)
    monkeypatch.setattr(backup_service, "_wait", wait)

    backup_service.run_backup_daemon(tmp_path, interval=3600)

    assert calls == [tmp_path, tmp_path]
    assert delays == [backup_service.INITIAL_RETRY_SECONDS, 3600]


def test_backup_daemon_does_not_hide_unexpected_errors(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(backup_service, "_running", True)

    def fail_cycle(_output_dir):
        raise ValueError("invalid backup config")

    monkeypatch.setattr(
        backup_service,
        "run_backup_cycle",
        fail_cycle,
    )

    try:
        backup_service.run_backup_daemon(tmp_path, interval=3600)
    except ValueError as exc:
        assert str(exc) == "invalid backup config"
    else:
        raise AssertionError("Unexpected backup errors must remain visible")
