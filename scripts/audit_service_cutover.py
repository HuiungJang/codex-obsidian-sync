from __future__ import annotations

import argparse
import json
import plistlib
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_LABEL = "com.codex.obsidian-sync"


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit read-only evidence for the Python-to-Rust LaunchAgent cutover.")
    parser.add_argument("--pre-status-json-file", required=True, type=Path, help="Status JSON captured before stop.")
    parser.add_argument("--pre-plist-file", required=True, type=Path, help="LaunchAgent plist captured before stop.")
    parser.add_argument("--pre-launchctl-print-file", required=True, type=Path, help="launchctl print before stop.")
    parser.add_argument("--stopped-status-json-file", required=True, type=Path, help="Status JSON captured after stop.")
    parser.add_argument(
        "--stopped-launchctl-print-file",
        required=True,
        type=Path,
        help="launchctl print captured after stop; empty/nonexistent service output is expected.",
    )
    parser.add_argument("--post-status-json-file", required=True, type=Path, help="Status JSON captured after Rust start.")
    parser.add_argument("--post-plist-file", required=True, type=Path, help="LaunchAgent plist captured after Rust start.")
    parser.add_argument("--post-launchctl-print-file", required=True, type=Path, help="launchctl print after Rust start.")
    parser.add_argument("--expected-python-program-arg0", required=True, help="Expected Python rollback ProgramArguments[0].")
    parser.add_argument("--expected-rust-program-arg0", required=True, help="Expected Rust ProgramArguments[0].")
    parser.add_argument("--expected-label", default=DEFAULT_LABEL, help="Expected LaunchAgent label.")
    parser.add_argument("--output", type=Path, help="Write the cutover audit report to this path.")
    args = parser.parse_args()

    try:
        result = audit_service_cutover(
            pre_status=read_json_object(args.pre_status_json_file),
            pre_plist=read_plist(args.pre_plist_file),
            pre_launchctl=read_text(args.pre_launchctl_print_file),
            stopped_status=read_json_object(args.stopped_status_json_file),
            stopped_launchctl=read_text(args.stopped_launchctl_print_file),
            post_status=read_json_object(args.post_status_json_file),
            post_plist=read_plist(args.post_plist_file),
            post_launchctl=read_text(args.post_launchctl_print_file),
            expected_python_program_arg0=args.expected_python_program_arg0,
            expected_rust_program_arg0=args.expected_rust_program_arg0,
            expected_label=args.expected_label,
        )
    except (OSError, json.JSONDecodeError, plistlib.InvalidFileException, ValueError) as error:
        result = invalid_evidence_result(args, error)
    output = resolve_output_path(args.output)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        write_json(output, result)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["ok"] else 1


def resolve_output_path(path: Path | None) -> Path | None:
    if path is None:
        return None
    output = path.expanduser()
    if output.is_symlink():
        raise RuntimeError(f"output path is a symlink: {output}")
    return output


def invalid_evidence_result(args: argparse.Namespace, error: Exception) -> dict[str, Any]:
    return {
        "ok": False,
        "generated_at": datetime.now(UTC).isoformat(),
        "expected_label": args.expected_label,
        "expected_python_program_arg0": args.expected_python_program_arg0,
        "expected_rust_program_arg0": args.expected_rust_program_arg0,
        "snapshots": {},
        "no_go_reasons": [f"cutover evidence is invalid: {error}"],
    }


def audit_service_cutover(
    *,
    pre_status: dict[str, Any],
    pre_plist: dict[str, Any],
    pre_launchctl: str,
    stopped_status: dict[str, Any],
    stopped_launchctl: str,
    post_status: dict[str, Any],
    post_plist: dict[str, Any],
    post_launchctl: str,
    expected_python_program_arg0: str,
    expected_rust_program_arg0: str,
    expected_label: str = DEFAULT_LABEL,
) -> dict[str, Any]:
    snapshots = {
        "pre": snapshot_summary(pre_status, pre_plist, pre_launchctl),
        "stopped": stopped_snapshot_summary(stopped_status, stopped_launchctl),
        "post": snapshot_summary(post_status, post_plist, post_launchctl),
    }
    no_go_reasons: list[str] = []
    no_go_reasons.extend(
        validate_loaded_snapshot(
            name="pre",
            status=pre_status,
            plist=pre_plist,
            launchctl=pre_launchctl,
            expected_program_arg0=expected_python_program_arg0,
            expected_label=expected_label,
        )
    )
    no_go_reasons.extend(
        validate_stopped_snapshot(stopped_status, stopped_launchctl, expected_label=expected_label)
    )
    no_go_reasons.extend(
        validate_loaded_snapshot(
            name="post",
            status=post_status,
            plist=post_plist,
            launchctl=post_launchctl,
            expected_program_arg0=expected_rust_program_arg0,
            expected_label=expected_label,
        )
    )

    pre_arg0 = snapshots["pre"]["program_arg0"]
    post_arg0 = snapshots["post"]["program_arg0"]
    pre_config_path = snapshots["pre"]["config_path"]
    post_config_path = snapshots["post"]["config_path"]
    if pre_arg0 and post_arg0 and pre_arg0 == post_arg0:
        no_go_reasons.append("pre and post ProgramArguments[0] are identical")
    if pre_config_path and post_config_path and pre_config_path != post_config_path:
        no_go_reasons.append("pre and post LaunchAgent --config paths differ")
    no_go_reasons.extend(validate_status_plist_paths(pre_status, stopped_status, post_status))
    if expected_python_program_arg0 == expected_rust_program_arg0:
        no_go_reasons.append("expected Python and Rust binaries must differ")
    if not is_absolute_path(expected_python_program_arg0):
        no_go_reasons.append("expected Python ProgramArguments[0] must be an absolute path")
    if not is_absolute_path(expected_rust_program_arg0):
        no_go_reasons.append("expected Rust ProgramArguments[0] must be an absolute path")

    return {
        "ok": not no_go_reasons,
        "generated_at": datetime.now(UTC).isoformat(),
        "expected_label": expected_label,
        "expected_python_program_arg0": expected_python_program_arg0,
        "expected_rust_program_arg0": expected_rust_program_arg0,
        "snapshots": snapshots,
        "no_go_reasons": no_go_reasons,
    }


