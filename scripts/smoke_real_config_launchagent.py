from __future__ import annotations

import argparse
import json
import os
import plistlib
import re
import shutil
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


LABEL_PREFIX = "com.codex.obsidian-sync.real-dry-run-smoke."
MARKER = ".codex-obsidian-sync-real-launchagent-smoke"
MARKER_CONTENT = "managed by smoke_real_config_launchagent.py\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Smoke test a real-config dry-run through an isolated temporary LaunchAgent label."
    )
    parser.add_argument("--binary", required=True, type=Path, help="codex-obsidian-sync binary to run.")
    parser.add_argument("--config", required=True, type=Path, help="Real config.toml to smoke with dry-run only.")
    parser.add_argument("--work-dir", type=Path, help="Dedicated /tmp/codex-obsidian-sync-* smoke directory.")
    parser.add_argument("--keep-work-dir", action="store_true", help="Reuse an existing managed work dir.")
    parser.add_argument("--launchctl", default="launchctl", help="launchctl executable. Defaults to PATH lookup.")
    parser.add_argument(
        "--codesign",
        default="codesign",
        help="codesign executable used to record macOS binary identity. Defaults to PATH lookup.",
    )
    parser.add_argument(
        "--label",
        help=f"Temporary LaunchAgent label. Defaults to {LABEL_PREFIX}<pid>.",
    )
    parser.add_argument("--timeout-seconds", type=float, default=180.0, help="Seconds to wait for sync output.")
    parser.add_argument(
        "--sample-on-timeout",
        action="store_true",
        help="Run sample(1) briefly against the timed-out process and save a stack file under the work dir.",
    )
    parser.add_argument("--sample", default="sample", help="sample executable. Defaults to PATH lookup.")
    parser.add_argument("--sample-seconds", type=float, default=3.0, help="Seconds to sample on timeout.")
    parser.add_argument(
        "--trace-read-paths",
        action="store_true",
        help="Ask sync-once to write a dry-run note read trace JSONL file under the work dir.",
    )
    parser.add_argument(
        "--cleanup-dry-run-output",
        action="store_true",
        help="Remove the dry-run output directory after collecting evidence.",
    )
    parser.add_argument("--output", type=Path, help="Write the smoke report to this path.")
    args = parser.parse_args()

    work_dir = prepare_work_dir(args.work_dir, keep=args.keep_work_dir)
    result = run_smoke(
        binary=args.binary.expanduser().resolve(),
        config=args.config.expanduser().resolve(),
        work_dir=work_dir,
        launchctl=args.launchctl,
        codesign=args.codesign,
        label=args.label or f"{LABEL_PREFIX}{os.getpid()}",
        timeout_seconds=args.timeout_seconds,
        sample_on_timeout=args.sample_on_timeout,
        sample=args.sample,
        sample_seconds=args.sample_seconds,
        trace_read_paths=args.trace_read_paths,
        cleanup_dry_run_output=args.cleanup_dry_run_output,
    )
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
        raise RuntimeError(f"Output path is a symlink: {output}")
    return output


