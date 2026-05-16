from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


DEFAULT_REPOSITORY = "HuiungJang/codex-obsidian-sync"
FORMULA_NAME = "codex-obsidian-sync"
MONITOR_MARKER = ".codex-obsidian-sync-cutover-monitor"
MONITOR_MARKER_CONTENT = "managed by record_cutover_monitor.py\n"
TARGETS = ("aarch64-apple-darwin", "x86_64-apple-darwin")


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit Rust cutover readiness without mutating services.")
    parser.add_argument(
        "--version",
        help="Release version or tag, for example 0.1.0 or v0.1.0. Defaults to rust/Cargo.toml.",
    )
    parser.add_argument(
        "--repository",
        default=DEFAULT_REPOSITORY,
        help=f"GitHub repository in owner/name form. Defaults to {DEFAULT_REPOSITORY}.",
    )
    parser.add_argument("--release-dir", type=Path, help="Directory containing release tarballs and .sha256 files.")
    parser.add_argument("--homebrew-formula", type=Path, help="Generated Homebrew formula path.")
    parser.add_argument("--expected-rust-binary", help="Rust binary path expected after cutover.")
    parser.add_argument(
        "--rollback-binary",
        default=str(Path.home() / ".local" / "bin" / FORMULA_NAME),
        help="Python/pipx rollback binary path.",
    )
    parser.add_argument("--status-binary", default=FORMULA_NAME, help="Command used to run status --json.")
    parser.add_argument("--config", type=Path, help="Optional config path passed to status --json.")
    parser.add_argument("--status-json-file", type=Path, help="Offline status JSON input.")
    parser.add_argument("--plist-file", type=Path, help="Offline LaunchAgent plist input.")
    parser.add_argument(
        "--expected-current-program-arg0",
        help="Optional ProgramArguments[0] expected before cutover, usually the Python rollback path.",
    )
    parser.add_argument(
        "--monitor-dir",
        type=Path,
        default=Path("/tmp/codex-obsidian-sync-cutover-monitor"),
        help="Post-cutover monitor output directory to validate.",
    )
    parser.add_argument("--output", type=Path, help="Write the readiness report to this path.")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    version = normalize_version(args.version or cargo_version(repo_root / "rust" / "Cargo.toml"))
    repository = validate_repository(args.repository)
    release_dir = args.release_dir.expanduser().resolve() if args.release_dir else None
    formula_path = args.homebrew_formula.expanduser().resolve() if args.homebrew_formula else None

    release_checks = audit_release_dir(release_dir)
    checksums = {
        check["details"]["target"]: check["details"]["checksum"]
        for check in release_checks
        if check["ok"] and check["details"].get("checksum")
    }
    status, status_check = collect_status(args)
    plist, plist_check = collect_plist(args.plist_file, status)

    checks = [
        *release_checks,
        audit_homebrew_formula(formula_path, version, repository, checksums),
        *audit_release_smoke_summaries(release_dir, version),
        audit_homebrew_smoke_summary(release_dir, formula_path, version),
        audit_expected_rust_binary(args.expected_rust_binary, version),
        audit_rollback_binary(args.rollback_binary, args.expected_rust_binary),
        status_check,
        plist_check,
        audit_launchagent(status, plist, args.expected_current_program_arg0),
        audit_monitor_dir(args.monitor_dir),
    ]
    result = build_result(version=version, repository=repository, checks=checks)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        write_json(args.output, result)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["ok"] else 1


def cargo_version(cargo_toml: Path) -> str:
    with cargo_toml.open("rb") as handle:
        data = tomllib.load(handle)
    return str(data["package"]["version"])


def normalize_version(value: str) -> str:
    version = value.strip()
    if version.startswith("v"):
        version = version[1:]
    if not re.fullmatch(r"\d+(?:\.\d+){1,3}(?:[-+][0-9A-Za-z.-]+)?", version):
        raise ValueError(f"Invalid release version: {value}")
    return version


def validate_repository(value: str) -> str:
    repository = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError(f"Invalid repository: {value}")
    return repository


def audit_release_dir(release_dir: Path | None) -> list[dict[str, Any]]:
    if release_dir is None:
        return [
            check(
                f"release artifact:{target}",
                False,
                {"target": target},
                ["release directory was not provided"],
            )
            for target in TARGETS
        ]

    root = release_dir.expanduser().resolve()
    return [audit_release_target(root, target) for target in TARGETS]


