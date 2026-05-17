from __future__ import annotations

import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory, gettempdir

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import compare_python_rust_sync


class ComparePythonRustSyncTests(unittest.TestCase):
    def test_refuses_symlinked_output_dir_before_runtime_setup(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-compare-test-") as temp_dir:
            root = Path(temp_dir)
            output_target = root / "target-output"
            output_link = Path(gettempdir()) / f"codex-obsidian-sync-compare-{root.name}"
            output_target.mkdir()
            remove_path(output_link)
            output_link.symlink_to(output_target, target_is_directory=True)

            try:
                with self.assertRaisesRegex(RuntimeError, "Output dir is a symlink"):
                    compare_python_rust_sync.resolve_output_dir(output_link)

                result = subprocess.run(
                    [
                        sys.executable,
                        "scripts/compare_python_rust_sync.py",
                        "--output-dir",
                        str(output_link),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
            finally:
                remove_path(output_link)

        self.assertEqual(result.returncode, 1)
        self.assertIn("Output dir is a symlink", result.stderr)
        self.assertFalse((output_target / ".codex-obsidian-sync-compare").exists())


def remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


if __name__ == "__main__":
    unittest.main()
