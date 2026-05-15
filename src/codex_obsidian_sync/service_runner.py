from __future__ import annotations

from datetime import UTC, datetime, timedelta
import time
from pathlib import Path

from .config import load_toml_config, resolve_service_paths, resolve_sync_config
from .event_logger import EventLogger
from .locking import process_lock
from .service_state import load_service_state, mutate_service_state, utc_now_iso
from .sync import sync_once

SETTLE_POLL_SECONDS = 0.25
SETTLE_STABLE_POLLS = 2
SETTLE_MAX_WAIT_SECONDS = 2.0


def run_service(*, config_path: Path | None, logger: EventLogger | None = None) -> int:
    config_data = load_toml_config(config_path)
    if not config_data:
        raise ValueError("Config is required before service-run can execute")

    sync_config = resolve_sync_config(
        vault=None,
        codex_home=None,
        state_file=None,
        lock_file=None,
        include_subagents=None,
        interval_seconds=None,
        recent_days=None,
        candidate_file_limit=None,
        candidate_bytes_limit=None,
        log_level=None,
        config_data=config_data,
        config_path=config_path,
    )
    paths = resolve_service_paths(config_path=config_path, config_data=config_data)
    logger = logger or EventLogger(sync_config.log_level)

    mutate_service_state(
        state_path=paths.service_state_file,
        lock_path=paths.service_state_lock_file,
        mutator=_mark_triggered,
    )

    try:
        with process_lock(paths.service_runner_lock_file):
            return _drain_pending(sync_config=sync_config, paths=paths, logger=logger)
    except RuntimeError:
        logger.debug("service_runner_busy")
        return 0


def _drain_pending(*, sync_config, paths, logger: EventLogger) -> int:
    while True:
        snapshot = mutate_service_state(
            state_path=paths.service_state_file,
            lock_path=paths.service_state_lock_file,
            mutator=_mark_previous_abnormal_exit,
        )

        if not snapshot.get("pending", False):
            return 0

        next_eligible_at = _parse_iso(snapshot.get("next_eligible_at"))
        if next_eligible_at is not None:
            sleep_seconds = (next_eligible_at - datetime.now(UTC)).total_seconds()
            if sleep_seconds > 0:
                logger.debug("service_runner_cooldown_wait", sleep_seconds=round(sleep_seconds, 3))
                time.sleep(sleep_seconds)
                continue

        source_before = _wait_for_source_settle(paths.sessions_path, logger=logger)
        started_at = utc_now_iso()
        mutate_service_state(
            state_path=paths.service_state_file,
            lock_path=paths.service_state_lock_file,
            mutator=lambda state: _mark_run_started(state, started_at),
        )

        summary: dict[str, int] | None = None
        error: Exception | None = None
        try:
            with process_lock(sync_config.lock_file):
                summary = sync_once(config=sync_config, logger=logger, allow_pause=True)
        except Exception as exc:
            error = exc

        source_after = _capture_source_fingerprint(paths.sessions_path)
        finished_at = utc_now_iso()
        mutate_service_state(
            state_path=paths.service_state_file,
            lock_path=paths.service_state_lock_file,
            mutator=lambda state: _mark_run_finished(
                state,
                finished_at=finished_at,
                interval_seconds=sync_config.interval_seconds,
                summary=summary,
                error=error,
                source_changed_during_run=source_after != source_before,
            ),
        )


def _mark_triggered(state: dict[str, object]) -> dict[str, object]:
    state["pending"] = True
    state["last_trigger_at"] = utc_now_iso()
    return state


def _mark_previous_abnormal_exit(state: dict[str, object]) -> dict[str, object]:
    started_at = _parse_iso(state.get("last_run_started_at"))
    finished_at = _parse_iso(state.get("last_run_finished_at"))
    if started_at is not None and (finished_at is None or finished_at < started_at):
        state["last_error_type"] = "AbnormalExit"
        state["last_error_summary"] = "previous service-run did not finish cleanly"
    return dict(state)


def _mark_run_started(state: dict[str, object], started_at: str) -> dict[str, object]:
    state["pending"] = False
    state["last_run_started_at"] = started_at
    return state


def _mark_run_finished(
    state: dict[str, object],
    *,
    finished_at: str,
    interval_seconds: int,
    summary: dict[str, int] | None,
    error: Exception | None,
    source_changed_during_run: bool,
) -> dict[str, object]:
    state["last_run_finished_at"] = finished_at
    state["next_eligible_at"] = _shift_iso(finished_at, interval_seconds)
    state["pending"] = bool(state.get("pending")) or source_changed_during_run
    if error is not None:
        state["last_error_type"] = type(error).__name__
        state["last_error_summary"] = _one_line(str(error) or "service-run failed")
        return state

    state["last_summary"] = {key: value for key, value in (summary or {}).items() if isinstance(value, int)}
    state["last_error_type"] = None
    state["last_error_summary"] = None
    if not summary or not summary.get("paused"):
        state["last_success_at"] = finished_at
    return state


def _parse_iso(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _shift_iso(value: str, seconds: int) -> str:
    base = _parse_iso(value)
    if base is None:
        base = datetime.now(UTC)
    return (base + timedelta(seconds=max(seconds, 1))).isoformat()


def _one_line(value: str) -> str:
    return " ".join(value.split())[:200]


def _wait_for_source_settle(sessions_path: Path, *, logger: EventLogger) -> tuple[str, int, int]:
    last = _capture_source_fingerprint(sessions_path)
    stable_polls = 0
    deadline = time.monotonic() + SETTLE_MAX_WAIT_SECONDS
    while time.monotonic() < deadline:
        time.sleep(SETTLE_POLL_SECONDS)
        current = _capture_source_fingerprint(sessions_path)
        if current == last:
            stable_polls += 1
            if stable_polls >= SETTLE_STABLE_POLLS:
                return current
            continue
        logger.debug("service_runner_settle_wait")
        last = current
        stable_polls = 0
    return last


def _capture_source_fingerprint(sessions_path: Path) -> tuple[str, int, int]:
    latest_path = ""
    latest_size = 0
    latest_mtime_ns = 0
    if not sessions_path.exists():
        return latest_path, latest_size, latest_mtime_ns

    for rollout_path in sessions_path.rglob("rollout-*.jsonl"):
        try:
            stat = rollout_path.stat()
        except OSError:
            continue
        if stat.st_mtime_ns > latest_mtime_ns:
            latest_path = str(rollout_path)
            latest_size = stat.st_size
            latest_mtime_ns = stat.st_mtime_ns
    return latest_path, latest_size, latest_mtime_ns
