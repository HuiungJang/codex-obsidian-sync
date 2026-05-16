from __future__ import annotations

import argparse
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


FORMULA_NAME = "codex-obsidian-sync"
MARKER = ".codex-obsidian-sync-launchagent-smoke"
MARKER_CONTENT = "managed by smoke_launchagent.py\n"
SESSION_ID = "019f5f16-0000-7000-8000-000000000401"
TRANSCRIPT_TEXT = "LaunchAgent smoke transcript text should stay out of service state"


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test a Rust LaunchAgent with an isolated temporary label.")
    parser.add_argument("--binary", required=True, type=Path, help="codex-obsidian-sync binary to run.")
    parser.add_argument("--work-dir", type=Path, help="Dedicated /tmp/codex-obsidian-sync-* smoke directory.")
    parser.add_argument("--keep-work-dir", action="store_true", help="Reuse an existing managed work dir.")
    parser.add_argument("--launchctl", default="launchctl", help="launchctl executable. Defaults to PATH lookup.")
    parser.add_argument("--label", help="Temporary LaunchAgent label. Defaults to com.codex.obsidian-sync.smoke.<pid>.")
    parser.add_argument("--timeout-seconds", type=float, default=30.0, help="Seconds to wait for service success.")
    parser.add_argument("--output", type=Path, help="Write the smoke report to this path.")
    args = parser.parse_args()

    work_dir = prepare_work_dir(args.work_dir, keep=args.keep_work_dir)
    binary = args.binary.expanduser().resolve()
    label = args.label or f"com.codex.obsidian-sync.smoke.{os.getpid()}"
    result = run_smoke(
        binary=binary,
        work_dir=work_dir,
        launchctl=args.launchctl,
        label=label,
        timeout_seconds=args.timeout_seconds,
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        write_json(args.output, result)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def prepare_work_dir(path: Path | None, *, keep: bool) -> Path:
    created = path is None
    if path is None:
        path = Path(tempfile.mkdtemp(prefix="codex-obsidian-sync-launchagent-smoke-"))
    path = path.expanduser().resolve()
    validate_work_dir(path)

    if path.exists():
        if not path.is_dir():
            raise RuntimeError(f"Work path is not a directory: {path}")
        if not created and not keep:
            if not is_managed_work_dir(path):
                raise RuntimeError(f"Refusing to remove unmanaged work dir: {path}")
            shutil.rmtree(path)

    private_mkdir(path)
    marker = path / MARKER
    marker.write_text(MARKER_CONTENT, encoding="utf-8")
    marker.chmod(0o600)
    return path


def validate_work_dir(path: Path) -> None:
    temp_roots = {Path(tempfile.gettempdir()).resolve(), Path("/tmp").resolve()}
    if path.parent not in temp_roots or not path.name.startswith("codex-obsidian-sync-"):
        roots = ", ".join(sorted(str(root) for root in temp_roots))
        raise RuntimeError(f"Work dir must be a dedicated codex-obsidian-sync-* path under {roots}: {path}")


def is_managed_work_dir(path: Path) -> bool:
    marker = path / MARKER
    return marker.is_file() and not marker.is_symlink() and marker.read_text(encoding="utf-8") == MARKER_CONTENT


def run_smoke(
    *,
    binary: Path,
    work_dir: Path,
    launchctl: str,
    label: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    if not binary.is_file():
        raise RuntimeError(f"Binary is missing: {binary}")
    if not os.access(binary, os.X_OK):
        raise RuntimeError(f"Binary is not executable: {binary}")
    if not label.startswith("com.codex.obsidian-sync.smoke."):
        raise RuntimeError("LaunchAgent smoke label must start with com.codex.obsidian-sync.smoke.")

    launchctl_path = resolve_executable(launchctl)
    if launchctl_path is None:
        raise RuntimeError(f"launchctl executable was not found: {launchctl}")

    runtime = work_dir / "runtime"
    private_mkdir(runtime)
    installed_binary = install_binary(binary, work_dir / "bin")
    config_path = prepare_fixture(runtime)
    plist_path = write_smoke_plist(
        work_dir=work_dir,
        binary=installed_binary,
        config_path=config_path,
        label=label,
    )
    target = f"gui/{os.getuid()}/{label}"
    commands: list[dict[str, Any]] = []
    commands_path = work_dir / "launchagent-smoke-commands.json"
    booted = False
    summary: dict[str, Any] | None = None
    try:
        pre_print = run_launchctl([launchctl_path, "print", target], commands)
        if pre_print.returncode == 0:
            raise RuntimeError(f"Temporary LaunchAgent label is already loaded: {label}")

        run_required([launchctl_path, "bootstrap", f"gui/{os.getuid()}", plist_path], commands)
        booted = True
        loaded_after_bootstrap = run_launchctl([launchctl_path, "print", target], commands).returncode == 0
        if not loaded_after_bootstrap:
            raise RuntimeError("LaunchAgent did not load after bootstrap")

        run_required([launchctl_path, "kickstart", "-k", target], commands)
        summary = wait_for_success(runtime, timeout_seconds)
        verify_outputs(runtime, summary)
    finally:
        if booted:
            run_launchctl([launchctl_path, "bootout", target], commands)
        write_json(commands_path, {"commands": commands})

    loaded_after_bootout = run_launchctl([launchctl_path, "print", target], commands).returncode == 0
    write_json(commands_path, {"commands": commands})
    if loaded_after_bootout:
        raise RuntimeError("Temporary LaunchAgent remained loaded after bootout")

    note_files = list((runtime / "vault").rglob("*.md"))
    return {
        "ok": True,
        "generated_at": datetime.now(UTC).isoformat(),
        "work_dir": str(work_dir),
        "label": label,
        "target": target,
        "binary": str(installed_binary),
        "source_binary": str(binary),
        "config": str(config_path),
        "plist": str(plist_path),
        "loaded_after_bootout": loaded_after_bootout,
        "last_success_at": summary.get("last_success_at") if summary else None,
        "last_summary": summary.get("last_summary") if summary else None,
        "note_files": len(note_files),
        "commands": commands,
    }


def prepare_fixture(runtime: Path) -> Path:
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
                "thread_name": "LaunchAgent Smoke",
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
                    "content": [{"type": "input_text", "text": "LaunchAgent smoke question"}],
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
    config_path = runtime / "config.toml"
    config_path.write_text(
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
                "interval_seconds = 3600",
                "rust_service_write_enabled = true",
                "",
            ]
        ),
        encoding="utf-8",
    )
    config_path.chmod(0o600)
    return config_path


