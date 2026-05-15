from __future__ import annotations

import os
import plistlib
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .config import DEFAULT_LAUNCHD_LABEL, ServicePaths


DEFAULT_THROTTLE_INTERVAL = 1


@dataclass(frozen=True)
class LaunchdStatus:
    label: str
    loaded: bool
    plist_path: Path
    target: str


def render_launch_agent_plist(
    *,
    config_path: Path,
    paths: ServicePaths,
    start_interval: int,
    throttle_interval: int = DEFAULT_THROTTLE_INTERVAL,
) -> str:
    payload = {
        "Label": DEFAULT_LAUNCHD_LABEL,
        "ProgramArguments": [
            sys.executable,
            "-m",
            "codex_obsidian_sync.cli",
            "--config",
            str(config_path),
            "service-run",
        ],
        "StartInterval": max(start_interval, 1),
        "RunAtLoad": False,
        "KeepAlive": False,
        "ThrottleInterval": max(throttle_interval, 1),
        "StandardOutPath": str(paths.launchd_stdout_path),
        "StandardErrorPath": str(paths.launchd_stderr_path),
    }
    return plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=False).decode("utf-8")


def write_launch_agent_plist(plist_path: Path, content: str) -> bool:
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    existing = plist_path.read_text(encoding="utf-8") if plist_path.exists() else None
    if existing == content:
        return False

    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=plist_path.parent,
        delete=False,
    ) as handle:
        handle.write(content)
        temp_name = handle.name
    os.replace(temp_name, plist_path)
    return True


def ensure_launch_agent_dirs(paths: ServicePaths) -> None:
    paths.launchd_plist_path.parent.mkdir(parents=True, exist_ok=True)
    paths.launchd_stdout_path.parent.mkdir(parents=True, exist_ok=True)
    paths.launchd_stderr_path.parent.mkdir(parents=True, exist_ok=True)


def read_launch_agent_plist(plist_path: Path) -> str | None:
    if not plist_path.exists():
        return None
    return plist_path.read_text(encoding="utf-8")


def query_launchd_status(
    *,
    label: str = DEFAULT_LAUNCHD_LABEL,
    plist_path: Path | None = None,
) -> LaunchdStatus:
    result = _run_launchctl(["print", launchd_target(label)])
    return LaunchdStatus(
        label=label,
        loaded=result.returncode == 0,
        plist_path=(plist_path or Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"),
        target=launchd_target(label),
    )


def bootstrap_launch_agent(plist_path: Path, *, label: str = DEFAULT_LAUNCHD_LABEL) -> None:
    _run_launchctl(["bootstrap", launchd_domain(), str(plist_path)], check=True)


def bootout_launch_agent(*, label: str = DEFAULT_LAUNCHD_LABEL) -> None:
    _run_launchctl(["bootout", launchd_target(label)], check=True)


def launchd_domain() -> str:
    return f"gui/{os.getuid()}"


def launchd_target(label: str = DEFAULT_LAUNCHD_LABEL) -> str:
    return f"{launchd_domain()}/{label}"


def _run_launchctl(arguments: list[str], *, check: bool = False) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["launchctl", *arguments],
        capture_output=True,
        text=True,
        check=False,
    )
    if check and result.returncode != 0:
        command = " ".join(["launchctl", *arguments])
        raise RuntimeError(result.stderr.strip() or f"Command failed: {command}")
    return result
