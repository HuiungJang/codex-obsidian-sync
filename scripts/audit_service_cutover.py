from __future__ import annotations

import argparse
import json
import plistlib
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
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        write_json(args.output, result)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["ok"] else 1


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
    if pre_arg0 and post_arg0 and pre_arg0 == post_arg0:
        no_go_reasons.append("pre and post ProgramArguments[0] are identical")
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
    if not status.get("configured"):
        reasons.append(f"{name}: status reports configured=false")
    if status.get("launchd_label") != expected_label:
        reasons.append(f"{name}: status launchd_label does not match expected label")
    if plist_label != expected_label:
        reasons.append(f"{name}: LaunchAgent Label does not match expected label")
    if not status.get("launchd_loaded"):
        reasons.append(f"{name}: status reports launchd_loaded=false")
    if not looks_loaded(launchctl):
        reasons.append(f"{name}: launchctl print did not return a loaded service")
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
    return reasons


def validate_stopped_snapshot(status: dict[str, Any], launchctl: str, *, expected_label: str) -> list[str]:
    reasons: list[str] = []
    if not status.get("configured"):
        reasons.append("stopped: status reports configured=false")
    if status.get("launchd_label") != expected_label:
        reasons.append("stopped: status launchd_label does not match expected label")
    if status.get("launchd_loaded"):
        reasons.append("stopped: status still reports launchd_loaded=true")
    if looks_loaded(launchctl):
        reasons.append("stopped: launchctl print still looks loaded")
    return reasons


def snapshot_summary(status: dict[str, Any], plist: dict[str, Any], launchctl: str) -> dict[str, Any]:
    raw_program_arguments = plist.get("ProgramArguments")
    program_arguments = raw_program_arguments if isinstance(raw_program_arguments, list) else []
    return {
        "configured": bool(status.get("configured")),
        "status_launchd_label": status.get("launchd_label"),
        "plist_label": plist.get("Label"),
        "launchd_loaded": bool(status.get("launchd_loaded")),
        "launchctl_loaded": looks_loaded(launchctl),
        "plist_path": status.get("plist_path"),
        "program_arg0": program_arguments[0] if program_arguments else None,
        "service_command": program_arguments[-1] if program_arguments else None,
        "last_success": status.get("last_success"),
        "last_error": status.get("last_error"),
    }


def stopped_snapshot_summary(status: dict[str, Any], launchctl: str) -> dict[str, Any]:
    return {
        "configured": bool(status.get("configured")),
        "status_launchd_label": status.get("launchd_label"),
        "launchd_loaded": bool(status.get("launchd_loaded")),
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
