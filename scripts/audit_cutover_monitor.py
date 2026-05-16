from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from record_cutover_monitor import (
    DEFAULT_LABEL,
    MARKER,
    MARKER_CONTENT,
    checkpoint_record_name,
)


ORDERED_CHECKPOINTS = ("+5m", "+1h", "+4h", "+24h")
COUNTER_FIELDS = ("skipped_invalid", "processed", "appended", "rewritten")
ALLOWED_NON_RECORD_NAMES = {
    MARKER,
    "latest-status.stdout",
    "latest-status.stderr",
    "latest-launchagent.plist",
    "latest-launchctl-print.txt",
    "latest-launchctl-print.stderr",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit the full post-cutover monitoring record set.")
    parser.add_argument(
        "--monitor-dir",
        type=Path,
        default=Path("/tmp/codex-obsidian-sync-cutover-monitor"),
        help="Directory containing record_cutover_monitor.py checkpoint records.",
    )
    parser.add_argument("--expected-label", default=DEFAULT_LABEL, help="Expected LaunchAgent label.")
    parser.add_argument("--expected-program-arg0", help="Expected LaunchAgent ProgramArguments[0].")
    parser.add_argument("--output", type=Path, help="Write the monitor audit report to this path.")
    args = parser.parse_args()

    result = audit_monitor_dir(
        monitor_dir=args.monitor_dir,
        expected_label=args.expected_label,
        expected_program_arg0=args.expected_program_arg0,
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        write_json(args.output, result)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["ok"] else 1


def audit_monitor_dir(
    *,
    monitor_dir: Path,
    expected_label: str = DEFAULT_LABEL,
    expected_program_arg0: str | None = None,
) -> dict[str, Any]:
    requested_root = monitor_dir.expanduser()
    root = requested_root.resolve()
    no_go_reasons: list[str] = []
    records: list[dict[str, Any]] = []
    record_summaries: list[dict[str, Any]] = []
    extra_record_names: list[str] = []
    unexpected_entry_names: list[str] = []

    if requested_root.is_symlink():
        no_go_reasons.append(f"monitor directory is a symlink: {requested_root}")
    elif not root.is_dir():
        no_go_reasons.append(f"monitor directory is missing: {root}")
    else:
        marker = root / MARKER
        if not marker.is_file():
            no_go_reasons.append("monitor directory marker is missing")
        elif marker.is_symlink():
            no_go_reasons.append("monitor directory marker is a symlink")
        elif marker.read_text(encoding="utf-8") != MARKER_CONTENT:
            no_go_reasons.append("monitor directory marker content is not managed by record_cutover_monitor.py")

        extra_record_names = find_extra_record_names(root)
        for record_name in extra_record_names:
            no_go_reasons.append(f"unexpected monitor record: {record_name}")
        unexpected_entry_names = find_unexpected_entry_names(root)
        for entry_name in unexpected_entry_names:
            no_go_reasons.append(f"unexpected monitor directory entry: {entry_name}")
        for symlink_name in find_symlinked_allowed_entry_names(root):
            no_go_reasons.append(f"monitor directory entry is a symlink: {symlink_name}")

        for checkpoint in ORDERED_CHECKPOINTS:
            record_path = root / checkpoint_record_name(checkpoint)
            record = read_record(record_path, checkpoint, no_go_reasons)
            if record is None:
                continue
            records.append(record)
            record_summaries.append(summarize_record(record, record_path))
            no_go_reasons.extend(validate_record(record, checkpoint, expected_label, expected_program_arg0))

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
        "extra_records": extra_record_names,
        "unexpected_entries": unexpected_entry_names,
        "expected_label": expected_label,
        "expected_program_arg0": expected_program_arg0,
        "records": record_summaries,
        "no_go_reasons": no_go_reasons,
    }


def find_extra_record_names(root: Path) -> list[str]:
    expected_names = {checkpoint_record_name(checkpoint) for checkpoint in ORDERED_CHECKPOINTS}
    return sorted(path.name for path in root.glob("*.json") if path.name not in expected_names)


def find_unexpected_entry_names(root: Path) -> list[str]:
    expected_record_names = {checkpoint_record_name(checkpoint) for checkpoint in ORDERED_CHECKPOINTS}
    allowed_names = expected_record_names | ALLOWED_NON_RECORD_NAMES
    return sorted(path.name for path in root.iterdir() if path.name not in allowed_names and path.suffix != ".json")


def find_symlinked_allowed_entry_names(root: Path) -> list[str]:
    expected_record_names = {checkpoint_record_name(checkpoint) for checkpoint in ORDERED_CHECKPOINTS}
    allowed_names = expected_record_names | ALLOWED_NON_RECORD_NAMES
    return sorted(path.name for path in root.iterdir() if path.name in allowed_names and path.is_symlink())


def read_record(path: Path, checkpoint: str, no_go_reasons: list[str]) -> dict[str, Any] | None:
    if not path.is_file():
        no_go_reasons.append(f"monitor record is missing for {checkpoint}")
        return None
    if path.is_symlink():
        no_go_reasons.append(f"monitor record is a symlink for {checkpoint}")
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
    expected_label: str,
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
    if record.get("launchctl_label_seen") is not True:
        reasons.append(f"{expected_checkpoint}: launchctl print did not include expected label")
    if record.get("status_launchd_label") != expected_label:
        reasons.append(f"{expected_checkpoint}: status launchd_label does not match expected label")
    if record.get("plist_label") != expected_label:
        reasons.append(f"{expected_checkpoint}: LaunchAgent Label does not match expected label")
    if record.get("service_command") != "service-run":
        reasons.append(f"{expected_checkpoint}: service command is not service-run")
    if expected_program_arg0 and record.get("program_arg0") != expected_program_arg0:
        reasons.append(f"{expected_checkpoint}: ProgramArguments[0] does not match expected binary")
    if record.get("program_arguments_count") != 4:
        reasons.append(f"{expected_checkpoint}: ProgramArguments count is not the Rust launchd shape")
    reasons.extend(validate_program_arguments(record, expected_checkpoint, expected_program_arg0))
    config_path = record.get("config_path")
    if not isinstance(config_path, str) or not config_path:
        reasons.append(f"{expected_checkpoint}: config_path is missing")
    elif not Path(config_path).is_absolute():
        reasons.append(f"{expected_checkpoint}: config_path is not absolute")
    if record.get("last_error") not in (None, "none", "never run"):
        reasons.append(f"{expected_checkpoint}: last_error is {record.get('last_error')}")
    if parse_iso_datetime(record.get("recorded_at")) is None:
        reasons.append(f"{expected_checkpoint}: recorded_at is missing or invalid")
    if parse_iso_datetime(record.get("last_success")) is None:
        reasons.append(f"{expected_checkpoint}: last_success is missing or invalid")
    for field in COUNTER_FIELDS:
        if not is_non_negative_int(record.get(field)):
            reasons.append(f"{expected_checkpoint}: {field} is missing or not a non-negative integer")
    return reasons


def validate_sequence(records: list[dict[str, Any]]) -> list[str]:
    reasons: list[str] = []
    previous_recorded_at: datetime | None = None
    previous_success: datetime | None = None
    previous_skipped_invalid: int | None = None
    label_values = {
        (record.get("status_launchd_label"), record.get("plist_label"))
        for record in records
        if record.get("status_launchd_label") or record.get("plist_label")
    }
    program_arg0_values = {record.get("program_arg0") for record in records if record.get("program_arg0")}
    config_path_values = {record.get("config_path") for record in records if record.get("config_path")}
    if len(label_values) > 1:
        reasons.append("LaunchAgent label changed across monitor records")
    if len(program_arg0_values) > 1:
        reasons.append("ProgramArguments[0] changed across monitor records")
    if len(config_path_values) > 1:
        reasons.append("LaunchAgent --config path changed across monitor records")

    for record in records:
        checkpoint = str(record.get("checkpoint"))
        current_recorded_at = parse_iso_datetime(record.get("recorded_at"))
        if previous_recorded_at and current_recorded_at and current_recorded_at < previous_recorded_at:
            reasons.append(f"{checkpoint}: recorded_at regressed from an earlier checkpoint")
        if current_recorded_at:
            previous_recorded_at = (
                max(previous_recorded_at, current_recorded_at) if previous_recorded_at else current_recorded_at
            )

        current_success = parse_iso_datetime(record.get("last_success"))
        if previous_success and current_success and current_success < previous_success:
            reasons.append(f"{checkpoint}: last_success regressed from an earlier checkpoint")
        if current_success:
            previous_success = max(previous_success, current_success) if previous_success else current_success

        skipped_invalid_value = record.get("skipped_invalid")
        if not is_non_negative_int(skipped_invalid_value):
            continue
        skipped_invalid = skipped_invalid_value
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
        "status_launchd_label": record.get("status_launchd_label"),
        "plist_label": record.get("plist_label"),
        "launchctl_label_seen": record.get("launchctl_label_seen"),
        "program_arguments": record.get("program_arguments"),
        "program_arg0": record.get("program_arg0"),
        "config_path": record.get("config_path"),
        "last_success": record.get("last_success"),
        "last_error": record.get("last_error"),
        "skipped_invalid": record.get("skipped_invalid"),
        "processed": record.get("processed"),
        "appended": record.get("appended"),
        "rewritten": record.get("rewritten"),
    }


