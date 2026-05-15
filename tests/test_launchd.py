from __future__ import annotations

import plistlib
import sys
import unittest
from pathlib import Path

from codex_obsidian_sync.config import resolve_service_paths
from codex_obsidian_sync.launchd import render_launch_agent_plist


class LaunchdTests(unittest.TestCase):
    def test_render_launch_agent_plist_uses_start_interval_schedule(self) -> None:
        paths = resolve_service_paths(
            config_path=Path("/tmp/config.toml"),
            config_data={"codex_home": "/tmp/codex-home"},
        )

        rendered = render_launch_agent_plist(
            config_path=Path("/tmp/config.toml"),
            paths=paths,
            start_interval=60,
        )
        payload = plistlib.loads(rendered.encode("utf-8"))

        self.assertEqual(payload["ProgramArguments"][0], sys.executable)
        self.assertEqual(payload["ProgramArguments"][1:5], ["-m", "codex_obsidian_sync.cli", "--config", "/tmp/config.toml"])
        self.assertEqual(payload["ProgramArguments"][5], "service-run")
        self.assertEqual(payload["StartInterval"], 60)
        self.assertFalse(payload["RunAtLoad"])
        self.assertFalse(payload["KeepAlive"])
        self.assertNotIn("WatchPaths", payload)


if __name__ == "__main__":
    unittest.main()
