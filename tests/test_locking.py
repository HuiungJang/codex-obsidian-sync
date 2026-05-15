from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from codex_obsidian_sync.locking import process_lock


class LockingTests(unittest.TestCase):
    def test_process_lock_rejects_second_process(self) -> None:
        with TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / "sync.lock"
            env = os.environ.copy()
            env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")

            with process_lock(lock_path):
                child_code = f"""
from pathlib import Path
from codex_obsidian_sync.locking import process_lock

lock_path = Path(r"{lock_path}")
try:
    with process_lock(lock_path):
        raise SystemExit(0)
except RuntimeError:
    raise SystemExit(11)
"""
                result = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        child_code,
                    ],
                    env=env,
                    check=False,
                )

        self.assertEqual(result.returncode, 11)


if __name__ == "__main__":
    unittest.main()