def prepare_work_dir(path: Path | None, *, keep: bool) -> Path:
    created = path is None
    if path is None:
        path = Path(tempfile.mkdtemp(prefix="codex-obsidian-sync-real-launchagent-smoke-"))
    path = path.expanduser()
    if path.is_symlink():
        raise RuntimeError(f"Work dir is a symlink: {path}")
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
    config: Path,
    work_dir: Path,
    launchctl: str,
    codesign: str,
    label: str,
    timeout_seconds: float,
    sample_on_timeout: bool,
    sample: str,
    sample_seconds: float,
    trace_read_paths: bool,
    cleanup_dry_run_output: bool,
) -> dict[str, Any]:
    no_go_reasons: list[str] = []
    details: dict[str, Any] = {
        "binary": str(binary),
        "config": str(config),
        "work_dir": str(work_dir),
        "label": label,
        "timeout_seconds": timeout_seconds,
        "sample_on_timeout": sample_on_timeout,
        "sample_seconds": sample_seconds,
        "trace_read_paths": trace_read_paths,
        "codesign": inspect_codesign(binary, codesign=codesign),
    }
    if not binary.is_file() or not os.access(binary, os.X_OK):
        no_go_reasons.append("binary is missing or not executable")
    if config.is_symlink():
        no_go_reasons.append("config path is a symlink")
    elif not config.is_file():
        no_go_reasons.append("config path is missing")
    if not label.startswith(LABEL_PREFIX):
        no_go_reasons.append(f"label must start with {LABEL_PREFIX}")
    if timeout_seconds <= 0:
        no_go_reasons.append("timeout must be positive")
    if sample_seconds <= 0:
        no_go_reasons.append("sample seconds must be positive")

    launchctl_path = resolve_executable(launchctl)
    if launchctl_path is None:
        no_go_reasons.append(f"launchctl executable was not found: {launchctl}")
    sample_path = resolve_executable(sample) if sample_on_timeout else None
    if sample_on_timeout and sample_path is None:
        no_go_reasons.append(f"sample executable was not found: {sample}")
    if no_go_reasons:
        details["command_count"] = 0
        return smoke_result(False, details, no_go_reasons)

    assert launchctl_path is not None
    dry_run_output = work_dir / "dry-run-output"
    read_trace_path = work_dir / "read-trace.jsonl" if trace_read_paths else None
    stdout_path = work_dir / "stdout.json"
    stderr_path = work_dir / "stderr.log"
    plist_path = work_dir / "agent.plist"
    commands_path = work_dir / "real-launchagent-smoke-commands.json"
    target = f"gui/{os.getuid()}/{label}"
    commands: list[dict[str, Any]] = []
    booted = False
    summary: dict[str, Any] | None = None
    stderr_text = ""
    stdout_text = ""
    final_launchctl = ""
    loaded_after_bootout = False
    timed_out = False

    try:
        write_smoke_plist(
            plist_path=plist_path,
            binary=binary,
            config=config,
            dry_run_output=dry_run_output,
            read_trace_path=read_trace_path,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            work_dir=work_dir,
            label=label,
        )
        pre_print = run_launchctl([launchctl_path, "print", target], commands)
        if pre_print.returncode == 0:
            no_go_reasons.append("temporary LaunchAgent label is already loaded")
            return smoke_result(False, details_with_paths(details, dry_run_output, stdout_path, stderr_path, target), no_go_reasons)

        bootstrap = run_launchctl([launchctl_path, "bootstrap", f"gui/{os.getuid()}", plist_path], commands)
        if bootstrap.returncode != 0:
            no_go_reasons.append("launchctl bootstrap failed")
            details["bootstrap_stderr"] = first_nonempty_line(bootstrap.stderr)
            return smoke_result(False, details_with_paths(details, dry_run_output, stdout_path, stderr_path, target), no_go_reasons)
        booted = True

        kickstart = run_launchctl([launchctl_path, "kickstart", "-k", target], commands)
        if kickstart.returncode != 0:
            no_go_reasons.append("launchctl kickstart failed")
            details["kickstart_stderr"] = first_nonempty_line(kickstart.stderr)

        deadline = time.monotonic() + timeout_seconds
        poll_interval = min(2.0, max(0.1, timeout_seconds / 20.0))
        while not no_go_reasons and time.monotonic() < deadline:
            stdout_text = read_text_if_exists(stdout_path)
            stderr_text = read_text_if_exists(stderr_path)
            if stdout_text.strip():
                summary = parse_summary(stdout_text, no_go_reasons)
                break
            if stderr_text.strip():
                no_go_reasons.append("sync-once dry-run wrote stderr")
                break

            state = run_launchctl([launchctl_path, "print", target], commands)
            final_launchctl = state.stdout if state.returncode == 0 else state.stderr
            details["pid"] = launchctl_pid(final_launchctl)
            if state.returncode != 0:
                no_go_reasons.append("temporary LaunchAgent disappeared before producing output")
                break
            if launchctl_finished(final_launchctl):
                if launchctl_exit_code(final_launchctl) != "0":
                    no_go_reasons.append("sync-once dry-run exited nonzero")
                else:
                    no_go_reasons.append("sync-once dry-run exited without stdout JSON")
                break
            time.sleep(poll_interval)

        if not no_go_reasons and summary is None:
            timed_out = True
            no_go_reasons.append("sync-once dry-run timed out")

        stdout_text = read_text_if_exists(stdout_path)
        stderr_text = read_text_if_exists(stderr_path)
        if not final_launchctl:
            final = run_launchctl([launchctl_path, "print", target], commands)
            final_launchctl = final.stdout if final.returncode == 0 else final.stderr

        if summary is not None:
            validate_summary(summary, dry_run_output, no_go_reasons, details)
        elif timed_out and sample_on_timeout and sample_path is not None:
            record_timeout_sample(
                sample_path=sample_path,
                sample_seconds=sample_seconds,
                work_dir=work_dir,
                launchctl_output=final_launchctl,
                details=details,
                commands=commands,
            )
    finally:
        if booted:
            run_launchctl([launchctl_path, "bootout", target], commands)
            loaded_after_bootout = wait_until_unloaded(launchctl_path, target, commands)
        cleanup_error = None
        if cleanup_dry_run_output and dry_run_output.exists():
            cleanup_error = cleanup_output_dir(dry_run_output, work_dir)
            if cleanup_error:
                no_go_reasons.append(cleanup_error)
        read_trace_head = read_trace_excerpt(read_trace_path, from_tail=False) if read_trace_path else []
        read_trace_tail = read_trace_excerpt(read_trace_path, from_tail=True) if read_trace_path else []
        timeout_diagnosis_text = timeout_diagnosis(details, read_trace_tail) if timed_out else None
        details.update(
            {
                "target": target,
                "plist": str(plist_path),
                "stdout": str(stdout_path),
                "stderr": str(stderr_path),
                "stdout_parseable_json": summary is not None,
                "stderr_tail": stderr_text[-1000:],
                "stdout_tail_if_not_json": "" if summary is not None else stdout_text[-1000:],
                "launchctl_state_tail": final_launchctl[-1200:],
                "dry_run_output": str(dry_run_output),
                "dry_run_output_exists_after_cleanup": dry_run_output.exists(),
                "read_trace": str(read_trace_path) if read_trace_path else None,
                "read_trace_exists": read_trace_path.is_file() if read_trace_path else None,
                "read_trace_head": read_trace_head,
                "read_trace_tail": read_trace_tail,
                "timeout_diagnosis": timeout_diagnosis_text,
                "cleanup_error": cleanup_error,
                "command_count": len(commands),
                "loaded_after_bootout": loaded_after_bootout,
            }
        )
        write_json(commands_path, {"commands": commands})
        details["commands_file"] = str(commands_path)

    if loaded_after_bootout:
        no_go_reasons.append("temporary LaunchAgent remained loaded after bootout")

    return smoke_result(not no_go_reasons, details, no_go_reasons)


