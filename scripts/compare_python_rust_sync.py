from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

SESSION_VALID = "019f5f16-0000-7000-8000-000000000101"
SESSION_INVALID = "019f5f16-0000-7000-8000-000000000102"
SESSION_SUBAGENT = "019f5f16-0000-7000-8000-000000000103"
SESSION_STALE = "019f5f16-0000-7000-8000-000000000104"
SESSION_MISSING = "019f5f16-0000-7000-8000-000000000105"
SESSION_LARGE = "019f5f16-0000-7000-8000-000000000106"

VOLATILE = "<volatile>"
PYTHON_ONLY_CORRUPT_STATE = "python-quarantines-corrupt-state-rust-fails-read-only"
OUTPUT_MARKER = ".codex-obsidian-sync-compare"
OUTPUT_MARKER_CONTENT = "managed by compare_python_rust_sync.py\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare Python sync output with Rust dry-run output.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("/tmp/codex-obsidian-sync-python-rust-compare"),
        help="Directory for comparison runtime and artifacts.",
    )
    parser.add_argument(
        "--rust-bin",
        type=Path,
        help="Path to codex-obsidian-sync-rs. Defaults to building rust/ target.",
    )
    parser.add_argument("--keep-runtime", action="store_true", help="Do not remove existing output dir first.")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    output_dir = args.output_dir.resolve()
    artifacts = output_dir / "artifacts"
    runtime = output_dir / "runtime"

    prepare_output_dir(output_dir, keep_runtime=args.keep_runtime)
    private_mkdir(artifacts)
    private_mkdir(runtime)

    rust_bin = args.rust_bin.resolve() if args.rust_bin else build_rust_binary(repo_root)

    synthetic = run_synthetic_comparison(repo_root, rust_bin, runtime / "synthetic", artifacts)
    negatives = run_negative_fixtures(repo_root, rust_bin, runtime / "negative", artifacts)
    large = run_large_log_fixture(repo_root, rust_bin, runtime / "large", artifacts)

    ok = synthetic["ok"] and all(item["ok"] for item in negatives) and large["ok"]
    return 0 if ok else 1


def prepare_output_dir(output_dir: Path, *, keep_runtime: bool) -> None:
    validate_output_location(output_dir)
    if output_dir.exists():
        if not output_dir.is_dir():
            raise RuntimeError(f"Output path is not a directory: {output_dir}")
        if not is_managed_output_dir(output_dir):
            raise RuntimeError(f"Refusing to use unmanaged output dir: {output_dir}")
        if not keep_runtime:
            shutil.rmtree(output_dir)

    private_mkdir(output_dir)
    write_private_text(output_dir / OUTPUT_MARKER, OUTPUT_MARKER_CONTENT)


def validate_output_location(output_dir: Path) -> None:
    temp_roots = {Path(tempfile.gettempdir()).resolve(), Path("/tmp").resolve()}
    if output_dir.parent not in temp_roots or not output_dir.name.startswith("codex-obsidian-sync-"):
        roots = ", ".join(sorted(str(root) for root in temp_roots))
        raise RuntimeError(f"Output dir must be a dedicated codex-obsidian-sync-* path under {roots}: {output_dir}")


def is_managed_output_dir(output_dir: Path) -> bool:
    marker = output_dir / OUTPUT_MARKER
    if not marker.exists() or marker.is_symlink():
        return False
    try:
        return marker.read_text(encoding="utf-8") == OUTPUT_MARKER_CONTENT
    except OSError:
        return False


def private_mkdir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


def write_private_text(path: Path, content: str) -> None:
    private_mkdir(path.parent)
    with path.open("w", encoding="utf-8") as handle:
        handle.write(content)
    path.chmod(0o600)


