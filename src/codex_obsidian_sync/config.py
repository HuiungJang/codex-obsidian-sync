from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import tomllib

from .writer import write_atomic


DEFAULT_CODEX_HOME = Path.home() / ".codex"
DEFAULT_CONFIG_PATH = DEFAULT_CODEX_HOME / "obsidian-sync" / "config.toml"
DEFAULT_LAUNCHD_LABEL = "com.codex.obsidian-sync"
DEFAULT_LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
DEFAULT_STATE_DIRNAME = "obsidian-sync"


@dataclass(frozen=True)
class SyncConfig:
    codex_home: Path
    vault: Path
    state_file: Path
    lock_file: Path
    include_subagents: bool
    interval_seconds: int
    recent_days: int
    candidate_file_limit: int
    candidate_bytes_limit: int
    log_level: str


@dataclass(frozen=True)
class ServicePaths:
    config_path: Path
    codex_home: Path
    state_dir: Path
    session_index_path: Path
    sessions_path: Path
    sync_state_file: Path
    sync_lock_file: Path
    service_state_file: Path
    service_runner_lock_file: Path
    service_state_lock_file: Path
    launchd_plist_path: Path
    launchd_stdout_path: Path
    launchd_stderr_path: Path


def load_toml_config(path: Path | None) -> dict[str, Any]:
    config_path = (path or DEFAULT_CONFIG_PATH).expanduser()
    if not config_path.exists():
        return {}
    with config_path.open("rb") as handle:
        return tomllib.load(handle)


def save_toml_config(path: Path | None, data: dict[str, Any]) -> Path:
    config_path = (path or DEFAULT_CONFIG_PATH).expanduser()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    rendered = render_toml(data)
    write_atomic(config_path, rendered)
    return config_path


def resolve_service_paths(
    *,
    config_path: Path | None,
    config_data: dict[str, Any],
) -> ServicePaths:
    resolved_config_path = (config_path or DEFAULT_CONFIG_PATH).expanduser()
    codex_home = _path_value(None, config_data.get("codex_home"), DEFAULT_CODEX_HOME)
    state_dir = codex_home / DEFAULT_STATE_DIRNAME
    sync_state_file = _path_value(None, config_data.get("state_file"), state_dir / "sync-state.json")
    sync_lock_file = _path_value(None, config_data.get("lock_file"), sync_state_file.with_suffix(".lock"))
    service_state_file = _path_value(None, config_data.get("service_state_file"), state_dir / "service-state.json")
    service_runner_lock_file = _path_value(
        None,
        config_data.get("service_runner_lock_file"),
        state_dir / "service-runner.lock",
    )
    service_state_lock_file = _path_value(
        None,
        config_data.get("service_state_lock_file"),
        state_dir / "service-state.lock",
    )
    launchd_plist_path = _path_value(
        None,
        config_data.get("launchd_plist_path"),
        DEFAULT_LAUNCH_AGENTS_DIR / f"{DEFAULT_LAUNCHD_LABEL}.plist",
    )
    launchd_stdout_path = _path_value(
        None,
        config_data.get("launchd_stdout_path"),
        state_dir / "launchd.stdout.log",
    )
    launchd_stderr_path = _path_value(
        None,
        config_data.get("launchd_stderr_path"),
        state_dir / "launchd.stderr.log",
    )
    return ServicePaths(
        config_path=resolved_config_path,
        codex_home=codex_home,
        state_dir=state_dir,
        session_index_path=codex_home / "session_index.jsonl",
        sessions_path=codex_home / "sessions",
        sync_state_file=sync_state_file,
        sync_lock_file=sync_lock_file,
        service_state_file=service_state_file,
        service_runner_lock_file=service_runner_lock_file,
        service_state_lock_file=service_state_lock_file,
        launchd_plist_path=launchd_plist_path,
        launchd_stdout_path=launchd_stdout_path,
        launchd_stderr_path=launchd_stderr_path,
    )