def details_with_paths(
    details: dict[str, Any],
    dry_run_output: Path,
    stdout_path: Path,
    stderr_path: Path,
    target: str,
) -> dict[str, Any]:
    details.update(
        {
            "target": target,
            "dry_run_output": str(dry_run_output),
            "stdout": str(stdout_path),
            "stderr": str(stderr_path),
        }
    )
    return details


def write_smoke_plist(
    *,
    plist_path: Path,
    binary: Path,
    config: Path,
    dry_run_output: Path,
    read_trace_path: Path | None,
    stdout_path: Path,
    stderr_path: Path,
    work_dir: Path,
    label: str,
) -> None:
    plist = {
        "Label": label,
        "ProgramArguments": [
            str(binary),
            "--config",
            str(config),
            "sync-once",
            "--dry-run-output",
            str(dry_run_output),
        ],
        "RunAtLoad": False,
        "StandardOutPath": str(stdout_path),
        "StandardErrorPath": str(stderr_path),
        "WorkingDirectory": str(work_dir),
    }
    if read_trace_path is not None:
        plist["ProgramArguments"].extend(["--trace-read-paths", str(read_trace_path)])
    with plist_path.open("wb") as handle:
        plistlib.dump(plist, handle)


def inspect_codesign(binary: Path, *, codesign: str) -> dict[str, Any]:
    tool = resolve_executable(codesign)
    if tool is None:
        return {"checked": False, "reason": f"codesign executable was not found: {codesign}"}
    if not binary.is_file():
        return {"checked": False, "reason": "binary is missing"}

    result = subprocess.run(
        [str(tool), "-dv", "--verbose=4", str(binary)],
        capture_output=True,
        text=True,
        check=False,
    )
    details = parse_codesign_output(result.stdout + result.stderr)
    details.update(
        {
            "checked": result.returncode == 0,
            "returncode": result.returncode,
            "tool": str(tool),
        }
    )
    if result.returncode != 0:
        details["reason"] = "codesign inspection failed"
    return details