def audit_release_target(release_dir: Path, target: str) -> dict[str, Any]:
    package_name = f"{FORMULA_NAME}-{target}.tar.gz"
    tarball = release_dir / package_name
    checksum_file = release_dir / f"{package_name}.sha256"
    details: dict[str, Any] = {
        "target": target,
        "tarball": str(tarball),
        "checksum_file": str(checksum_file),
    }
    reasons: list[str] = []

    if not tarball.is_file():
        reasons.append("release tarball is missing")
    if not checksum_file.is_file():
        reasons.append("checksum file is missing")
    if reasons:
        return check(f"release artifact:{target}", False, details, reasons)

    try:
        expected_checksum = read_checksum(checksum_file, package_name)
        actual_checksum = hashlib.sha256(tarball.read_bytes()).hexdigest()
        details["checksum"] = expected_checksum
        details["checksum_matches"] = actual_checksum == expected_checksum
        if actual_checksum != expected_checksum:
            reasons.append("checksum file does not match tarball bytes")
        validate_tarball_shape(tarball)
    except Exception as error:
        reasons.append(str(error))

    return check(f"release artifact:{target}", not reasons, details, reasons)


def read_checksum(path: Path, package_name: str) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.strip().split()
        if not parts:
            continue
        checksum = parts[0]
        if len(parts) > 1 and Path(parts[-1]).name != package_name:
            continue
        if not re.fullmatch(r"[0-9a-fA-F]{64}", checksum):
            raise ValueError(f"invalid SHA-256 checksum in {path}")
        return checksum.lower()
    raise ValueError(f"checksum file does not reference {package_name}")


def validate_tarball_shape(tarball: Path) -> None:
    with tarfile.open(tarball, "r:gz") as archive:
        members = archive.getmembers()
        binary_members = []
        for member in members:
            member_path = Path(member.name)
            if member_path.is_absolute() or ".." in member_path.parts:
                raise ValueError(f"unsafe tar member path: {member.name}")
            if not (member.isfile() or member.isdir()):
                raise ValueError(f"unsupported tar member type: {member.name}")
            if member.isfile() and member_path.name == FORMULA_NAME:
                binary_members.append(member.name)
        if len(binary_members) != 1:
            raise ValueError(f"expected exactly one {FORMULA_NAME} binary in tarball, found {len(binary_members)}")


def audit_homebrew_formula(
    formula_path: Path | None,
    version: str,
    repository: str,
    checksums: dict[str, str],
) -> dict[str, Any]:
    details: dict[str, Any] = {"version": version, "repository": repository}
    reasons: list[str] = []
    if formula_path is None:
        return check("homebrew formula", False, details, ["Homebrew formula path was not provided"])

    path = formula_path.expanduser().resolve()
    details["path"] = str(path)
    if not path.is_file():
        return check("homebrew formula", False, details, ["Homebrew formula is missing"])

    content = path.read_text(encoding="utf-8")
    details["ruby_syntax_checked"] = False
    if f'version "{version}"' not in content:
        reasons.append("formula version does not match release version")

    tag = f"v{version}"
    base_url = f"https://github.com/{repository}/releases/download/{tag}"
    for target in TARGETS:
        package_name = f"{FORMULA_NAME}-{target}.tar.gz"
        expected_url = f"{base_url}/{package_name}"
        if expected_url not in content:
            reasons.append(f"formula URL is missing for {target}")
        checksum = checksums.get(target)
        if checksum is None:
            reasons.append(f"release checksum is unavailable for {target}")
        elif f'sha256 "{checksum}"' not in content:
            reasons.append(f"formula checksum mismatch for {target}")

    ruby = shutil.which("ruby")
    if ruby:
        syntax = subprocess.run([ruby, "-c"], input=content, text=True, capture_output=True, check=False)
        details["ruby_syntax_checked"] = True
        details["ruby_syntax_ok"] = syntax.returncode == 0
        if syntax.returncode != 0:
            reasons.append("formula Ruby syntax check failed")

    return check("homebrew formula", not reasons, details, reasons)


def audit_release_smoke_summaries(release_dir: Path | None, version: str) -> list[dict[str, Any]]:
    return [audit_release_smoke_summary(release_dir, target, version) for target in TARGETS]


