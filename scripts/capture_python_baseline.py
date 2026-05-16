from __future__ import annotations

import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from codex_obsidian_sync.config import resolve_service_paths, save_toml_config
from codex_obsidian_sync.launchd import render_launch_agent_plist
from codex_obsidian_sync.service_state import default_service_state
from codex_obsidian_sync.status_snapshot import (
    build_status_snapshot,
    status_snapshot_to_dict,
)


BASELINE_DIR = Path("baseline")
RUNTIME_ROOT = Path("/tmp/codex-obsidian-sync-python-baseline")

SESSION_VALID = "019f5f16-0000-7000-8000-000000000001"
SESSION_INVALID = "019f5f16-0000-7000-8000-000000000002"
SESSION_SUBAGENT = "019f5f16-0000-7000-8000-000000000003"
SESSION_STALE = "019f5f16-0000-7000-8000-000000000004"


def main() -> int:
    repo_root = Path.cwd()
    prepare_output_dirs()
    prepare_runtime_root()

    codex_home = RUNTIME_ROOT / ".codex"
    vault = RUNTIME_ROOT / "vault"
    state_file = codex_home / "obsidian-sync" / "sync-state.json"
    lock_file = codex_home / "obsidian-sync" / "sync-state.lock"
    config_path = RUNTIME_ROOT / "config.toml"
    vault.mkdir(parents=True)

    write_fixture(codex_home)
    write_config(config_path, codex_home, vault, state_file, lock_file)
    capture_python_tests(repo_root)
    capture_cli_help(repo_root)

    valid_rollout = rollout_path(codex_home, "2026", "05", "14", "09-00-00", SESSION_VALID)
    invalid_rollout = rollout_path(codex_home, "2026", "05", "14", "09-10-00", SESSION_INVALID)
    subagent_rollout = rollout_path(codex_home, "2026", "05", "14", "09-20-00", SESSION_SUBAGENT)
    stale_rollout = rollout_path(codex_home, "2026", "05", "14", "09-30-00", SESSION_STALE)

    write_json_artifact(
        BASELINE_DIR / "summary" / "initial.json",
        run_sync(config_path, vault, codex_home, state_file, lock_file),
    )
    copy_synthetic_fixtures(codex_home)
    invalid_rollout.unlink()
    subagent_rollout.unlink()

    append_jsonl(
        valid_rollout,
        [
            message_record(
                timestamp="2026-05-14T00:02:00Z",
                role="assistant",
                text="Baseline appended response",
                phase="final_answer",
            )
        ],
    )
    set_mtime(valid_rollout, 1_778_716_920)
    write_json_artifact(
        BASELINE_DIR / "summary" / "append.json",
        run_sync(config_path, vault, codex_home, state_file, lock_file),
    )

    append_jsonl(
        stale_rollout,
        [
            message_record(
                timestamp="2026-05-14T00:31:00Z",
                role="assistant",
                text="Stale offset recovered response",
                phase="final_answer",
            )
        ],
    )
    set_mtime(stale_rollout, 1_778_718_660)
    corrupt_stale_offset(state_file, stale_rollout)
    write_json_artifact(
        BASELINE_DIR / "summary" / "stale-offset.json",
        run_sync(config_path, vault, codex_home, state_file, lock_file),
    )

    capture_inspect_outputs(repo_root, config_path, codex_home, valid_rollout)
    capture_status_and_plist(config_path, codex_home, vault, state_file, lock_file)
    copy_notes_artifacts(vault)
    write_state_schema_contract(state_file)
    write_fixture_contract()
    write_metadata()
    assert_no_private_fixture_text()
    return 0


def prepare_output_dirs() -> None:
    BASELINE_DIR.mkdir(exist_ok=True)
    for name in (
        "cli-help",
        "fixtures",
        "inspect",
        "notes",
        "plist",
        "state",
        "status",
        "summary",
    ):
        path = BASELINE_DIR / name
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True)


def prepare_runtime_root() -> None:
    if RUNTIME_ROOT.exists():
        shutil.rmtree(RUNTIME_ROOT)
    RUNTIME_ROOT.mkdir(parents=True)