def validate_program_arguments(
    record: dict[str, Any],
    expected_checkpoint: str,
    expected_program_arg0: str | None,
) -> list[str]:
    reasons: list[str] = []
    value = record.get("program_arguments")
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        return [f"{expected_checkpoint}: ProgramArguments are missing or malformed"]

    if len(value) != 4 or value[1] != "--config" or value[3] != "service-run":
        reasons.append(
            f"{expected_checkpoint}: Rust LaunchAgent ProgramArguments must be exactly "
            "binary, --config, config path, service-run"
        )
        return reasons

    program_arg0, _, config_path, service_command = value
    if expected_program_arg0 and program_arg0 != expected_program_arg0:
        reasons.append(f"{expected_checkpoint}: ProgramArguments[0] does not match expected binary")
    if record.get("program_arg0") != program_arg0:
        reasons.append(f"{expected_checkpoint}: ProgramArguments[0] does not match recorded program_arg0")
    if record.get("config_path") != config_path:
        reasons.append(f"{expected_checkpoint}: ProgramArguments --config path does not match recorded config_path")
    if record.get("service_command") != service_command:
        reasons.append(f"{expected_checkpoint}: ProgramArguments service command does not match recorded service_command")
    return reasons


def parse_iso_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or value in {"none", "never run"}:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def is_non_negative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    path.chmod(0o600)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