def audit_release_smoke_summary(release_dir: Path | None, target: str, version: str) -> dict[str, Any]:
    package_name = f"{FORMULA_NAME}-{target}.tar.gz"
    checksum_name = f"{package_name}.sha256"
    path = release_dir / f"{FORMULA_NAME}-{target}.smoke-summary.json" if release_dir else None
    details: dict[str, Any] = {
        "target": target,
        "path": str(path) if path else None,
        "expected_version": f"{FORMULA_NAME} {version}",
    }
    reasons: list[str] = []
    if path is None:
        reasons.append("release directory was not provided")
        return check(f"release smoke summary:{target}", False, details, reasons)

    summary = read_json_object(path, reasons)
    if summary is None:
        return check(f"release smoke summary:{target}", False, details, reasons)

    commands = summary.get("commands")
    installed_binary = summary.get("installed_binary")
    details.update(
        {
            "ok": summary.get("ok"),
            "version": summary.get("version"),
            "installed_binary": installed_binary,
            "status_configured": summary.get("status_configured"),
            "status_json_parsed": summary.get("status_json_parsed"),
            "dry_run": summary.get("dry_run"),
            "vault_unchanged": summary.get("vault_unchanged"),
            "uninstalled": summary.get("uninstalled"),
            "installed_after": summary.get("installed_after"),
            "inspect_count": summary.get("inspect_count"),
            "note_files": summary.get("note_files"),
            "command_count": len(commands) if isinstance(commands, list) else None,
        }
    )
    if summary.get("ok") is not True:
        reasons.append("release smoke summary ok is not true")
    if summary.get("version") != f"{FORMULA_NAME} {version}":
        reasons.append("release smoke version does not match release version")
    if summary_path_name(summary.get("tarball")) != package_name:
        reasons.append("release smoke tarball does not match target artifact")
    if summary_path_name(summary.get("checksum")) != checksum_name:
        reasons.append("release smoke checksum does not match target artifact")
    for field in ("status_configured", "status_json_parsed", "dry_run", "vault_unchanged"):
        if summary.get(field) is not True:
            reasons.append(f"release smoke {field} is not true")
    if summary.get("uninstalled") is not True:
        reasons.append("release smoke did not prove the artifact was uninstalled")
    if summary.get("installed_after") is not False:
        reasons.append("release smoke did not prove the artifact install was removed")
    if not positive_int(summary.get("inspect_count")):
        reasons.append("release smoke inspect_count is not positive")
    if not positive_int(summary.get("note_files")):
        reasons.append("release smoke note_files is not positive")
    if not isinstance(installed_binary, str) or not installed_binary:
        reasons.append("release smoke installed_binary is missing")
    if not isinstance(commands, list):
        reasons.append("release smoke commands are missing")
    else:
        required_binary_commands = {
            "version": ("--version",),
            "status": ("status", "--json"),
            "inspect": ("inspect-recent",),
            "sync": ("sync-once", "--dry-run-output"),
        }
        for label, sequence in required_binary_commands.items():
            if not has_successful_installed_binary_command(commands, sequence, installed_binary):
                reasons.append(f"release smoke did not record successful installed binary {label}")
        if not has_successful_version_command(commands, f"{FORMULA_NAME} {version}", installed_binary):
            reasons.append("release smoke did not record expected installed binary version output")

    return check(f"release smoke summary:{target}", not reasons, details, reasons)