def parse_codesign_output(output: str) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "identifier": None,
        "signature": None,
        "team_identifier": None,
        "cdhash": None,
        "authorities": [],
    }
    authorities: list[str] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if line.startswith("Identifier="):
            fields["identifier"] = line.removeprefix("Identifier=")
        elif line.startswith("Signature="):
            fields["signature"] = line.removeprefix("Signature=")
        elif line.startswith("TeamIdentifier="):
            fields["team_identifier"] = line.removeprefix("TeamIdentifier=")
        elif line.startswith("CDHash="):
            fields["cdhash"] = line.removeprefix("CDHash=")
        elif line.startswith("Authority="):
            authorities.append(line.removeprefix("Authority="))
    fields["authorities"] = authorities
    return fields


def parse_summary(stdout: str, reasons: list[str]) -> dict[str, Any] | None:
    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError:
        reasons.append("sync-once dry-run stdout is not valid JSON")
        return None
    if not isinstance(parsed, dict):
        reasons.append("sync-once dry-run stdout is not an object")
        return None
    return parsed


def validate_summary(
    summary: dict[str, Any],
    dry_run_output: Path,
    reasons: list[str],
    details: dict[str, Any],
) -> None:
    details["dry_run"] = summary.get("dry_run")
    details["processed"] = summary.get("processed")
    details["planned_writes"] = summary.get("planned_writes")
    details["temp_state_file"] = summary.get("temp_state_file")
    if summary.get("dry_run") is not True:
        reasons.append("sync-once did not report dry_run=true")
    note_count = len(list(dry_run_output.rglob("*.md"))) if dry_run_output.exists() else 0
    details["note_files"] = note_count
    if note_count <= 0:
        reasons.append("sync-once dry-run produced no note files")
    temp_state = dry_run_output / "sync-state.json"
    details["temp_state_exists"] = temp_state.is_file()
    if not temp_state.is_file():
        reasons.append("sync-once dry-run did not produce temp sync-state.json")


def launchctl_finished(output: str) -> bool:
    return launchctl_exit_code(output) not in {None, "(never exited)"}


def launchctl_exit_code(output: str) -> str | None:
    match = re.search(r"last exit code = ([^\n]+)", output)
    return match.group(1).strip() if match else None


def launchctl_pid(output: str) -> int | None:
    match = re.search(r"\bpid = (\d+)", output)
    if not match:
        return None
    return int(match.group(1))