def resolve_sync_config(
    *,
    vault: Path | None,
    codex_home: Path | None,
    state_file: Path | None,
    lock_file: Path | None,
    include_subagents: bool | None,
    interval_seconds: int | None,
    recent_days: int | None,
    candidate_file_limit: int | None,
    candidate_bytes_limit: int | None,
    log_level: str | None,
    config_data: dict[str, Any],
    config_path: Path | None = None,
) -> SyncConfig:
    paths = resolve_service_paths(config_path=config_path, config_data=config_data)
    resolved_codex_home = _path_value(codex_home, config_data.get("codex_home"), paths.codex_home)
    resolved_state_file = _path_value(state_file, config_data.get("state_file"), paths.sync_state_file)
    resolved_lock_file = _path_value(lock_file, config_data.get("lock_file"), paths.sync_lock_file)
    resolved_vault = _required_path_value(vault, config_data.get("vault"))
    return SyncConfig(
        codex_home=resolved_codex_home,
        vault=resolved_vault,
        state_file=resolved_state_file,
        lock_file=resolved_lock_file,
        include_subagents=_bool_value(include_subagents, config_data.get("include_subagents"), False),
        interval_seconds=max(1, _int_value(interval_seconds, config_data.get("interval_seconds"), 10)),
        recent_days=max(1, _int_value(recent_days, config_data.get("recent_days"), 30)),
        candidate_file_limit=max(1, _int_value(candidate_file_limit, config_data.get("candidate_file_limit"), 100)),
        candidate_bytes_limit=_int_value(
            candidate_bytes_limit,
            config_data.get("candidate_bytes_limit"),
            500 * 1024 * 1024,
        ),
        log_level=_str_value(log_level, config_data.get("log_level"), "INFO").upper(),
    )


def render_toml(data: dict[str, Any]) -> str:
    lines: list[str] = []
    _render_table(lines, (), data)
    return "\n".join(lines).rstrip() + "\n"


def _render_table(lines: list[str], path: tuple[str, ...], table: dict[str, Any]) -> None:
    scalar_items: list[tuple[str, Any]] = []
    table_items: list[tuple[str, dict[str, Any]]] = []
    for key, value in table.items():
        if value is None:
            continue
        if isinstance(value, dict):
            table_items.append((key, value))
        else:
            scalar_items.append((key, value))

    if path:
        if lines:
            lines.append("")
        lines.append(f"[{'.'.join(path)}]")

    for key, value in scalar_items:
        lines.append(f"{key} = {_toml_literal(value)}")

    for key, value in table_items:
        _render_table(lines, (*path, key), value)


def _path_value(cli_value: Path | None, config_value: Any, default: Path) -> Path:
    if cli_value is not None:
        return cli_value.expanduser()
    if isinstance(config_value, str) and config_value.strip():
        return Path(config_value).expanduser()
    return default.expanduser()


def _required_path_value(cli_value: Path | None, config_value: Any) -> Path:
    if cli_value is not None:
        return cli_value.expanduser()
    if isinstance(config_value, str) and config_value.strip():
        return Path(config_value).expanduser()
    raise ValueError("Vault path is required via CLI or config.toml")


def _int_value(cli_value: int | None, config_value: Any, default: int) -> int:
    if cli_value is not None:
        return cli_value
    if isinstance(config_value, int):
        return config_value
    return default


def _bool_value(cli_value: bool | None, config_value: Any, default: bool) -> bool:
    if cli_value is not None:
        return cli_value
    if isinstance(config_value, bool):
        return config_value
    return default


def _str_value(cli_value: str | None, config_value: Any, default: str) -> str:
    if cli_value is not None and cli_value.strip():
        return cli_value
    if isinstance(config_value, str) and config_value.strip():
        return config_value
    return default


def _toml_literal(value: Any) -> str:
    if isinstance(value, Path):
        return _toml_literal(str(value))
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        return _quote_string(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_literal(item) for item in value) + "]"
    raise TypeError(f"Unsupported TOML value type: {type(value).__name__}")


def _quote_string(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\b", "\\b")
        .replace("\t", "\\t")
        .replace("\n", "\\n")
        .replace("\f", "\\f")
        .replace("\r", "\\r")
    )
    return f'"{escaped}"'
