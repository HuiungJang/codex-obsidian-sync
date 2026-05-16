from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Any


MARKER = ".codex-obsidian-sync-release-smoke"
MARKER_CONTENT = "managed by smoke_release_artifact.py\n"
SESSION_ID = "019f5f16-0000-7000-8000-000000000201"
TRANSCRIPT_TEXT = "Release smoke transcript text should stay out of status summary state"


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test a codex-obsidian-sync release tarball.")
    parser.add_argument("--tarball", required=True, type=Path, help="Release .tar.gz artifact.")
    parser.add_argument(
        "--checksum",
        type=Path,
        help="Checksum file. Defaults to <tarball>.sha256.",
    )
    parser.add_argument(
        "--expected-version",
        help="Expected semantic version without the binary name, for example 0.1.0.",
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        help="Dedicated /tmp/codex-obsidian-sync-* smoke directory. Created if omitted.",
    )
    parser.add_argument("--keep-work-dir", action="store_true", help="Reuse an existing managed work dir.")
    args = parser.parse_args()

    tarball = args.tarball.resolve()
    checksum = (args.checksum or Path(f"{tarball}.sha256")).resolve()
    work_dir = prepare_work_dir(args.work_dir, keep=args.keep_work_dir)

    result = run_smoke(
        tarball=tarball,
        checksum=checksum,
        work_dir=work_dir,
        expected_version=args.expected_version,
    )
    write_json(work_dir / "smoke-summary.json", result)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def prepare_work_dir(path: Path | None, *, keep: bool) -> Path:
    created = path is None
    if path is None:
        path = Path(tempfile.mkdtemp(prefix="codex-obsidian-sync-release-smoke-"))
    path = path.resolve()
    validate_work_dir(path)

    if path.exists():
        if not path.is_dir():
            raise RuntimeError(f"Work path is not a directory: {path}")
        if not created and not keep:
            if not is_managed_work_dir(path):
                raise RuntimeError(f"Refusing to remove unmanaged work dir: {path}")
            shutil.rmtree(path)

    private_mkdir(path)
    (path / MARKER).write_text(MARKER_CONTENT, encoding="utf-8")
    (path / MARKER).chmod(0o600)
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
    tarball: Path,
    checksum: Path,
    work_dir: Path,
    expected_version: str | None,
) -> dict[str, Any]:
    if not tarball.is_file():
        raise RuntimeError(f"Missing tarball: {tarball}")
    if not checksum.is_file():
        raise RuntimeError(f"Missing checksum: {checksum}")

    verify_checksum(tarball, checksum)
    extract_dir = work_dir / "extract"
    safe_extract_tarball(tarball, extract_dir)
    extracted_binary = find_extracted_binary(extract_dir)
    installed_binary = install_binary(extracted_binary, work_dir / "bin")

    runtime = work_dir / "runtime"
    dry_run_output = work_dir / "dry-run-output"
    private_mkdir(runtime)
    prepare_fixture(runtime)

    config_path = runtime / "config.toml"
    vault = runtime / "vault"
    vault_before = hash_tree(vault)

    version_output = run_jsonless([str(installed_binary), "--version"], work_dir / "version")
    version_line = version_output.stdout.strip()
    if expected_version and version_line != f"codex-obsidian-sync {expected_version}":
        raise RuntimeError(f"Unexpected version: {version_line!r}")

    status = run_json(
        [str(installed_binary), "--config", str(config_path), "status", "--json"],
        work_dir / "status",
    )
    inspect = run_json(
        [
            str(installed_binary),
            "inspect-recent",
            "--codex-home",
            str(runtime / ".codex"),
            "--limit",
            "3",
        ],
        work_dir / "inspect-recent",
    )
    summary = run_json(
        [
            str(installed_binary),
            "--config",
            str(config_path),
            "sync-once",
            "--dry-run-output",
            str(dry_run_output),
        ],
        work_dir / "sync-once",
    )

    vault_after = hash_tree(vault)
    if vault_before != vault_after:
        raise RuntimeError("Dry-run mutated the configured vault")
    if not summary.get("dry_run"):
        raise RuntimeError("sync-once did not report dry_run=true")
    if not list(dry_run_output.rglob("*.md")):
        raise RuntimeError("Dry-run did not produce any note files")
    temp_state = dry_run_output / "sync-state.json"
    if not temp_state.is_file():
        raise RuntimeError("Dry-run did not produce a temp sync-state.json")
    if not isinstance(inspect, list) or not inspect:
        raise RuntimeError("inspect-recent did not return any sessions")

    leak_paths = [
        work_dir / "status.stdout",
        work_dir / "sync-once.stdout",
        temp_state,
    ]
    leaked = [str(path) for path in leak_paths if TRANSCRIPT_TEXT in path.read_text(encoding="utf-8")]
    if leaked:
        raise RuntimeError(f"Raw transcript text leaked into smoke artifacts: {leaked}")

    uninstall_binary(installed_binary)
    installed_after = installed_binary.exists()
    if installed_after:
        raise RuntimeError(f"Installed binary remained after uninstall: {installed_binary}")

    return {
        "ok": True,
        "tarball": str(tarball),
        "checksum": str(checksum),
        "work_dir": str(work_dir),
        "installed_binary": str(installed_binary),
        "uninstalled": True,
        "installed_after": installed_after,
        "version": version_line,
        "status_configured": status.get("configured"),
        "status_json_parsed": True,
        "inspect_count": len(inspect),
        "dry_run": summary.get("dry_run"),
        "processed": summary.get("processed"),
        "vault_unchanged": vault_before == vault_after,
        "note_files": len(list(dry_run_output.rglob("*.md"))),
        "temp_state_file": str(temp_state),
    }


