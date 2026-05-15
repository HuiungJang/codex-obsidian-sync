from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from codex_obsidian_sync.state_store import load_state, save_state


class StateStoreTests(unittest.TestCase):
    def test_load_state_quarantines_malformed_json_and_returns_empty_state(self) -> None:
        with TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "sync-state.json"
            state_path.write_text('{"files": ', encoding="utf-8")

            state = load_state(state_path)
            quarantined = list(state_path.parent.glob("sync-state.json.corrupt-*"))
            quarantined_text = quarantined[0].read_text(encoding="utf-8")
            state_path_exists = state_path.exists()

            self.assertEqual(state, {"files": {}})
            self.assertEqual(len(quarantined), 1)
            self.assertFalse(state_path_exists)
            self.assertEqual(quarantined_text, '{"files": ')

    def test_save_state_uses_shared_atomic_write_helper(self) -> None:
        with TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "sync-state.json"

            with patch("codex_obsidian_sync.state_store.write_atomic") as write_atomic:
                save_state(state_path, {"files": {"one": {"included": True}}})

        write_atomic.assert_called_once_with(
            state_path,
            json.dumps(
                {"files": {"one": {"included": True}}},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
        )


if __name__ == "__main__":
    unittest.main()