def install_binary(source: Path, bin_dir: Path) -> Path:
    private_mkdir(bin_dir)
    target = bin_dir / FORMULA_NAME
    shutil.copyfile(source, target)
    target.chmod(0o755)
    return target


def write_smoke_plist(*, work_dir: Path, binary: Path, config_path: Path, label: str) -> Path:
    plist_path = work_dir / f"{label}.plist"
    stdout_path = work_dir / "launchd.stdout.log"
    stderr_path = work_dir / "launchd.stderr.log"
    payload = {
        "Label": label,
        "Program": str(binary),
        "ProgramArguments": [str(binary), "--config", str(config_path), "service-run"],
        "RunAtLoad": True,
        "KeepAlive": False,
        "StartInterval": 3600,
        "ThrottleInterval": 1,
        "StandardOutPath": str(stdout_path),
        "StandardErrorPath": str(stderr_path),
    }
    plist_path.write_bytes(plistlib.dumps(payload, sort_keys=False))
    plist_path.chmod(0o600)
    return plist_path


def wait_for_success(runtime: Path, timeout_seconds: float) -> dict[str, Any]:
    service_state = runtime / "state" / "service-state.json"
    deadline = time.monotonic() + timeout_seconds
    last_state: dict[str, Any] = {}
    while time.monotonic() < deadline:
        if service_state.is_file():
            last_state = read_json_object(service_state)
            if last_state.get("last_success_at") and last_state.get("pending") is False:
                return last_state
        time.sleep(0.25)
    raise RuntimeError(f"LaunchAgent service-run did not finish before timeout; last_state={last_state}")


def verify_outputs(runtime: Path, service_state: dict[str, Any]) -> None:
    summary = service_state.get("last_summary")
    if not isinstance(summary, dict) or not positive_int(summary.get("processed")):
        raise RuntimeError("LaunchAgent service-run did not record processed summary")
    if service_state.get("last_error_type") not in (None, ""):
        raise RuntimeError(f"LaunchAgent service-run recorded an error: {service_state.get('last_error_type')}")
    if not list((runtime / "vault").rglob("*.md")):
        raise RuntimeError("LaunchAgent service-run did not write note files")
    state_text = (runtime / "state" / "service-state.json").read_text(encoding="utf-8")
    if TRANSCRIPT_TEXT in state_text:
        raise RuntimeError("Raw transcript text leaked into service state")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    private_mkdir(path.parent)
    content = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n"
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)


def read_json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON file is not an object: {path}")
    return value


def run_required(command: list[Path | str], commands: list[dict[str, Any]]) -> subprocess.CompletedProcess[str]:
    result = run_launchctl(command, commands)
    if result.returncode != 0:
        raise RuntimeError(format_command_failure(result))
    return result


def run_launchctl(command: list[Path | str], commands: list[dict[str, Any]]) -> subprocess.CompletedProcess[str]:
    result = run_capture([str(part) for part in command])
    commands.append(command_record(result))
    return result


def run_capture(command: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, capture_output=True, text=True, check=False, timeout=30)
    except FileNotFoundError as error:
        return subprocess.CompletedProcess(command, 127, "", str(error))
    except subprocess.TimeoutExpired as error:
        return subprocess.CompletedProcess(
            command,
            124,
            normalize_output(error.stdout),
            normalize_output(error.stderr) or "command timed out",
        )


def command_record(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    return {
        "command": [str(part) for part in result.args],
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


def positive_int(value: Any) -> bool:
    return isinstance(value, int) and value > 0


def normalize_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value


def resolve_executable(value: str) -> Path | None:
    path = Path(value).expanduser()
    if path.is_absolute() or "/" in value:
        return path.resolve()
    resolved = shutil.which(value)
    return Path(resolved).resolve() if resolved else None


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    path.chmod(0o600)


def private_mkdir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
