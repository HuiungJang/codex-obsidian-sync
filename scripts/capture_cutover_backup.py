from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


DEFAULT_LABEL = "com.codex.obsidian-sync"
MARKER = ".codex-obsidian-sync-cutover-backup"
MARKER_CONTENT = "managed by capture_cutover_backup.py\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture rollback backup evidence before Rust cutover writes.")
    parser.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help="Dedicated empty/nonexistent codex-obsidian-sync-* backup directory under /tmp.",
    )
    parser.add_argument("--state-file", type=Path, help="sync-state.json path to copy for rollback.")
    parser.add_argument("--plist-file", type=Path, help="LaunchAgent plist path to copy for rollback.")
    parser.add_argument("--status-binary", default="codex-obsidian-sync", help="Command used to run status --json.")
    parser.add_argument("--config", type=Path, help="Optional config path passed to status --json.")
    parser.add_argument("--label", default=DEFAULT_LABEL, help="LaunchAgent label for launchctl print.")
    parser.add_argument("--status-json-file", type=Path, help="Offline status JSON input for tests.")
    parser.add_argument("--launchctl-print-file", type=Path, help="Offline launchctl print input for tests.")
    args = parser.parse_args()

    output_dir = prepare_output_dir(args.output_dir)
    manifest = capture_backup(args, output_dir)
    write_json(output_dir / "backup-manifest.json", manifest)
    print(json.dumps(manifest, indent=2, ensure_ascii=False))
    return 0 if manifest["ok"] else 1


def prepare_output_dir(path: Path) -> Path:
    requested_path = path.expanduser()
    if requested_path.is_symlink():
        raise RuntimeError(f"backup path is a symlink: {requested_path}")

    resolved = requested_path.resolve()
    validate_output_dir(resolved)
    if resolved.exists() and not resolved.is_dir():
        raise RuntimeError(f"backup path is not a directory: {resolved}")
    if resolved.exists() and any(resolved.iterdir()):
        raise RuntimeError(f"backup directory is not empty: {resolved}")
    resolved.mkdir(parents=True, exist_ok=True, mode=0o700)
    resolved.chmod(0o700)
    marker = resolved / MARKER
    marker.write_text(MARKER_CONTENT, encoding="utf-8")
    marker.chmod(0o600)
    return resolved


def validate_output_dir(path: Path) -> None:
    temp_roots = {Path(tempfile.gettempdir()).resolve(), Path("/tmp").resolve()}
    if path.parent not in temp_roots or not path.name.startswith("codex-obsidian-sync-"):
        roots = ", ".join(sorted(str(root) for root in temp_roots))
        raise RuntimeError(f"backup dir must be a dedicated codex-obsidian-sync-* path under {roots}: {path}")


def capture_backup(args: argparse.Namespace, output_dir: Path) -> dict[str, Any]:
    no_go_reasons: list[str] = []
    files: list[dict[str, Any]] = []

    status, status_record = capture_status(args, output_dir)
    files.append(status_record)

    state_file = args.state_file
    if state_file is None and isinstance(status.get("state_file"), str):
        state_file = Path(status["state_file"])
    if state_file:
        files.append(copy_backup_file(state_file, output_dir / "sync-state.json"))
    else:
        no_go_reasons.append("sync-state.json path was not provided and status JSON did not include state_file")

    plist_file = args.plist_file
    if plist_file is None and isinstance(status.get("plist_path"), str):
        plist_file = Path(status["plist_path"])
    if plist_file:
        files.append(copy_backup_file(plist_file, output_dir / "launchagent.plist"))
    else:
        no_go_reasons.append("LaunchAgent plist path was not provided and status JSON did not include plist_path")

    files.append(capture_launchctl(args, output_dir))
    missing_required = [file["name"] for file in files if file.get("required") and not file.get("exists")]
    if missing_required:
        no_go_reasons.append(f"required backup files are missing: {missing_required}")

    return {
        "ok": not no_go_reasons,
        "generated_at": datetime.now(UTC).isoformat(),
        "output_dir": str(output_dir),
        "no_go_reasons": no_go_reasons,
        "files": files,
    }


def capture_status(args: argparse.Namespace, output_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    destination = output_dir / "status.json"
    if args.status_json_file:
        status = read_json_object(args.status_json_file)
        destination.write_text(json.dumps(status, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    else:
        command = [args.status_binary]
        if args.config:
            command.extend(["--config", str(args.config.expanduser())])
        command.extend(["status", "--json"])
        result = run_capture(command)
        destination.write_text(result.stdout, encoding="utf-8")
        (output_dir / "status.stderr").write_text(result.stderr, encoding="utf-8")
        status = json.loads(result.stdout)
        if not isinstance(status, dict):
            raise RuntimeError("status --json did not return an object")
    destination.chmod(0o600)
    return status, file_record("status.json", destination, required=True)


def capture_launchctl(args: argparse.Namespace, output_dir: Path) -> dict[str, Any]:
    destination = output_dir / "launchctl-print.txt"
    if args.launchctl_print_file:
        reject_symlinked_evidence(args.launchctl_print_file)
        content = args.launchctl_print_file.read_text(encoding="utf-8")
        destination.write_text(content, encoding="utf-8")
    else:
        target = f"gui/{os.getuid()}/{args.label}"
        result = subprocess.run(["launchctl", "print", target], capture_output=True, text=True, check=False)
        destination.write_text(result.stdout, encoding="utf-8")
        (output_dir / "launchctl-print.stderr").write_text(result.stderr, encoding="utf-8")
    destination.chmod(0o600)
    return file_record("launchctl-print.txt", destination, required=False)


def copy_backup_file(source: Path, destination: Path) -> dict[str, Any]:
    requested_source = source.expanduser()
    reject_symlinked_evidence(requested_source)
    resolved = requested_source.resolve()
    if not resolved.is_file():
        return {
            "name": destination.name,
            "source": str(resolved),
            "path": str(destination),
            "exists": False,
            "required": True,
        }
    shutil.copy2(resolved, destination)
    destination.chmod(0o600)
    record = file_record(destination.name, destination, required=True)
    record["source"] = str(resolved)
    return record


def file_record(name: str, path: Path, *, required: bool) -> dict[str, Any]:
    exists = path.is_file()
    record: dict[str, Any] = {
        "name": name,
        "path": str(path),
        "exists": exists,
        "required": required,
    }
    if exists:
        payload = path.read_bytes()
        record["size"] = len(payload)
        record["sha256"] = hashlib.sha256(payload).hexdigest()
    return record


def read_json_object(path: Path) -> dict[str, Any]:
    reject_symlinked_evidence(path)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON file is not an object: {path}")
    return value


def reject_symlinked_evidence(path: Path) -> None:
    if path.expanduser().is_symlink():
        raise RuntimeError(f"evidence file is a symlink: {path}")


def run_capture(command: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(format_command_output(command, result))
    return result


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


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    path.chmod(0o600)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