def build_rust_binary(repo_root: Path) -> Path:
    result = subprocess.run(
        ["cargo", "build", "--manifest-path", str(repo_root / "rust" / "Cargo.toml"), "--quiet"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(format_command_output(["cargo", "build"], result))
    return repo_root / "rust" / "target" / "debug" / "codex-obsidian-sync-rs"


def run_synthetic_comparison(
    repo_root: Path,
    rust_bin: Path,
    runtime: Path,
    artifacts: Path,
) -> dict[str, Any]:
    if runtime.exists():
        shutil.rmtree(runtime)
    python_env, rust_env = create_env_pair(runtime, write_synthetic_fixture)

    input_hashes = {
        "python_before": hash_tree(python_env.codex_home),
        "rust_before": hash_tree(rust_env.codex_home),
    }

    summary_steps: list[dict[str, Any]] = []

    summary_steps.append(
        compare_step(
            "initial",
            repo_root,
            rust_bin,
            python_env,
            rust_env,
            runtime / "rust-output-initial",
        )
    )

    for env in (python_env, rust_env):
        remove_optional_rollouts(env.codex_home)
    append_records_to_pair(
        python_env,
        rust_env,
        "09-00-00",
        SESSION_VALID,
        [message_record("2026-05-14T00:02:00Z", "assistant", "Appended response", "final_answer")],
        mtime=1_778_716_920,
    )
    summary_steps.append(
        compare_step(
            "append",
            repo_root,
            rust_bin,
            python_env,
            rust_env,
            runtime / "rust-output-append",
        )
    )

    append_records_to_pair(
        python_env,
        rust_env,
        "09-30-00",
        SESSION_STALE,
        [message_record("2026-05-14T00:31:00Z", "assistant", "Stale offset recovered", "final_answer")],
        mtime=1_778_718_660,
    )
    for env in (python_env, rust_env):
        corrupt_stale_offset(env.state_file, rollout_path(env.codex_home, "09-30-00", SESSION_STALE))
    summary_steps.append(
        compare_step(
            "stale-offset",
            repo_root,
            rust_bin,
            python_env,
            rust_env,
            runtime / "rust-output-stale",
        )
    )

    input_hashes["python_after"] = hash_tree(python_env.codex_home)
    input_hashes["rust_after"] = hash_tree(rust_env.codex_home)

    notes_diff = diff_note_trees(python_env.vault / "Codex", rust_env.vault / "Codex")
    state_diff = diff_json_values(
        normalize_state(read_json(python_env.state_file), python_env.codex_home),
        normalize_state(read_json(rust_env.state_file), rust_env.codex_home),
    )
    summary_diff = {
        "ok": all(step["ok"] for step in summary_steps),
        "steps": summary_steps,
    }
    input_hash_report = {
        **input_hashes,
        "python_changed_files": changed_files(input_hashes["python_before"], input_hashes["python_after"]),
        "rust_changed_files": changed_files(input_hashes["rust_before"], input_hashes["rust_after"]),
    }

    write_json(artifacts / "summary.diff.json", summary_diff)
    write_text(artifacts / "notes.diff", notes_diff or "No differences\n")
    write_json(artifacts / "state.diff.json", {"ok": not state_diff, "diff": state_diff})
    write_json(artifacts / "input_hashes.json", input_hash_report)

    return {
        "ok": summary_diff["ok"] and not notes_diff and not state_diff,
        "summary": summary_diff,
        "notes_diff": notes_diff,
        "state_diff": state_diff,
    }


def compare_step(
    name: str,
    repo_root: Path,
    rust_bin: Path,
    python_env: "RuntimeEnv",
    rust_env: "RuntimeEnv",
    rust_output: Path,
) -> dict[str, Any]:
    if rust_output.exists():
        shutil.rmtree(rust_output)
    python_result = run_python_sync(repo_root, python_env)
    rust_result = run_rust_sync(rust_bin, rust_env, rust_output)

    python_summary = parse_json_stdout(python_result)
    rust_summary = parse_json_stdout(rust_result)
    shared_python = normalize_summary(python_summary)
    shared_rust = normalize_summary(rust_summary)
    dry_metadata = {
        "dry_run": rust_summary.get("dry_run"),
        "output_dir": normalize_runtime_path(str(rust_summary.get("output_dir", ""))),
        "temp_state_file": normalize_runtime_path(str(rust_summary.get("temp_state_file", ""))),
        "planned_writes": rust_summary.get("planned_writes"),
        "lock_exists": rust_summary.get("lock_exists"),
    }
    diff = diff_json_values(shared_python, shared_rust)
    stdout_behavior = {
        "python_exit": python_result.returncode,
        "rust_exit": rust_result.returncode,
        "python_stdout_json": python_summary is not None,
        "rust_stdout_json": rust_summary is not None,
        "python_stderr": bool(python_result.stderr.strip()),
        "rust_stderr": bool(rust_result.stderr.strip()),
    }
    ok = not diff and python_result.returncode == rust_result.returncode == 0 and all(
        [dry_metadata["dry_run"] is True, dry_metadata["output_dir"], dry_metadata["temp_state_file"]]
    )
    if rust_result.returncode == 0:
        materialize_rust_output(rust_env, rust_output)
    return {
        "name": name,
        "ok": ok,
        "summary_diff": diff,
        "python_summary": shared_python,
        "rust_summary": shared_rust,
        "rust_dry_run_metadata": dry_metadata,
        "stdout_stderr": stdout_behavior,
    }


def run_negative_fixtures(
    repo_root: Path,
    rust_bin: Path,
    runtime: Path,
    artifacts: Path,
) -> list[dict[str, Any]]:
    if runtime.exists():
        shutil.rmtree(runtime)
    private_mkdir(runtime)
    results = [
        negative_invalid_config(repo_root, rust_bin, runtime / "invalid-config"),
        negative_corrupt_state(repo_root, rust_bin, runtime / "corrupt-state"),
        negative_malformed_note_markers(repo_root, rust_bin, runtime / "malformed-note"),
        negative_missing_source(repo_root, rust_bin, runtime / "missing-source"),
        negative_permission_denied_output(repo_root, rust_bin, runtime / "permission-denied-output"),
    ]
    write_json(artifacts / "negative-results.json", {"ok": all(item["ok"] for item in results), "cases": results})
    return results


def negative_invalid_config(repo_root: Path, rust_bin: Path, runtime: Path) -> dict[str, Any]:
    python_env, rust_env = create_env_pair(runtime, write_synthetic_fixture)
    shutil.rmtree(python_env.vault)
    shutil.rmtree(rust_env.vault)
    python_result = run_python_sync(repo_root, python_env)
    rust_result = run_rust_sync(rust_bin, rust_env, runtime / "rust-output")
    return negative_result("invalid-config", python_result, rust_result, python_result.returncode != 0 and rust_result.returncode != 0)


def negative_corrupt_state(repo_root: Path, rust_bin: Path, runtime: Path) -> dict[str, Any]:
    python_env, rust_env = create_env_pair(runtime, write_synthetic_fixture)
    private_mkdir(python_env.state_file.parent)
    private_mkdir(rust_env.state_file.parent)
    write_private_text(python_env.state_file, '{"files": ')
    write_private_text(rust_env.state_file, '{"files": ')
    python_result = run_python_sync(repo_root, python_env)
    rust_result = run_rust_sync(rust_bin, rust_env, runtime / "rust-output")
    ok = python_result.returncode == 0 and rust_result.returncode != 0
    return negative_result(
        "corrupt-state",
        python_result,
        rust_result,
        ok,
        note=PYTHON_ONLY_CORRUPT_STATE,
    )


def negative_malformed_note_markers(repo_root: Path, rust_bin: Path, runtime: Path) -> dict[str, Any]:
    python_env, rust_env = create_env_pair(runtime, write_synthetic_fixture)
    run_python_sync(repo_root, python_env)
    run_rust_sync(rust_bin, rust_env, runtime / "rust-output-initial")
    materialize_rust_output(rust_env, runtime / "rust-output-initial")

    remove_transcript_marker_and_refresh_state(python_env, rollout_path(python_env.codex_home, "09-00-00", SESSION_VALID))
    remove_transcript_marker_and_refresh_state(rust_env, rollout_path(rust_env.codex_home, "09-00-00", SESSION_VALID))
    append_records_to_pair(
        python_env,
        rust_env,
        "09-00-00",
        SESSION_VALID,
        [message_record("2026-05-14T00:03:00Z", "user", "Marker recovery question")],
    )
    python_result = run_python_sync(repo_root, python_env)
    rust_result = run_rust_sync(rust_bin, rust_env, runtime / "rust-output-recovery")
    ok = python_result.returncode == 0 and rust_result.returncode == 0
    return negative_result("malformed-note-markers", python_result, rust_result, ok)


def negative_missing_source(repo_root: Path, rust_bin: Path, runtime: Path) -> dict[str, Any]:
    python_env, rust_env = create_env_pair(runtime, write_missing_source_fixture)
    python_result = run_python_sync(repo_root, python_env)
    rust_result = run_rust_sync(rust_bin, rust_env, runtime / "rust-output")
    python_summary = parse_json_stdout(python_result) or {}
    rust_summary = parse_json_stdout(rust_result) or {}
    ok = (
        python_result.returncode == 0
        and rust_result.returncode == 0
        and python_summary.get("processed") == 0
        and rust_summary.get("processed") == 0
    )
    return negative_result("missing-source", python_result, rust_result, ok)


def negative_permission_denied_output(repo_root: Path, rust_bin: Path, runtime: Path) -> dict[str, Any]:
    rust_env = RuntimeEnv.create(runtime / "rust")
    write_synthetic_fixture(rust_env.codex_home)
    denied_parent = runtime / "denied"
    private_mkdir(denied_parent)
    denied_parent.chmod(0o500)
    denied_output = denied_parent / "output"
    try:
        rust_result = run_rust_sync(rust_bin, rust_env, denied_output)
    finally:
        denied_parent.chmod(0o700)
    ok = rust_result.returncode != 0
    empty_python = subprocess.CompletedProcess(args=[], returncode=0, stdout="{}", stderr="")
    return negative_result("permission-denied-output", empty_python, rust_result, ok)


def negative_result(
    name: str,
    python_result: subprocess.CompletedProcess[str],
    rust_result: subprocess.CompletedProcess[str],
    ok: bool,
    *,
    note: str | None = None,
) -> dict[str, Any]:
    payload = {
        "name": name,
        "ok": ok,
        "python_exit": python_result.returncode,
        "rust_exit": rust_result.returncode,
        "python_stdout_json": parse_json_stdout(python_result) is not None,
        "rust_stdout_json": parse_json_stdout(rust_result) is not None,
        "python_stderr": python_result.stderr.strip().splitlines()[-3:],
        "rust_stderr": rust_result.stderr.strip().splitlines()[-3:],
    }
    if note:
        payload["note"] = note
    return payload


def run_large_log_fixture(
    repo_root: Path,
    rust_bin: Path,
    runtime: Path,
    artifacts: Path,
) -> dict[str, Any]:
    if runtime.exists():
        shutil.rmtree(runtime)
    python_env, rust_env = create_env_pair(
        runtime,
        lambda codex_home: write_large_fixture(codex_home, message_count=300),
    )
    scanned_bytes = tree_size(python_env.codex_home / "sessions")
    python_started = time.perf_counter()
    python_result = run_python_sync(repo_root, python_env)
    python_elapsed = int((time.perf_counter() - python_started) * 1000)

    rust_started = time.perf_counter()
    rust_result = run_rust_sync(rust_bin, rust_env, runtime / "rust-output")
    rust_elapsed = int((time.perf_counter() - rust_started) * 1000)

    metrics = {
        "ok": python_result.returncode == 0 and rust_result.returncode == 0,
        "scanned_bytes": scanned_bytes,
        "message_count": 300,
        "python_runtime_ms": python_elapsed,
        "rust_runtime_ms": rust_elapsed,
        "peak_memory_kb": None,
        "peak_memory_threshold_kb": 512 * 1024,
        "memory_threshold_enforced": False,
    }
    write_json(artifacts / "large-log-metrics.json", metrics)
    return metrics


class RuntimeEnv:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.codex_home = root / ".codex"
        self.vault = root / "vault"
        self.state_file = self.codex_home / "obsidian-sync" / "sync-state.json"
        self.lock_file = self.codex_home / "obsidian-sync" / "sync-state.lock"
        self.config_path = root / "config.toml"

    @classmethod
    def create(cls, root: Path) -> "RuntimeEnv":
        if root.exists():
            shutil.rmtree(root)
        env = cls(root)
        private_mkdir(env.vault)
        private_mkdir(env.codex_home)
        env.write_config()
        return env

    def write_config(self) -> None:
        write_private_text(
            self.config_path,
            "\n".join(
                [
                    f'vault = "{self.vault}"',
                    f'codex_home = "{self.codex_home}"',
                    f'state_file = "{self.state_file}"',
                    f'lock_file = "{self.lock_file}"',
                    "recent_days = 3650",
                    "candidate_file_limit = 1000",
                    "candidate_bytes_limit = 524288000",
                    'log_level = "ERROR"',
                    "",
                ]
            ),
        )


def create_env_pair(runtime: Path, fixture_writer: Callable[[Path], None]) -> tuple[RuntimeEnv, RuntimeEnv]:
    python_env = RuntimeEnv.create(runtime / "python")
    rust_env = RuntimeEnv.create(runtime / "rust")
    fixture_writer(python_env.codex_home)
    fixture_writer(rust_env.codex_home)
    return python_env, rust_env


def append_records_to_pair(
    python_env: RuntimeEnv,
    rust_env: RuntimeEnv,
    time_part: str,
    session_id: str,
    records: list[dict[str, Any]],
    *,
    mtime: int | None = None,
) -> None:
    for env in (python_env, rust_env):
        path = rollout_path(env.codex_home, time_part, session_id)
        append_jsonl(path, records)
        if mtime is not None:
            set_mtime(path, mtime)


def run_python_sync(repo_root: Path, env: RuntimeEnv) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        "-m",
        "codex_obsidian_sync.cli",
        "--config",
        str(env.config_path),
        "sync-once",
        "--vault",
        str(env.vault),
        "--codex-home",
        str(env.codex_home),
        "--state-file",
        str(env.state_file),
        "--lock-file",
        str(env.lock_file),
        "--recent-days",
        "3650",
        "--log-level",
        "ERROR",
    ]
    return subprocess.run(
        command,
        cwd=repo_root,
        env={**os.environ, "PYTHONPATH": str(repo_root / "src")},
        capture_output=True,
        text=True,
        check=False,
    )


def run_rust_sync(rust_bin: Path, env: RuntimeEnv, output_dir: Path) -> subprocess.CompletedProcess[str]:
    command = [
        str(rust_bin),
        "--config",
        str(env.config_path),
        "sync-once",
        "--vault",
        str(env.vault),
        "--codex-home",
        str(env.codex_home),
        "--state-file",
        str(env.state_file),
        "--lock-file",
        str(env.lock_file),
        "--dry-run-output",
        str(output_dir),
        "--recent-days",
        "3650",
        "--log-level",
        "ERROR",
    ]
    return subprocess.run(command, capture_output=True, text=True, check=False)


def write_synthetic_fixture(codex_home: Path) -> None:
    write_jsonl(
        codex_home / "session_index.jsonl",
        [
            {"id": SESSION_VALID, "thread_name": "Baseline Conversation", "updated_at": "2026-05-14T00:00:00Z"},
            {"id": SESSION_INVALID, "thread_name": "Invalid Rollout", "updated_at": "2026-05-14T00:10:00Z"},
            {"id": SESSION_SUBAGENT, "thread_name": "Subagent Thread", "updated_at": "2026-05-14T00:20:00Z"},
            {"id": SESSION_STALE, "thread_name": "Stale Offset", "updated_at": "2026-05-14T00:30:00Z"},
        ],
    )
    set_mtime(codex_home / "session_index.jsonl", 1_778_716_700)

    valid_rollout = rollout_path(codex_home, "09-00-00", SESSION_VALID)
    write_jsonl(
        valid_rollout,
        [
            session_meta(SESSION_VALID, "2026-05-14T00:00:00Z"),
            message_record("2026-05-14T00:01:00Z", "user", "Baseline first question"),
            message_record(
                "2026-05-14T00:01:30Z",
                "assistant",
                "Use sk-abcdefghijklmnopqrstuvwxyz1234567890 for redaction baseline",
                "final_answer",
            ),
        ],
    )
    set_mtime(valid_rollout, 1_778_716_800)

    invalid_rollout = rollout_path(codex_home, "09-10-00", SESSION_INVALID)
    private_mkdir(invalid_rollout.parent)
    write_private_text(
        invalid_rollout,
        json.dumps(session_meta(SESSION_INVALID, "2026-05-14T00:10:00Z"), sort_keys=True)
        + '\n{"timestamp":"2026-05-14T00:11:00Z","type":"response_item","payload":\n',
    )
    set_mtime(invalid_rollout, 1_778_717_400)

    subagent_rollout = rollout_path(codex_home, "09-20-00", SESSION_SUBAGENT)
    write_jsonl(
        subagent_rollout,
        [
            session_meta(
                SESSION_SUBAGENT,
                "2026-05-14T00:20:00Z",
                source={"subagent": {"thread_spawn": {"parent_thread_id": SESSION_VALID}}},
            ),
            message_record("2026-05-14T00:21:00Z", "user", "Subagent body excluded by default"),
        ],
    )
    set_mtime(subagent_rollout, 1_778_718_000)

    stale_rollout = rollout_path(codex_home, "09-30-00", SESSION_STALE)
    write_jsonl(
        stale_rollout,
        [
            session_meta(SESSION_STALE, "2026-05-14T00:30:00Z"),
            message_record("2026-05-14T00:30:30Z", "user", "Stale offset first question"),
        ],
    )
    set_mtime(stale_rollout, 1_778_718_600)


def write_missing_source_fixture(codex_home: Path) -> None:
    write_jsonl(
        codex_home / "session_index.jsonl",
        [{"id": SESSION_MISSING, "thread_name": "Missing Source", "updated_at": "2026-05-14T00:00:00Z"}],
    )


def write_large_fixture(codex_home: Path, *, message_count: int) -> None:
    write_jsonl(
        codex_home / "session_index.jsonl",
        [{"id": SESSION_LARGE, "thread_name": "Large Fixture", "updated_at": "2026-05-14T00:00:00Z"}],
    )
    records = [session_meta(SESSION_LARGE, "2026-05-14T00:00:00Z")]
    for index in range(message_count):
        records.append(
            message_record(
                f"2026-05-14T00:{index % 60:02}:00Z",
                "user" if index % 2 == 0 else "assistant",
                f"Large fixture message {index:04d} " + ("x" * 200),
                "final_answer" if index % 2 == 1 else None,
            )
        )
    write_jsonl(rollout_path(codex_home, "10-00-00", SESSION_LARGE), records)


def session_meta(session_id: str, timestamp: str, source: object = "vscode") -> dict[str, Any]:
    return {
        "timestamp": timestamp,
        "type": "session_meta",
        "payload": {
            "id": session_id,
            "timestamp": timestamp,
            "cwd": "/tmp/baseline-project",
            "originator": "Codex Desktop",
            "source": source,
        },
    }


def message_record(timestamp: str, role: str, text: str, phase: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": "message",
        "role": role,
        "content": [{"type": "output_text" if role == "assistant" else "input_text", "text": text}],
    }
    if phase is not None:
        payload["phase"] = phase
    return {"timestamp": timestamp, "type": "response_item", "payload": payload}


def rollout_path(codex_home: Path, time_part: str, session_id: str) -> Path:
    return codex_home / "sessions" / "2026" / "05" / "14" / f"rollout-2026-05-14T{time_part}-{session_id}.jsonl"


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    content = "".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records)
    write_private_text(path, content)