def write_fixture(codex_home: Path) -> None:
    sessions_dir = codex_home / "sessions" / "2026" / "05" / "14"
    sessions_dir.mkdir(parents=True)
    index_path = codex_home / "session_index.jsonl"
    write_jsonl(
        index_path,
        [
            {
                "id": SESSION_VALID,
                "thread_name": "Baseline Conversation",
                "updated_at": "2026-05-14T00:00:00Z",
            },
            {
                "id": SESSION_INVALID,
                "thread_name": "Invalid Rollout",
                "updated_at": "2026-05-14T00:10:00Z",
            },
            {
                "id": SESSION_SUBAGENT,
                "thread_name": "Subagent Thread",
                "updated_at": "2026-05-14T00:20:00Z",
            },
            {
                "id": SESSION_STALE,
                "thread_name": "Stale Offset",
                "updated_at": "2026-05-14T00:30:00Z",
            },
        ],
    )

    valid_rollout = rollout_path(codex_home, "2026", "05", "14", "09-00-00", SESSION_VALID)
    write_jsonl(
        valid_rollout,
        [
            session_meta(SESSION_VALID, "2026-05-14T00:00:00Z", cwd="/tmp/baseline-project"),
            message_record(
                timestamp="2026-05-14T00:01:00Z",
                role="user",
                text="Baseline first question",
            ),
            message_record(
                timestamp="2026-05-14T00:01:30Z",
                role="assistant",
                text="Use sk-abcdefghijklmnopqrstuvwxyz1234567890 for redaction baseline",
                phase="final_answer",
            ),
        ],
    )
    set_mtime(valid_rollout, 1_778_716_800)

    invalid_rollout = rollout_path(codex_home, "2026", "05", "14", "09-10-00", SESSION_INVALID)
    invalid_rollout.write_text(
        "\n".join(
            [
                json.dumps(
                    session_meta(SESSION_INVALID, "2026-05-14T00:10:00Z", cwd="/tmp/baseline-project"),
                    ensure_ascii=False,
                ),
                '{"timestamp":"2026-05-14T00:11:00Z","type":"response_item","payload":',
                "",
            ]
        ),
        encoding="utf-8",
    )
    set_mtime(invalid_rollout, 1_778_717_400)

    subagent_rollout = rollout_path(codex_home, "2026", "05", "14", "09-20-00", SESSION_SUBAGENT)
    write_jsonl(
        subagent_rollout,
        [
            session_meta(
                SESSION_SUBAGENT,
                "2026-05-14T00:20:00Z",
                cwd="/tmp/baseline-project",
                source={"subagent": {"thread_spawn": {"parent_thread_id": SESSION_VALID}}},
            ),
            message_record(
                timestamp="2026-05-14T00:21:00Z",
                role="user",
                text="Subagent body excluded by default",
            ),
        ],
    )
    set_mtime(subagent_rollout, 1_778_718_000)

    stale_rollout = rollout_path(codex_home, "2026", "05", "14", "09-30-00", SESSION_STALE)
    write_jsonl(
        stale_rollout,
        [
            session_meta(SESSION_STALE, "2026-05-14T00:30:00Z", cwd="/tmp/baseline-project"),
            message_record(
                timestamp="2026-05-14T00:30:30Z",
                role="user",
                text="Stale offset first question",
            ),
        ],
    )
    set_mtime(stale_rollout, 1_778_718_600)


def write_config(config_path: Path, codex_home: Path, vault: Path, state_file: Path, lock_file: Path) -> None:
    save_toml_config(
        config_path,
        {
            "vault": str(vault),
            "codex_home": str(codex_home),
            "state_file": str(state_file),
            "lock_file": str(lock_file),
            "interval_seconds": 60,
            "recent_days": 3650,
            "candidate_file_limit": 100,
            "candidate_bytes_limit": 524288000,
            "log_level": "ERROR",
            "launchd_plist_path": str(RUNTIME_ROOT / "LaunchAgents" / "com.codex.obsidian-sync.plist"),
            "launchd_stdout_path": str(codex_home / "obsidian-sync" / "launchd.stdout.log"),
            "launchd_stderr_path": str(codex_home / "obsidian-sync" / "launchd.stderr.log"),
        },
    )


def capture_cli_help(repo_root: Path) -> None:
    commands = {
        "root": ["--help"],
        "setup": ["setup", "--help"],
        "start": ["start", "--help"],
        "stop": ["stop", "--help"],
        "status": ["status", "--help"],
        "service-run": ["service-run", "--help"],
        "inspect-rollout": ["inspect-rollout", "--help"],
        "inspect-recent": ["inspect-recent", "--help"],
        "sync-once": ["sync-once", "--help"],
        "watch": ["watch", "--help"],
    }
    for name, args in commands.items():
        result = run_cli(repo_root, args)
        write_text_artifact(
            BASELINE_DIR / "cli-help" / f"{name}.txt",
            format_command_output(["codex-obsidian-sync", *args], result),
        )


def capture_python_tests(repo_root: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        cwd=repo_root,
        env={**os.environ, "PYTHONPATH": str(repo_root / "src")},
        capture_output=True,
        text=True,
        check=False,
    )
    write_text_artifact(
        BASELINE_DIR / "python-test-output.txt",
        normalize_test_output(
            format_command_output(
                ["PYTHONPATH=src", "<python3.13>", "-m", "unittest", "discover", "-s", "tests", "-v"],
                result,
            )
        ),
    )
    if result.returncode != 0:
        raise RuntimeError("Python tests failed; see baseline/python-test-output.txt")


