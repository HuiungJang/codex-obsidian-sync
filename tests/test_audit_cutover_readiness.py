from __future__ import annotations

import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tarfile
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import audit_cutover_readiness


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
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)
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

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(report["ok"], report["no_go_reasons"])
        self.assertEqual(report["tag"], "v0.1.0")
        self.assertEqual(report["no_go_reasons"], [])
        self.assertCheckOk(report, "release artifact:aarch64-apple-darwin")
        self.assertCheckOk(report, "release artifact:x86_64-apple-darwin")
        self.assertCheckOk(report, "release directory contents")
        self.assertCheckOk(report, "homebrew formula")
        self.assertCheckOk(report, "release smoke summary:aarch64-apple-darwin")
        self.assertCheckOk(report, "release smoke summary:x86_64-apple-darwin")
        self.assertCheckOk(report, "homebrew smoke summary")
        self.assertCheckOk(report, "launchagent current state")

    def test_refuses_symlinked_output_report_path(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-audit-") as temp_dir:
            root = Path(temp_dir)
            output_target = root / "target-report.json"
            output = root / "cutover-readiness.json"
            output_target.write_text("keep\n", encoding="utf-8")
            output.symlink_to(output_target)

            with self.assertRaisesRegex(RuntimeError, "output path is a symlink"):
                audit_cutover_readiness.resolve_output_path(output)

            self.assertEqual(output_target.read_text(encoding="utf-8"), "keep\n")

    def test_relaxed_summary_paths_accepts_downloaded_release_evidence(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-audit-") as temp_dir:
            root = Path(temp_dir)
            release_dir = root / "downloaded-release"
            formula = root / "Formula" / "codex-obsidian-sync.rb"
            rust_binary = write_fake_binary(root / "bin" / "codex-obsidian-sync", "0.1.0")
            rollback_binary = write_fake_binary(root / "rollback" / "codex-obsidian-sync", "0.1.0")
            status_path = root / "status.json"
            plist_path = root / "agent.plist"
            monitor_dir = Path("/tmp") / f"codex-obsidian-sync-monitor-{os.getpid()}-{root.name}"
            checksums = write_release_artifacts(release_dir)
            write_formula(formula, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)
            rewrite_summary_paths_as_downloaded_evidence(release_dir)
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
                    "--relaxed-summary-paths",
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

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(report["ok"], report["no_go_reasons"])
        self.assertCheckOk(report, "release smoke summary:aarch64-apple-darwin")
        self.assertCheckOk(report, "release smoke summary:x86_64-apple-darwin")
        self.assertCheckOk(report, "homebrew smoke summary")

    def test_installed_rust_binary_smoke_accepts_real_config_dry_run(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-audit-") as temp_dir:
            root = Path(temp_dir)
            release_dir = root / "dist"
            formula = root / "Formula" / "codex-obsidian-sync.rb"
            rust_binary = write_fake_binary(root / "bin" / "codex-obsidian-sync", "0.1.0")
            installed_binary = write_fake_installed_binary(root / "installed" / "codex-obsidian-sync", "0.1.0")
            rollback_binary = write_fake_binary(root / "rollback" / "codex-obsidian-sync", "0.1.0")
            status_path = root / "status.json"
            plist_path = root / "agent.plist"
            real_launchagent_summary = root / "real-launchagent-summary.json"
            config_path = root / "config.toml"
            codex_home = root / ".codex"
            vault = root / "vault"
            installed_output = Path("/tmp") / f"codex-obsidian-sync-installed-smoke-{os.getpid()}-{root.name}"
            monitor_dir = Path("/tmp") / f"codex-obsidian-sync-monitor-{os.getpid()}-{root.name}"
            codex_home.mkdir()
            write_installed_smoke_config(config_path, codex_home, vault)
            checksums = write_release_artifacts(release_dir)
            write_formula(formula, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)
            write_status(status_path, plist_path)
            write_plist(plist_path, str(rollback_binary))
            write_real_launchagent_smoke_summary(real_launchagent_summary, installed_binary, config_path)

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
                    "--installed-rust-binary",
                    str(installed_binary),
                    "--installed-smoke-config",
                    str(config_path),
                    "--installed-smoke-output-dir",
                    str(installed_output),
                    "--cleanup-installed-smoke-output",
                    "--real-launchagent-dry-run-summary",
                    str(real_launchagent_summary),
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

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(report["ok"], report["no_go_reasons"])
        self.assertCheckOk(report, "installed Rust binary smoke")
        self.assertCheckOk(report, "real LaunchAgent dry-run smoke summary")
        self.assertFalse(installed_output.exists())

    def test_real_launchagent_smoke_summary_failure_blocks_readiness(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-audit-") as temp_dir:
            root = Path(temp_dir)
            release_dir = root / "dist"
            formula = root / "Formula" / "codex-obsidian-sync.rb"
            rust_binary = write_fake_binary(root / "bin" / "codex-obsidian-sync", "0.1.0")
            installed_binary = write_fake_installed_binary(root / "installed" / "codex-obsidian-sync", "0.1.0")
            rollback_binary = write_fake_binary(root / "rollback" / "codex-obsidian-sync", "0.1.0")
            status_path = root / "status.json"
            plist_path = root / "agent.plist"
            real_launchagent_summary = root / "real-launchagent-summary.json"
            config_path = root / "config.toml"
            codex_home = root / ".codex"
            vault = root / "vault"
            installed_output = Path("/tmp") / f"codex-obsidian-sync-installed-smoke-{os.getpid()}-{root.name}"
            monitor_dir = Path("/tmp") / f"codex-obsidian-sync-monitor-{os.getpid()}-{root.name}"
            codex_home.mkdir()
            write_installed_smoke_config(config_path, codex_home, vault)
            checksums = write_release_artifacts(release_dir)
            write_formula(formula, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)
            write_status(status_path, plist_path)
            write_plist(plist_path, str(rollback_binary))
            write_real_launchagent_smoke_summary(
                real_launchagent_summary,
                installed_binary,
                config_path,
                ok=False,
                no_go_reasons=["sync-once dry-run timed out"],
                timeout_diagnosis=(
                    "sync-once timed out while opening an existing vault note; on macOS, "
                    "grant Full Disk Access to the binary path recorded in details.binary and rerun this smoke."
                ),
                read_trace_tail=[
                    '{"event":"read_attempt","source":"vault","relative_path":"Codex/Daily/2026-05-14.md"}',
                ],
            )

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
                    "--installed-rust-binary",
                    str(installed_binary),
                    "--installed-smoke-config",
                    str(config_path),
                    "--installed-smoke-output-dir",
                    str(installed_output),
                    "--cleanup-installed-smoke-output",
                    "--real-launchagent-dry-run-summary",
                    str(real_launchagent_summary),
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
            "real LaunchAgent dry-run smoke summary: summary ok is not true",
            report["no_go_reasons"],
        )
        self.assertIn(
            "real LaunchAgent dry-run smoke summary: summary contains no-go reasons",
            report["no_go_reasons"],
        )
        self.assertIn(
            "real LaunchAgent dry-run smoke summary: summary timeout diagnosis indicates Full Disk Access is required",
            report["no_go_reasons"],
        )
        smoke_check = self.getCheck(report, "real LaunchAgent dry-run smoke summary")
        self.assertIn("Full Disk Access", smoke_check["details"]["timeout_diagnosis"])
        self.assertEqual(
            smoke_check["details"]["read_trace_tail"],
            ['{"event":"read_attempt","source":"vault","relative_path":"Codex/Daily/2026-05-14.md"}'],
        )

    def test_real_launchagent_smoke_summary_requires_signing_evidence(self) -> None:
        cases = [
            (
                "missing codesign",
                {"include_codesign": False},
                "summary codesign evidence is missing",
            ),
            (
                "unexpected expected identifier",
                {
                    "expected_signing_identifier": "codex_obsidian_sync_rs-random",
                    "codesign_identifier": "codex_obsidian_sync_rs-random",
                },
                "summary expected signing identifier does not match LaunchAgent identifier",
            ),
            (
                "unexpected codesign identifier",
                {"codesign_identifier": "codex_obsidian_sync_rs-random"},
                "summary codesign identifier does not match expected signing identifier",
            ),
            (
                "unchecked codesign",
                {"codesign_checked": False},
                "summary codesign was not checked",
            ),
        ]
        for name, summary_kwargs, reason in cases:
            with self.subTest(name=name):
                with TemporaryDirectory(prefix="codex-obsidian-sync-audit-") as temp_dir:
                    root = Path(temp_dir)
                    installed_binary = root / "installed" / "codex-obsidian-sync"
                    config_path = root / "config.toml"
                    summary_path = root / "real-launchagent-summary.json"
                    write_real_launchagent_smoke_summary(
                        summary_path,
                        installed_binary,
                        config_path,
                        **summary_kwargs,
                    )

                    result = audit_cutover_readiness.audit_real_launchagent_dry_run_summary(
                        summary_path,
                        str(installed_binary),
                        config_path,
                    )

                self.assertFalse(result["ok"])
                self.assertIn(reason, result["no_go_reasons"])

    def test_installed_rust_binary_smoke_fails_when_vault_codex_path_is_not_directory(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-audit-") as temp_dir:
            root = Path(temp_dir)
            release_dir = root / "dist"
            formula = root / "Formula" / "codex-obsidian-sync.rb"
            rust_binary = write_fake_binary(root / "bin" / "codex-obsidian-sync", "0.1.0")
            installed_binary = write_fake_installed_binary(root / "installed" / "codex-obsidian-sync", "0.1.0")
            rollback_binary = write_fake_binary(root / "rollback" / "codex-obsidian-sync", "0.1.0")
            status_path = root / "status.json"
            plist_path = root / "agent.plist"
            config_path = root / "config.toml"
            codex_home = root / ".codex"
            vault = root / "vault"
            installed_output = Path("/tmp") / f"codex-obsidian-sync-installed-smoke-{os.getpid()}-{root.name}"
            monitor_dir = Path("/tmp") / f"codex-obsidian-sync-monitor-{os.getpid()}-{root.name}"
            codex_home.mkdir()
            write_installed_smoke_config(config_path, codex_home, vault)
            (vault / "Codex").rmdir()
            (vault / "Codex").write_text("not a directory\n", encoding="utf-8")
            checksums = write_release_artifacts(release_dir)
            write_formula(formula, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)
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
                    "--installed-rust-binary",
                    str(installed_binary),
                    "--installed-smoke-config",
                    str(config_path),
                    "--installed-smoke-output-dir",
                    str(installed_output),
                    "--cleanup-installed-smoke-output",
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
            "installed Rust binary smoke: installed smoke vault Codex path is not a directory",
            report["no_go_reasons"],
        )
        self.assertFalse(installed_output.exists())

    def test_installed_rust_binary_smoke_fails_when_real_config_dry_run_fails(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-audit-") as temp_dir:
            root = Path(temp_dir)
            release_dir = root / "dist"
            formula = root / "Formula" / "codex-obsidian-sync.rb"
            rust_binary = write_fake_binary(root / "bin" / "codex-obsidian-sync", "0.1.0")
            installed_binary = write_fake_installed_binary(
                root / "installed" / "codex-obsidian-sync",
                "0.1.0",
                fail_sync=True,
            )
            rollback_binary = write_fake_binary(root / "rollback" / "codex-obsidian-sync", "0.1.0")
            status_path = root / "status.json"
            plist_path = root / "agent.plist"
            config_path = root / "config.toml"
            codex_home = root / ".codex"
            vault = root / "vault"
            installed_output = Path("/tmp") / f"codex-obsidian-sync-installed-smoke-{os.getpid()}-{root.name}"
            monitor_dir = Path("/tmp") / f"codex-obsidian-sync-monitor-{os.getpid()}-{root.name}"
            codex_home.mkdir()
            write_installed_smoke_config(config_path, codex_home, vault)
            checksums = write_release_artifacts(release_dir)
            write_formula(formula, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)
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
                    "--installed-rust-binary",
                    str(installed_binary),
                    "--installed-smoke-config",
                    str(config_path),
                    "--installed-smoke-output-dir",
                    str(installed_output),
                    "--cleanup-installed-smoke-output",
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
            "installed Rust binary smoke: installed sync-once dry-run failed",
            report["no_go_reasons"],
        )
        self.assertFalse(installed_output.exists())

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
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)
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
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)
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

    def test_fails_when_release_smoke_summary_is_missing(self) -> None:
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
            write_homebrew_smoke_summary(release_dir, formula)
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
            "release smoke summary:aarch64-apple-darwin: summary file is missing",
            report["no_go_reasons"],
        )

    def test_fails_when_release_smoke_counter_is_boolean(self) -> None:
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
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)
            write_status(status_path, plist_path)
            write_plist(plist_path, str(rollback_binary))
            summary_path = release_dir / "codex-obsidian-sync-aarch64-apple-darwin.smoke-summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            summary["note_files"] = True
            summary_path.write_text(json.dumps(summary) + "\n", encoding="utf-8")

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
            "release smoke summary:aarch64-apple-darwin: release smoke note_files is not positive",
            report["no_go_reasons"],
        )

    def test_fails_when_homebrew_smoke_version_does_not_match_release(self) -> None:
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
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula, version="codex-obsidian-sync 0.2.0")
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
            "homebrew smoke summary: Homebrew smoke version does not match release version",
            report["no_go_reasons"],
        )

    def test_fails_when_homebrew_smoke_version_command_output_does_not_match_release(self) -> None:
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
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(
                release_dir,
                formula,
                version_command_stdout="codex-obsidian-sync 0.2.0\n",
            )
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
            "homebrew smoke summary: Homebrew smoke did not record expected installed binary version output",
            report["no_go_reasons"],
        )

    def test_fails_when_monitor_dir_contains_stale_records_before_cutover(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-audit-") as temp_dir:
            with TemporaryDirectory(prefix="codex-obsidian-sync-monitor-", dir="/tmp") as monitor_temp:
                root = Path(temp_dir)
                release_dir = root / "dist"
                formula = root / "Formula" / "codex-obsidian-sync.rb"
                rust_binary = write_fake_binary(root / "bin" / "codex-obsidian-sync", "0.1.0")
                rollback_binary = write_fake_binary(root / "rollback" / "codex-obsidian-sync", "0.1.0")
                status_path = root / "status.json"
                plist_path = root / "agent.plist"
                monitor_dir = Path(monitor_temp)
                checksums = write_release_artifacts(release_dir)
                write_formula(formula, checksums)
                write_release_smoke_summaries(release_dir)
                write_homebrew_smoke_summary(release_dir, formula)
                write_status(status_path, plist_path)
                write_plist(plist_path, str(rollback_binary))
                (monitor_dir / "plus5m.json").write_text(
                    json.dumps({"ok": True, "checkpoint": "+5m"}) + "\n",
                    encoding="utf-8",
                )

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
            "monitor prerequisites: monitor dir already contains JSON records: ['plus5m.json']",
            report["no_go_reasons"],
        )

    def test_fails_when_monitor_dir_contains_stale_non_json_entry_before_cutover(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-audit-") as temp_dir:
            with TemporaryDirectory(prefix="codex-obsidian-sync-monitor-", dir="/tmp") as monitor_temp:
                root = Path(temp_dir)
                release_dir = root / "dist"
                formula = root / "Formula" / "codex-obsidian-sync.rb"
                rust_binary = write_fake_binary(root / "bin" / "codex-obsidian-sync", "0.1.0")
                rollback_binary = write_fake_binary(root / "rollback" / "codex-obsidian-sync", "0.1.0")
                status_path = root / "status.json"
                plist_path = root / "agent.plist"
                monitor_dir = Path(monitor_temp)
                checksums = write_release_artifacts(release_dir)
                write_formula(formula, checksums)
                write_release_smoke_summaries(release_dir)
                write_homebrew_smoke_summary(release_dir, formula)
                write_status(status_path, plist_path)
                write_plist(plist_path, str(rollback_binary))
                (monitor_dir / "latest-status.stdout").write_text("{}\n", encoding="utf-8")

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
            "monitor prerequisites: monitor dir contains unexpected entries: ['latest-status.stdout']",
            report["no_go_reasons"],
        )

    def test_fails_when_monitor_dir_marker_is_symlink_before_cutover(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-audit-") as temp_dir:
            with TemporaryDirectory(prefix="codex-obsidian-sync-monitor-", dir="/tmp") as monitor_temp:
                root = Path(temp_dir)
                release_dir = root / "dist"
                formula = root / "Formula" / "codex-obsidian-sync.rb"
                rust_binary = write_fake_binary(root / "bin" / "codex-obsidian-sync", "0.1.0")
                rollback_binary = write_fake_binary(root / "rollback" / "codex-obsidian-sync", "0.1.0")
                status_path = root / "status.json"
                plist_path = root / "agent.plist"
                monitor_dir = Path(monitor_temp)
                checksums = write_release_artifacts(release_dir)
                write_formula(formula, checksums)
                write_release_smoke_summaries(release_dir)
                write_homebrew_smoke_summary(release_dir, formula)
                write_status(status_path, plist_path)
                write_plist(plist_path, str(rollback_binary))
                marker_target = monitor_dir / "marker-target"
                marker_target.write_text("managed by record_cutover_monitor.py\n", encoding="utf-8")
                (monitor_dir / ".codex-obsidian-sync-cutover-monitor").symlink_to(marker_target)

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
        self.assertIn("monitor prerequisites: monitor dir marker is a symlink", report["no_go_reasons"])

    def test_fails_when_monitor_dir_is_symlink_before_cutover(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-audit-") as temp_dir:
            with TemporaryDirectory(prefix="codex-obsidian-sync-monitor-target-", dir="/tmp") as monitor_temp:
                root = Path(temp_dir)
                release_dir = root / "dist"
                formula = root / "Formula" / "codex-obsidian-sync.rb"
                rust_binary = write_fake_binary(root / "bin" / "codex-obsidian-sync", "0.1.0")
                rollback_binary = write_fake_binary(root / "rollback" / "codex-obsidian-sync", "0.1.0")
                status_path = root / "status.json"
                plist_path = root / "agent.plist"
                monitor_dir = Path("/tmp") / f"codex-obsidian-sync-monitor-{os.getpid()}-{root.name}"
                target_dir = Path(monitor_temp)
                checksums = write_release_artifacts(release_dir)
                write_formula(formula, checksums)
                write_release_smoke_summaries(release_dir)
                write_homebrew_smoke_summary(release_dir, formula)
                write_status(status_path, plist_path)
                write_plist(plist_path, str(rollback_binary))
                monitor_dir.symlink_to(target_dir, target_is_directory=True)

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
                monitor_dir.unlink()

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertIn("monitor prerequisites: monitor dir is a symlink", report["no_go_reasons"])

    def test_fails_when_release_dir_contains_unexpected_file(self) -> None:
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
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)
            write_status(status_path, plist_path)
            write_plist(plist_path, str(rollback_binary))
            (release_dir / "debug.log").write_text("unexpected\n", encoding="utf-8")

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
            "release directory contents: release directory contains unexpected files: ['debug.log']",
            report["no_go_reasons"],
        )

    def test_fails_when_release_dir_contains_unexpected_directory(self) -> None:
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
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)
            write_status(status_path, plist_path)
            write_plist(plist_path, str(rollback_binary))
            (release_dir / "scratch").mkdir()

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
            "release directory contents: release directory contains unexpected directories: ['scratch']",
            report["no_go_reasons"],
        )

    def test_fails_when_release_dir_contains_symlinked_artifact(self) -> None:
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
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)
            write_status(status_path, plist_path)
            write_plist(plist_path, str(rollback_binary))
            package = release_dir / "codex-obsidian-sync-aarch64-apple-darwin.tar.gz"
            target = root / "outside-aarch64.tar.gz"
            target.write_bytes(package.read_bytes())
            package.unlink()
            package.symlink_to(target)

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
            "release directory contents: release directory contains symlinks: "
            "['codex-obsidian-sync-aarch64-apple-darwin.tar.gz']",
            report["no_go_reasons"],
        )

    def test_fails_when_release_dir_is_symlink(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-audit-") as temp_dir:
            root = Path(temp_dir)
            release_target = root / "dist-target"
            release_dir = root / "dist"
            formula = root / "Formula" / "codex-obsidian-sync.rb"
            rust_binary = write_fake_binary(root / "bin" / "codex-obsidian-sync", "0.1.0")
            rollback_binary = write_fake_binary(root / "rollback" / "codex-obsidian-sync", "0.1.0")
            status_path = root / "status.json"
            plist_path = root / "agent.plist"
            monitor_dir = Path("/tmp") / f"codex-obsidian-sync-monitor-{os.getpid()}-{root.name}"
            checksums = write_release_artifacts(release_target)
            write_formula(formula, checksums)
            write_release_smoke_summaries(release_target)
            write_homebrew_smoke_summary(release_target, formula)
            write_status(status_path, plist_path)
            write_plist(plist_path, str(rollback_binary))
            release_dir.symlink_to(release_target, target_is_directory=True)

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
        self.assertIn("release artifact:aarch64-apple-darwin: release directory is a symlink", report["no_go_reasons"])
        self.assertIn("release directory contents: release directory is a symlink", report["no_go_reasons"])

    def test_fails_when_homebrew_formula_is_symlink(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-audit-") as temp_dir:
            root = Path(temp_dir)
            release_dir = root / "dist"
            formula_target = root / "FormulaTarget" / "codex-obsidian-sync.rb"
            formula = root / "FormulaLink" / "codex-obsidian-sync.rb"
            rust_binary = write_fake_binary(root / "bin" / "codex-obsidian-sync", "0.1.0")
            rollback_binary = write_fake_binary(root / "rollback" / "codex-obsidian-sync", "0.1.0")
            status_path = root / "status.json"
            plist_path = root / "agent.plist"
            monitor_dir = Path("/tmp") / f"codex-obsidian-sync-monitor-{os.getpid()}-{root.name}"
            checksums = write_release_artifacts(release_dir)
            write_formula(formula_target, checksums)
            formula.parent.mkdir(parents=True)
            formula.symlink_to(formula_target)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)
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
        self.assertIn("homebrew formula: Homebrew formula is a symlink", report["no_go_reasons"])

    def test_fails_when_status_snapshot_file_is_symlink(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-audit-") as temp_dir:
            root = Path(temp_dir)
            release_dir = root / "dist"
            formula = root / "Formula" / "codex-obsidian-sync.rb"
            rust_binary = write_fake_binary(root / "bin" / "codex-obsidian-sync", "0.1.0")
            rollback_binary = write_fake_binary(root / "rollback" / "codex-obsidian-sync", "0.1.0")
            status_path = root / "status.json"
            status_target = root / "status-target.json"
            plist_path = root / "agent.plist"
            monitor_dir = Path("/tmp") / f"codex-obsidian-sync-monitor-{os.getpid()}-{root.name}"
            checksums = write_release_artifacts(release_dir)
            write_formula(formula, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)
            write_status(status_target, plist_path)
            status_path.symlink_to(status_target)
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
        self.assertIn("status snapshot: evidence file is a symlink: " + str(status_path), report["no_go_reasons"])

    def test_fails_when_plist_file_is_symlink(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-audit-") as temp_dir:
            root = Path(temp_dir)
            release_dir = root / "dist"
            formula = root / "Formula" / "codex-obsidian-sync.rb"
            rust_binary = write_fake_binary(root / "bin" / "codex-obsidian-sync", "0.1.0")
            rollback_binary = write_fake_binary(root / "rollback" / "codex-obsidian-sync", "0.1.0")
            status_path = root / "status.json"
            plist_path = root / "agent.plist"
            plist_target = root / "agent-target.plist"
            monitor_dir = Path("/tmp") / f"codex-obsidian-sync-monitor-{os.getpid()}-{root.name}"
            checksums = write_release_artifacts(release_dir)
            write_formula(formula, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)
            write_status(status_path, plist_path)
            write_plist(plist_target, str(rollback_binary))
            plist_path.symlink_to(plist_target)

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
        self.assertIn("launchagent plist: evidence file is a symlink: " + str(plist_path), report["no_go_reasons"])

    def test_fails_when_status_loaded_flag_is_not_boolean(self) -> None:
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
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)
            write_status(status_path, plist_path)
            status = json.loads(status_path.read_text(encoding="utf-8"))
            status["launchd_loaded"] = "true"
            status_path.write_text(json.dumps(status) + "\n", encoding="utf-8")
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
        self.assertIn("status snapshot: status launchd_loaded is not boolean", report["no_go_reasons"])
        self.assertIn("launchagent current state: status launchd_loaded is not boolean true", report["no_go_reasons"])

    def test_fails_when_current_status_label_is_unexpected(self) -> None:
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
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)
            write_status(status_path, plist_path, label="com.codex.obsidian-sync.other")
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
            "launchagent current state: status launchd_label does not match expected label",
            report["no_go_reasons"],
        )

    def test_fails_when_current_plist_label_is_unexpected(self) -> None:
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
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)
            write_status(status_path, plist_path)
            write_plist(plist_path, str(rollback_binary), label="com.codex.obsidian-sync.other")

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
            "launchagent current state: LaunchAgent Label does not match expected label",
            report["no_go_reasons"],
        )

    def test_fails_when_current_plist_missing_config_argument(self) -> None:
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
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)
            write_status(status_path, plist_path)
            write_plist(plist_path, str(rollback_binary), include_config=False)

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
            "launchagent current state: LaunchAgent ProgramArguments must include --config before service-run",
            report["no_go_reasons"],
        )

    def test_fails_when_current_program_arg0_is_relative(self) -> None:
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
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)
            write_status(status_path, plist_path)
            write_plist(plist_path, "codex-obsidian-sync")

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
            "launchagent current state: ProgramArguments[0] is not an absolute path",
            report["no_go_reasons"],
        )

    def test_fails_when_current_status_plist_path_is_relative(self) -> None:
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
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)
            write_status(status_path, Path("Library/LaunchAgents/com.codex.obsidian-sync.plist"))
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
            "launchagent current state: status plist_path is not absolute",
            report["no_go_reasons"],
        )

    def test_fails_when_current_config_path_is_relative(self) -> None:
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
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)
            write_status(status_path, plist_path)
            write_plist(plist_path, str(rollback_binary), config_path="relative-config.toml")

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
            "launchagent current state: LaunchAgent --config path is not absolute",
            report["no_go_reasons"],
        )

    def test_fails_when_status_config_path_is_relative(self) -> None:
        result = audit_cutover_readiness.audit_launchagent(
            status=launchagent_status(config_path="relative-config.toml"),
            plist=launchagent_plist(),
            expected_current_program_arg0="/tmp/codex-obsidian-sync",
        )

        self.assertFalse(result["ok"])
        self.assertIn(
            "status config_path is not absolute",
            result["no_go_reasons"],
        )

    def test_fails_when_status_config_path_differs_from_launchagent_config(self) -> None:
        result = audit_cutover_readiness.audit_launchagent(
            status=launchagent_status(config_path="/tmp/other-config.toml"),
            plist=launchagent_plist(config_path="/tmp/config.toml"),
            expected_current_program_arg0="/tmp/codex-obsidian-sync",
        )

        self.assertFalse(result["ok"])
        self.assertIn(
            "status config_path does not match LaunchAgent --config path",
            result["no_go_reasons"],
        )

    def assertCheckOk(self, report: dict[str, object], name: str) -> None:
        match = self.getCheck(report, name)
        self.assertTrue(match["ok"], match["no_go_reasons"])

    def getCheck(self, report: dict[str, object], name: str) -> dict[str, object]:
        checks = report["checks"]
        self.assertIsInstance(checks, list)
        matches = [item for item in checks if item["name"] == name]
        self.assertEqual(len(matches), 1)
        return matches[0]