def append_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
            handle.write("\n")


def set_mtime(path: Path, timestamp: int) -> None:
    os.utime(path, (timestamp, timestamp))


def remove_optional_rollouts(codex_home: Path) -> None:
    for time_part, session_id in [("09-10-00", SESSION_INVALID), ("09-20-00", SESSION_SUBAGENT)]:
        path = rollout_path(codex_home, time_part, session_id)
        if path.exists():
            path.unlink()


def corrupt_stale_offset(state_file: Path, stale_rollout: Path) -> None:
    state = read_json(state_file)
    state["files"][str(stale_rollout)]["offset"] += 1
    write_json(state_file, state)


def remove_transcript_marker_and_refresh_state(env: RuntimeEnv, rollout: Path) -> None:
    state = read_json(env.state_file)
    entry = state["files"][str(rollout)]
    note_path = env.vault / entry["conversation_note_path"]
    text = note_path.read_text(encoding="utf-8")
    write_private_text(note_path, text.replace("<!-- BEGIN CODEX TRANSCRIPT -->", "<!-- BROKEN TRANSCRIPT -->"))
    entry["conversation_note_fingerprint"] = file_fingerprint(note_path)
    write_json(env.state_file, state)


def materialize_rust_output(env: RuntimeEnv, output: Path) -> None:
    codex_output = output / "Codex"
    codex_target = env.vault / "Codex"
    if codex_output.exists():
        for path in codex_output.rglob("*"):
            if not path.is_file():
                continue
            target = codex_target / path.relative_to(codex_output)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    env.state_file.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(output / "sync-state.json", env.state_file)


