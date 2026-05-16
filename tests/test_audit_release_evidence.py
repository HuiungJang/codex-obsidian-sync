from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tarfile
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


class AuditReleaseEvidenceTests(unittest.TestCase):
    def test_reports_ready_from_complete_release_evidence(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-evidence-") as temp_dir:
            release_dir = Path(temp_dir)
            checksums = write_release_artifacts(release_dir)
            formula = write_formula(release_dir, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)
            output = release_dir / "release-evidence-summary.json"

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_release_evidence.py",
                    "--version",
                    "v0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                    "--output",
                    str(output),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(report["ok"], report["no_go_reasons"])
        self.assertEqual(report["tag"], "v0.1.0")
        self.assertEqual(report["no_go_reasons"], [])
        self.assertCheckOk(report, "release artifact:aarch64-apple-darwin")
        self.assertCheckOk(report, "release artifact:x86_64-apple-darwin")
        self.assertCheckOk(report, "homebrew formula")
        self.assertCheckOk(report, "release smoke summary:aarch64-apple-darwin")
        self.assertCheckOk(report, "release smoke summary:x86_64-apple-darwin")
        self.assertCheckOk(report, "homebrew smoke summary")

    def test_fails_when_release_smoke_summary_is_not_ok(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-evidence-") as temp_dir:
            release_dir = Path(temp_dir)
            checksums = write_release_artifacts(release_dir)
            formula = write_formula(release_dir, checksums)
            write_release_smoke_summaries(release_dir, failed_target="x86_64-apple-darwin")
            write_homebrew_smoke_summary(release_dir, formula)

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_release_evidence.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "release smoke summary:x86_64-apple-darwin: release smoke summary ok is not true",
            report["no_go_reasons"],
        )

    def test_fails_when_release_smoke_did_not_uninstall_artifact(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-evidence-") as temp_dir:
            release_dir = Path(temp_dir)
            checksums = write_release_artifacts(release_dir)
            formula = write_formula(release_dir, checksums)
            write_release_smoke_summaries(release_dir, installed_after=True)
            write_homebrew_smoke_summary(release_dir, formula)

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_release_evidence.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "release smoke summary:aarch64-apple-darwin: release smoke did not prove the artifact install was removed",
            report["no_go_reasons"],
        )

    def test_fails_when_release_smoke_version_command_output_does_not_match(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-evidence-") as temp_dir:
            release_dir = Path(temp_dir)
            checksums = write_release_artifacts(release_dir)
            formula = write_formula(release_dir, checksums)
            write_release_smoke_summaries(
                release_dir,
                version_command_stdout="codex-obsidian-sync 0.2.0\n",
            )
            write_homebrew_smoke_summary(release_dir, formula)

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_release_evidence.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "release smoke summary:aarch64-apple-darwin: release smoke did not record successful installed binary version",
            report["no_go_reasons"],
        )

    def test_fails_when_homebrew_smoke_did_not_uninstall(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-evidence-") as temp_dir:
            release_dir = Path(temp_dir)
            checksums = write_release_artifacts(release_dir)
            formula = write_formula(release_dir, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula, installed_after=True)

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_release_evidence.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "homebrew smoke summary: Homebrew smoke did not prove the formula was uninstalled",
            report["no_go_reasons"],
        )

    def test_fails_when_homebrew_smoke_dry_run_mutated_vault(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-evidence-") as temp_dir:
            release_dir = Path(temp_dir)
            checksums = write_release_artifacts(release_dir)
            formula = write_formula(release_dir, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula, vault_unchanged=False)

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_release_evidence.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "homebrew smoke summary: Homebrew smoke vault_unchanged is not true",
            report["no_go_reasons"],
        )

    def test_fails_when_homebrew_smoke_version_does_not_match(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-evidence-") as temp_dir:
            release_dir = Path(temp_dir)
            checksums = write_release_artifacts(release_dir)
            formula = write_formula(release_dir, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula, version="codex-obsidian-sync 0.2.0")

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_release_evidence.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "homebrew smoke summary: Homebrew smoke version does not match release version",
            report["no_go_reasons"],
        )

    def test_fails_when_homebrew_smoke_uninstall_command_failed(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-evidence-") as temp_dir:
            release_dir = Path(temp_dir)
            checksums = write_release_artifacts(release_dir)
            formula = write_formula(release_dir, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula, uninstall_returncode=1)

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_release_evidence.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "homebrew smoke summary: Homebrew smoke did not record successful brew uninstall",
            report["no_go_reasons"],
        )

    def assertCheckOk(self, report: dict[str, object], name: str) -> None:
        checks = report["checks"]
        self.assertIsInstance(checks, list)
        matches = [item for item in checks if item["name"] == name]
        self.assertEqual(len(matches), 1)
        self.assertTrue(matches[0]["ok"], matches[0]["no_go_reasons"])


def write_release_artifacts(release_dir: Path) -> dict[str, str]:
    checksums = {}
    for target in ("aarch64-apple-darwin", "x86_64-apple-darwin"):
        package_name = f"codex-obsidian-sync-{target}.tar.gz"
        package = release_dir / package_name
        source_dir = release_dir / f"source-{target}"
        source_dir.mkdir()
        binary = source_dir / "codex-obsidian-sync"
        binary.write_text("#!/bin/sh\necho codex-obsidian-sync 0.1.0\n", encoding="utf-8")
        binary.chmod(0o755)
        with tarfile.open(package, "w:gz") as archive:
            archive.add(binary, arcname=f"codex-obsidian-sync-{target}/codex-obsidian-sync")
        checksum = hashlib.sha256(package.read_bytes()).hexdigest()
        checksums[target] = checksum
        (release_dir / f"{package_name}.sha256").write_text(f"{checksum}  {package_name}\n", encoding="utf-8")
    return checksums


def write_formula(release_dir: Path, checksums: dict[str, str]) -> Path:
    path = release_dir / "codex-obsidian-sync.rb"
    path.write_text(
        "\n".join(
            [
                "class CodexObsidianSync < Formula",
                '  desc "Sync local Codex conversations into an Obsidian vault"',
                '  homepage "https://github.com/HuiungJang/codex-obsidian-sync"',
                '  license "Apache-2.0"',
                '  version "0.1.0"',
                "",
                "  depends_on :macos",
                "",
                "  on_macos do",
                "    on_arm do",
                '      url "https://github.com/HuiungJang/codex-obsidian-sync/releases/download/v0.1.0/codex-obsidian-sync-aarch64-apple-darwin.tar.gz"',
                f'      sha256 "{checksums["aarch64-apple-darwin"]}"',
                "    end",
                "",
                "    on_intel do",
                '      url "https://github.com/HuiungJang/codex-obsidian-sync/releases/download/v0.1.0/codex-obsidian-sync-x86_64-apple-darwin.tar.gz"',
                f'      sha256 "{checksums["x86_64-apple-darwin"]}"',
                "    end",
                "  end",
                "",
                "  def install",
                '    bin.install "codex-obsidian-sync"',
                "  end",
                "end",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def write_release_smoke_summaries(
    release_dir: Path,
    failed_target: str | None = None,
    installed_after: bool = False,
    version_command_stdout: str = "codex-obsidian-sync 0.1.0\n",
) -> None:
    for target in ("aarch64-apple-darwin", "x86_64-apple-darwin"):
        (release_dir / f"codex-obsidian-sync-{target}.smoke-summary.json").write_text(
            json.dumps(
                {
                    "ok": target != failed_target,
                    "tarball": str(release_dir / f"codex-obsidian-sync-{target}.tar.gz"),
                    "checksum": str(release_dir / f"codex-obsidian-sync-{target}.tar.gz.sha256"),
                    "version": "codex-obsidian-sync 0.1.0",
                    "status_configured": True,
                    "status_json_parsed": True,
                    "inspect_count": 1,
                    "dry_run": True,
                    "processed": 1,
                    "vault_unchanged": True,
                    "uninstalled": not installed_after,
                    "installed_after": installed_after,
                    "note_files": 1,
                    "commands": [
                        {
                            "command": ["/tmp/codex-obsidian-sync/bin/codex-obsidian-sync", "--version"],
                            "returncode": 0,
                            "stdout": version_command_stdout,
                        }
                    ],
                }
            )
            + "\n",
            encoding="utf-8",
        )


def write_homebrew_smoke_summary(
    release_dir: Path,
    formula: Path,
    installed_after: bool = False,
    uninstall_returncode: int = 0,
    vault_unchanged: bool = True,
    version: str = "codex-obsidian-sync 0.1.0",
) -> None:
    (release_dir / "homebrew-smoke-summary.json").write_text(
        json.dumps(
            {
                "ok": True,
                "formula": str(formula.resolve()),
                "expected_version": "0.1.0",
                "version": version,
                "installed_after": installed_after,
                "status_configured": True,
                "status_json_parsed": True,
                "dry_run": True,
                "vault_unchanged": vault_unchanged,
                "note_files": 1,
                "commands": [
                    {"command": ["brew", "list", "--formula", "codex-obsidian-sync"], "returncode": 1},
                    {"command": ["brew", "install", "--formula", str(formula.resolve())], "returncode": 0},
                    {"command": ["brew", "--prefix", "codex-obsidian-sync"], "returncode": 0},
                    {
                        "command": ["/tmp/codex-obsidian-sync/bin/codex-obsidian-sync", "--version"],
                        "returncode": 0,
                        "stdout": f"{version}\n",
                    },
                    {"command": ["brew", "test", "codex-obsidian-sync"], "returncode": 0},
                    {
                        "command": ["brew", "uninstall", "--formula", "codex-obsidian-sync"],
                        "returncode": uninstall_returncode,
                    },
                    {"command": ["brew", "list", "--formula", "codex-obsidian-sync"], "returncode": 1},
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