def launchagent_status(*, config_path: str = "/tmp/config.toml") -> dict[str, object]:
    return {
        "configured": True,
        "launchd_label": "com.codex.obsidian-sync",
        "launchd_loaded": True,
        "plist_path": "/tmp/com.codex.obsidian-sync.plist",
        "config_path": config_path,
        "last_error": "none",
    }


def launchagent_plist(
    *,
    program_arg0: str = "/tmp/codex-obsidian-sync",
    config_path: str = "/tmp/config.toml",
) -> dict[str, object]:
    return {
        "Label": "com.codex.obsidian-sync",
        "ProgramArguments": [program_arg0, "--config", config_path, "service-run"],
    }


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
        shutil.rmtree(source_dir)
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
                "",
                "  test do",
                '    assert_match "codex-obsidian-sync #{version}", shell_output("#{bin}/codex-obsidian-sync --version")',
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


def write_fake_installed_binary(path: Path, version: str, *, fail_sync: bool = False) -> Path:
    path.parent.mkdir(parents=True)
    path.write_text(
        f"""#!{sys.executable}
import json
import sys
from pathlib import Path

VERSION = {version!r}
FAIL_SYNC = {fail_sync!r}

args = sys.argv[1:]
if args == ["--version"]:
    print(f"codex-obsidian-sync {{VERSION}}")
elif len(args) == 4 and args[0] == "--config" and args[2:] == ["status", "--json"]:
    print(json.dumps({{"configured": True, "launchd_loaded": True}}))
elif len(args) == 5 and args[0] == "inspect-recent" and args[1] == "--codex-home" and args[3] == "--limit":
    print(json.dumps([{{"id": "session"}}]))
elif len(args) == 5 and args[0] == "--config" and args[2:4] == ["sync-once", "--dry-run-output"]:
    output = Path(args[4])
    (output / "Codex").mkdir(parents=True, exist_ok=True)
    (output / "Codex" / "note.md").write_text("# Note\\n", encoding="utf-8")
    if FAIL_SYNC:
        print("dry-run output error", file=sys.stderr)
        raise SystemExit(1)
    (output / "sync-state.json").write_text(json.dumps({{"files": {{}}}}) + "\\n", encoding="utf-8")
    print(json.dumps({{"dry_run": True, "processed": 1, "planned_writes": 2, "temp_state_file": str(output / "sync-state.json")}}))
else:
    print("unexpected", args, file=sys.stderr)
    raise SystemExit(1)
""",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def write_installed_smoke_config(path: Path, codex_home: Path, vault: Path) -> None:
    vault.mkdir()
    (vault / "Codex").mkdir()
    state_file = path.parent / "state.json"
    state_file.write_text(json.dumps({"files": {}}) + "\n", encoding="utf-8")
    path.write_text(
        "\n".join(
            [
                f'vault = "{vault}"',
                f'codex_home = "{codex_home}"',
                f'state_file = "{state_file}"',
                f'lock_file = "{path.parent / "sync.lock"}"',
                "",
            ]
        ),
        encoding="utf-8",
    )


def write_real_launchagent_smoke_summary(
    path: Path,
    binary: Path,
    config: Path,
    *,
    ok: bool = True,
    no_go_reasons: list[str] | None = None,
    expected_signing_identifier: str | None = "com.codex.obsidian-sync",
    include_codesign: bool = True,
    codesign_checked: bool = True,
    codesign_identifier: str | None = "com.codex.obsidian-sync",
    codesign_signature: str | None = "adhoc",
    timeout_diagnosis: str | None = None,
    read_trace_tail: list[str] | None = None,
) -> None:
    details = {
        "binary": str(binary),
        "config": str(config),
        "label": "com.codex.obsidian-sync.real-dry-run-smoke.test",
        "expected_signing_identifier": expected_signing_identifier,
        "dry_run": True,
        "processed": 1,
        "note_files": 1,
        "temp_state_exists": True,
        "stdout_parseable_json": True,
        "loaded_after_bootout": False,
        "dry_run_output_exists_after_cleanup": False,
        "timeout_diagnosis": timeout_diagnosis,
        "read_trace_tail": read_trace_tail or [],
        "sample_excerpt": [],
    }
    if include_codesign:
        details["codesign"] = {
            "checked": codesign_checked,
            "identifier": codesign_identifier,
            "signature": codesign_signature,
            "team_identifier": "not set",
            "cdhash": "abc123",
        }
    path.write_text(
        json.dumps(
            {
                "ok": ok,
                "generated_at": "2026-05-17T00:00:00+00:00",
                "details": details,
                "no_go_reasons": no_go_reasons or [],
            }
        )
        + "\n",
        encoding="utf-8",
    )


def write_release_smoke_summaries(release_dir: Path) -> None:
    for target in ("aarch64-apple-darwin", "x86_64-apple-darwin"):
        installed_binary = "/tmp/codex-obsidian-sync/bin/codex-obsidian-sync"
        tarball = release_dir / f"codex-obsidian-sync-{target}.tar.gz"
        checksum = release_dir / f"codex-obsidian-sync-{target}.tar.gz.sha256"
        (release_dir / f"codex-obsidian-sync-{target}.smoke-summary.json").write_text(
            json.dumps(
                {
                    "ok": True,
                    "generated_at": "2026-05-16T00:00:00+00:00",
                    "tarball": str(tarball),
                    "tarball_sha256": hashlib.sha256(tarball.read_bytes()).hexdigest(),
                    "checksum": str(checksum),
                    "checksum_sha256": hashlib.sha256(checksum.read_bytes()).hexdigest(),
                    "installed_binary": installed_binary,
                    "codesign": {
                        "checked": True,
                        "identifier": "com.codex.obsidian-sync",
                        "signature": "adhoc",
                        "team_identifier": "not set",
                        "cdhash": "abc123",
                    },
                    "version": "codex-obsidian-sync 0.1.0",
                    "status_configured": True,
                    "status_json_parsed": True,
                    "inspect_count": 1,
                    "dry_run": True,
                    "processed": 1,
                    "vault_unchanged": True,
                    "uninstalled": True,
                    "installed_after": False,
                    "note_files": 1,
                    "commands": [
                        {
                            "command": [installed_binary, "--version"],
                            "returncode": 0,
                            "stdout": "codex-obsidian-sync 0.1.0\n",
                        },
                        {
                            "command": [
                                installed_binary,
                                "--config",
                                "/tmp/config.toml",
                                "status",
                                "--json",
                            ],
                            "returncode": 0,
                            "stdout": json.dumps({"configured": True}) + "\n",
                        },
                        {
                            "command": [
                                installed_binary,
                                "inspect-recent",
                                "--codex-home",
                                "/tmp/.codex",
                                "--limit",
                                "3",
                            ],
                            "returncode": 0,
                            "stdout": json.dumps([{"id": "session"}]) + "\n",
                        },
                        {
                            "command": [
                                installed_binary,
                                "--config",
                                "/tmp/config.toml",
                                "sync-once",
                                "--dry-run-output",
                                "/tmp/output",
                            ],
                            "returncode": 0,
                            "stdout": json.dumps({"dry_run": True, "processed": 1}) + "\n",
                        },
                    ],
                }
            )
            + "\n",
            encoding="utf-8",
        )


def write_homebrew_smoke_summary(
    release_dir: Path,
    formula: Path,
    *,
    version: str = "codex-obsidian-sync 0.1.0",
    version_command_stdout: str = "codex-obsidian-sync 0.1.0\n",
) -> None:
    installed_binary = "/tmp/codex-obsidian-sync/bin/codex-obsidian-sync"
    (release_dir / "homebrew-smoke-summary.json").write_text(
        json.dumps(
            {
                "ok": True,
                "generated_at": "2026-05-16T00:00:00+00:00",
                "formula": str(formula.resolve()),
                "formula_sha256": hashlib.sha256(formula.resolve().read_bytes()).hexdigest(),
                "expected_version": "0.1.0",
                "version": version,
                "installed_binary": installed_binary,
                "installed_after": False,
                "status_configured": True,
                "status_json_parsed": True,
                "dry_run": True,
                "processed": 1,
                "vault_unchanged": True,
                "note_files": 1,
                "commands": [
                    {"command": ["brew", "list", "--formula", "codex-obsidian-sync"], "returncode": 1},
                    {"command": ["brew", "install", "--formula", str(formula.resolve())], "returncode": 0},
                    {
                        "command": ["brew", "--prefix", "codex-obsidian-sync"],
                        "returncode": 0,
                        "stdout": "/tmp/codex-obsidian-sync\n",
                    },
                    {
                        "command": [installed_binary, "--version"],
                        "returncode": 0,
                        "stdout": version_command_stdout,
                    },
                    {
                        "command": [
                            installed_binary,
                            "--config",
                            "/tmp/config.toml",
                            "status",
                            "--json",
                        ],
                        "returncode": 0,
                        "stdout": json.dumps({"configured": True}) + "\n",
                    },
                    {
                        "command": [
                            installed_binary,
                            "--config",
                            "/tmp/config.toml",
                            "sync-once",
                            "--dry-run-output",
                            "/tmp/output",
                        ],
                        "returncode": 0,
                        "stdout": json.dumps({"dry_run": True, "processed": 1}) + "\n",
                    },
                    {"command": ["brew", "test", "codex-obsidian-sync"], "returncode": 0},
                    {"command": ["brew", "uninstall", "--formula", "codex-obsidian-sync"], "returncode": 0},
                    {"command": ["brew", "list", "--formula", "codex-obsidian-sync"], "returncode": 1},
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )


def rewrite_summary_paths_as_downloaded_evidence(release_dir: Path) -> None:
    for target in ("aarch64-apple-darwin", "x86_64-apple-darwin"):
        summary_path = release_dir / f"codex-obsidian-sync-{target}.smoke-summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary["tarball"] = f"/runner/dist/codex-obsidian-sync-{target}.tar.gz"
        summary["checksum"] = f"/runner/dist/codex-obsidian-sync-{target}.tar.gz.sha256"
        summary_path.write_text(json.dumps(summary) + "\n", encoding="utf-8")

    homebrew_path = release_dir / "homebrew-smoke-summary.json"
    summary = json.loads(homebrew_path.read_text(encoding="utf-8"))
    install_formula = "codex-smoke/codex-obsidian-sync-smoke-test/codex-obsidian-sync"
    summary["formula"] = "/runner/dist/codex-obsidian-sync.rb"
    summary["install_formula"] = install_formula
    summary["install_formula_path"] = "/runner/tap/Formula/codex-obsidian-sync.rb"
    summary["install_formula_sha256"] = summary["formula_sha256"]
    for command in summary.get("commands", []):
        command_value = command.get("command") if isinstance(command, dict) else None
        if (
            isinstance(command_value, list)
            and len(command_value) >= 4
            and command_value[:3] == ["brew", "install", "--formula"]
        ):
            command_value[3] = install_formula
    homebrew_path.write_text(json.dumps(summary) + "\n", encoding="utf-8")


def write_status(
    path: Path,
    plist_path: Path,
    *,
    label: str = "com.codex.obsidian-sync",
    config_path: str = "/tmp/config.toml",
) -> None:
    path.write_text(
        json.dumps(
            {
                "configured": True,
                "launchd_label": label,
                "launchd_loaded": True,
                "plist_path": str(plist_path),
                "config_path": config_path,
                "last_error": "none",
                "last_summary": {"processed": 12, "skipped_invalid": 0},
            }
        )
        + "\n",
        encoding="utf-8",
    )


def write_plist(
    path: Path,
    program_arg0: str,
    *,
    label: str = "com.codex.obsidian-sync",
    config_path: str = "/tmp/config.toml",
    include_config: bool = True,
) -> None:
    program_arguments = [program_arg0]
    if include_config:
        program_arguments.extend(["--config", config_path])
    program_arguments.append("service-run")
    path.write_bytes(
        plistlib.dumps(
            {
                "Label": label,
                "ProgramArguments": program_arguments,
            },
            fmt=plistlib.FMT_XML,
            sort_keys=False,
        )
    )


if __name__ == "__main__":
    unittest.main()
