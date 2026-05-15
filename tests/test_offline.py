from __future__ import annotations

import ast
import unittest
from pathlib import Path


BANNED_IMPORTS = {
    "aiohttp",
    "http.client",
    "httpx",
    "requests",
    "socket",
    "urllib.request",
    "websocket",
    "websockets",
}


class OfflineOnlyTests(unittest.TestCase):
    def test_source_does_not_import_network_clients(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        source_root = project_root / "src" / "codex_obsidian_sync"

        imported_modules: set[str] = set()
        for source_path in source_root.glob("*.py"):
            module = ast.parse(source_path.read_text(encoding="utf-8"))
            for node in ast.walk(module):
                if isinstance(node, ast.Import):
                    imported_modules.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported_modules.add(node.module)

        offending = sorted(
            module
            for module in imported_modules
            if module in BANNED_IMPORTS or any(module.startswith(f"{name}.") for name in BANNED_IMPORTS)
        )
        self.assertEqual(offending, [])


if __name__ == "__main__":
    unittest.main()
