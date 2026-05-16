from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from record_cutover_monitor import (
    MARKER,
    MARKER_CONTENT,
    sanitize_checkpoint,
)


ORDERED_CHECKPOINTS = ("+5m", "+1h", "+4h", "+24h")


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit the full post-cutover monitoring record set.")
    parser.add_argument(
        "--monitor-dir",
        type=Path,
        default=Path("/tmp/codex-obsidian-sync-cutover-monitor"),
        help="Directory containing record_cutover_monitor.py checkpoint records.",
    )
    parser.add_argument("--expected-program-arg0", help="Expected LaunchAgent ProgramArguments[0].")
    parser.add_argument("--output", type=Path, help="Write the monitor audit report to this path.")
    args = parser.parse_args()

    result = audit_monitor_dir(
        monitor_dir=args.monitor_dir,
        expected_program_arg0=args.expected_program_arg0,
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        write_json(args.output, result)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["ok"] else 1


def audit_monitor_dir(*, monitor_dir: Path, expected_program_arg0: str | None = None) -> dict[str, Any]:
    root = monitor_dir.expanduser().resolve()
    no_go_reasons: list[str] = []
    records: list[dict[str, Any]] = []
    record_summaries: list[dict[str, Any]] = []

    if not root.is_dir():
        no_go_reasons.append(f"monitor directory is missing: {root}")
    else:
        marker = root / MARKER
        if not marker.is_file():
            no_go_reasons.append("monitor directory marker is missing")
        elif marker.read_text(encoding="utf-8") != MARKER_CONTENT:
            no_go_reasons.append("monitor directory marker content is not managed by record_cutover_monitor.py")

        for checkpoint in ORDERED_CHECKPOINTS:
            record_path = root / f"{sanitize_checkpoint(checkpoint)}.json"
            record = read_record(record_path, checkpoint, no_go_reasons)
            if record is None:
                continue
            records.append(record)
            record_summaries.append(summarize_record(record, record_path))
            no_go_reasons.extend(validate_record(record, checkpoint, expected_program_arg0))

    no_go_reasons.extend(validate_sequence(records))
    present_checkpoints = {record.get("checkpoint") for record in records}
    missing_checkpoints = [checkpoint for checkpoint in ORDERED_CHECKPOINTS if checkpoint not in present_checkpoints]

    return {
        "ok": not no_go_reasons,
        "generated_at": datetime.now(UTC).isoformat(),
        "monitor_dir": str(root),
        "required_checkpoints": list(ORDERED_CHECKPOINTS),
        "present_checkpoints": [record.get("checkpoint") for record in records],
        "missing_checkpoints": missing_checkpoints,
        "expected_program_arg0": expected_program_arg0,
        "records": record_summaries,
        "no_go_reasons": no_go_reasons,
    }


def read_record(path: Path, checkpoint: str, no_go_reasons: list[str]) -> dict[str, Any] | None:
    if not path.is_file():
        no_go_reasons.append(f"monitor record is missing for {checkpoint}")
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        no_go_reasons.append(f"monitor record is invalid JSON for {checkpoint}: {error}")
        return None
    if not isinstance(value, dict):
        no_go_reasons.append(f"monitor record is not an object for {checkpoint}")
        return None
    return value


def validate_record(
    record: dict[str, Any],
    expected_checkpoint: str,
    expected_program_arg0: str | None,
) -> list[str]:
    reasons: list[str] = []
    checkpoint = record.get("checkpoint")
    if checkpoint != expected_checkpoint:
        reasons.append(f"{expected_checkpoint}: record checkpoint is {checkpoint}")
    if record.get("ok") is not True:
        reasons.append(f"{expected_checkpoint}: record ok is not true")
    record_reasons = record.get("no_go_reasons")
    if record_reasons not in ([], None):
        reasons.append(f"{expected_checkpoint}: record contains no-go reasons")
    for field in ("configured", "launchd_loaded", "launchctl_loaded"):
        if record.get(field) is not True:
            reasons.append(f"{expected_checkpoint}: {field} is not true")
    if record.get("service_command") != "service-run":
        reasons.append(f"{expected_checkpoint}: service command is not service-run")
    if expected_program_arg0 and record.get("program_arg0") != expected_program_arg0:
        reasons.append(f"{expected_checkpoint}: ProgramArguments[0] does not match expected binary")
    if record.get("last_error") not in (None, "none", "never run"):
        reasons.append(f"{expected_checkpoint}: last_error is {record.get('last_error')}")
    return reasons


def validate_sequence(records: list[dict[str, Any]]) -> list[str]:
    reasons: list[str] = []
    previous_success: datetime | None = None
    previous_skipped_invalid: int | None = None
    program_arg0_values = {record.get("program_arg0") for record in records if record.get("program_arg0")}
    if len(program_arg0_values) > 1:
        reasons.append("ProgramArguments[0] changed across monitor records")

    for record in records:
        checkpoint = str(record.get("checkpoint"))
        current_success = parse_iso_datetime(record.get("last_success"))
        if previous_success and current_success and current_success < previous_success:
            reasons.append(f"{checkpoint}: last_success regressed from an earlier checkpoint")
        if current_success:
            previous_success = max(previous_success, current_success) if previous_success else current_success

        skipped_invalid = int(record.get("skipped_invalid") or 0)
        if previous_skipped_invalid is not None and skipped_invalid > previous_skipped_invalid:
            reasons.append(f"{checkpoint}: skipped_invalid increased from an earlier checkpoint")
        previous_skipped_invalid = max(previous_skipped_invalid or 0, skipped_invalid)
    return reasons


def summarize_record(record: dict[str, Any], path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "checkpoint": record.get("checkpoint"),
        "ok": record.get("ok"),
        "recorded_at": record.get("recorded_at"),
        "program_arg0": record.get("program_arg0"),
        "last_success": record.get("last_success"),
        "last_error": record.get("last_error"),
        "skipped_invalid": record.get("skipped_invalid"),
        "processed": record.get("processed"),
        "appended": record.get("appended"),
        "rewritten": record.get("rewritten"),
    }


def parse_iso_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or value in {"none", "never run"}:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    path.chmod(0o600)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
