from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from audit_cutover_readiness import (
    DEFAULT_REPOSITORY,
    FORMULA_NAME,
    TARGETS,
    audit_homebrew_formula,
    audit_release_dir,
    build_result,
    cargo_version,
    check,
    normalize_version,
    validate_repository,
    write_json,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit release artifacts and smoke evidence before cutover.")
    parser.add_argument(
        "--version",
        help="Release version or tag, for example 0.1.0 or v0.1.0. Defaults to rust/Cargo.toml.",
    )
    parser.add_argument(
        "--repository",
        default=DEFAULT_REPOSITORY,
        help=f"GitHub repository in owner/name form. Defaults to {DEFAULT_REPOSITORY}.",
    )
    parser.add_argument(
        "--release-dir",
        default=Path("dist"),
        type=Path,
        help="Directory containing release artifacts, formula, and smoke summaries. Defaults to dist.",
    )
    parser.add_argument("--homebrew-formula", type=Path, help="Generated Homebrew formula path.")
    parser.add_argument("--output", type=Path, help="Write the evidence report to this path.")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    version = normalize_version(args.version or cargo_version(repo_root / "rust" / "Cargo.toml"))
    repository = validate_repository(args.repository)
    release_dir = args.release_dir.expanduser().resolve()
    formula_path = (args.homebrew_formula or release_dir / f"{FORMULA_NAME}.rb").expanduser().resolve()

    release_checks = audit_release_dir(release_dir)
    checksums = {
        release_check["details"]["target"]: release_check["details"]["checksum"]
        for release_check in release_checks
        if release_check["ok"] and release_check["details"].get("checksum")
    }
    checks = [
        *release_checks,
        audit_homebrew_formula(formula_path, version, repository, checksums),
        *audit_release_smoke_summaries(release_dir, version),
        audit_homebrew_smoke_summary(release_dir, formula_path, version),
    ]
    result = build_result(version=version, repository=repository, checks=checks)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        write_json(args.output, result)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["ok"] else 1


def audit_release_smoke_summaries(release_dir: Path, version: str) -> list[dict[str, Any]]:
    return [audit_release_smoke_summary(release_dir, target, version) for target in TARGETS]


def audit_release_smoke_summary(release_dir: Path, target: str, version: str) -> dict[str, Any]:
    path = release_dir / f"{FORMULA_NAME}-{target}.smoke-summary.json"
    package_name = f"{FORMULA_NAME}-{target}.tar.gz"
    checksum_name = f"{package_name}.sha256"
    details: dict[str, Any] = {
        "target": target,
        "path": str(path),
        "expected_version": f"{FORMULA_NAME} {version}",
    }
    reasons: list[str] = []

    summary = read_json_object(path, reasons)
    if summary is None:
        return check(f"release smoke summary:{target}", False, details, reasons)

    details.update(
        {
            "ok": summary.get("ok"),
            "version": summary.get("version"),
            "status_configured": summary.get("status_configured"),
            "status_json_parsed": summary.get("status_json_parsed"),
            "dry_run": summary.get("dry_run"),
            "vault_unchanged": summary.get("vault_unchanged"),
            "inspect_count": summary.get("inspect_count"),
            "note_files": summary.get("note_files"),
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
    if not positive_int(summary.get("inspect_count")):
        reasons.append("release smoke inspect_count is not positive")
    if not positive_int(summary.get("note_files")):
        reasons.append("release smoke note_files is not positive")

    return check(f"release smoke summary:{target}", not reasons, details, reasons)


def audit_homebrew_smoke_summary(release_dir: Path, formula_path: Path, version: str) -> dict[str, Any]:
    path = release_dir / "homebrew-smoke-summary.json"
    details: dict[str, Any] = {
        "path": str(path),
        "expected_version": version,
        "formula": str(formula_path),
    }
    reasons: list[str] = []

    summary = read_json_object(path, reasons)
    if summary is None:
        return check("homebrew smoke summary", False, details, reasons)

    commands = summary.get("commands")
    details.update(
        {
            "ok": summary.get("ok"),
            "summary_expected_version": summary.get("expected_version"),
            "installed_after": summary.get("installed_after"),
            "command_count": len(commands) if isinstance(commands, list) else None,
        }
    )
    if summary.get("ok") is not True:
        reasons.append("Homebrew smoke summary ok is not true")
    if summary.get("expected_version") != version:
        reasons.append("Homebrew smoke expected_version does not match release version")
    if summary_path_name(summary.get("formula")) != formula_path.name:
        reasons.append("Homebrew smoke formula does not match generated formula")
    if summary.get("installed_after") is not False:
        reasons.append("Homebrew smoke did not prove the formula was uninstalled")
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

    return check("homebrew smoke summary", not reasons, details, reasons)


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


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
