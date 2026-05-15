from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .config import (
    DEFAULT_CONFIG_PATH,
    load_toml_config,
    resolve_service_paths,
    resolve_sync_config,
    save_toml_config,
)
from .discovery import build_session_envelope, load_session_index, recent_rollout_files
from .event_logger import EventLogger
from .intervals import parse_interval
from .launchd import (
    bootstrap_launch_agent,
    bootout_launch_agent,
    ensure_launch_agent_dirs,
    query_launchd_status,
    render_launch_agent_plist,
    write_launch_agent_plist,
)
from .locking import process_lock
from .redaction import redact_text
from .service_runner import run_service
from .service_state import load_service_state
from .status_snapshot import build_status_snapshot, render_status_snapshot, status_snapshot_to_dict
from .sync import sync_once, watch
from .writer import validate_vault_root


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="codex-obsidian-sync")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    subparsers = parser.add_subparsers(dest="command", required=True)

    setup_parser = subparsers.add_parser("setup")
    setup_parser.add_argument("--vault", type=Path)
    setup_parser.add_argument("--cooldown", "--interval", dest="cooldown", type=str)

    subparsers.add_parser("start")
    subparsers.add_parser("stop")
    status_parser = subparsers.add_parser("status")
    status_parser.add_argument("--json", action="store_true", dest="as_json")

    service_run_parser = subparsers.add_parser("service-run", help=argparse.SUPPRESS)
    service_run_parser.add_argument("--log-level", type=str)

    inspect_rollout = subparsers.add_parser("inspect-rollout")
    inspect_rollout.add_argument("rollout_path", type=Path)
    inspect_rollout.add_argument(
        "--session-index",
        type=Path,
        default=Path.home() / ".codex" / "session_index.jsonl",
    )

    inspect_recent = subparsers.add_parser("inspect-recent")
    inspect_recent.add_argument(
        "--codex-home",
        type=Path,
        default=Path.home() / ".codex",
    )
    inspect_recent.add_argument("--limit", type=int, default=5)
    inspect_recent.add_argument("--include-subagents", action="store_true")

    sync_once_parser = subparsers.add_parser("sync-once")
    sync_once_parser.add_argument("--vault", type=Path)
    sync_once_parser.add_argument("--codex-home", type=Path)
    sync_once_parser.add_argument("--state-file", type=Path)
    sync_once_parser.add_argument("--lock-file", type=Path)
    sync_once_parser.add_argument("--include-subagents", action="store_true", default=None)
    sync_once_parser.add_argument("--log-level", type=str)
    sync_once_parser.add_argument("--recent-days", type=int)
    sync_once_parser.add_argument("--candidate-file-limit", type=int)
    sync_once_parser.add_argument("--candidate-bytes-limit", type=int)

    watch_parser = subparsers.add_parser("watch")
    watch_parser.add_argument("--vault", type=Path)
    watch_parser.add_argument("--codex-home", type=Path)
    watch_parser.add_argument("--state-file", type=Path)
    watch_parser.add_argument("--lock-file", type=Path)
    watch_parser.add_argument("--include-subagents", action="store_true", default=None)
    watch_parser.add_argument("--interval", type=int)
    watch_parser.add_argument("--log-level", type=str)
    watch_parser.add_argument("--recent-days", type=int)
    watch_parser.add_argument("--candidate-file-limit", type=int)
    watch_parser.add_argument("--candidate-bytes-limit", type=int)

    args = parser.parse_args(argv)
    try:
        return _dispatch(args)
    except (EOFError, KeyboardInterrupt):
        print("Cancelled.", file=sys.stderr)
        return 1