def audit_homebrew_smoke_summary(
    release_dir: Path | None,
    formula_path: Path | None,
    version: str,
) -> dict[str, Any]:
    path = release_dir / "homebrew-smoke-summary.json" if release_dir else None
    details: dict[str, Any] = {
        "path": str(path) if path else None,
        "expected_version": version,
        "formula": str(formula_path) if formula_path else None,
    }
    reasons: list[str] = []
    if path is None:
        reasons.append("release directory was not provided")
        return check("homebrew smoke summary", False, details, reasons)
    if formula_path is None:
        reasons.append("Homebrew formula path was not provided")
        return check("homebrew smoke summary", False, details, reasons)

    summary = read_json_object(path, reasons)
    if summary is None:
        return check("homebrew smoke summary", False, details, reasons)

    commands = summary.get("commands")
    installed_binary = summary.get("installed_binary")
    details.update(
        {
            "ok": summary.get("ok"),
            "summary_expected_version": summary.get("expected_version"),
            "version": summary.get("version"),
            "installed_binary": installed_binary,
            "installed_after": summary.get("installed_after"),
            "status_configured": summary.get("status_configured"),
            "status_json_parsed": summary.get("status_json_parsed"),
            "dry_run": summary.get("dry_run"),
            "vault_unchanged": summary.get("vault_unchanged"),
            "note_files": summary.get("note_files"),
            "command_count": len(commands) if isinstance(commands, list) else None,
        }
    )
    if summary.get("ok") is not True:
        reasons.append("Homebrew smoke summary ok is not true")
    if summary.get("expected_version") != version:
        reasons.append("Homebrew smoke expected_version does not match release version")
    if summary.get("version") != f"{FORMULA_NAME} {version}":
        reasons.append("Homebrew smoke version does not match release version")
    if summary_path_name(summary.get("formula")) != formula_path.name:
        reasons.append("Homebrew smoke formula does not match generated formula")
    if summary.get("installed_after") is not False:
        reasons.append("Homebrew smoke did not prove the formula was uninstalled")
    for field in ("status_configured", "status_json_parsed", "dry_run", "vault_unchanged"):
        if summary.get(field) is not True:
            reasons.append(f"Homebrew smoke {field} is not true")
    if not positive_int(summary.get("note_files")):
        reasons.append("Homebrew smoke note_files is not positive")
    if not isinstance(installed_binary, str) or not installed_binary:
        reasons.append("Homebrew smoke installed_binary is missing")
    if not isinstance(commands, list):
        reasons.append("Homebrew smoke commands are missing")
    else:
        required_commands = {
            "install": ("install", "--formula"),
            "test": ("test", FORMULA_NAME),
            "uninstall": ("uninstall", "--formula", FORMULA_NAME),
        }
        for label, sequence in required_commands.items():
            if not has_successful_command(commands, sequence):
                reasons.append(f"Homebrew smoke did not record successful brew {label}")
        required_binary_commands = {
            "version": ("--version",),
            "status": ("status", "--json"),
            "sync": ("sync-once", "--dry-run-output"),
        }
        for label, sequence in required_binary_commands.items():
            if not has_successful_installed_binary_command(commands, sequence, installed_binary):
                reasons.append(f"Homebrew smoke did not record successful installed binary {label}")
        if not has_successful_version_command(commands, f"{FORMULA_NAME} {version}", installed_binary):
            reasons.append("Homebrew smoke did not record expected installed binary version output")

    return check("homebrew smoke summary", not reasons, details, reasons)


def audit_expected_rust_binary(binary: str | None, version: str) -> dict[str, Any]:
    path = resolve_executable(binary)
    details: dict[str, Any] = {"path": str(path) if path else None, "expected_version": version}
    reasons: list[str] = []
    if path is None:
        return check("expected Rust binary", False, details, ["expected Rust binary was not provided or not found"])
    if not is_executable_file(path):
        reasons.append("expected Rust binary is missing or not executable")
    else:
        result = run_capture([str(path), "--version"])
        details["version_output"] = result.stdout.strip()
        if result.returncode != 0:
            reasons.append("expected Rust binary --version failed")
        elif result.stdout.strip() != f"{FORMULA_NAME} {version}":
            reasons.append("expected Rust binary version does not match release version")
    return check("expected Rust binary", not reasons, details, reasons)


def audit_rollback_binary(rollback_binary: str | None, expected_rust_binary: str | None) -> dict[str, Any]:
    path = resolve_executable(rollback_binary)
    expected_path = resolve_executable(expected_rust_binary)
    details: dict[str, Any] = {
        "path": str(path) if path else None,
        "expected_rust_binary": str(expected_path) if expected_path else None,
    }
    reasons: list[str] = []
    if path is None:
        return check("rollback binary", False, details, ["rollback binary was not provided or not found"])
    if not is_executable_file(path):
        reasons.append("rollback binary is missing or not executable")
    if expected_path and same_path(path, expected_path):
        reasons.append("rollback binary points to the expected Rust binary")
    if not reasons:
        result = run_capture([str(path), "--help"])
        details["help_returncode"] = result.returncode
        if result.returncode != 0:
            reasons.append("rollback binary --help failed")
    return check("rollback binary", not reasons, details, reasons)


