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
DEFAULT_LAUNCHD_LABEL = "com.codex.obsidian-sync"
MONITOR_MARKER = ".codex-obsidian-sync-cutover-monitor"
MONITOR_MARKER_CONTENT = "managed by record_cutover_monitor.py\n"
TARGETS = ("aarch64-apple-darwin", "x86_64-apple-darwin")
RELEASE_EVIDENCE_SUMMARY = "release-evidence-summary.json"


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
    release_dir = args.release_dir.expanduser() if args.release_dir else None
    formula_path = args.homebrew_formula.expanduser() if args.homebrew_formula else None

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
        audit_release_dir_contents(release_dir, formula_path),
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

    requested_root = release_dir.expanduser()
    if requested_root.is_symlink():
        return [
            check(
                f"release artifact:{target}",
                False,
                {"target": target, "path": str(requested_root)},
                ["release directory is a symlink"],
            )
            for target in TARGETS
        ]

    root = requested_root.resolve()
    return [audit_release_target(root, target) for target in TARGETS]


def expected_release_dir_file_names(formula_path: Path | None = None, release_dir: Path | None = None) -> set[str]:
    names = {RELEASE_EVIDENCE_SUMMARY, "homebrew-smoke-summary.json"}
    for target in TARGETS:
        package_name = f"{FORMULA_NAME}-{target}.tar.gz"
        names.update(
            {
                package_name,
                f"{package_name}.sha256",
                f"{FORMULA_NAME}-{target}.smoke-summary.json",
            }
        )
    if formula_path is not None:
        try:
            if release_dir is None or formula_path.expanduser().resolve().parent == release_dir.expanduser().resolve():
                names.add(formula_path.name)
        except OSError:
            names.add(formula_path.name)
    return names


def audit_release_dir_contents(release_dir: Path | None, formula_path: Path | None = None) -> dict[str, Any]:
    details: dict[str, Any] = {"path": str(release_dir) if release_dir else None}
    reasons: list[str] = []
    if release_dir is None:
        return check("release directory contents", False, details, ["release directory was not provided"])

    requested_root = release_dir.expanduser()
    details["path"] = str(requested_root)
    if requested_root.is_symlink():
        return check("release directory contents", False, details, ["release directory is a symlink"])

    root = requested_root.resolve()
    expected_names = expected_release_dir_file_names(formula_path, root)
    details["path"] = str(root)
    details["expected_files"] = sorted(expected_names)
    if not root.is_dir():
        return check("release directory contents", False, details, ["release directory is missing"])

    symlinks = sorted(path.name for path in root.iterdir() if path.is_symlink())
    unexpected_files = sorted(path.name for path in root.iterdir() if path.is_file() and path.name not in expected_names)
    unexpected_directories = sorted(
        path.name for path in root.iterdir() if path.is_dir() and path.name not in expected_names
    )
    details["symlinks"] = symlinks
    details["unexpected_files"] = unexpected_files
    details["unexpected_directories"] = unexpected_directories
    if symlinks:
        reasons.append(f"release directory contains symlinks: {symlinks}")
    if unexpected_files:
        reasons.append(f"release directory contains unexpected files: {unexpected_files}")
    if unexpected_directories:
        reasons.append(f"release directory contains unexpected directories: {unexpected_directories}")
    return check("release directory contents", not reasons, details, reasons)


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
    matches: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.strip().split()
        if not parts:
            continue
        checksum = parts[0]
        if len(parts) == 1 or Path(parts[-1]).name != package_name:
            continue
        if not re.fullmatch(r"[0-9a-fA-F]{64}", checksum):
            raise ValueError(f"invalid SHA-256 checksum in {path}")
        matches.append(checksum.lower())
    if not matches:
        raise ValueError(f"checksum file does not reference {package_name}")
    if len(matches) > 1:
        raise ValueError(f"checksum file contains duplicate entries for {package_name}")
    return matches[0]