def validate_loaded_snapshot(
    *,
    name: str,
    status: dict[str, Any],
    plist: dict[str, Any],
    launchctl: str,
    expected_program_arg0: str,
    expected_label: str,
) -> list[str]:
    reasons: list[str] = []
    program_arguments = validated_program_arguments(plist.get("ProgramArguments"), name, reasons)
    plist_label = plist.get("Label")
    validate_status_flag(
        status,
        "configured",
        True,
        reasons,
        bool_mismatch_reason=f"{name}: status reports configured=false",
        type_mismatch_reason=f"{name}: status configured is not boolean true",
    )
    if status.get("launchd_label") != expected_label:
        reasons.append(f"{name}: status launchd_label does not match expected label")
    if plist_label != expected_label:
        reasons.append(f"{name}: LaunchAgent Label does not match expected label")
    validate_status_flag(
        status,
        "launchd_loaded",
        True,
        reasons,
        bool_mismatch_reason=f"{name}: status reports launchd_loaded=false",
        type_mismatch_reason=f"{name}: status launchd_loaded is not boolean true",
    )
    if not looks_loaded(launchctl):
        reasons.append(f"{name}: launchctl print did not return a loaded service")
    elif not launchctl_contains_label(launchctl, expected_label):
        reasons.append(f"{name}: launchctl print did not include expected label")
    if status.get("last_error") not in (None, "none", "never run"):
        reasons.append(f"{name}: last_error is {status.get('last_error')}")
    if not program_arguments:
        reasons.append(f"{name}: LaunchAgent ProgramArguments are missing")
    else:
        if not is_absolute_path(program_arguments[0]):
            reasons.append(f"{name}: ProgramArguments[0] is not an absolute path")
        if program_arguments[0] != expected_program_arg0:
            reasons.append(f"{name}: ProgramArguments[0] does not match expected binary")
        if program_arguments[-1] != "service-run":
            reasons.append(f"{name}: LaunchAgent does not end with service-run")
        validate_config_argument(program_arguments, name, reasons)
        if name == "pre":
            validate_python_program_arguments_shape(program_arguments, name, reasons)
        elif name == "post":
            validate_rust_program_arguments_shape(program_arguments, name, reasons)
    return reasons


def validate_stopped_snapshot(status: dict[str, Any], launchctl: str, *, expected_label: str) -> list[str]:
    reasons: list[str] = []
    validate_status_flag(
        status,
        "configured",
        True,
        reasons,
        bool_mismatch_reason="stopped: status reports configured=false",
        type_mismatch_reason="stopped: status configured is not boolean true",
    )
    if status.get("launchd_label") != expected_label:
        reasons.append("stopped: status launchd_label does not match expected label")
    validate_status_flag(
        status,
        "launchd_loaded",
        False,
        reasons,
        bool_mismatch_reason="stopped: status still reports launchd_loaded=true",
        type_mismatch_reason="stopped: status launchd_loaded is not boolean false",
    )
    if looks_loaded(launchctl):
        reasons.append("stopped: launchctl print still looks loaded")
    return reasons


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


def validate_status_plist_paths(
    pre_status: dict[str, Any],
    stopped_status: dict[str, Any],
    post_status: dict[str, Any],
) -> list[str]:
    reasons: list[str] = []
    paths = {
        "pre": pre_status.get("plist_path"),
        "stopped": stopped_status.get("plist_path"),
        "post": post_status.get("plist_path"),
    }
    valid_paths: dict[str, str] = {}
    for name, value in paths.items():
        if not isinstance(value, str) or not value:
            reasons.append(f"{name}: status plist_path is missing")
        elif not Path(value).is_absolute():
            reasons.append(f"{name}: status plist_path is not absolute")
        else:
            valid_paths[name] = value
    if len(valid_paths) == len(paths) and len(set(valid_paths.values())) != 1:
        reasons.append("pre, stopped, and post status plist_path values differ")
    return reasons


