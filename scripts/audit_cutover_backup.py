from __future__ import annotations

import argparse
import hashlib
import json
import plistlib
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from capture_cutover_backup import DEFAULT_LABEL, MARKER, MARKER_CONTENT

EXPECTED_FILE_ENTRY_NAMES = {
    "status.json",
    "sync-state.json",
    "launchagent.plist",
    "launchctl-print.txt",
}
ALLOWED_UNRECORDED_NAMES = {
    MARKER,
    "backup-manifest.json",
    "status.stderr",
    "launchctl-print.stderr",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit a captured cutover rollback backup manifest.")
    parser.add_argument("--backup-dir", required=True, type=Path, help="Directory created by capture_cutover_backup.py.")
    parser.add_argument("--output", type=Path, help="Write the backup audit report to this path.")
    args = parser.parse_args()

    result = audit_backup_dir(args.backup_dir)
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


def audit_backup_dir(backup_dir: Path) -> dict[str, Any]:
    requested_root = backup_dir.expanduser()
    root = requested_root.resolve()
    manifest_path = root / "backup-manifest.json"
    no_go_reasons: list[str] = []
    file_results: list[dict[str, Any]] = []
    manifest: dict[str, Any] | None = None

    if requested_root.is_symlink():
        no_go_reasons.append(f"backup directory is a symlink: {requested_root}")
    elif not root.is_dir():
        no_go_reasons.append(f"backup directory is missing: {root}")
    else:
        marker = root / MARKER
        if not marker.is_file():
            no_go_reasons.append("backup directory marker is missing")
        elif marker.is_symlink():
            no_go_reasons.append("backup directory marker is a symlink")
        elif marker.read_text(encoding="utf-8") != MARKER_CONTENT:
            no_go_reasons.append("backup directory marker content is not managed by capture_cutover_backup.py")

        if not manifest_path.is_file():
            no_go_reasons.append("backup manifest is missing")
        elif manifest_path.is_symlink():
            no_go_reasons.append("backup manifest is a symlink")
        else:
            try:
                manifest = read_json_object(manifest_path)
            except (OSError, json.JSONDecodeError, RuntimeError) as error:
                no_go_reasons.append(f"backup manifest is invalid: {error}")

        if manifest is not None:
            manifest_output_dir = manifest.get("output_dir")
            if isinstance(manifest_output_dir, str):
                if Path(manifest_output_dir).expanduser().resolve() != root:
                    no_go_reasons.append("backup manifest output_dir does not match audited backup directory")
            else:
                no_go_reasons.append("backup manifest output_dir is missing")
            if manifest.get("ok") is not True:
                no_go_reasons.append("backup manifest ok is not true")
            if not is_parseable_timestamp(manifest.get("generated_at")):
                no_go_reasons.append("backup manifest generated_at is missing or invalid")
            manifest_reasons = manifest.get("no_go_reasons")
            if manifest_reasons not in ([], None):
                no_go_reasons.append("backup manifest contains no-go reasons")

            file_entries = manifest.get("files")
            if not isinstance(file_entries, list):
                no_go_reasons.append("backup manifest files are missing")
            else:
                seen_names: set[str] = set()
                seen_paths: set[Path] = set()
                manifest_entries: list[dict[str, Any]] = []
                for entry in file_entries:
                    if isinstance(entry, dict):
                        manifest_entries.append(entry)
                        name = str(entry.get("name") or "")
                        if name in seen_names:
                            no_go_reasons.append(f"duplicate backup file entry: {name}")
                        seen_names.add(name)
                        if name and name not in EXPECTED_FILE_ENTRY_NAMES:
                            no_go_reasons.append(f"unexpected backup file entry: {name}")
                        result = audit_file_entry(root, entry)
                        file_results.append(result)
                        result_path = Path(str(result["path"]))
                        if result_path in seen_paths:
                            no_go_reasons.append(f"duplicate backup file path: {result_path.name}")
                        seen_paths.add(result_path)
                        no_go_reasons.extend(result["no_go_reasons"])
                    else:
                        no_go_reasons.append("backup manifest contains a non-object file entry")
                no_go_reasons.extend(validate_status_sources(root, manifest_entries))
                no_go_reasons.extend(validate_launchagent_backup(root))
        no_go_reasons.extend(audit_directory_contents(root, file_results))

    required_names = {result["name"] for result in file_results if result.get("required")}
    for name in ("status.json", "sync-state.json", "launchagent.plist"):
        if name not in required_names:
            no_go_reasons.append(f"required backup file entry is missing: {name}")

    return {
        "ok": not no_go_reasons,
        "generated_at": datetime.now(UTC).isoformat(),
        "backup_dir": str(root),
        "manifest_path": str(manifest_path),
        "manifest_generated_at": manifest.get("generated_at") if manifest else None,
        "files": file_results,
        "no_go_reasons": no_go_reasons,
    }


def validate_status_sources(root: Path, file_entries: list[dict[str, Any]]) -> list[str]:
    status_path = root / "status.json"
    if status_path.is_symlink() or not status_path.is_file():
        return []
    try:
        status = read_json_object(status_path)
    except (OSError, json.JSONDecodeError, RuntimeError) as error:
        return [f"backup status.json is invalid: {error}"]

    entries_by_name = {str(entry.get("name") or ""): entry for entry in file_entries}
    return (
        validate_source_path(
            entries_by_name,
            backup_name="sync-state.json",
            status=status,
            status_field="state_file",
        )
        + validate_source_path(
            entries_by_name,
            backup_name="launchagent.plist",
            status=status,
            status_field="plist_path",
        )
    )


def validate_source_path(
    entries_by_name: dict[str, dict[str, Any]],
    *,
    backup_name: str,
    status: dict[str, Any],
    status_field: str,
) -> list[str]:
    reasons: list[str] = []
    status_value = status.get(status_field)
    if not isinstance(status_value, str) or not status_value:
        reasons.append(f"backup status.json {status_field} is missing")
        return reasons
    status_path = Path(status_value).expanduser()
    if not status_path.is_absolute():
        reasons.append(f"backup status.json {status_field} is not absolute")
        return reasons

    entry = entries_by_name.get(backup_name)
    if entry is None:
        return reasons
    source_value = entry.get("source")
    if not isinstance(source_value, str) or not source_value:
        reasons.append(f"backup file source is missing: {backup_name}")
        return reasons
    source_path = Path(source_value).expanduser()
    if not source_path.is_absolute():
        reasons.append(f"backup file source is not absolute: {backup_name}")
    elif source_path.resolve() != status_path.resolve():
        reasons.append(f"backup file source does not match status {status_field}: {backup_name}")
    return reasons


def validate_launchagent_backup(root: Path) -> list[str]:
    path = root / "launchagent.plist"
    if path.is_symlink() or not path.is_file():
        return []
    try:
        plist = plistlib.loads(path.read_bytes())
    except plistlib.InvalidFileException as error:
        return [f"backup launchagent.plist is invalid: {error}"]
    if not isinstance(plist, dict):
        return ["backup launchagent.plist is not a dictionary"]

    reasons: list[str] = []
    if plist.get("Label") != DEFAULT_LABEL:
        reasons.append("backup LaunchAgent Label does not match expected label")
    program_arguments = validated_program_arguments(plist.get("ProgramArguments"), reasons)
    if not program_arguments:
        reasons.append("backup LaunchAgent ProgramArguments are missing")
    else:
        if not Path(program_arguments[0]).is_absolute():
            reasons.append("backup LaunchAgent ProgramArguments[0] is not absolute")
        if program_arguments[-1] != "service-run":
            reasons.append("backup LaunchAgent does not end with service-run")
        validate_config_argument(program_arguments, reasons)
    return reasons


def validated_program_arguments(value: Any, reasons: list[str]) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        reasons.append("backup LaunchAgent ProgramArguments is not a list")
        return []
    if not all(isinstance(item, str) and item for item in value):
        reasons.append("backup LaunchAgent ProgramArguments contains non-string or empty values")
        return []
    return value


def validate_config_argument(program_arguments: list[str], reasons: list[str]) -> None:
    config_indexes = [index for index, argument in enumerate(program_arguments) if argument == "--config"]
    if not config_indexes:
        reasons.append("backup LaunchAgent ProgramArguments must include --config before service-run")
        return
    if len(config_indexes) > 1:
        reasons.append("backup LaunchAgent ProgramArguments contains multiple --config values")
        return

    config_index = config_indexes[0]
    service_index = len(program_arguments) - 1
    if config_index >= service_index - 1:
        reasons.append("backup LaunchAgent --config value is missing before service-run")
        return

    config_path = program_arguments[config_index + 1]
    if not Path(config_path).is_absolute():
        reasons.append("backup LaunchAgent --config path is not absolute")


def audit_file_entry(root: Path, entry: dict[str, Any]) -> dict[str, Any]:
    name = str(entry.get("name") or "")
    path_value = entry.get("path")
    reasons: list[str] = []
    path = Path(path_value).expanduser() if isinstance(path_value, str) else root / name
    if not path.is_absolute():
        path = root / path
    resolved_path = path.resolve()

    expected_exists = entry.get("exists") is True
    required = entry.get("required") is True
    details: dict[str, Any] = {
        "name": name,
        "path": str(resolved_path),
        "required": required,
        "expected_exists": expected_exists,
        "exists": path.is_file(),
    }
    if not name:
        reasons.append("backup file entry name is missing")
    elif resolved_path.name != name:
        reasons.append(f"backup file path basename does not match entry name: {name}")
    if required and not expected_exists:
        reasons.append(f"required backup file exists flag is not true: {name}")
    if not is_relative_to(resolved_path, root):
        reasons.append(f"backup file path is outside backup directory: {name}")
        return {**details, "no_go_reasons": reasons}
    if path.is_symlink():
        reasons.append(f"backup file is a symlink: {name}")
        return {**details, "no_go_reasons": reasons}
    if not path.is_file():
        if expected_exists or required:
            reasons.append(f"backup file is missing: {name}")
        return {**details, "no_go_reasons": reasons}

    payload = path.read_bytes()
    actual_size = len(payload)
    actual_checksum = hashlib.sha256(payload).hexdigest()
    details["size"] = actual_size
    details["sha256"] = actual_checksum

    expected_size = entry.get("size")
    if isinstance(expected_size, int) and expected_size != actual_size:
        reasons.append(f"backup file size mismatch: {name}")
    if required and not is_non_negative_int(expected_size):
        reasons.append(f"required backup file size is missing or invalid: {name}")
    expected_checksum = entry.get("sha256")
    if isinstance(expected_checksum, str) and expected_checksum != actual_checksum:
        reasons.append(f"backup file checksum mismatch: {name}")
    if required and not isinstance(expected_checksum, str):
        reasons.append(f"required backup file checksum is missing: {name}")
    return {**details, "no_go_reasons": reasons}


def audit_directory_contents(root: Path, file_results: list[dict[str, Any]]) -> list[str]:
    expected_names = {Path(str(result["path"])).name for result in file_results if result.get("path")}
    allowed_names = expected_names | ALLOWED_UNRECORDED_NAMES
    reasons: list[str] = []
    for child in sorted(root.iterdir(), key=lambda path: path.name):
        if child.name not in allowed_names:
            reasons.append(f"unexpected backup directory entry: {child.name}")
        elif child.name in ALLOWED_UNRECORDED_NAMES and child.is_symlink():
            reasons.append(f"unrecorded backup file is a symlink: {child.name}")
    return reasons


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def is_non_negative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def is_parseable_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        timestamp = datetime.fromisoformat(value)
    except ValueError:
        return False
    return timestamp.tzinfo is not None and timestamp.utcoffset() is not None


def read_json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON file is not an object: {path}")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    path.chmod(0o600)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