def parse_json_stdout(result: subprocess.CompletedProcess[str]) -> Any | None:
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def normalize_summary(summary: Any) -> dict[str, Any]:
    if not isinstance(summary, dict):
        return {}
    keys = [
        "processed",
        "appended",
        "rewritten",
        "skipped_subagents",
        "skipped_invalid",
        "unchanged",
        "total_rollouts",
        "paused",
        "fast_path",
    ]
    return {key: summary.get(key) for key in keys}


def normalize_state(state: dict[str, Any], codex_home: Path) -> dict[str, Any]:
    normalized = json.loads(json.dumps(state))
    normalized["last_poll_at"] = VOLATILE
    if "session_index" in normalized:
        normalize_mtime_fields(normalized["session_index"])
    files = normalized.get("files", {})
    normalized_files: dict[str, Any] = {}
    for raw_path, entry in sorted(files.items()):
        relative = Path(raw_path).relative_to(codex_home).as_posix()
        entry = dict(entry)
        for key in ("mtime_ns", "last_note_write_at"):
            if key in entry:
                entry[key] = VOLATILE
        if isinstance(entry.get("conversation_note_fingerprint"), dict):
            normalize_mtime_fields(entry["conversation_note_fingerprint"])
        normalized_files[f"<codex_home>/{relative}"] = entry
    normalized["files"] = normalized_files
    return normalized