def collect_status(args: argparse.Namespace) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    details: dict[str, Any] = {}
    reasons: list[str] = []
    try:
        if args.status_json_file:
            path = args.status_json_file.expanduser().resolve()
            details["source"] = str(path)
            status = json.loads(path.read_text(encoding="utf-8"))
        else:
            command = [args.status_binary]
            if args.config:
                command.extend(["--config", str(args.config.expanduser())])
            command.extend(["status", "--json"])
            details["command"] = command
            result = run_capture(command)
            details["returncode"] = result.returncode
            if result.returncode != 0:
                reasons.append("status --json failed")
                return None, check("status snapshot", False, details, reasons)
            status = json.loads(result.stdout)
        if not isinstance(status, dict):
            reasons.append("status JSON is not an object")
            return None, check("status snapshot", False, details, reasons)
        details["configured"] = bool(status.get("configured"))
        details["launchd_loaded"] = bool(status.get("launchd_loaded"))
        return status, check("status snapshot", not reasons, details, reasons)
    except Exception as error:
        reasons.append(str(error))
        return None, check("status snapshot", False, details, reasons)


def collect_plist(plist_file: Path | None, status: dict[str, Any] | None) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    details: dict[str, Any] = {}
    reasons: list[str] = []
    path = plist_file
    if path is None and status:
        plist_path = status.get("plist_path")
        if isinstance(plist_path, str) and plist_path:
            path = Path(plist_path)
    if path is None:
        return None, check("launchagent plist", False, details, ["LaunchAgent plist path was not provided"])

    resolved = path.expanduser().resolve()
    details["path"] = str(resolved)
    try:
        plist = plistlib.loads(resolved.read_bytes())
        if not isinstance(plist, dict):
            reasons.append("LaunchAgent plist is not a dictionary")
            return None, check("launchagent plist", False, details, reasons)
        details["label"] = plist.get("Label")
        return plist, check("launchagent plist", not reasons, details, reasons)
    except Exception as error:
        reasons.append(str(error))
        return None, check("launchagent plist", False, details, reasons)


def audit_launchagent(
    status: dict[str, Any] | None,
    plist: dict[str, Any] | None,
    expected_current_program_arg0: str | None,
) -> dict[str, Any]:
    details: dict[str, Any] = {}
    reasons: list[str] = []
    if status is None:
        reasons.append("status snapshot is unavailable")
    else:
        details["configured"] = bool(status.get("configured"))
        details["launchd_loaded"] = bool(status.get("launchd_loaded"))
        details["last_error"] = status.get("last_error")
        if not status.get("configured"):
            reasons.append("status reports configured=false")
        if not status.get("launchd_loaded"):
            reasons.append("status reports launchd_loaded=false")
        if status.get("last_error") not in (None, "none", "never run"):
            reasons.append(f"last_error is {status.get('last_error')}")

    program_arguments = list(plist.get("ProgramArguments") or []) if plist else []
    details["program_arg0"] = program_arguments[0] if program_arguments else None
    details["service_command"] = program_arguments[-1] if program_arguments else None
    if not program_arguments:
        reasons.append("LaunchAgent ProgramArguments are missing")
    elif program_arguments[-1] != "service-run":
        reasons.append("LaunchAgent does not end with service-run")

    if expected_current_program_arg0 and (
        not program_arguments or program_arguments[0] != expected_current_program_arg0
    ):
        reasons.append("current ProgramArguments[0] does not match expected pre-cutover binary")

    return check("launchagent current state", not reasons, details, reasons)


