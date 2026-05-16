from __future__ import annotations

import argparse
import json
import os
import plistlib
import re
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


DEFAULT_LABEL = "com.codex.obsidian-sync"
MARKER = ".codex-obsidian-sync-cutover-monitor"
MARKER_CONTENT = "managed by record_cutover_monitor.py\n"
CHECKPOINTS = {"+5m", "+1h", "+4h", "+24h"}
SUMMARY_COUNTER_FIELDS = ("processed", "appended", "rewritten", "skipped_invalid")


def main() -> int:
    parser = argparse.ArgumentParser(description="Record one post-cutover monitoring checkpoint.")
    parser.add_argument("--checkpoint", required=True, help="Checkpoint label, for example +5m, +1h, +4h, or +24h.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("/tmp/codex-obsidian-sync-cutover-monitor"),
        help="Dedicated codex-obsidian-sync-* directory for monitor records.",
    )
    parser.add_argument("--binary", default="codex-obsidian-sync", help="Command to run status --json.")
    parser.add_argument("--config", type=Path, help="Optional config path passed to the status command.")
    parser.add_argument("--plist", type=Path, default=default_plist_path(), help="LaunchAgent plist path.")
    parser.add_argument("--label", default=DEFAULT_LABEL, help="LaunchAgent label.")
    parser.add_argument("--expected-program-arg0", help="Expected LaunchAgent ProgramArguments[0].")
    parser.add_argument("--allow-unloaded", action="store_true", help="Do not fail when launchd/status reports unloaded.")
    parser.add_argument(
        "--allow-skipped-invalid-increase",
        action="store_true",
        help="Do not fail if skipped_invalid is higher than previous records.",
    )
    parser.add_argument("--note-path", action="append", type=Path, default=[], help="Optional note path to record.")
    parser.add_argument("--status-json-file", type=Path, help="Offline status JSON input for tests.")
    parser.add_argument("--plist-file", type=Path, help="Offline plist input for tests.")
    parser.add_argument("--launchctl-print-file", type=Path, help="Offline launchctl print input for tests.")
    args = parser.parse_args()

    output_dir = prepare_output_dir(args.output_dir)
    record = record_checkpoint(args, output_dir)
    record_path = output_dir / f"{sanitize_checkpoint(args.checkpoint)}.json"
    write_json(record_path, record)
    print(json.dumps(record, indent=2, ensure_ascii=False))
    return 0 if record["ok"] else 1


def default_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{DEFAULT_LABEL}.plist"


def prepare_output_dir(path: Path) -> Path:
    requested_path = path.expanduser()
    if requested_path.exists() and requested_path.is_symlink():
        raise RuntimeError(f"Output path is a symlink: {requested_path}")
    resolved_path = requested_path.resolve()
    validate_output_dir(resolved_path)
    if resolved_path.exists() and not resolved_path.is_dir():
        raise RuntimeError(f"Output path is not a directory: {resolved_path}")
    resolved_path.mkdir(parents=True, exist_ok=True, mode=0o700)
    resolved_path.chmod(0o700)
    marker = resolved_path / MARKER
    if marker.is_symlink():
        raise RuntimeError(f"Refusing symlinked monitor directory marker: {marker}")
    if marker.exists() and marker.read_text(encoding="utf-8") != MARKER_CONTENT:
        raise RuntimeError(f"Refusing unmanaged monitor directory: {resolved_path}")
    marker.write_text(MARKER_CONTENT, encoding="utf-8")
    marker.chmod(0o600)
    return resolved_path


def validate_output_dir(path: Path) -> None:
    temp_roots = {Path(tempfile.gettempdir()).resolve(), Path("/tmp").resolve()}
    if path.parent not in temp_roots or not path.name.startswith("codex-obsidian-sync-"):
        roots = ", ".join(sorted(str(root) for root in temp_roots))
        raise RuntimeError(f"Output dir must be a dedicated codex-obsidian-sync-* path under {roots}: {path}")


