from __future__ import annotations

import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


class GenerateHomebrewFormulaTests(unittest.TestCase):
    def test_generates_arch_specific_formula_from_release_checksums(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            aarch64 = root / "codex-obsidian-sync-aarch64-apple-darwin.tar.gz.sha256"
            x86_64 = root / "codex-obsidian-sync-x86_64-apple-darwin.tar.gz.sha256"
            output = root / "codex-obsidian-sync.rb"
            aarch64_checksum = "a" * 64
            x86_64_checksum = "b" * 64
            aarch64.write_text(
                f"{aarch64_checksum}  codex-obsidian-sync-aarch64-apple-darwin.tar.gz\n",
                encoding="utf-8",
            )
            x86_64.write_text(
                f"{x86_64_checksum}  codex-obsidian-sync-x86_64-apple-darwin.tar.gz\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/generate_homebrew_formula.py",
                    "--version",
                    "v0.1.0",
                    "--aarch64-checksum",
                    str(aarch64),
                    "--x86-64-checksum",
                    str(x86_64),
                    "--output",
                    str(output),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            formula = output.read_text(encoding="utf-8")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("class CodexObsidianSync < Formula", formula)
        self.assertIn('version "0.1.0"', formula)
        self.assertIn("depends_on :macos", formula)
        self.assertIn("on_arm do", formula)
        self.assertIn("on_intel do", formula)
        self.assertIn(
            "https://github.com/HuiungJang/codex-obsidian-sync/releases/download/v0.1.0/"
            "codex-obsidian-sync-aarch64-apple-darwin.tar.gz",
            formula,
        )
        self.assertIn(
            "https://github.com/HuiungJang/codex-obsidian-sync/releases/download/v0.1.0/"
            "codex-obsidian-sync-x86_64-apple-darwin.tar.gz",
            formula,
        )
        self.assertIn(f'sha256 "{aarch64_checksum}"', formula)
        self.assertIn(f'sha256 "{x86_64_checksum}"', formula)
        self.assertNotIn("<artifact>", formula)
        self.assertNotIn("<version>", formula)

        ruby = shutil.which("ruby")
        if ruby:
            syntax = subprocess.run([ruby, "-c"], input=formula, text=True, capture_output=True, check=False)
            self.assertEqual(syntax.returncode, 0, syntax.stderr)

    def test_rejects_checksum_for_wrong_target(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            aarch64 = root / "aarch64.sha256"
            x86_64 = root / "x86_64.sha256"
            aarch64.write_text(f"{'a' * 64}  unexpected.tar.gz\n", encoding="utf-8")
            x86_64.write_text(
                f"{'b' * 64}  codex-obsidian-sync-x86_64-apple-darwin.tar.gz\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/generate_homebrew_formula.py",
                    "--version",
                    "0.1.0",
                    "--aarch64-checksum",
                    str(aarch64),
                    "--x86-64-checksum",
                    str(x86_64),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not reference codex-obsidian-sync-aarch64-apple-darwin.tar.gz", result.stderr)


if __name__ == "__main__":
    unittest.main()