def _dispatch(args: argparse.Namespace) -> int:
    config_path = args.config.expanduser()
    config_data = load_toml_config(config_path)
    bootstrap_logger = EventLogger("WARNING")

    if args.command == "setup":
        return _run_setup(config_path=config_path, config_data=config_data, args=args)

    if args.command == "start":
        return _run_start(config_path=config_path, config_data=config_data)

    if args.command == "stop":
        return _run_stop(config_path=config_path, config_data=config_data)

    if args.command == "status":
        return _run_status(config_path=config_path, config_data=config_data, as_json=args.as_json)

    if args.command == "service-run":
        log_level = args.log_level or str(config_data.get("log_level", "INFO"))
        logger = EventLogger(log_level)
        return run_service(config_path=config_path, logger=logger)

    if args.command == "inspect-rollout":
        session_index = load_session_index(args.session_index, logger=bootstrap_logger)
        envelope = build_session_envelope(args.rollout_path, session_index)
        print(json.dumps(_envelope_to_dict(envelope), indent=2, ensure_ascii=False))
        return 0

    if args.command == "inspect-recent":
        codex_home = args.codex_home or Path(config_data.get("codex_home", Path.home() / ".codex"))
        session_index = load_session_index(codex_home / "session_index.jsonl", logger=bootstrap_logger)
        summaries = []
        for rollout_path in recent_rollout_files(codex_home, limit=args.limit * 5):
            try:
                envelope = build_session_envelope(rollout_path, session_index)
            except Exception as exc:
                print(f"skip {rollout_path}: {exc}", file=sys.stderr)
                continue

            if envelope.is_subagent and not args.include_subagents:
                continue

            summaries.append(_envelope_to_dict(envelope))
            if len(summaries) >= args.limit:
                break

        print(json.dumps(summaries, indent=2, ensure_ascii=False))
        return 0

    if args.command == "sync-once":
        config = resolve_sync_config(
            vault=args.vault,
            codex_home=args.codex_home,
            state_file=args.state_file,
            lock_file=args.lock_file,
            include_subagents=args.include_subagents,
            interval_seconds=None,
            recent_days=args.recent_days,
            candidate_file_limit=args.candidate_file_limit,
            candidate_bytes_limit=args.candidate_bytes_limit,
            log_level=args.log_level,
            config_data=config_data,
            config_path=config_path,
        )
        logger = EventLogger(config.log_level)
        with process_lock(config.lock_file):
            summary = sync_once(config=config, logger=logger)
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return 0

    if args.command == "watch":
        config = resolve_sync_config(
            vault=args.vault,
            codex_home=args.codex_home,
            state_file=args.state_file,
            lock_file=args.lock_file,
            include_subagents=args.include_subagents,
            interval_seconds=args.interval,
            recent_days=args.recent_days,
            candidate_file_limit=args.candidate_file_limit,
            candidate_bytes_limit=args.candidate_bytes_limit,
            log_level=args.log_level,
            config_data=config_data,
            config_path=config_path,
        )
        logger = EventLogger(config.log_level)
        with process_lock(config.lock_file):
            watch(config=config, logger=logger)
        return 0

    return 1


def _run_setup(*, config_path: Path, config_data: dict[str, Any], args: argparse.Namespace) -> int:
    if config_data:
        _print_current_config(config_data)
    updated_config = _collect_setup_config(
        config_data=config_data,
        vault_arg=args.vault,
        cooldown_arg=args.cooldown,
    )
    changed = updated_config != config_data
    if config_data and changed and (args.vault is None or args.cooldown is None):
        if not _confirm("Save updated config? [Y/n]: ", default=True):
            print("Config unchanged.")
            return 0
    saved_path = _save_configuration(config_path=config_path, config_data=updated_config)
    print(f"Saved config: {saved_path}")
    return 0


def _run_start(*, config_path: Path, config_data: dict[str, Any]) -> int:
    updated_config = config_data or _collect_setup_config(config_data={}, vault_arg=None, cooldown_arg=None)
    saved_path = _save_configuration(config_path=config_path, config_data=updated_config)
    paths = resolve_service_paths(config_path=saved_path, config_data=updated_config)
    launchd_status = query_launchd_status(plist_path=paths.launchd_plist_path)

    if launchd_status.loaded:
        bootout_launch_agent()
        bootstrap_launch_agent(paths.launchd_plist_path)
        print("LaunchAgent reloaded.")
        return 0

    bootstrap_launch_agent(paths.launchd_plist_path)
    print("LaunchAgent started.")
    return 0


def _run_stop(*, config_path: Path, config_data: dict[str, Any]) -> int:
    paths = resolve_service_paths(config_path=config_path, config_data=config_data)
    launchd_status = query_launchd_status(plist_path=paths.launchd_plist_path)
    if not launchd_status.loaded:
        print("LaunchAgent already stopped.")
        return 0

    bootout_launch_agent()
    print("LaunchAgent stopped.")
    return 0


def _run_status(*, config_path: Path, config_data: dict[str, Any], as_json: bool) -> int:
    paths = resolve_service_paths(config_path=config_path, config_data=config_data)
    launchd_status = query_launchd_status(plist_path=paths.launchd_plist_path)
    service_state = load_service_state(paths.service_state_file)
    snapshot = build_status_snapshot(
        config_path=config_path,
        config_data=config_data,
        paths=paths,
        launchd_loaded=launchd_status.loaded,
        service_state=service_state,
    )
    if as_json:
        print(json.dumps(status_snapshot_to_dict(snapshot), indent=2, ensure_ascii=False))
        return 0

    print(render_status_snapshot(snapshot))
    return 0


def _collect_setup_config(
    *,
    config_data: dict[str, Any],
    vault_arg: Path | None,
    cooldown_arg: str | None,
) -> dict[str, Any]:
    updated = dict(config_data)
    updated["vault"] = str(_resolve_vault(vault_arg, updated.get("vault")))
    updated["interval_seconds"] = _resolve_cooldown(cooldown_arg, updated.get("interval_seconds"))
    return updated