def record_timeout_sample(
    *,
    sample_path: Path,
    sample_seconds: float,
    work_dir: Path,
    launchctl_output: str,
    details: dict[str, Any],
    commands: list[dict[str, Any]],
) -> None:
    pid = launchctl_pid(launchctl_output)
    details["pid"] = pid
    if pid is None:
        details["sample_skipped"] = "pid unavailable"
        return

    output_path = work_dir / "timeout-sample.txt"
    result = subprocess.run(
        [str(sample_path), str(pid), sample_seconds_arg(sample_seconds), "-file", str(output_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    commands.append(command_record(result))
    details["sample_returncode"] = result.returncode
    details["sample_output"] = str(output_path)
    sample_text = read_text_if_exists(output_path)
    if sample_text:
        details["sample_excerpt"] = sample_excerpt(sample_text)
    elif result.stdout or result.stderr:
        details["sample_excerpt"] = sample_excerpt(f"{result.stdout}\n{result.stderr}")


def sample_excerpt(text: str, *, limit: int = 40) -> list[str]:
    interesting_tokens = (
        "codex_obsidian_sync",
        "DryRunOutput",
        "sync_once",
        "run_sync",
        "read_relative",
        "read_to_string",
        "open",
        "__open",
    )
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if any(token in stripped for token in interesting_tokens):
            lines.append(stripped)
        if len(lines) >= limit:
            break
    if lines:
        return lines
    return [line.strip() for line in text.splitlines()[:limit] if line.strip()]


def sample_seconds_arg(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return str(value)


def cleanup_output_dir(path: Path, work_dir: Path) -> str | None:
    try:
        if path.is_symlink():
            return "dry-run output cleanup refused symlink"
        resolved = path.resolve()
        if not (resolved == work_dir or resolved.is_relative_to(work_dir)):
            return "dry-run output cleanup refused path outside work dir"
        shutil.rmtree(path)
    except OSError as error:
        return f"dry-run output cleanup failed: {error}"
    return None


def wait_until_unloaded(
    launchctl_path: Path,
    target: str,
    commands: list[dict[str, Any]],
    *,
    timeout_seconds: float = 5.0,
) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while True:
        result = run_launchctl([launchctl_path, "print", target], commands)
        loaded = result.returncode == 0
        if not loaded:
            return False
        if time.monotonic() >= deadline:
            return True
        time.sleep(0.2)


def smoke_result(ok: bool, details: dict[str, Any], reasons: list[str]) -> dict[str, Any]:
    return {
        "ok": ok,
        "generated_at": datetime.now(UTC).isoformat(),
        "details": details,
        "no_go_reasons": reasons,
    }


def run_launchctl(command: list[Path | str], commands: list[dict[str, Any]]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run([str(part) for part in command], capture_output=True, text=True, check=False)
    commands.append(command_record(result))
    return result


def command_record(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    return {
        "command": result.args,
        "returncode": result.returncode,
        "stdout_tail": result.stdout[-500:],
        "stderr_tail": result.stderr[-500:],
    }


def resolve_executable(name: str) -> Path | None:
    found = shutil.which(name)
    return Path(found).resolve() if found else None


def read_text_if_exists(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def read_trace_excerpt(path: Path, *, from_tail: bool, limit: int = 20) -> list[str]:
    if not path.exists():
        return []
    lines = [
        line
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
        if line.strip()
    ]
    if from_tail:
        return lines[-limit:]
    return lines[:limit]


def timeout_diagnosis(details: dict[str, Any], read_trace_tail: list[str]) -> str | None:
    sample_excerpt = "\n".join(str(line) for line in details.get("sample_excerpt") or [])
    blocked_on_file_open = "read_to_string" in sample_excerpt and (
        "\nopen" in sample_excerpt or "__open" in sample_excerpt
    )
    if read_trace_tail and blocked_on_file_open:
        return (
            "sync-once timed out while opening an existing vault note; on macOS, grant Full Disk "
            "Access to the binary path recorded in details.binary and rerun this smoke."
        )
    if blocked_on_file_open and "DryRunOutput::read_relative" in sample_excerpt:
        return (
            "sync-once timed out while opening a dry-run overlay file; on macOS real-config "
            "LaunchAgent smoke, grant Full Disk Access to the binary path recorded in details.binary. "
            "When supported, rerun with --trace-read-paths to identify the exact note path."
        )
    return None


def first_nonempty_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


def private_mkdir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