def record_checkpoint(args: argparse.Namespace, output_dir: Path) -> dict[str, Any]:
    timestamp = datetime.now(UTC).isoformat()
    status = load_status(args, output_dir)
    plist = load_plist(args, output_dir)
    launchctl = load_launchctl_print(args, output_dir)
    previous = load_previous_records(output_dir, args.checkpoint)
    return build_record(
        checkpoint=args.checkpoint,
        recorded_at=timestamp,
        status=status,
        plist=plist,
        launchctl=launchctl,
        previous_records=previous,
        expected_label=args.label,
        expected_program_arg0=args.expected_program_arg0,
        allow_unloaded=args.allow_unloaded,
        allow_skipped_invalid_increase=args.allow_skipped_invalid_increase,
        note_paths=args.note_path,
    )


def load_status(args: argparse.Namespace, output_dir: Path) -> dict[str, Any]:
    if args.status_json_file:
        reject_symlinked_evidence(args.status_json_file)
        return json.loads(args.status_json_file.read_text(encoding="utf-8"))

    command = [args.binary]
    if args.config:
        command.extend(["--config", str(args.config)])
    command.extend(["status", "--json"])
    result = run_capture(command)
    (output_dir / "latest-status.stdout").write_text(result.stdout, encoding="utf-8")
    (output_dir / "latest-status.stderr").write_text(result.stderr, encoding="utf-8")
    return json.loads(result.stdout)


def load_plist(args: argparse.Namespace, output_dir: Path) -> dict[str, Any]:
    plist_path = args.plist_file or args.plist
    reject_symlinked_evidence(plist_path)
    content = plist_path.read_bytes()
    (output_dir / "latest-launchagent.plist").write_bytes(content)
    return plistlib.loads(content)


def load_launchctl_print(args: argparse.Namespace, output_dir: Path) -> str:
    if args.launchctl_print_file:
        reject_symlinked_evidence(args.launchctl_print_file)
        content = args.launchctl_print_file.read_text(encoding="utf-8")
        (output_dir / "latest-launchctl-print.txt").write_text(content, encoding="utf-8")
        return content

    target = f"gui/{os.getuid()}/{args.label}"
    result = subprocess.run(["launchctl", "print", target], capture_output=True, text=True, check=False)
    (output_dir / "latest-launchctl-print.txt").write_text(result.stdout, encoding="utf-8")
    (output_dir / "latest-launchctl-print.stderr").write_text(result.stderr, encoding="utf-8")
    if result.returncode != 0:
        return ""
    return result.stdout