def verify_checksum(tarball: Path, checksum: Path) -> None:
    expected = None
    for line in checksum.read_text(encoding="utf-8").splitlines():
        parts = line.strip().split()
        if not parts:
            continue
        if len(parts) == 1 or Path(parts[-1]).name == tarball.name:
            expected = parts[0]
            break
    if expected is None:
        raise RuntimeError(f"Checksum file does not reference {tarball.name}: {checksum}")

    digest = hashlib.sha256(tarball.read_bytes()).hexdigest()
    if digest.lower() != expected.lower():
        raise RuntimeError(f"Checksum mismatch for {tarball.name}")


def safe_extract_tarball(tarball: Path, extract_dir: Path) -> None:
    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    private_mkdir(extract_dir)
    with tarfile.open(tarball, "r:gz") as archive:
        members = archive.getmembers()
        for member in members:
            member_path = Path(member.name)
            if member_path.is_absolute() or ".." in member_path.parts:
                raise RuntimeError(f"Unsafe tar member path: {member.name}")
            if not (member.isfile() or member.isdir()):
                raise RuntimeError(f"Unsupported tar member type: {member.name}")
        archive.extractall(extract_dir, members=members)


def find_extracted_binary(extract_dir: Path) -> Path:
    candidates = [
        path
        for path in extract_dir.rglob("codex-obsidian-sync")
        if path.is_file() and not path.is_symlink()
    ]
    if len(candidates) != 1:
        raise RuntimeError(f"Expected exactly one codex-obsidian-sync binary, found {len(candidates)}")
    return candidates[0]


def install_binary(source: Path, bin_dir: Path) -> Path:
    private_mkdir(bin_dir)
    target = bin_dir / "codex-obsidian-sync"
    shutil.copy2(source, target)
    target.chmod(0o755)
    return target


def uninstall_binary(target: Path) -> None:
    if target.exists() or target.is_symlink():
        target.unlink()


def prepare_fixture(runtime: Path) -> None:
    codex_home = runtime / ".codex"
    vault = runtime / "vault"
    state_dir = runtime / "state"
    sessions = codex_home / "sessions" / "2026" / "05" / "16"
    private_mkdir(vault)
    private_mkdir(state_dir)
    private_mkdir(sessions)
    write_jsonl(
        codex_home / "session_index.jsonl",
        [
            {
                "id": SESSION_ID,
                "thread_name": "Release Smoke",
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
                    "content": [{"type": "input_text", "text": "Release smoke question"}],
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


def run_json(command: list[str], output_base: Path) -> Any:
    result = run_jsonless(command, output_base)
    return json.loads(result.stdout)


def run_jsonless(command: list[str], output_base: Path) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    (output_base.with_suffix(".stdout")).write_text(result.stdout, encoding="utf-8")
    (output_base.with_suffix(".stderr")).write_text(result.stderr, encoding="utf-8")
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