def run_sync(
    config_path: Path,
    vault: Path,
    codex_home: Path,
    state_file: Path,
    lock_file: Path,
) -> dict[str, Any]:
    result = run_cli(
        Path.cwd(),
        [
            "--config",
            str(config_path),
            "sync-once",
            "--vault",
            str(vault),
            "--codex-home",
            str(codex_home),
            "--state-file",
            str(state_file),
            "--lock-file",
            str(lock_file),
            "--recent-days",
            "3650",
            "--log-level",
            "ERROR",
        ],
    )
    if result.returncode != 0:
        raise RuntimeError(format_command_output(["codex-obsidian-sync", "sync-once"], result))
    payload = json.loads(result.stdout)
    payload["duration_ms"] = "<duration_ms>"
    return payload


def capture_inspect_outputs(repo_root: Path, config_path: Path, codex_home: Path, rollout: Path) -> None:
    rollout_result = run_cli(
        repo_root,
        [
            "--config",
            str(config_path),
            "inspect-rollout",
            str(rollout),
            "--session-index",
            str(codex_home / "session_index.jsonl"),
        ],
    )
    write_text_artifact(
        BASELINE_DIR / "inspect" / "inspect-rollout.json",
        normalized_json_stdout(rollout_result),
    )

    recent_result = run_cli(
        repo_root,
        [
            "--config",
            str(config_path),
            "inspect-recent",
            "--codex-home",
            str(codex_home),
            "--limit",
            "3",
        ],
    )
    write_text_artifact(
        BASELINE_DIR / "inspect" / "inspect-recent.json",
        normalized_json_stdout(recent_result),
    )


def capture_status_and_plist(
    config_path: Path,
    codex_home: Path,
    vault: Path,
    state_file: Path,
    lock_file: Path,
) -> None:
    config_data = {
        "vault": str(vault),
        "codex_home": str(codex_home),
        "state_file": str(state_file),
        "lock_file": str(lock_file),
        "interval_seconds": 60,
        "launchd_plist_path": str(RUNTIME_ROOT / "LaunchAgents" / "com.codex.obsidian-sync.plist"),
        "launchd_stdout_path": str(codex_home / "obsidian-sync" / "launchd.stdout.log"),
        "launchd_stderr_path": str(codex_home / "obsidian-sync" / "launchd.stderr.log"),
    }
    service_state = default_service_state()
    service_state.update(
        {
            "pending": True,
            "next_eligible_at": "2026-05-14T00:40:00+00:00",
            "last_run_started_at": "2026-05-14T00:35:00+00:00",
            "last_run_finished_at": "2026-05-14T00:35:02+00:00",
            "last_success_at": "2026-05-14T00:35:02+00:00",
            "last_summary": {
                "processed": 1,
                "appended": 1,
                "rewritten": 0,
                "skipped_subagents": 0,
                "skipped_invalid": 0,
                "unchanged": 3,
                "total_rollouts": 4,
                "paused": 0,
                "fast_path": 0,
            },
            "updated_at": "2026-05-14T00:35:02+00:00",
        }
    )
    paths = resolve_service_paths(config_path=config_path, config_data=config_data)
    snapshot = build_status_snapshot(
        config_path=config_path,
        config_data=config_data,
        paths=paths,
        launchd_loaded=True,
        service_state=service_state,
    )
    write_json_artifact(BASELINE_DIR / "status" / "status-json.json", status_snapshot_to_dict(snapshot))

    plist = render_launch_agent_plist(config_path=config_path, paths=paths, start_interval=60)
    parsed = plistlib.loads(plist.encode("utf-8"))
    parsed["ProgramArguments"][0] = "<python-executable>"
    plist = plistlib.dumps(parsed, fmt=plistlib.FMT_XML, sort_keys=False).decode("utf-8")
    write_text_artifact(BASELINE_DIR / "plist" / "launch-agent.plist", plist)
    write_json_artifact(BASELINE_DIR / "plist" / "launch-agent.parsed.json", parsed)


def copy_synthetic_fixtures(codex_home: Path) -> None:
    fixture_root = BASELINE_DIR / "fixtures" / "synthetic"
    (fixture_root / "codex-home").mkdir(parents=True)
    shutil.copy2(codex_home / "session_index.jsonl", fixture_root / "codex-home" / "session_index.jsonl")
    shutil.copytree(codex_home / "sessions", fixture_root / "codex-home" / "sessions")


def copy_notes_artifacts(vault: Path) -> None:
    shutil.copytree(vault / "Codex", BASELINE_DIR / "notes" / "Codex")


