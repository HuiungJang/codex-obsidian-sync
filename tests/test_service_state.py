from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from codex_obsidian_sync.service_state import load_service_state, mutate_service_state


class ServiceStateTests(unittest.TestCase):
    def test_load_service_state_returns_defaults_for_corrupt_file(self) -> None:
        with TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "service-state.json"
            state_path.write_text("{not-json", encoding="utf-8")

            state = load_service_state(state_path)

        self.assertFalse(state["pending"])
        self.assertEqual(state["last_summary"], {})

    def test_mutate_service_state_persists_changes(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state_path = root / "service-state.json"
            lock_path = root / "service-state.lock"

            mutate_service_state(
                state_path=state_path,
                lock_path=lock_path,
                mutator=lambda state: state.update({"pending": True}),
            )
            state = load_service_state(state_path)

        self.assertTrue(state["pending"])
        self.assertIsNotNone(state["updated_at"])


if __name__ == "__main__":
    unittest.main()
