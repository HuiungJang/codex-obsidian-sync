from __future__ import annotations

import hashlib
import json
import os
import plistlib
import subprocess
import sys
import tarfile
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


class AuditCutoverReadinessTests(unittest.TestCase):
    def test_reports_ready_from_offline_release_and_launchagent_inputs(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-audit-") as temp_dir:
            root = Path(temp_dir)
            release_dir = root / "dist"
            formula = root / "Formula" / "codex-obsidian-sync.rb"
            rust_binary = write_fake_binary(root / "bin" / "codex-obsidian-sync", "0.1.0")
            rollback_binary = write_fake_binary(root / "rollback" / "codex-obsidian-sync", "0.1.0")
            status_path = root / "status.json"
            plist_path = root / "agent.plist"
            monitor_dir = Path("/tmp") / f"codex-obsidian-sync-monitor-{os.getpid()}-{root.name}"
            checksums = write_release_artifacts(release_dir)
            write_formula(formula, checksums)
            write_status(status_path, plist_path)
            write_plist(plist_path, str(rollback_binary))

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_cutover_readiness.py",
                    "--version",
                    "v0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                    "--expected-rust-binary",
                    str(rust_binary),
                    "--rollback-binary",
                    str(rollback_binary),
                    "--status-json-file",
                    str(status_path),
                    "--plist-file",
                    str(plist_path),
                    "--expected-current-program-arg0",
                    str(rollback_binary),
                    "--monitor-dir",
                    str(monitor_dir),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(report["ok"], report["no_go_reasons"])
        self.assertEqual(report["tag"], "v0.1.0")
        self.assertEqual(report["no_go_reasons"], [])
        self.assertCheckOk(report, "release artifact:aarch64-apple-darwin")
        self.assertCheckOk(report, "release artifact:x86_64-apple-darwin")
        self.assertCheckOk(report, "homebrew formula")
        self.assertCheckOk(report, "launchagent current state")

    def test_fails_when_formula_checksum_does_not_match_release_checksum(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-audit-") as temp_dir:
            root = Path(temp_dir)
            release_dir = root / "dist"
            formula = root / "Formula" / "codex-obsidian-sync.rb"
            rust_binary = write_fake_binary(root / "bin" / "codex-obsidian-sync", "0.1.0")
            rollback_binary = write_fake_binary(root / "rollback" / "codex-obsidian-sync", "0.1.0")
            status_path = root / "status.json"
            plist_path = root / "agent.plist"
            monitor_dir = Path("/tmp") / f"codex-obsidian-sync-monitor-{os.getpid()}-{root.name}"
            checksums = write_release_artifacts(release_dir)
            checksums["x86_64-apple-darwin"] = "f" * 64
            write_formula(formula, checksums)
            write_status(status_path, plist_path)
            write_plist(plist_path, str(rollback_binary))

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_cutover_readiness.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                    "--expected-rust-binary",
                    str(rust_binary),
                    "--rollback-binary",
                    str(rollback_binary),
                    "--status-json-file",
                    str(status_path),
                    "--plist-file",
                    str(plist_path),
                    "--expected-current-program-arg0",
                    str(rollback_binary),
                    "--monitor-dir",
                    str(monitor_dir),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertIn(
            "homebrew formula: formula checksum mismatch for x86_64-apple-darwin",
            report["no_go_reasons"],
        )

    def test_fails_when_expected_rust_binary_reports_wrong_version(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-audit-") as temp_dir:
            root = Path(temp_dir)
            release_dir = root / "dist"
            formula = root / "Formula" / "codex-obsidian-sync.rb"
            rust_binary = write_fake_binary(root / "bin" / "codex-obsidian-sync", "0.2.0")
            rollback_binary = write_fake_binary(root / "rollback" / "codex-obsidian-sync", "0.1.0")
            status_path = root / "status.json"
            plist_path = root / "agent.plist"
            monitor_dir = Path("/tmp") / f"codex-obsidian-sync-monitor-{os.getpid()}-{root.name}"
            checksums = write_release_artifacts(release_dir)
            write_formula(formula, checksums)
            write_status(status_path, plist_path)
            write_plist(plist_path, str(rollback_binary))

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_cutover_readiness.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                    "--expected-rust-binary",
                    str(rust_binary),
                    "--rollback-binary",
                    str(rollback_binary),
                    "--status-json-file",
                    str(status_path),
                    "--plist-file",
                    str(plist_path),
                    "--expected-current-program-arg0",
                    str(rollback_binary),
                    "--monitor-dir",
                    str(monitor_dir),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "expected Rust binary: expected Rust binary version does not match release version",
            report["no_go_reasons"],
        )

    def assertCheckOk(self, report: dict[str, object], name: str) -> None:
        checks = report["checks"]
        self.assertIsInstance(checks, list)
        matches = [item for item in checks if item["name"] == name]
        self.assertEqual(len(matches), 1)
        self.assertTrue(matches[0]["ok"], matches[0]["no_go_reasons"])


def write_release_artifacts(release_dir: Path) -> dict[str, str]:
    release_dir.mkdir(parents=True)
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


def write_formula(path: Path, checksums: dict[str, str]) -> None:
    path.parent.mkdir(parents=True)
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


def write_fake_binary(path: Path, version: str) -> Path:
    path.parent.mkdir(parents=True)
    path.write_text(
        "\n".join(
            [
                "#!/bin/sh",
                'if [ "$1" = "--version" ]; then',
                f"  echo codex-obsidian-sync {version}",
                'elif [ "$1" = "--help" ]; then',
                "  echo usage",
                "else",
                "  echo unexpected >&2",
                "  exit 1",
                "fi",
                "",
            ]
        ),
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def write_status(path: Path, plist_path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "configured": True,
                "launchd_loaded": True,
                "plist_path": str(plist_path),
                "last_error": "none",
                "last_summary": {"processed": 12, "skipped_invalid": 0},
            }
        )
        + "\n",
        encoding="utf-8",
    )


def write_plist(path: Path, program_arg0: str) -> None:
    path.write_bytes(
        plistlib.dumps(
            {
                "Label": "com.codex.obsidian-sync",
                "ProgramArguments": [
                    program_arg0,
                    "--config",
                    "/tmp/config.toml",
                    "service-run",
                ],
            },
            fmt=plistlib.FMT_XML,
            sort_keys=False,
        )
    )


if __name__ == "__main__":
    unittest.main()