def write_state_schema_contract(state_file: Path) -> None:
    state = json.loads(state_file.read_text(encoding="utf-8"))
    entry_keys = sorted({key for entry in state["files"].values() for key in entry})
    contract = {
        "sync_state": {
            "top_level": {"files": "object keyed by absolute rollout path"},
            "file_entry_keys": entry_keys,
            "numeric_fields": ["offset", "size", "mtime_ns"],
            "fingerprint_fields": {
                "conversation_note_fingerprint": {"size": "int", "mtime_ns": "int"}
            },
            "unknown_top_level_fields": "preserve where practical",
            "unknown_file_entry_fields": "preserve where practical",
            "raw_transcript_storage": "forbidden",
        },
        "service_state": {
            "schema_version": 1,
            "keys": sorted(default_service_state()),
            "unknown_fields": "ignored by current Python loader",
        },
    }
    write_json_artifact(BASELINE_DIR / "state-schema.json", contract)


def write_fixture_contract() -> None:
    contract = {
        "minimal_committed_synthetic_fixture": "baseline/fixtures/synthetic",
        "covers": [
            "conversation note",
            "daily note",
            "project note",
            "append",
            "invalid rollout",
            "default subagent exclusion",
            "stale offset fallback",
            "redaction",
        ],
        "local_anonymized_fixture_convention": {
            "environment_variable": "CODEX_OBSIDIAN_SYNC_ANON_FIXTURE",
            "expected_shape": {
                "codex_home": "path containing session_index.jsonl and sessions/",
                "vault": "copied Obsidian vault fixture",
                "state_file": "copied sync-state.json",
            },
            "commit_policy": "never commit raw real logs; run token and transcript leak checks first",
        },
    }
    write_json_artifact(BASELINE_DIR / "fixture-contract.json", contract)


def write_metadata() -> None:
    metadata = {
        "python": ">=3.13",
        "executable": "<python-executable>",
        "runtime_root": str(RUNTIME_ROOT),
        "test_command": "PYTHONPATH=src <python3.13> -m unittest discover -s tests -v",
        "volatile_normalization": [
            "launchd.ProgramArguments[0]",
            "metadata.executable",
            "metadata.python",
            "summary.duration_ms",
        ],
    }
    write_json_artifact(BASELINE_DIR / "metadata.json", metadata)


def assert_no_private_fixture_text() -> None:
    forbidden = {"BEGIN PRIVATE", str(Path.home()), Path.home().name, str(Path.cwd())}
    for path in BASELINE_DIR.rglob("*"):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for item in forbidden:
            if item and item in text:
                raise RuntimeError(f"private text marker {item!r} found in {path}")


def run_cli(repo_root: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo_root / "src")
    return subprocess.run(
        [sys.executable, "-m", "codex_obsidian_sync.cli", *args],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def normalized_json_stdout(result: subprocess.CompletedProcess[str]) -> str:
    if result.returncode != 0:
        raise RuntimeError(format_command_output(["codex-obsidian-sync"], result))
    return json.dumps(json.loads(result.stdout), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def normalize_test_output(content: str) -> str:
    return re.sub(r"Ran (\d+) tests in [0-9.]+s", r"Ran \1 tests in <duration>s", content)


def format_command_output(command: list[str], result: subprocess.CompletedProcess[str]) -> str:
    return "\n".join(
        [
            f"$ {' '.join(command)}",
            f"exit_code: {result.returncode}",
            "",
            "[stdout]",
            result.stdout.rstrip(),
            "",
            "[stderr]",
            result.stderr.rstrip(),
            "",
        ]
    )


def rollout_path(codex_home: Path, year: str, month: str, day: str, time_part: str, session_id: str) -> Path:
    return codex_home / "sessions" / year / month / day / f"rollout-2026-05-14T{time_part}-{session_id}.jsonl"


def session_meta(session_id: str, timestamp: str, *, cwd: str, source: object = "vscode") -> dict[str, Any]:
    return {
        "timestamp": timestamp,
        "type": "session_meta",
        "payload": {
            "id": session_id,
            "timestamp": timestamp,
            "cwd": cwd,
            "originator": "Codex Desktop",
            "source": source,
        },
    }


def message_record(
    *,
    timestamp: str,
    role: str,
    text: str,
    phase: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": "message",
        "role": role,
        "content": [{"type": "output_text" if role == "assistant" else "input_text", "text": text}],
    }
    if phase is not None:
        payload["phase"] = phase
    return {"timestamp": timestamp, "type": "response_item", "payload": payload}


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
            handle.write("\n")


def append_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
            handle.write("\n")


def set_mtime(path: Path, timestamp: int) -> None:
    os.utime(path, (timestamp, timestamp))


def corrupt_stale_offset(state_file: Path, stale_rollout: Path) -> None:
    state = json.loads(state_file.read_text(encoding="utf-8"))
    entry = state["files"][str(stale_rollout)]
    entry["offset"] += 1
    state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_json_artifact(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_text_artifact(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