def validate_tarball_shape(tarball: Path) -> None:
    expected_binary_path = expected_tarball_binary_path(tarball)
    with tarfile.open(tarball, "r:gz") as archive:
        members = archive.getmembers()
        binary_members = []
        seen_paths: set[Path] = set()
        for member in members:
            member_path = Path(member.name)
            if member_path.is_absolute() or ".." in member_path.parts:
                raise ValueError(f"unsafe tar member path: {member.name}")
            if member_path in seen_paths:
                raise ValueError(f"duplicate tar member path: {member.name}")
            seen_paths.add(member_path)
            if not (member.isfile() or member.isdir()):
                raise ValueError(f"unsupported tar member type: {member.name}")
            if member.isfile() and member_path.name == FORMULA_NAME:
                binary_members.append(member)
        if len(binary_members) != 1:
            raise ValueError(f"expected exactly one {FORMULA_NAME} binary in tarball, found {len(binary_members)}")
        binary_member = binary_members[0]
        if Path(binary_member.name) != expected_binary_path:
            raise ValueError(f"release tarball binary path is unexpected: {binary_member.name}")
        if binary_member.mode & 0o111 == 0:
            raise ValueError(f"release tarball binary is not executable: {binary_member.name}")


def expected_tarball_binary_path(tarball: Path) -> Path:
    package_root = tarball.name.removesuffix(".tar.gz")
    return Path(package_root) / FORMULA_NAME


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

    requested_path = formula_path.expanduser()
    details["path"] = str(requested_path)
    if requested_path.is_symlink():
        return check("homebrew formula", False, details, ["Homebrew formula is a symlink"])

    path = requested_path.resolve()
    details["path"] = str(path)
    if not path.is_file():
        return check("homebrew formula", False, details, ["Homebrew formula is missing"])

    content = path.read_text(encoding="utf-8")
    details["ruby_syntax_checked"] = False
    if f'version "{version}"' not in content:
        reasons.append("formula version does not match release version")
    if f'bin.install "{FORMULA_NAME}"' not in content:
        reasons.append("formula install block does not install the expected binary")
    if "test do" not in content:
        reasons.append("formula test block is missing")
    if f'shell_output("#{{bin}}/{FORMULA_NAME} --version")' not in content:
        reasons.append("formula test does not run installed binary --version")
    if f'assert_match "{FORMULA_NAME} #{{version}}"' not in content:
        reasons.append("formula test does not assert the installed binary version")

    tag = f"v{version}"
    base_url = f"https://github.com/{repository}/releases/download/{tag}"
    target_details: dict[str, Any] = {}
    for target in TARGETS:
        package_name = f"{FORMULA_NAME}-{target}.tar.gz"
        expected_url = f"{base_url}/{package_name}"
        target_block = formula_target_block(content, target)
        urls = formula_statement_values(target_block, "url")
        sha256s = formula_statement_values(target_block, "sha256")
        target_details[target] = {"urls": urls, "sha256s": sha256s}
        if urls != [expected_url]:
            reasons.append(f"formula URL is missing for {target}")
        checksum = checksums.get(target)
        if checksum is None:
            reasons.append(f"release checksum is unavailable for {target}")
        elif sha256s != [checksum]:
            reasons.append(f"formula checksum mismatch for {target}")
    details["targets"] = target_details

    ruby = shutil.which("ruby")
    if ruby:
        syntax = subprocess.run([ruby, "-c"], input=content, text=True, capture_output=True, check=False)
        details["ruby_syntax_checked"] = True
        details["ruby_syntax_ok"] = syntax.returncode == 0
        if syntax.returncode != 0:
            reasons.append("formula Ruby syntax check failed")

    return check("homebrew formula", not reasons, details, reasons)


def formula_target_block(content: str, target: str) -> str:
    selectors = {
        "aarch64-apple-darwin": "on_arm",
        "x86_64-apple-darwin": "on_intel",
    }
    selector = selectors[target]
    pattern = rf"^[ \t]*{selector}[ \t]+do[ \t]*\n(?P<body>.*?)(?=^[ \t]*end[ \t]*$)"
    match = re.search(pattern, content, re.MULTILINE | re.DOTALL)
    if not match:
        return ""
    return match.group("body")


def formula_statement_values(block: str, statement: str) -> list[str]:
    pattern = rf'^[ \t]*{re.escape(statement)}[ \t]+"([^"]+)"[ \t]*$'
    return re.findall(pattern, block, re.MULTILINE)