def audit_monitor_dir(monitor_dir: Path) -> dict[str, Any]:
    path = monitor_dir.expanduser().resolve()
    details: dict[str, Any] = {"path": str(path)}
    reasons: list[str] = []
    temp_roots = {Path(tempfile.gettempdir()).resolve(), Path("/tmp").resolve()}
    if path.parent not in temp_roots or not path.name.startswith("codex-obsidian-sync-"):
        roots = ", ".join(sorted(str(root) for root in temp_roots))
        reasons.append(f"monitor dir must be a dedicated codex-obsidian-sync-* path under {roots}")

    record_count = 0
    record_names: list[str] = []
    failed_records: list[str] = []
    if path.exists():
        marker = path / MONITOR_MARKER
        if marker.exists() and marker.read_text(encoding="utf-8") != MONITOR_MARKER_CONTENT:
            reasons.append("monitor dir marker content is not managed by record_cutover_monitor.py")
        for record_path in sorted(path.glob("*.json")):
            record_names.append(record_path.name)
            record_count += 1
            try:
                record = json.loads(record_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if record.get("ok") is False:
                failed_records.append(record_path.name)
    details["existing_record_count"] = record_count
    details["existing_records"] = record_names
    details["failed_records"] = failed_records
    if record_names:
        reasons.append(f"monitor dir already contains JSON records: {record_names}")
    if failed_records:
        reasons.append(f"existing monitor records contain no-go results: {failed_records}")

    return check("monitor prerequisites", not reasons, details, reasons)


def read_json_object(path: Path, reasons: list[str]) -> dict[str, Any] | None:
    if not path.is_file():
        reasons.append("summary file is missing")
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        reasons.append(f"summary JSON is invalid: {error}")
        return None
    if not isinstance(value, dict):
        reasons.append("summary JSON is not an object")
        return None
    return value


def summary_path_name(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return Path(value).name


def positive_int(value: Any) -> bool:
    return isinstance(value, int) and value > 0


def command_has_sequence(command: Any, sequence: tuple[str, ...]) -> bool:
    if not isinstance(command, list):
        return False
    parts = [str(part) for part in command]
    width = len(sequence)
    return any(tuple(parts[index : index + width]) == sequence for index in range(0, len(parts) - width + 1))


def has_successful_command(commands: list[Any], sequence: tuple[str, ...]) -> bool:
    for command in commands:
        if not isinstance(command, dict):
            continue
        if command.get("returncode") == 0 and command_has_sequence(command.get("command"), sequence):
            return True
    return False


def has_successful_version_command(commands: list[Any], expected_output: str, expected_binary: Any) -> bool:
    for command in commands:
        if not isinstance(command, dict) or command.get("returncode") != 0:
            continue
        command_value = command.get("command")
        if not isinstance(command_value, list):
            continue
        parts = [str(part) for part in command_value]
        if len(parts) != 2 or not command_uses_expected_binary(parts[0], expected_binary) or parts[1] != "--version":
            continue
        if str(command.get("stdout") or "").strip() == expected_output:
            return True
    return False


def has_successful_installed_binary_command(
    commands: list[Any],
    sequence: tuple[str, ...],
    expected_binary: Any,
) -> bool:
    for command in commands:
        if not isinstance(command, dict) or command.get("returncode") != 0:
            continue
        command_value = command.get("command")
        if not isinstance(command_value, list) or not command_value:
            continue
        parts = [str(part) for part in command_value]
        if command_uses_expected_binary(parts[0], expected_binary) and command_has_sequence(parts, sequence):
            return True
    return False


def command_uses_expected_binary(command_arg0: str, expected_binary: Any) -> bool:
    return isinstance(expected_binary, str) and bool(expected_binary) and command_arg0 == expected_binary


def resolve_executable(value: str | None) -> Path | None:
    if not value:
        return None
    expanded = Path(value).expanduser()
    if expanded.is_absolute() or "/" in value:
        return expanded.resolve()
    resolved = shutil.which(value)
    return Path(resolved).resolve() if resolved else None


def is_executable_file(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def same_path(left: Path, right: Path) -> bool:
    try:
        return left.samefile(right)
    except OSError:
        return left == right


def run_capture(command: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, capture_output=True, text=True, check=False, timeout=15)
    except FileNotFoundError as error:
        return subprocess.CompletedProcess(command, 127, "", str(error))
    except subprocess.TimeoutExpired as error:
        return subprocess.CompletedProcess(command, 124, error.stdout or "", error.stderr or "command timed out")


def check(name: str, ok: bool, details: dict[str, Any], reasons: list[str]) -> dict[str, Any]:
    return {
        "name": name,
        "ok": ok,
        "details": details,
        "no_go_reasons": reasons,
    }


def build_result(*, version: str, repository: str, checks: list[dict[str, Any]]) -> dict[str, Any]:
    no_go_reasons = [
        f"{item['name']}: {reason}"
        for item in checks
        for reason in item["no_go_reasons"]
    ]
    return {
        "ok": not no_go_reasons,
        "generated_at": datetime.now(UTC).isoformat(),
        "version": version,
        "tag": f"v{version}",
        "repository": repository,
        "no_go_reasons": no_go_reasons,
        "checks": checks,
    }


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    path.chmod(0o600)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