def _resolve_vault(vault_arg: Path | None, existing_value: Any) -> Path:
    if vault_arg is not None:
        return validate_vault_root(vault_arg.expanduser())

    default = Path(existing_value).expanduser() if isinstance(existing_value, str) and existing_value.strip() else None
    while True:
        prompt = f"Vault [{default}]: " if default is not None else "Vault: "
        value = input(prompt).strip()
        if not value:
            if default is not None:
                return validate_vault_root(default)
            print("Vault is required.", file=sys.stderr)
            continue
        try:
            return validate_vault_root(Path(value).expanduser())
        except (OSError, ValueError) as exc:
            print(f"Invalid vault: {exc}", file=sys.stderr)


def _resolve_cooldown(cooldown_arg: str | None, existing_value: Any) -> int:
    if cooldown_arg is not None:
        return parse_interval(cooldown_arg)

    default_seconds = int(existing_value) if isinstance(existing_value, int) else None
    while True:
        default_label = _render_cooldown_for_prompt(default_seconds) if default_seconds is not None else None
        prompt = f"Cooldown [{default_label}]: " if default_label is not None else "Cooldown (e.g. 10s, 1m): "
        value = input(prompt).strip()
        if not value:
            if default_seconds is not None:
                return max(default_seconds, 1)
            print("Cooldown is required.", file=sys.stderr)
            continue
        try:
            return parse_interval(value)
        except ValueError as exc:
            print(f"Invalid cooldown: {exc}", file=sys.stderr)


def _save_configuration(*, config_path: Path, config_data: dict[str, Any]) -> Path:
    resolved = resolve_sync_config(
        vault=None,
        codex_home=None,
        state_file=None,
        lock_file=None,
        include_subagents=None,
        interval_seconds=None,
        recent_days=None,
        candidate_file_limit=None,
        candidate_bytes_limit=None,
        log_level=None,
        config_data=config_data,
        config_path=config_path,
    )
    normalized = dict(config_data)
    normalized["vault"] = str(validate_vault_root(resolved.vault))
    normalized["interval_seconds"] = resolved.interval_seconds
    saved_path = save_toml_config(config_path, normalized)
    paths = resolve_service_paths(config_path=saved_path, config_data=normalized)
    ensure_launch_agent_dirs(paths)
    plist_content = render_launch_agent_plist(
        config_path=saved_path,
        paths=paths,
        start_interval=resolved.interval_seconds,
    )
    write_launch_agent_plist(paths.launchd_plist_path, plist_content)
    return saved_path


def _print_current_config(config_data: dict[str, Any]) -> None:
    vault = config_data.get("vault", "not configured")
    cooldown = _render_cooldown_for_prompt(config_data.get("interval_seconds"))
    print("Current config:")
    print(f"  Vault: {vault}")
    print(f"  Cooldown: {cooldown}")
    print("Press Enter to keep the current value.")


def _confirm(prompt: str, *, default: bool) -> bool:
    value = input(prompt).strip().lower()
    if not value:
        return default
    return value in {"y", "yes"}


def _render_cooldown_for_prompt(value: Any) -> str:
    if isinstance(value, int):
        if value % 3600 == 0:
            return f"{value // 3600}h"
        if value % 60 == 0:
            return f"{value // 60}m"
        return f"{value}s"
    return "10s"


def _envelope_to_dict(envelope: object) -> dict[str, object]:
    messages = getattr(envelope, "messages", ())
    return {
        "canonical_session_id": getattr(envelope, "canonical_session_id"),
        "rollout_path": str(getattr(envelope, "rollout_path")),
        "originator": getattr(envelope, "originator"),
        "source_kind": getattr(envelope, "source_kind"),
        "is_subagent": getattr(envelope, "is_subagent"),
        "parent_session_id": getattr(envelope, "parent_session_id"),
        "cwd": getattr(envelope, "cwd"),
        "project_slug": getattr(envelope, "project_slug"),
        "thread_name": _redact_optional_text(getattr(envelope, "thread_name")),
        "title_seed": redact_text(getattr(envelope, "title_seed")),
        "started_at": getattr(envelope, "started_at"),
        "updated_at": getattr(envelope, "updated_at"),
        "message_count": len(messages),
        "messages": [
            {
                "message_key": message.message_key,
                "role": message.role,
                "phase": message.phase,
                "timestamp": message.timestamp,
                "text_preview": redact_text(message.text)[:120],
            }
            for message in messages
        ],
    }


def _redact_optional_text(value: object) -> str | None:
    if isinstance(value, str):
        return redact_text(value)
    return None


if __name__ == "__main__":
    raise SystemExit(main())