def run_capture(command: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(format_command_output(command, result))
    return result


def build_record(
    *,
    checkpoint: str,
    recorded_at: str,
    status: dict[str, Any],
    plist: dict[str, Any],
    launchctl: str,
    previous_records: list[dict[str, Any]],
    expected_label: str,
    expected_program_arg0: str | None,
    allow_unloaded: bool,
    allow_skipped_invalid_increase: bool,
    note_paths: list[Path],
) -> dict[str, Any]:
    no_go_reasons: list[str] = []
    program_arguments = validated_program_arguments(plist.get("ProgramArguments"), no_go_reasons)
    config_path = config_argument_path(program_arguments)
    status_launchd_label = status.get("launchd_label")
    plist_label = plist.get("Label")
    last_summary = status.get("last_summary") if isinstance(status.get("last_summary"), dict) else {}
    summary_counters = {
        field: non_negative_int_value(last_summary.get(field))
        for field in SUMMARY_COUNTER_FIELDS
    }
    skipped_invalid = summary_counters["skipped_invalid"]
    previous_skipped_invalid_values = [
        value
        for value in (non_negative_int_value(record.get("skipped_invalid")) for record in previous_records)
        if value is not None
    ]
    previous_skipped_invalid = max(
        previous_skipped_invalid_values,
        default=skipped_invalid or 0,
    )
    last_success = status.get("last_success")
    current_success = parse_iso_datetime(last_success)
    previous_success = max(
        [
            parsed
            for parsed in (parse_iso_datetime(record.get("last_success")) for record in previous_records)
            if parsed is not None
        ],
        default=current_success,
    )
    if checkpoint not in CHECKPOINTS:
        no_go_reasons.append(f"unexpected checkpoint: {checkpoint}")
    validate_status_flag(
        status,
        "configured",
        True,
        no_go_reasons,
        bool_mismatch_reason="status reports configured=false",
        type_mismatch_reason="status configured is not boolean true",
    )
    if status_launchd_label != expected_label:
        no_go_reasons.append("status launchd_label does not match expected label")
    if plist_label != expected_label:
        no_go_reasons.append("LaunchAgent Label does not match expected label")
    if allow_unloaded:
        if not isinstance(status.get("launchd_loaded"), bool):
            no_go_reasons.append("status launchd_loaded is not boolean")
    else:
        validate_status_flag(
            status,
            "launchd_loaded",
            True,
            no_go_reasons,
            bool_mismatch_reason="status reports launchd_loaded=false",
            type_mismatch_reason="status launchd_loaded is not boolean true",
        )
    launchctl_loaded = looks_loaded(launchctl)
    if not allow_unloaded and not launchctl_loaded:
        no_go_reasons.append("launchctl print did not return a loaded service")
    launchctl_label_seen = launchctl_contains_label(launchctl, expected_label)
    if launchctl_loaded and not launchctl_label_seen:
        no_go_reasons.append("launchctl print did not include expected label")
    if status.get("last_error") not in (None, "none", "never run"):
        no_go_reasons.append(f"last_error is {status.get('last_error')}")
    if expected_program_arg0 and (not program_arguments or program_arguments[0] != expected_program_arg0):
        no_go_reasons.append("ProgramArguments[0] does not match expected binary")
    if not program_arguments:
        no_go_reasons.append("LaunchAgent ProgramArguments are missing")
    else:
        if program_arguments[-1] != "service-run":
            no_go_reasons.append("LaunchAgent does not end with service-run")
        validate_config_argument(program_arguments, no_go_reasons)
        validate_rust_program_arguments_shape(program_arguments, no_go_reasons)
    for field, value in summary_counters.items():
        if value is None:
            no_go_reasons.append(f"last_summary {field} is missing or not a non-negative integer")
    if (
        skipped_invalid is not None
        and skipped_invalid > previous_skipped_invalid
        and not allow_skipped_invalid_increase
    ):
        no_go_reasons.append("skipped_invalid increased from previous monitor record")
    if current_success is None:
        no_go_reasons.append("last_success is missing or invalid")
    if current_success and previous_success and current_success < previous_success:
        no_go_reasons.append("last_success regressed from previous monitor record")

    notes = [note_snapshot(path) for path in note_paths]
    missing_notes = [note["path"] for note in notes if not note["exists"]]
    if missing_notes:
        no_go_reasons.append(f"monitored note paths are missing: {missing_notes}")

    return {
        "ok": not no_go_reasons,
        "checkpoint": checkpoint,
        "recorded_at": recorded_at,
        "expected_label": expected_label,
        "no_go_reasons": no_go_reasons,
        "configured": status.get("configured"),
        "status_launchd_label": status_launchd_label,
        "plist_label": plist_label,
        "launchd_loaded": status.get("launchd_loaded"),
        "launchctl_loaded": launchctl_loaded,
        "launchctl_label_seen": launchctl_label_seen,
        "plist_path": status.get("plist_path"),
        "program_arguments": program_arguments,
        "program_arg0": program_arguments[0] if program_arguments else None,
        "program_arguments_count": len(program_arguments),
        "config_path": config_path,
        "service_command": program_arguments[-1] if program_arguments else None,
        "last_run": status.get("last_run"),
        "last_success": last_success,
        "last_error": status.get("last_error"),
        "skipped_invalid": skipped_invalid,
        "processed": summary_counters["processed"],
        "appended": summary_counters["appended"],
        "rewritten": summary_counters["rewritten"],
        "last_summary": last_summary,
        "notes": notes,
    }


def load_previous_records(output_dir: Path, checkpoint: str) -> list[dict[str, Any]]:
    current_name = checkpoint_record_name(checkpoint)
    record_names = {checkpoint_record_name(value) for value in CHECKPOINTS}
    records = []
    for path in sorted(output_dir.glob("*.json")):
        if path.name == current_name or path.name not in record_names:
            continue
        if path.is_symlink():
            raise RuntimeError(f"monitor record is a symlink: {path.name}")
        try:
            records.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return records


def note_snapshot(path: Path) -> dict[str, Any]:
    resolved = path.expanduser()
    if not resolved.exists():
        return {"path": str(resolved), "exists": False}
    stat = resolved.stat()
    return {
        "path": str(resolved),
        "exists": True,
        "size": stat.st_size,
        "mtime": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
    }


def parse_iso_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or value in {"none", "never run"}:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def non_negative_int_value(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def validated_program_arguments(value: Any, reasons: list[str]) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        reasons.append("LaunchAgent ProgramArguments is not a list")
        return []
    if not all(isinstance(item, str) and item for item in value):
        reasons.append("LaunchAgent ProgramArguments contains non-string or empty values")
        return []
    return value


def validate_config_argument(program_arguments: list[str], reasons: list[str]) -> None:
    config_indexes = [index for index, argument in enumerate(program_arguments) if argument == "--config"]
    if not config_indexes:
        reasons.append("LaunchAgent ProgramArguments must include --config before service-run")
        return
    if len(config_indexes) > 1:
        reasons.append("LaunchAgent ProgramArguments contains multiple --config values")
        return

    config_index = config_indexes[0]
    service_index = len(program_arguments) - 1
    if config_index >= service_index - 1:
        reasons.append("LaunchAgent --config value is missing before service-run")
        return

    config_path = program_arguments[config_index + 1]
    if not Path(config_path).is_absolute():
        reasons.append("LaunchAgent --config path is not absolute")


def validate_rust_program_arguments_shape(program_arguments: list[str], reasons: list[str]) -> None:
    if len(program_arguments) != 4 or program_arguments[1] != "--config" or program_arguments[3] != "service-run":
        reasons.append("Rust LaunchAgent ProgramArguments must be exactly binary, --config, config path, service-run")


def config_argument_path(program_arguments: list[str]) -> str | None:
    try:
        config_index = program_arguments.index("--config")
    except ValueError:
        return None
    value_index = config_index + 1
    if value_index >= len(program_arguments) - 1:
        return None
    return program_arguments[value_index]


def validate_status_flag(
    status: dict[str, Any],
    field: str,
    expected: bool,
    reasons: list[str],
    *,
    bool_mismatch_reason: str,
    type_mismatch_reason: str,
) -> None:
    value = status.get(field)
    if value is expected:
        return
    reasons.append(bool_mismatch_reason if isinstance(value, bool) else type_mismatch_reason)


def looks_loaded(launchctl: str) -> bool:
    text = launchctl.strip()
    if not text:
        return False
    lowered = text.lower()
    unloaded_markers = (
        "could not find service",
        "not loaded",
        "service is not loaded",
        "no such process",
    )
    return not any(marker in lowered for marker in unloaded_markers)


def launchctl_contains_label(launchctl: str, expected_label: str) -> bool:
    label_pattern = re.escape(expected_label)
    return re.search(rf"(^|[\s/]){label_pattern}(?=\s*(=|\{{|$))", launchctl) is not None


def sanitize_checkpoint(value: str) -> str:
    return value.replace("+", "plus").replace("/", "_").replace(":", "_")


def checkpoint_record_name(checkpoint: str) -> str:
    return f"{sanitize_checkpoint(checkpoint)}.json"


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    path.chmod(0o600)


def reject_symlinked_evidence(path: Path) -> None:
    if path.expanduser().is_symlink():
        raise RuntimeError(f"evidence file is a symlink: {path}")


def format_command_output(command: list[str], result: subprocess.CompletedProcess[str]) -> str:
    return "\n".join(
        [
            f"Command failed ({result.returncode}): {' '.join(command)}",
            "--- stdout ---",
            result.stdout,
            "--- stderr ---",
            result.stderr,
        ]
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