def audit_release_smoke_summaries(
    release_dir: Path | None,
    version: str,
    *,
    strict_summary_paths: bool = True,
) -> list[dict[str, Any]]:
    return [
        audit_release_smoke_summary(
            release_dir,
            target,
            version,
            strict_summary_paths=strict_summary_paths,
        )
        for target in TARGETS
    ]


def audit_release_smoke_summary(
    release_dir: Path | None,
    target: str,
    version: str,
    *,
    strict_summary_paths: bool = True,
) -> dict[str, Any]:
    package_name = f"{FORMULA_NAME}-{target}.tar.gz"
    checksum_name = f"{package_name}.sha256"
    path = release_dir / f"{FORMULA_NAME}-{target}.smoke-summary.json" if release_dir else None
    tarball = release_dir / package_name if release_dir else None
    checksum_file = release_dir / checksum_name if release_dir else None
    details: dict[str, Any] = {
        "target": target,
        "path": str(path) if path else None,
        "expected_version": f"{FORMULA_NAME} {version}",
        "expected_tarball_sha256": file_sha256(tarball),
        "expected_checksum_sha256": file_sha256(checksum_file),
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
            "generated_at": summary.get("generated_at"),
            "version": summary.get("version"),
            "tarball_sha256": summary.get("tarball_sha256"),
            "checksum_sha256": summary.get("checksum_sha256"),
            "installed_binary": installed_binary,
            "status_configured": summary.get("status_configured"),
            "status_json_parsed": summary.get("status_json_parsed"),
            "dry_run": summary.get("dry_run"),
            "vault_unchanged": summary.get("vault_unchanged"),
            "uninstalled": summary.get("uninstalled"),
            "installed_after": summary.get("installed_after"),
            "inspect_count": summary.get("inspect_count"),
            "note_files": summary.get("note_files"),
            "summary_no_go_reasons": summary.get("no_go_reasons"),
            "command_count": len(commands) if isinstance(commands, list) else None,
        }
    )
    if summary.get("ok") is not True:
        reasons.append("release smoke summary ok is not true")
    if not is_parseable_timestamp(summary.get("generated_at")):
        reasons.append("release smoke generated_at is missing or invalid")
    if summary.get("no_go_reasons") not in ([], None):
        reasons.append("release smoke summary contains no-go reasons")
    if summary.get("version") != f"{FORMULA_NAME} {version}":
        reasons.append("release smoke version does not match release version")
    if summary_path_name(summary.get("tarball")) != package_name:
        reasons.append("release smoke tarball does not match target artifact")
    if not summary_path_is_absolute(summary.get("tarball")):
        reasons.append("release smoke tarball path is not absolute")
    elif strict_summary_paths and not summary_path_matches(summary.get("tarball"), tarball):
        reasons.append("release smoke tarball path does not match target artifact")
    if summary_path_name(summary.get("checksum")) != checksum_name:
        reasons.append("release smoke checksum does not match target artifact")
    if not summary_path_is_absolute(summary.get("checksum")):
        reasons.append("release smoke checksum path is not absolute")
    elif strict_summary_paths and not summary_path_matches(summary.get("checksum"), checksum_file):
        reasons.append("release smoke checksum path does not match target artifact")
    if summary.get("tarball_sha256") != details["expected_tarball_sha256"]:
        reasons.append("release smoke tarball checksum does not match target artifact")
    if summary.get("checksum_sha256") != details["expected_checksum_sha256"]:
        reasons.append("release smoke checksum file digest does not match target artifact")
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
    elif not Path(installed_binary).is_absolute():
        reasons.append("release smoke installed_binary is not absolute")
    if not isinstance(commands, list):
        reasons.append("release smoke commands are missing")
    else:
        required_binary_commands = {
            "version": ("--version",),
        }
        for label, sequence in required_binary_commands.items():
            if not has_successful_installed_binary_command(commands, sequence, installed_binary):
                reasons.append(f"release smoke did not record successful installed binary {label}")
        if not has_successful_inspect_recent_command(commands, installed_binary):
            reasons.append("release smoke did not record successful installed binary inspect")
        if not has_successful_configured_status_command(commands, installed_binary):
            reasons.append("release smoke did not record successful installed binary status with --config")
        if not has_successful_configured_sync_command(commands, installed_binary):
            reasons.append("release smoke did not record successful installed binary sync with --config")
        if not has_successful_version_command(commands, f"{FORMULA_NAME} {version}", installed_binary):
            reasons.append("release smoke did not record expected installed binary version output")

    return check(f"release smoke summary:{target}", not reasons, details, reasons)