def normalize_mtime_fields(value: dict[str, Any]) -> None:
    if "mtime_ns" in value:
        value["mtime_ns"] = VOLATILE


def normalize_runtime_path(value: str) -> str:
    if not value:
        return value
    return "<runtime>/" + Path(value).name


def diff_json_values(expected: Any, actual: Any) -> dict[str, Any]:
    if expected == actual:
        return {}
    return {"expected": expected, "actual": actual}


def diff_note_trees(python_codex: Path, rust_codex: Path) -> str:
    python_files = read_text_tree(python_codex)
    rust_files = read_text_tree(rust_codex)
    if python_files == rust_files:
        return ""
    expected = json.dumps(python_files, ensure_ascii=False, indent=2, sort_keys=True).splitlines(keepends=True)
    actual = json.dumps(rust_files, ensure_ascii=False, indent=2, sort_keys=True).splitlines(keepends=True)
    return "".join(difflib.unified_diff(expected, actual, fromfile="python-notes", tofile="rust-notes"))


def read_text_tree(root: Path) -> dict[str, str]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def hash_tree(root: Path) -> dict[str, str]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): sha256(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def changed_files(before: dict[str, str], after: dict[str, str]) -> list[str]:
    keys = sorted(set(before) | set(after))
    return [key for key in keys if before.get(key) != after.get(key)]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def file_fingerprint(path: Path) -> dict[str, int]:
    stat = path.stat()
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def tree_size(root: Path) -> int:
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    write_private_text(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def write_text(path: Path, content: str) -> None:
    write_private_text(path, content)


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


if __name__ == "__main__":
    raise SystemExit(main())
