from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


FORMULA_NAME = "codex-obsidian-sync"
SESSION_ID = "019f5f16-0000-7000-8000-000000000301"
TRANSCRIPT_TEXT = "Homebrew smoke transcript text should stay out of status summary state"


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

    output = resolve_output_path(args.output)
    result = run_smoke(
        formula=args.formula,
        expected_version=normalize_version(args.expected_version),
        brew=args.brew,
    )
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        write_json(output, result)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def resolve_output_path(path: Path | None) -> Path | None:
    if path is None:
        return None
    output = path.expanduser()
    if output.is_symlink():
        raise RuntimeError(f"Output path is a symlink: {output}")
    return output


def run_smoke(*, formula: Path, expected_version: str, brew: str) -> dict[str, Any]:
    formula = formula.expanduser()
    if formula.is_symlink():
        raise RuntimeError(f"Formula is a symlink: {formula}")
    formula = formula.resolve()
    if not formula.is_file():
        raise RuntimeError(f"Formula is missing: {formula}")
    formula_sha256 = hashlib.sha256(formula.read_bytes()).hexdigest()

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

        installed_binary = Path(prefix) / "bin" / FORMULA_NAME
        runtime = Path(tempfile.mkdtemp(prefix="codex-obsidian-sync-homebrew-smoke-"))
        smoke_details = run_installed_binary_smoke(installed_binary, runtime, commands)
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
        "formula_sha256": formula_sha256,
        "brew": str(brew_path),
        "expected_version": expected_version,
        "version": version_output,
        "installed_binary": str(installed_binary),
        "installed_after": installed_after,
        **smoke_details,
        "commands": commands,
    }


def brew_formula_is_installed(brew_path: Path, commands: list[dict[str, Any]]) -> bool:
    result = run_capture([str(brew_path), "list", "--formula", FORMULA_NAME])
    commands.append(command_record(result))
    return result.returncode == 0


def run_installed_binary_smoke(
    binary: Path,
    runtime: Path,
    commands: list[dict[str, Any]],
) -> dict[str, Any]:
    private_mkdir(runtime)
    prepare_fixture(runtime)
    config_path = runtime / "config.toml"
    vault = runtime / "vault"
    dry_run_output = runtime / "dry-run-output"
    vault_before = hash_tree(vault)

    status = run_json([str(binary), "--config", str(config_path), "status", "--json"], runtime / "status", commands)
    summary = run_json(
        [
            str(binary),
            "--config",
            str(config_path),
            "sync-once",
            "--dry-run-output",
            str(dry_run_output),
        ],
        runtime / "sync-once",
        commands,
    )

    vault_after = hash_tree(vault)
    if vault_before != vault_after:
        raise RuntimeError("Homebrew binary dry-run mutated the configured vault")
    if not summary.get("dry_run"):
        raise RuntimeError("Homebrew binary sync-once did not report dry_run=true")
    note_files = len(list(dry_run_output.rglob("*.md")))
    if note_files <= 0:
        raise RuntimeError("Homebrew binary dry-run did not produce any note files")
    temp_state = dry_run_output / "sync-state.json"
    if not temp_state.is_file():
        raise RuntimeError("Homebrew binary dry-run did not produce sync-state.json")

    leak_paths = [runtime / "status.stdout", runtime / "sync-once.stdout", temp_state]
    leaked = [str(path) for path in leak_paths if TRANSCRIPT_TEXT in path.read_text(encoding="utf-8")]
    if leaked:
        raise RuntimeError(f"Raw transcript text leaked into Homebrew smoke artifacts: {leaked}")

    return {
        "work_dir": str(runtime),
        "status_configured": status.get("configured"),
        "status_json_parsed": True,
        "dry_run": summary.get("dry_run"),
        "processed": summary.get("processed"),
        "vault_unchanged": vault_before == vault_after,
        "note_files": note_files,
        "temp_state_file": str(temp_state),
    }


def prepare_fixture(runtime: Path) -> None:
    codex_home = runtime / ".codex"
    vault = runtime / "vault"
    state_dir = runtime / "state"
    sessions = codex_home / "sessions" / "2026" / "05" / "16"
    private_mkdir(codex_home)
    private_mkdir(vault)
    private_mkdir(state_dir)
    private_mkdir(sessions)
    write_jsonl(
        codex_home / "session_index.jsonl",
        [
            {
                "id": SESSION_ID,
                "thread_name": "Homebrew Smoke",
                "updated_at": "2026-05-16T00:00:00Z",
            }
        ],
    )
    write_jsonl(
        sessions / f"rollout-{SESSION_ID}.jsonl",
        [
            {
                "timestamp": "2026-05-16T00:00:00Z",
                "type": "session_meta",
                "payload": {
                    "id": SESSION_ID,
                    "timestamp": "2026-05-16T00:00:00Z",
                    "cwd": str(runtime / "project"),
                    "originator": "codex_cli_rs",
                },
            },
            {
                "timestamp": "2026-05-16T00:01:00Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Homebrew smoke question"}],
                },
            },
            {
                "timestamp": "2026-05-16T00:02:00Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "phase": "final_answer",
                    "content": [{"type": "output_text", "text": TRANSCRIPT_TEXT}],
                },
            },
        ],
    )
    (runtime / "config.toml").write_text(
        "\n".join(
            [
                f'vault = "{vault}"',
                f'codex_home = "{codex_home}"',
                f'state_file = "{state_dir / "sync-state.json"}"',
                f'lock_file = "{state_dir / "sync-state.lock"}"',
                f'service_state_file = "{state_dir / "service-state.json"}"',
                f'service_runner_lock_file = "{state_dir / "service-runner.lock"}"',
                f'service_state_lock_file = "{state_dir / "service-state.lock"}"',
                f'launchd_plist_path = "{runtime / "LaunchAgents" / "com.codex.obsidian-sync.plist"}"',
                f'launchd_stdout_path = "{runtime / "logs" / "stdout.log"}"',
                f'launchd_stderr_path = "{runtime / "logs" / "stderr.log"}"',
                "interval_seconds = 60",
                "",
            ]
        ),
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    private_mkdir(path.parent)
    content = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n"
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)


def run_json(command: list[str], output_base: Path, commands: list[dict[str, Any]]) -> Any:
    result = run_checked(command, commands)
    output_base.with_suffix(".stdout").write_text(result.stdout, encoding="utf-8")
    output_base.with_suffix(".stderr").write_text(result.stderr, encoding="utf-8")
    return json.loads(result.stdout)


def hash_tree(path: Path) -> str:
    digest = hashlib.sha256()
    if not path.exists():
        return digest.hexdigest()
    for item in sorted(path.rglob("*")):
        relative = item.relative_to(path).as_posix()
        if item.is_dir():
            digest.update(f"dir:{relative}\n".encode())
        elif item.is_file() and not item.is_symlink():
            digest.update(f"file:{relative}\n".encode())
            digest.update(hashlib.sha256(item.read_bytes()).hexdigest().encode())
            digest.update(b"\n")
        else:
            digest.update(f"other:{relative}\n".encode())
    return digest.hexdigest()


def private_mkdir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


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
