from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


class SmokeHomebrewFormulaTests(unittest.TestCase):
    def test_installs_verifies_tests_and_uninstalls_formula(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-brew-smoke-") as temp_dir:
            root = Path(temp_dir)
            formula = root / "codex-obsidian-sync.rb"
            formula.write_text("class CodexObsidianSync < Formula\nend\n", encoding="utf-8")
            brew = write_fake_brew(root, version="0.1.0")
            output = root / "summary.json"

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/smoke_homebrew_formula.py",
                    "--formula",
                    str(formula),
                    "--expected-version",
                    "v0.1.0",
                    "--brew",
                    str(brew),
                    "--output",
                    str(output),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(output.read_text(encoding="utf-8"))
            log = (root / "brew.log").read_text(encoding="utf-8")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(report["ok"])
        self.assertFalse(report["installed_after"])
        self.assertIn(f"install --formula {formula.resolve()}", log)
        self.assertIn("--prefix codex-obsidian-sync", log)
        self.assertIn("test codex-obsidian-sync", log)
        self.assertIn("uninstall --formula codex-obsidian-sync", log)

    def test_refuses_to_smoke_when_formula_is_already_installed(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-brew-smoke-") as temp_dir:
            root = Path(temp_dir)
            formula = root / "codex-obsidian-sync.rb"
            formula.write_text("class CodexObsidianSync < Formula\nend\n", encoding="utf-8")
            brew = write_fake_brew(root, version="0.1.0", preinstalled=True)

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/smoke_homebrew_formula.py",
                    "--formula",
                    str(formula),
                    "--expected-version",
                    "0.1.0",
                    "--brew",
                    str(brew),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            log = (root / "brew.log").read_text(encoding="utf-8")

        self.assertEqual(result.returncode, 1)
        self.assertIn("already installed", result.stderr)
        self.assertNotIn("uninstall --formula codex-obsidian-sync", log)

    def test_uninstalls_when_version_check_fails(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-brew-smoke-") as temp_dir:
            root = Path(temp_dir)
            formula = root / "codex-obsidian-sync.rb"
            formula.write_text("class CodexObsidianSync < Formula\nend\n", encoding="utf-8")
            brew = write_fake_brew(root, version="0.2.0")

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/smoke_homebrew_formula.py",
                    "--formula",
                    str(formula),
                    "--expected-version",
                    "0.1.0",
                    "--brew",
                    str(brew),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            log = (root / "brew.log").read_text(encoding="utf-8")

        self.assertEqual(result.returncode, 1)
        self.assertIn("Unexpected Homebrew binary version", result.stderr)
        self.assertIn("uninstall --formula codex-obsidian-sync", log)


def write_fake_brew(root: Path, *, version: str, preinstalled: bool = False) -> Path:
    brew = root / "brew"
    prefix = root / "Cellar" / "codex-obsidian-sync" / version
    state = root / "installed"
    if preinstalled:
        state.write_text(str(prefix), encoding="utf-8")
    brew.write_text(
        f"""#!/usr/bin/env python3
from pathlib import Path
import shutil
import sys

root = Path({str(root)!r})
state = root / "installed"
prefix = Path({str(prefix)!r})
log = root / "brew.log"
log.write_text(log.read_text(encoding="utf-8") + " ".join(sys.argv[1:]) + "\\n" if log.exists() else " ".join(sys.argv[1:]) + "\\n", encoding="utf-8")

def install_binary():
    binary = prefix / "bin" / "codex-obsidian-sync"
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_text("#!/bin/sh\\necho codex-obsidian-sync {version}\\n", encoding="utf-8")
    binary.chmod(0o755)
    state.write_text(str(prefix), encoding="utf-8")

args = sys.argv[1:]
if args == ["list", "--formula", "codex-obsidian-sync"]:
    sys.exit(0 if state.exists() else 1)
if len(args) == 3 and args[0:2] == ["install", "--formula"]:
    if not Path(args[2]).is_file():
        print("formula missing", file=sys.stderr)
        sys.exit(1)
    install_binary()
    sys.exit(0)
if args == ["--prefix", "codex-obsidian-sync"]:
    if not state.exists():
        sys.exit(1)
    print(state.read_text(encoding="utf-8"))
    sys.exit(0)
if args == ["test", "codex-obsidian-sync"]:
    if not state.exists():
        sys.exit(1)
    sys.exit(0)
if args == ["uninstall", "--formula", "codex-obsidian-sync"]:
    if state.exists():
        shutil.rmtree(prefix.parent, ignore_errors=True)
        state.unlink()
    sys.exit(0)
print("unexpected args: " + " ".join(args), file=sys.stderr)
sys.exit(2)
""",
        encoding="utf-8",
    )
    brew.chmod(0o755)
    return brew


if __name__ == "__main__":
    unittest.main()