def snapshot_summary(status: dict[str, Any], plist: dict[str, Any], launchctl: str) -> dict[str, Any]:
    raw_program_arguments = plist.get("ProgramArguments")
    program_arguments = raw_program_arguments if isinstance(raw_program_arguments, list) else []
    return {
        "configured": status.get("configured"),
        "status_launchd_label": status.get("launchd_label"),
        "plist_label": plist.get("Label"),
        "launchd_loaded": status.get("launchd_loaded"),
        "launchctl_loaded": looks_loaded(launchctl),
        "plist_path": status.get("plist_path"),
        "program_arguments": program_arguments,
        "program_arguments_count": len(program_arguments),
        "program_arg0": program_arguments[0] if program_arguments else None,
        "config_path": config_argument_path(program_arguments),
        "service_command": program_arguments[-1] if program_arguments else None,
        "last_success": status.get("last_success"),
        "last_error": status.get("last_error"),
    }


def stopped_snapshot_summary(status: dict[str, Any], launchctl: str) -> dict[str, Any]:
    return {
        "configured": status.get("configured"),
        "status_launchd_label": status.get("launchd_label"),
        "launchd_loaded": status.get("launchd_loaded"),
        "launchctl_loaded": looks_loaded(launchctl),
        "plist_path": status.get("plist_path"),
        "last_success": status.get("last_success"),
        "last_error": status.get("last_error"),
    }


def looks_loaded(launchctl: str) -> bool:
    text = launchctl.strip()
    if not text:
        return False
    lowered = text.lower()
    unloaded_markers = (
        "could not find service",
        "no such process",
        "service is not loaded",
        "not found",
    )
    return not any(marker in lowered for marker in unloaded_markers)


def launchctl_contains_label(launchctl: str, expected_label: str) -> bool:
    label_pattern = re.escape(expected_label)
    return re.search(rf"(^|[\s/]){label_pattern}(?=\s*(=|\{{|$))", launchctl) is not None


def validated_program_arguments(value: Any, name: str, reasons: list[str]) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        reasons.append(f"{name}: LaunchAgent ProgramArguments is not a list")
        return []
    if not all(isinstance(item, str) and item for item in value):
        reasons.append(f"{name}: LaunchAgent ProgramArguments contains non-string or empty values")
        return []
    return value


def validate_config_argument(program_arguments: list[str], name: str, reasons: list[str]) -> None:
    config_indexes = [index for index, argument in enumerate(program_arguments) if argument == "--config"]
    if not config_indexes:
        reasons.append(f"{name}: LaunchAgent ProgramArguments must include --config before service-run")
        return
    if len(config_indexes) > 1:
        reasons.append(f"{name}: LaunchAgent ProgramArguments contains multiple --config values")
        return

    config_index = config_indexes[0]
    service_index = len(program_arguments) - 1
    if config_index >= service_index - 1:
        reasons.append(f"{name}: LaunchAgent --config value is missing before service-run")
        return

    config_path = program_arguments[config_index + 1]
    if not Path(config_path).is_absolute():
        reasons.append(f"{name}: LaunchAgent --config path is not absolute")


def validate_python_program_arguments_shape(program_arguments: list[str], name: str, reasons: list[str]) -> None:
    script_shape = len(program_arguments) == 4 and program_arguments[1] == "--config"
    module_shape = (
        len(program_arguments) == 6
        and program_arguments[1:4] == ["-m", "codex_obsidian_sync.cli", "--config"]
        and program_arguments[5] == "service-run"
    )
    if not script_shape and not module_shape:
        reasons.append(
            f"{name}: Python LaunchAgent ProgramArguments must be either binary, --config, config path, service-run "
            "or python, -m, codex_obsidian_sync.cli, --config, config path, service-run"
        )


def validate_rust_program_arguments_shape(program_arguments: list[str], name: str, reasons: list[str]) -> None:
    if len(program_arguments) != 4 or program_arguments[1] != "--config" or program_arguments[3] != "service-run":
        reasons.append(
            f"{name}: Rust LaunchAgent ProgramArguments must be exactly binary, --config, config path, service-run"
        )


def config_argument_path(program_arguments: list[str]) -> str | None:
    try:
        config_index = program_arguments.index("--config")
    except ValueError:
        return None
    value_index = config_index + 1
    if value_index >= len(program_arguments) - 1:
        return None
    return program_arguments[value_index]


def is_absolute_path(value: Any) -> bool:
    return isinstance(value, str) and Path(value).is_absolute()


def read_json_object(path: Path) -> dict[str, Any]:
    reject_symlinked_evidence(path)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON file is not an object: {path}")
    return value


def read_plist(path: Path) -> dict[str, Any]:
    reject_symlinked_evidence(path)
    value = plistlib.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise ValueError(f"plist file is not a dictionary: {path}")
    return value


def read_text(path: Path) -> str:
    reject_symlinked_evidence(path)
    return path.read_text(encoding="utf-8")


def reject_symlinked_evidence(path: Path) -> None:
    if path.expanduser().is_symlink():
        raise RuntimeError(f"evidence file is a symlink: {path}")


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    path.chmod(0o600)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
