from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


FORMULA_NAME = "codex-obsidian-sync"


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test a Homebrew formula install and uninstall.")
    parser.add_argument("--formula", required=True, type=Path, help="Homebrew formula file.")
    parser.add_argument(
        "--expected-version",
        required=True,
        help="Expected release version or tag, for example 0.1.0 or v0.1.0.",
    )
    parser.add_argument("--brew", default="brew", help="brew executable. Defaults to PATH lookup.")
    parser.add_argument("--output", type=Path, help="Write the smoke report to this path.")
    args = parser.parse_args()

    result = run_smoke(
        formula=args.formula,
        expected_version=normalize_version(args.expected_version),
        brew=args.brew,
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        write_json(args.output, result)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def run_smoke(*, formula: Path, expected_version: str, brew: str) -> dict[str, Any]:
    formula = formula.expanduser().resolve()
    if not formula.is_file():
        raise RuntimeError(f"Formula is missing: {formula}")

    brew_path = resolve_executable(brew)
    if brew_path is None:
        raise RuntimeError(f"brew executable was not found: {brew}")

    commands: list[dict[str, Any]] = []
    if brew_formula_is_installed(brew_path, commands):
        raise RuntimeError(f"Homebrew formula is already installed: {FORMULA_NAME}")

    installed_by_script = False
    try:
        run_checked([str(brew_path), "install", "--formula", str(formula)], commands)
        installed_by_script = True

        prefix = run_checked([str(brew_path), "--prefix", FORMULA_NAME], commands).stdout.strip()
        binary = Path(prefix) / "bin" / FORMULA_NAME
        version_result = run_checked([str(binary), "--version"], commands)
        version_output = version_result.stdout.strip()
        expected_output = f"{FORMULA_NAME} {expected_version}"
        if version_output != expected_output:
            raise RuntimeError(f"Unexpected Homebrew binary version: {version_output!r}, expected {expected_output!r}")

        run_checked([str(brew_path), "test", FORMULA_NAME], commands)
    finally:
        if installed_by_script:
            run_checked([str(brew_path), "uninstall", "--formula", FORMULA_NAME], commands)

    installed_after = brew_formula_is_installed(brew_path, commands)
    if installed_after:
        raise RuntimeError(f"Homebrew formula remained installed after uninstall: {FORMULA_NAME}")

    return {
        "ok": True,
        "generated_at": datetime.now(UTC).isoformat(),
        "formula": str(formula),
        "brew": str(brew_path),
        "expected_version": expected_version,
        "installed_after": installed_after,
        "commands": commands,
    }


def brew_formula_is_installed(brew_path: Path, commands: list[dict[str, Any]]) -> bool:
    result = run_capture([str(brew_path), "list", "--formula", FORMULA_NAME])
    commands.append(command_record(result))
    return result.returncode == 0


def run_checked(command: list[str], commands: list[dict[str, Any]]) -> subprocess.CompletedProcess[str]:
    result = run_capture(command)
    commands.append(command_record(result))
    if result.returncode != 0:
        raise RuntimeError(format_command_failure(result))
    return result


def run_capture(command: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, capture_output=True, text=True, check=False, timeout=120)
    except FileNotFoundError as error:
        return subprocess.CompletedProcess(command, 127, "", str(error))
    except subprocess.TimeoutExpired as error:
        return subprocess.CompletedProcess(command, 124, error.stdout or "", error.stderr or "command timed out")


def command_record(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    return {
        "command": list(result.args),
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def format_command_failure(result: subprocess.CompletedProcess[str]) -> str:
    return "\n".join(
        [
            f"Command failed ({result.returncode}): {' '.join(str(part) for part in result.args)}",
            "--- stdout ---",
            result.stdout,
            "--- stderr ---",
            result.stderr,
        ]
    )


def normalize_version(value: str) -> str:
    version = value.strip()
    if version.startswith("v"):
        version = version[1:]
    if not re.fullmatch(r"\d+(?:\.\d+){1,3}(?:[-+][0-9A-Za-z.-]+)?", version):
        raise ValueError(f"Invalid release version: {value}")
    return version


def resolve_executable(value: str) -> Path | None:
    path = Path(value).expanduser()
    if path.is_absolute() or "/" in value:
        return path.resolve()
    resolved = shutil.which(value)
    return Path(resolved).resolve() if resolved else None


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    path.chmod(0o600)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
