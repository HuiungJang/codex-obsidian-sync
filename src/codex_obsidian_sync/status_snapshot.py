from __future__ import annotations

from datetime import UTC, datetime, timedelta
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .config import DEFAULT_LAUNCHD_LABEL, ServicePaths
from .intervals import format_interval


@dataclass(frozen=True)
class StatusSnapshot:
    configured: bool
    config_path: str
    vault: str
    cooldown: str
    launchd_label: str
    launchd_loaded: bool
    plist_path: str
    pending: str
    next_eligible_run: str
    last_run: str
    last_success: str
    last_error: str
    last_summary: dict[str, int]


def build_status_snapshot(
    *,
    config_path: Path,
    config_data: dict[str, Any],
    paths: ServicePaths,
    launchd_loaded: bool,
    service_state: dict[str, Any] | None,
) -> StatusSnapshot:
    if not config_data:
        return StatusSnapshot(
            configured=False,
            config_path=str(config_path),
            vault="not configured",
            cooldown="not configured",
            launchd_label=DEFAULT_LAUNCHD_LABEL,
            launchd_loaded=launchd_loaded,
            plist_path=str(paths.launchd_plist_path),
            pending="not configured",
            next_eligible_run="not configured",
            last_run="not configured",
            last_success="not configured",
            last_error="not configured",
            last_summary={},
        )

    service_state = service_state or {}
    interval_seconds = int(config_data.get("interval_seconds", 10))
    error_type = service_state.get("last_error_type")
    error_summary = service_state.get("last_error_summary")
    if error_type and error_summary:
        last_error = f"{error_type}: {error_summary}"
    else:
        last_error = "none"

    next_eligible_run = _next_eligible_run(service_state=service_state, cooldown_seconds=interval_seconds)

    return StatusSnapshot(
        configured=True,
        config_path=str(config_path),
        vault=str(config_data.get("vault", "not configured")),
        cooldown=format_interval(interval_seconds),
        launchd_label=DEFAULT_LAUNCHD_LABEL,
        launchd_loaded=launchd_loaded,
        plist_path=str(paths.launchd_plist_path),
        pending="yes" if service_state.get("pending") else "no",
        next_eligible_run=next_eligible_run,
        last_run=str(service_state.get("last_run_finished_at") or "never run"),
        last_success=str(service_state.get("last_success_at") or "never run"),
        last_error=last_error,
        last_summary=_normalize_summary(service_state.get("last_summary")),
    )


def render_status_snapshot(snapshot: StatusSnapshot) -> str:
    launchd_state = "loaded" if snapshot.launchd_loaded else "unloaded"
    summary_text = (
        ", ".join(f"{key}={value}" for key, value in snapshot.last_summary.items())
        if snapshot.last_summary
        else "none"
    )
    lines = [
        f"Config: {snapshot.config_path}",
        f"Vault: {snapshot.vault}",
        f"Cooldown: {snapshot.cooldown}",
        f"LaunchAgent: {launchd_state}",
        f"Pending: {snapshot.pending}",
        f"Next eligible run: {snapshot.next_eligible_run}",
        f"Last run: {snapshot.last_run}",
        f"Last success: {snapshot.last_success}",
        f"Last error: {snapshot.last_error}",
        f"Last summary: {summary_text}",
        f"Plist: {snapshot.plist_path}",
    ]
    return "\n".join(lines)


def status_snapshot_to_dict(snapshot: StatusSnapshot) -> dict[str, Any]:
    return asdict(snapshot)


def _normalize_summary(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, int] = {}
    for key, item in value.items():
        if isinstance(item, int):
            normalized[str(key)] = item
    return normalized


def _next_eligible_run(*, service_state: dict[str, Any], cooldown_seconds: int) -> str:
    if service_state.get("pending") and not service_state.get("next_eligible_at"):
        return "pending"

    stored_next = _parse_iso(service_state.get("next_eligible_at"))
    if service_state.get("pending") and stored_next is not None:
        return stored_next.isoformat()

    last_run = _parse_iso(service_state.get("last_run_finished_at"))
    if last_run is None:
        return "never run"

    eligible_at = last_run + timedelta(seconds=max(cooldown_seconds, 1))
    if eligible_at <= datetime.now(UTC):
        return "ready now"
    return eligible_at.isoformat()


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