def audit_homebrew_smoke_summary(
    release_dir: Path | None,
    formula_path: Path | None,
    version: str,
    *,
    strict_summary_paths: bool = True,
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
    formula_value = summary.get("formula")
    brew_value = summary.get("brew")
    expected_formula_sha256 = formula_sha256(formula_path)
    details.update(
        {
            "ok": summary.get("ok"),
            "generated_at": summary.get("generated_at"),
            "brew": brew_value,
            "formula_sha256": summary.get("formula_sha256"),
            "expected_formula_sha256": expected_formula_sha256,
            "summary_expected_version": summary.get("expected_version"),
            "version": summary.get("version"),
            "installed_binary": installed_binary,
            "installed_after": summary.get("installed_after"),
            "status_configured": summary.get("status_configured"),
            "status_json_parsed": summary.get("status_json_parsed"),
            "dry_run": summary.get("dry_run"),
            "vault_unchanged": summary.get("vault_unchanged"),
            "note_files": summary.get("note_files"),
            "summary_no_go_reasons": summary.get("no_go_reasons"),
            "command_count": len(commands) if isinstance(commands, list) else None,
        }
    )
    if summary.get("ok") is not True:
        reasons.append("Homebrew smoke summary ok is not true")
    if not is_parseable_timestamp(summary.get("generated_at")):
        reasons.append("Homebrew smoke generated_at is missing or invalid")
    if summary.get("no_go_reasons") not in ([], None):
        reasons.append("Homebrew smoke summary contains no-go reasons")
    if summary.get("expected_version") != version:
        reasons.append("Homebrew smoke expected_version does not match release version")
    if summary.get("version") != f"{FORMULA_NAME} {version}":
        reasons.append("Homebrew smoke version does not match release version")
    if summary_path_name(formula_value) != formula_path.name:
        reasons.append("Homebrew smoke formula does not match generated formula")
    if not summary_path_is_absolute(formula_value):
        reasons.append("Homebrew smoke formula path is not absolute")
    elif strict_summary_paths and not summary_path_matches(formula_value, formula_path):
        reasons.append("Homebrew smoke formula path does not match generated formula")
    if brew_value is not None:
        if not isinstance(brew_value, str) or not brew_value:
            reasons.append("Homebrew smoke brew path is missing")
        elif not Path(brew_value).is_absolute():
            reasons.append("Homebrew smoke brew path is not absolute")
    if summary.get("formula_sha256") != expected_formula_sha256:
        reasons.append("Homebrew smoke formula checksum does not match generated formula")
    if summary.get("installed_after") is not False:
        reasons.append("Homebrew smoke did not prove the formula was uninstalled")
    for field in ("status_configured", "status_json_parsed", "dry_run", "vault_unchanged"):
        if summary.get(field) is not True:
            reasons.append(f"Homebrew smoke {field} is not true")
    if not positive_int(summary.get("note_files")):
        reasons.append("Homebrew smoke note_files is not positive")
    if not isinstance(installed_binary, str) or not installed_binary:
        reasons.append("Homebrew smoke installed_binary is missing")
    elif not Path(installed_binary).is_absolute():
        reasons.append("Homebrew smoke installed_binary is not absolute")
    if not isinstance(commands, list):
        reasons.append("Homebrew smoke commands are missing")
    else:
        homebrew_prefix = successful_brew_prefix(commands, brew_value)
        details["homebrew_prefix"] = homebrew_prefix
        required_commands = {
            "test": ("test", FORMULA_NAME),
            "uninstall": ("uninstall", "--formula", FORMULA_NAME),
        }
        if not has_successful_brew_command_with_next_arg(commands, ("install", "--formula"), formula_value, brew_value):
            reasons.append("Homebrew smoke did not record successful brew install for recorded formula")
        if homebrew_prefix is None:
            reasons.append("Homebrew smoke did not record successful brew prefix")
        elif not Path(homebrew_prefix).is_absolute():
            reasons.append("Homebrew smoke brew prefix is not absolute")
        elif isinstance(installed_binary, str):
            expected_binary = Path(homebrew_prefix) / "bin" / FORMULA_NAME
            if Path(installed_binary) != expected_binary:
                reasons.append("Homebrew smoke installed_binary does not match brew prefix")
        for label, sequence in required_commands.items():
            if not has_successful_brew_command(commands, sequence, brew_value):
                reasons.append(f"Homebrew smoke did not record successful brew {label}")
        if not has_failed_brew_list_after_successful_uninstall(commands, brew_value):
            reasons.append("Homebrew smoke did not record failed brew list after uninstall")
        required_binary_commands = {
            "version": ("--version",),
        }
        for label, sequence in required_binary_commands.items():
            if not has_successful_installed_binary_command(commands, sequence, installed_binary):
                reasons.append(f"Homebrew smoke did not record successful installed binary {label}")
        if not has_successful_configured_status_command(commands, installed_binary):
            reasons.append("Homebrew smoke did not record successful installed binary status with --config")
        if not has_successful_configured_sync_command(commands, installed_binary):
            reasons.append("Homebrew smoke did not record successful installed binary sync with --config")
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
            path = args.status_json_file.expanduser()
            details["source"] = str(path)
            reject_symlinked_evidence(path)
            resolved = path.resolve()
            details["source"] = str(resolved)
            status = json.loads(resolved.read_text(encoding="utf-8"))
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
        details["configured"] = status.get("configured")
        details["launchd_loaded"] = status.get("launchd_loaded")
        if not isinstance(status.get("configured"), bool):
            reasons.append("status configured is not boolean")
        if not isinstance(status.get("launchd_loaded"), bool):
            reasons.append("status launchd_loaded is not boolean")
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

    requested_path = path.expanduser()
    details["path"] = str(requested_path)
    try:
        reject_symlinked_evidence(requested_path)
        resolved = requested_path.resolve()
        details["path"] = str(resolved)
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
        details["configured"] = status.get("configured")
        details["launchd_loaded"] = status.get("launchd_loaded")
        details["last_error"] = status.get("last_error")
        details["status_plist_path"] = status.get("plist_path")
        details["status_config_path"] = status.get("config_path")
        validate_status_flag(
            status,
            "configured",
            True,
            reasons,
            bool_mismatch_reason="status reports configured=false",
            type_mismatch_reason="status configured is not boolean true",
        )
        validate_status_flag(
            status,
            "launchd_loaded",
            True,
            reasons,
            bool_mismatch_reason="status reports launchd_loaded=false",
            type_mismatch_reason="status launchd_loaded is not boolean true",
        )
        if status.get("launchd_label") != DEFAULT_LAUNCHD_LABEL:
            reasons.append("status launchd_label does not match expected label")
        validate_status_plist_path(status.get("plist_path"), reasons)
        if status.get("last_error") not in (None, "none", "never run"):
            reasons.append(f"last_error is {status.get('last_error')}")

    program_arguments = validated_program_arguments(plist.get("ProgramArguments"), reasons) if plist else []
    plist_label = plist.get("Label") if plist else None
    config_path = config_argument_path(program_arguments)
    details["plist_label"] = plist_label
    details["program_arg0"] = program_arguments[0] if program_arguments else None
    details["config_path"] = config_path
    details["service_command"] = program_arguments[-1] if program_arguments else None
    if plist and plist_label != DEFAULT_LAUNCHD_LABEL:
        reasons.append("LaunchAgent Label does not match expected label")
    if not program_arguments:
        reasons.append("LaunchAgent ProgramArguments are missing")
    else:
        if not Path(program_arguments[0]).is_absolute():
            reasons.append("ProgramArguments[0] is not an absolute path")
        if program_arguments[-1] != "service-run":
            reasons.append("LaunchAgent does not end with service-run")
        validate_config_argument(program_arguments, reasons)

    if status is not None:
        validate_status_config_path(status.get("config_path"), config_path, reasons)

    if expected_current_program_arg0 and (
        not program_arguments or program_arguments[0] != expected_current_program_arg0
    ):
        reasons.append("current ProgramArguments[0] does not match expected pre-cutover binary")

    return check("launchagent current state", not reasons, details, reasons)


def validate_status_plist_path(value: Any, reasons: list[str]) -> None:
    if not isinstance(value, str) or not value:
        reasons.append("status plist_path is missing")
    elif not Path(value).is_absolute():
        reasons.append("status plist_path is not absolute")


def validate_status_config_path(value: Any, launchagent_config_path: str | None, reasons: list[str]) -> None:
    if not isinstance(value, str) or not value:
        reasons.append("status config_path is missing")
    elif not Path(value).is_absolute():
        reasons.append("status config_path is not absolute")
    elif launchagent_config_path and value != launchagent_config_path:
        reasons.append("status config_path does not match LaunchAgent --config path")


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


def config_argument_path(program_arguments: list[str]) -> str | None:
    try:
        config_index = program_arguments.index("--config")
    except ValueError:
        return None
    value_index = config_index + 1
    if value_index >= len(program_arguments) - 1:
        return None
    return program_arguments[value_index]


def audit_monitor_dir(monitor_dir: Path) -> dict[str, Any]:
    requested_path = monitor_dir.expanduser()
    path = requested_path.resolve()
    details: dict[str, Any] = {"path": str(path)}
    reasons: list[str] = []
    temp_roots = {Path(tempfile.gettempdir()).resolve(), Path("/tmp").resolve()}
    if path.parent not in temp_roots or not path.name.startswith("codex-obsidian-sync-"):
        roots = ", ".join(sorted(str(root) for root in temp_roots))
        reasons.append(f"monitor dir must be a dedicated codex-obsidian-sync-* path under {roots}")

    record_count = 0
    record_names: list[str] = []
    unexpected_entry_names: list[str] = []
    symlink_names: list[str] = []
    failed_records: list[str] = []
    if requested_path.is_symlink():
        reasons.append("monitor dir is a symlink")
    elif path.exists():
        marker = path / MONITOR_MARKER
        if marker.is_symlink():
            reasons.append("monitor dir marker is a symlink")
        elif marker.exists() and marker.read_text(encoding="utf-8") != MONITOR_MARKER_CONTENT:
            reasons.append("monitor dir marker content is not managed by record_cutover_monitor.py")
        for entry in sorted(path.iterdir(), key=lambda candidate: candidate.name):
            if entry.name == MONITOR_MARKER:
                continue
            if entry.is_symlink():
                symlink_names.append(entry.name)
            if entry.suffix != ".json":
                unexpected_entry_names.append(entry.name)
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
    details["unexpected_entries"] = unexpected_entry_names
    details["symlinks"] = symlink_names
    details["failed_records"] = failed_records
    if record_names:
        reasons.append(f"monitor dir already contains JSON records: {record_names}")
    if unexpected_entry_names:
        reasons.append(f"monitor dir contains unexpected entries: {unexpected_entry_names}")
    if symlink_names:
        reasons.append(f"monitor dir contains symlinks: {symlink_names}")
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


def reject_symlinked_evidence(path: Path) -> None:
    if path.expanduser().is_symlink():
        raise RuntimeError(f"evidence file is a symlink: {path}")


def summary_path_name(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return Path(value).name


def summary_path_is_absolute(value: Any) -> bool:
    return isinstance(value, str) and bool(value) and Path(value).is_absolute()


def summary_path_matches(value: Any, expected: Path | None) -> bool:
    if not isinstance(value, str) or expected is None:
        return False
    try:
        return Path(value).expanduser().resolve() == expected.expanduser().resolve()
    except OSError:
        return False


def positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def is_parseable_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        timestamp = datetime.fromisoformat(value)
    except ValueError:
        return False
    return timestamp.tzinfo is not None and timestamp.utcoffset() is not None


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


def command_has_sequence(command: Any, sequence: tuple[str, ...]) -> bool:
    if not isinstance(command, list):
        return False
    parts = [str(part) for part in command]
    width = len(sequence)
    return any(tuple(parts[index : index + width]) == sequence for index in range(0, len(parts) - width + 1))


def has_successful_brew_command(
    commands: list[Any],
    sequence: tuple[str, ...],
    expected_brew: Any = None,
) -> bool:
    for command in commands:
        if not isinstance(command, dict) or command.get("returncode") != 0:
            continue
        command_value = command.get("command")
        if not isinstance(command_value, list):
            continue
        parts = [str(part) for part in command_value]
        width = len(sequence)
        if parts and command_uses_brew(parts[0], expected_brew) and tuple(parts[1 : 1 + width]) == sequence:
            return True
    return False


def has_successful_brew_command_with_next_arg(
    commands: list[Any],
    sequence: tuple[str, ...],
    expected_arg: Any,
    expected_brew: Any = None,
) -> bool:
    if not isinstance(expected_arg, str) or not expected_arg:
        return False
    for command in commands:
        if not isinstance(command, dict) or command.get("returncode") != 0:
            continue
        command_value = command.get("command")
        if not isinstance(command_value, list):
            continue
        parts = [str(part) for part in command_value]
        width = len(sequence)
        if (
            len(parts) > 1 + width
            and command_uses_brew(parts[0], expected_brew)
            and tuple(parts[1 : 1 + width]) == sequence
            and parts[1 + width] == expected_arg
        ):
            return True
    return False


def command_uses_brew(command_arg0: str, expected_brew: Any = None) -> bool:
    if isinstance(expected_brew, str) and expected_brew:
        return command_arg0 == expected_brew
    return Path(command_arg0).name == "brew"


def successful_brew_prefix(commands: list[Any], expected_brew: Any = None) -> str | None:
    for command in commands:
        if not isinstance(command, dict) or command.get("returncode") != 0:
            continue
        command_value = command.get("command")
        if not isinstance(command_value, list):
            continue
        parts = [str(part) for part in command_value]
        if (
            len(parts) == 3
            and command_uses_brew(parts[0], expected_brew)
            and parts[1:] == ["--prefix", FORMULA_NAME]
        ):
            prefix = str(command.get("stdout") or "").strip()
            return prefix or None
    return None


def has_failed_brew_list_after_successful_uninstall(commands: list[Any], expected_brew: Any = None) -> bool:
    saw_successful_uninstall = False
    for command in commands:
        if not isinstance(command, dict):
            continue
        command_value = command.get("command")
        if not isinstance(command_value, list):
            continue
        parts = [str(part) for part in command_value]
        if not parts or not command_uses_brew(parts[0], expected_brew):
            continue
        if command.get("returncode") == 0 and parts[1:] == ["uninstall", "--formula", FORMULA_NAME]:
            saw_successful_uninstall = True
            continue
        if (
            saw_successful_uninstall
            and command.get("returncode") != 0
            and parts[1:] == ["list", "--formula", FORMULA_NAME]
        ):
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


def has_successful_configured_status_command(commands: list[Any], expected_binary: Any) -> bool:
    for parts in successful_expected_binary_commands(commands, expected_binary):
        if (
            len(parts) == 5
            and parts[1] == "--config"
            and Path(parts[2]).is_absolute()
            and parts[3:] == ["status", "--json"]
        ):
            return True
    return False


def has_successful_inspect_recent_command(commands: list[Any], expected_binary: Any) -> bool:
    for parts in successful_expected_binary_commands(commands, expected_binary):
        if (
            len(parts) == 6
            and parts[1] == "inspect-recent"
            and parts[2] == "--codex-home"
            and Path(parts[3]).is_absolute()
            and parts[4] == "--limit"
            and parts[5].isdecimal()
            and int(parts[5]) > 0
        ):
            return True
    return False


def has_successful_configured_sync_command(commands: list[Any], expected_binary: Any) -> bool:
    for parts in successful_expected_binary_commands(commands, expected_binary):
        if (
            len(parts) == 6
            and parts[1] == "--config"
            and Path(parts[2]).is_absolute()
            and parts[3:5] == ["sync-once", "--dry-run-output"]
            and Path(parts[5]).is_absolute()
        ):
            return True
    return False


def successful_expected_binary_commands(commands: list[Any], expected_binary: Any) -> list[list[str]]:
    matches: list[list[str]] = []
    for command in commands:
        if not isinstance(command, dict) or command.get("returncode") != 0:
            continue
        command_value = command.get("command")
        if not isinstance(command_value, list) or not command_value:
            continue
        parts = [str(part) for part in command_value]
        if command_uses_expected_binary(parts[0], expected_binary):
            matches.append(parts)
    return matches


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


def formula_sha256(path: Path) -> str | None:
    return file_sha256(path)


def file_sha256(path: Path | None) -> str | None:
    try:
        if path is None or not path.is_file():
            return None
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


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
