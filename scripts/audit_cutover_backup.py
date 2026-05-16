from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from capture_cutover_backup import MARKER, MARKER_CONTENT

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
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        write_json(args.output, result)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["ok"] else 1


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
            manifest = read_json_object(manifest_path)
            manifest_output_dir = manifest.get("output_dir")
            if isinstance(manifest_output_dir, str):
                if Path(manifest_output_dir).expanduser().resolve() != root:
                    no_go_reasons.append("backup manifest output_dir does not match audited backup directory")
            else:
                no_go_reasons.append("backup manifest output_dir is missing")
            if manifest.get("ok") is not True:
                no_go_reasons.append("backup manifest ok is not true")
            manifest_reasons = manifest.get("no_go_reasons")
            if manifest_reasons not in ([], None):
                no_go_reasons.append("backup manifest contains no-go reasons")

            file_entries = manifest.get("files")
            if not isinstance(file_entries, list):
                no_go_reasons.append("backup manifest files are missing")
            else:
                seen_names: set[str] = set()
                for entry in file_entries:
                    if isinstance(entry, dict):
                        name = str(entry.get("name") or "")
                        if name in seen_names:
                            no_go_reasons.append(f"duplicate backup file entry: {name}")
                        seen_names.add(name)
                        if name and name not in EXPECTED_FILE_ENTRY_NAMES:
                            no_go_reasons.append(f"unexpected backup file entry: {name}")
                        result = audit_file_entry(root, entry)
                        file_results.append(result)
                        no_go_reasons.extend(result["no_go_reasons"])
                    else:
                        no_go_reasons.append("backup manifest contains a non-object file entry")
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
