from __future__ import annotations

import json
import plistlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import audit_service_cutover


class AuditServiceCutoverTests(unittest.TestCase):
    def test_reports_ready_from_python_stopped_and_rust_started_evidence(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-service-cutover-") as temp_dir:
            root = Path(temp_dir)
            python_binary = "/Users/test/.local/bin/codex-obsidian-sync"
            rust_binary = "/opt/homebrew/bin/codex-obsidian-sync"
            files = write_cutover_files(root, python_binary=python_binary, rust_binary=rust_binary)

            result = run_audit(files, python_binary, rust_binary)
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(report["ok"], report["no_go_reasons"])
        self.assertEqual(report["snapshots"]["pre"]["plist_label"], "com.codex.obsidian-sync")
        self.assertEqual(
            report["snapshots"]["pre"]["program_arguments"],
            [python_binary, "--config", "/tmp/config.toml", "service-run"],
        )
        self.assertEqual(report["snapshots"]["pre"]["program_arguments_count"], 4)
        self.assertEqual(report["snapshots"]["pre"]["program_arg0"], python_binary)
        self.assertFalse(report["snapshots"]["stopped"]["launchd_loaded"])
        self.assertEqual(report["snapshots"]["post"]["program_arg0"], rust_binary)

    def test_reports_ready_from_python_module_launchagent_shape(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-service-cutover-") as temp_dir:
            root = Path(temp_dir)
            python_binary = "/Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13"
            rust_binary = "/opt/homebrew/bin/codex-obsidian-sync"
            files = write_cutover_files(
                root,
                python_binary=python_binary,
                rust_binary=rust_binary,
                pre_python_module=True,
            )

            result = run_audit(files, python_binary, rust_binary)
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(report["ok"], report["no_go_reasons"])
        self.assertEqual(
            report["snapshots"]["pre"]["program_arguments"],
            [python_binary, "-m", "codex_obsidian_sync.cli", "--config", "/tmp/config.toml", "service-run"],
        )
        self.assertEqual(report["snapshots"]["pre"]["program_arguments_count"], 6)

    def test_refuses_symlinked_output_report_path(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-service-cutover-") as temp_dir:
            root = Path(temp_dir)
            output_target = root / "target-report.json"
            output = root / "service-cutover-audit.json"
            output_target.write_text("keep\n", encoding="utf-8")
            output.symlink_to(output_target)

            with self.assertRaisesRegex(RuntimeError, "output path is a symlink"):
                audit_service_cutover.resolve_output_path(output)

            self.assertEqual(output_target.read_text(encoding="utf-8"), "keep\n")

    def test_fails_when_stopped_snapshot_is_still_loaded(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-service-cutover-") as temp_dir:
            root = Path(temp_dir)
            python_binary = "/Users/test/.local/bin/codex-obsidian-sync"
            rust_binary = "/opt/homebrew/bin/codex-obsidian-sync"
            files = write_cutover_files(
                root,
                python_binary=python_binary,
                rust_binary=rust_binary,
                stopped_loaded=True,
            )

            result = run_audit(files, python_binary, rust_binary)
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertIn("stopped: status still reports launchd_loaded=true", report["no_go_reasons"])
        self.assertIn("stopped: launchctl print still looks loaded", report["no_go_reasons"])

    def test_fails_when_post_plist_still_points_to_python_binary(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-service-cutover-") as temp_dir:
            root = Path(temp_dir)
            python_binary = "/Users/test/.local/bin/codex-obsidian-sync"
            rust_binary = "/opt/homebrew/bin/codex-obsidian-sync"
            files = write_cutover_files(
                root,
                python_binary=python_binary,
                rust_binary=rust_binary,
                post_program_arg0=python_binary,
            )

            result = run_audit(files, python_binary, rust_binary)
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertIn("post: ProgramArguments[0] does not match expected binary", report["no_go_reasons"])

    def test_fails_when_post_plist_missing_config_argument(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-service-cutover-") as temp_dir:
            root = Path(temp_dir)
            python_binary = "/Users/test/.local/bin/codex-obsidian-sync"
            rust_binary = "/opt/homebrew/bin/codex-obsidian-sync"
            files = write_cutover_files(root, python_binary=python_binary, rust_binary=rust_binary)
            write_plist(
                files["post_plist"],
                rust_binary,
                label="com.codex.obsidian-sync",
                include_config=False,
            )

            result = run_audit(files, python_binary, rust_binary)
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertIn(
            "post: LaunchAgent ProgramArguments must include --config before service-run",
            report["no_go_reasons"],
        )

    def test_fails_when_post_config_path_is_relative(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-service-cutover-") as temp_dir:
            root = Path(temp_dir)
            python_binary = "/Users/test/.local/bin/codex-obsidian-sync"
            rust_binary = "/opt/homebrew/bin/codex-obsidian-sync"
            files = write_cutover_files(root, python_binary=python_binary, rust_binary=rust_binary)
            write_plist(
                files["post_plist"],
                rust_binary,
                label="com.codex.obsidian-sync",
                config_path="relative-config.toml",
            )

            result = run_audit(files, python_binary, rust_binary)
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertIn("post: LaunchAgent --config path is not absolute", report["no_go_reasons"])

    def test_fails_when_post_plist_has_extra_argument_before_service_run(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-service-cutover-") as temp_dir:
            root = Path(temp_dir)
            python_binary = "/Users/test/.local/bin/codex-obsidian-sync"
            rust_binary = "/opt/homebrew/bin/codex-obsidian-sync"
            files = write_cutover_files(root, python_binary=python_binary, rust_binary=rust_binary)
            write_plist(
                files["post_plist"],
                rust_binary,
                label="com.codex.obsidian-sync",
                extra_before_service_run="--unexpected",
            )

            result = run_audit(files, python_binary, rust_binary)
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertIn(
            "post: Rust LaunchAgent ProgramArguments must be exactly binary, --config, config path, service-run",
            report["no_go_reasons"],
        )

    def test_fails_when_pre_and_post_config_paths_differ(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-service-cutover-") as temp_dir:
            root = Path(temp_dir)
            python_binary = "/Users/test/.local/bin/codex-obsidian-sync"
            rust_binary = "/opt/homebrew/bin/codex-obsidian-sync"
            files = write_cutover_files(
                root,
                python_binary=python_binary,
                rust_binary=rust_binary,
                pre_config_path="/tmp/python-config.toml",
                post_config_path="/tmp/rust-config.toml",
            )

            result = run_audit(files, python_binary, rust_binary)
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertIn("pre and post LaunchAgent --config paths differ", report["no_go_reasons"])

    def test_fails_when_expected_rust_binary_is_relative(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-service-cutover-") as temp_dir:
            root = Path(temp_dir)
            python_binary = "/Users/test/.local/bin/codex-obsidian-sync"
            rust_binary = "codex-obsidian-sync"
            files = write_cutover_files(root, python_binary=python_binary, rust_binary=rust_binary)

            result = run_audit(files, python_binary, rust_binary)
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertIn("expected Rust ProgramArguments[0] must be an absolute path", report["no_go_reasons"])
        self.assertIn("post: ProgramArguments[0] is not an absolute path", report["no_go_reasons"])

    def test_fails_when_plist_program_arguments_is_not_a_list(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-service-cutover-") as temp_dir:
            root = Path(temp_dir)
            python_binary = "/Users/test/.local/bin/codex-obsidian-sync"
            rust_binary = "/opt/homebrew/bin/codex-obsidian-sync"
            files = write_cutover_files(root, python_binary=python_binary, rust_binary=rust_binary)
            files["post_plist"].write_bytes(
                plistlib.dumps(
                    {
                        "Label": "com.codex.obsidian-sync",
                        "ProgramArguments": rust_binary,
                    },
                    fmt=plistlib.FMT_XML,
                    sort_keys=False,
                )
            )

            result = run_audit(files, python_binary, rust_binary)
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertIn("post: LaunchAgent ProgramArguments is not a list", report["no_go_reasons"])

    def test_fails_when_plist_program_arguments_contains_empty_value(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-service-cutover-") as temp_dir:
            root = Path(temp_dir)
            python_binary = "/Users/test/.local/bin/codex-obsidian-sync"
            rust_binary = "/opt/homebrew/bin/codex-obsidian-sync"
            files = write_cutover_files(root, python_binary=python_binary, rust_binary=rust_binary)
            write_plist(files["post_plist"], rust_binary, label="com.codex.obsidian-sync", extra_argument="")

            result = run_audit(files, python_binary, rust_binary)
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertIn(
            "post: LaunchAgent ProgramArguments contains non-string or empty values",
            report["no_go_reasons"],
        )

    def test_fails_when_post_launchctl_does_not_find_service(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-service-cutover-") as temp_dir:
            root = Path(temp_dir)
            python_binary = "/Users/test/.local/bin/codex-obsidian-sync"
            rust_binary = "/opt/homebrew/bin/codex-obsidian-sync"
            files = write_cutover_files(root, python_binary=python_binary, rust_binary=rust_binary)
            files["post_launchctl"].write_text("could not find service\n", encoding="utf-8")

            result = run_audit(files, python_binary, rust_binary)
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertIn("post: launchctl print did not return a loaded service", report["no_go_reasons"])

    def test_fails_when_loaded_launchctl_output_lacks_expected_label(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-service-cutover-") as temp_dir:
            root = Path(temp_dir)
            python_binary = "/Users/test/.local/bin/codex-obsidian-sync"
            rust_binary = "/opt/homebrew/bin/codex-obsidian-sync"
            files = write_cutover_files(root, python_binary=python_binary, rust_binary=rust_binary)
            files["post_launchctl"].write_text(
                launchctl_loaded_text("com.codex.obsidian-sync.other"),
                encoding="utf-8",
            )

            result = run_audit(files, python_binary, rust_binary)
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertIn("post: launchctl print did not include expected label", report["no_go_reasons"])

    def test_fails_when_pre_python_plist_has_unexpected_shape(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-service-cutover-") as temp_dir:
            root = Path(temp_dir)
            python_binary = "/Users/test/.local/bin/codex-obsidian-sync"
            rust_binary = "/opt/homebrew/bin/codex-obsidian-sync"
            files = write_cutover_files(
                root,
                python_binary=python_binary,
                rust_binary=rust_binary,
                pre_extra_before_service_run="--unexpected",
            )

            result = run_audit(files, python_binary, rust_binary)
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertIn(
            "pre: Python LaunchAgent ProgramArguments must be either binary, --config, config path, service-run "
            "or python, -m, codex_obsidian_sync.cli, --config, config path, service-run",
            report["no_go_reasons"],
        )

    def test_fails_when_post_status_loaded_flag_is_not_boolean(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-service-cutover-") as temp_dir:
            root = Path(temp_dir)
            python_binary = "/Users/test/.local/bin/codex-obsidian-sync"
            rust_binary = "/opt/homebrew/bin/codex-obsidian-sync"
            files = write_cutover_files(root, python_binary=python_binary, rust_binary=rust_binary)
            status = json.loads(files["post_status"].read_text(encoding="utf-8"))
            status["launchd_loaded"] = "true"
            files["post_status"].write_text(json.dumps(status) + "\n", encoding="utf-8")

            result = run_audit(files, python_binary, rust_binary)
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertIn("post: status launchd_loaded is not boolean true", report["no_go_reasons"])

    def test_fails_when_post_plist_label_is_unexpected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-service-cutover-") as temp_dir:
            root = Path(temp_dir)
            python_binary = "/Users/test/.local/bin/codex-obsidian-sync"
            rust_binary = "/opt/homebrew/bin/codex-obsidian-sync"
            files = write_cutover_files(
                root,
                python_binary=python_binary,
                rust_binary=rust_binary,
                post_plist_label="com.codex.obsidian-sync.other",
            )

            result = run_audit(files, python_binary, rust_binary)
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertIn("post: LaunchAgent Label does not match expected label", report["no_go_reasons"])

    def test_fails_when_stopped_status_label_is_unexpected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-service-cutover-") as temp_dir:
            root = Path(temp_dir)
            python_binary = "/Users/test/.local/bin/codex-obsidian-sync"
            rust_binary = "/opt/homebrew/bin/codex-obsidian-sync"
            files = write_cutover_files(
                root,
                python_binary=python_binary,
                rust_binary=rust_binary,
                stopped_status_label="com.codex.obsidian-sync.other",
            )

            result = run_audit(files, python_binary, rust_binary)
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertIn("stopped: status launchd_label does not match expected label", report["no_go_reasons"])

    def test_fails_when_stopped_status_has_last_error(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-service-cutover-") as temp_dir:
            root = Path(temp_dir)
            python_binary = "/Users/test/.local/bin/codex-obsidian-sync"
            rust_binary = "/opt/homebrew/bin/codex-obsidian-sync"
            files = write_cutover_files(root, python_binary=python_binary, rust_binary=rust_binary)
            status = json.loads(files["stopped_status"].read_text(encoding="utf-8"))
            status["last_error"] = "launchctl bootout failed"
            files["stopped_status"].write_text(json.dumps(status) + "\n", encoding="utf-8")

            result = run_audit(files, python_binary, rust_binary)
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertIn("stopped: last_error is launchctl bootout failed", report["no_go_reasons"])

    def test_fails_when_status_plist_paths_differ(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-service-cutover-") as temp_dir:
            root = Path(temp_dir)
            python_binary = "/Users/test/.local/bin/codex-obsidian-sync"
            rust_binary = "/opt/homebrew/bin/codex-obsidian-sync"
            files = write_cutover_files(root, python_binary=python_binary, rust_binary=rust_binary)
            status = json.loads(files["post_status"].read_text(encoding="utf-8"))
            status["plist_path"] = "/tmp/other-com.codex.obsidian-sync.plist"
            files["post_status"].write_text(json.dumps(status) + "\n", encoding="utf-8")

            result = run_audit(files, python_binary, rust_binary)
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertIn("pre, stopped, and post status plist_path values differ", report["no_go_reasons"])

    def test_fails_when_status_plist_path_is_relative(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-service-cutover-") as temp_dir:
            root = Path(temp_dir)
            python_binary = "/Users/test/.local/bin/codex-obsidian-sync"
            rust_binary = "/opt/homebrew/bin/codex-obsidian-sync"
            files = write_cutover_files(root, python_binary=python_binary, rust_binary=rust_binary)
            status = json.loads(files["pre_status"].read_text(encoding="utf-8"))
            status["plist_path"] = "Library/LaunchAgents/com.codex.obsidian-sync.plist"
            files["pre_status"].write_text(json.dumps(status) + "\n", encoding="utf-8")

            result = run_audit(files, python_binary, rust_binary)
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertIn("pre: status plist_path is not absolute", report["no_go_reasons"])

    def test_fails_when_last_success_is_invalid(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-service-cutover-") as temp_dir:
            root = Path(temp_dir)
            python_binary = "/Users/test/.local/bin/codex-obsidian-sync"
            rust_binary = "/opt/homebrew/bin/codex-obsidian-sync"
            files = write_cutover_files(root, python_binary=python_binary, rust_binary=rust_binary)
            status = json.loads(files["pre_status"].read_text(encoding="utf-8"))
            status["last_success"] = "never run"
            files["pre_status"].write_text(json.dumps(status) + "\n", encoding="utf-8")

            result = run_audit(files, python_binary, rust_binary)
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertIn("pre: last_success is missing or invalid", report["no_go_reasons"])

    def test_fails_when_post_last_success_regresses(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-service-cutover-") as temp_dir:
            root = Path(temp_dir)
            python_binary = "/Users/test/.local/bin/codex-obsidian-sync"
            rust_binary = "/opt/homebrew/bin/codex-obsidian-sync"
            files = write_cutover_files(root, python_binary=python_binary, rust_binary=rust_binary)
            status = json.loads(files["post_status"].read_text(encoding="utf-8"))
            status["last_success"] = "2026-05-15T23:55:00+00:00"
            files["post_status"].write_text(json.dumps(status) + "\n", encoding="utf-8")

            result = run_audit(files, python_binary, rust_binary)
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertIn("post: last_success regressed from stopped snapshot", report["no_go_reasons"])

    def test_fails_with_report_when_status_json_is_invalid(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-service-cutover-") as temp_dir:
            root = Path(temp_dir)
            python_binary = "/Users/test/.local/bin/codex-obsidian-sync"
            rust_binary = "/opt/homebrew/bin/codex-obsidian-sync"
            files = write_cutover_files(root, python_binary=python_binary, rust_binary=rust_binary)
            files["post_status"].write_text("{not json\n", encoding="utf-8")

            result = run_audit(files, python_binary, rust_binary)
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertEqual(report["snapshots"], {})
        self.assertTrue(
            any(reason.startswith("cutover evidence is invalid:") for reason in report["no_go_reasons"])
        )

    def test_fails_with_report_when_plist_is_invalid(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-service-cutover-") as temp_dir:
            root = Path(temp_dir)
            python_binary = "/Users/test/.local/bin/codex-obsidian-sync"
            rust_binary = "/opt/homebrew/bin/codex-obsidian-sync"
            files = write_cutover_files(root, python_binary=python_binary, rust_binary=rust_binary)
            files["post_plist"].write_text("not a plist\n", encoding="utf-8")

            result = run_audit(files, python_binary, rust_binary)
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertEqual(report["snapshots"], {})
        self.assertTrue(
            any(reason.startswith("cutover evidence is invalid:") for reason in report["no_go_reasons"])
        )

    def test_refuses_symlinked_evidence_file(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-service-cutover-") as temp_dir:
            root = Path(temp_dir)
            python_binary = "/Users/test/.local/bin/codex-obsidian-sync"
            rust_binary = "/opt/homebrew/bin/codex-obsidian-sync"
            files = write_cutover_files(root, python_binary=python_binary, rust_binary=rust_binary)
            target = root / "external-post.plist"
            target.write_bytes(files["post_plist"].read_bytes())
            files["post_plist"].unlink()
            files["post_plist"].symlink_to(target)

            result = run_audit(files, python_binary, rust_binary)

        self.assertEqual(result.returncode, 1)
        self.assertIn("evidence file is a symlink", result.stderr)


def run_audit(files: dict[str, Path], python_binary: str, rust_binary: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "scripts/audit_service_cutover.py",
            "--pre-status-json-file",
            str(files["pre_status"]),
            "--pre-plist-file",
            str(files["pre_plist"]),
            "--pre-launchctl-print-file",
            str(files["pre_launchctl"]),
            "--stopped-status-json-file",
            str(files["stopped_status"]),
            "--stopped-launchctl-print-file",
            str(files["stopped_launchctl"]),
            "--post-status-json-file",
            str(files["post_status"]),
            "--post-plist-file",
            str(files["post_plist"]),
            "--post-launchctl-print-file",
            str(files["post_launchctl"]),
            "--expected-python-program-arg0",
            python_binary,
            "--expected-rust-program-arg0",
            rust_binary,
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def write_cutover_files(
    root: Path,
    *,
    python_binary: str,
    rust_binary: str,
    stopped_loaded: bool = False,
    post_program_arg0: str | None = None,
    pre_status_label: str = "com.codex.obsidian-sync",
    stopped_status_label: str = "com.codex.obsidian-sync",
    post_status_label: str = "com.codex.obsidian-sync",
    pre_plist_label: str = "com.codex.obsidian-sync",
    post_plist_label: str = "com.codex.obsidian-sync",
    pre_config_path: str = "/tmp/config.toml",
    post_config_path: str = "/tmp/config.toml",
    pre_extra_before_service_run: str | None = None,
    pre_python_module: bool = False,
) -> dict[str, Path]:
    post_program_arg0 = post_program_arg0 or rust_binary
    files = {
        "pre_status": root / "pre-status.json",
        "pre_plist": root / "pre.plist",
        "pre_launchctl": root / "pre-launchctl.txt",
        "stopped_status": root / "stopped-status.json",
        "stopped_launchctl": root / "stopped-launchctl.txt",
        "post_status": root / "post-status.json",
        "post_plist": root / "post.plist",
        "post_launchctl": root / "post-launchctl.txt",
    }
    write_status(files["pre_status"], launchd_loaded=True, label=pre_status_label)
    write_plist(
        files["pre_plist"],
        python_binary,
        label=pre_plist_label,
        config_path=pre_config_path,
        extra_before_service_run=pre_extra_before_service_run,
        module_invocation=pre_python_module,
    )
    files["pre_launchctl"].write_text(launchctl_loaded_text(pre_status_label), encoding="utf-8")
    write_status(files["stopped_status"], launchd_loaded=stopped_loaded, label=stopped_status_label)
    files["stopped_launchctl"].write_text(
        launchctl_loaded_text(stopped_status_label) if stopped_loaded else "could not find service\n",
        encoding="utf-8",
    )
    write_status(files["post_status"], launchd_loaded=True, label=post_status_label)
    write_plist(files["post_plist"], post_program_arg0, label=post_plist_label, config_path=post_config_path)
    files["post_launchctl"].write_text(launchctl_loaded_text(post_status_label), encoding="utf-8")
    return files


def write_status(path: Path, *, launchd_loaded: bool, label: str) -> None:
    path.write_text(
        json.dumps(
            {
                "configured": True,
                "launchd_label": label,
                "launchd_loaded": launchd_loaded,
                "plist_path": "/tmp/com.codex.obsidian-sync.plist",
                "last_success": "2026-05-16T00:05:00+00:00",
                "last_error": "none",
            }
        )
        + "\n",
        encoding="utf-8",
    )


def write_plist(
    path: Path,
    program_arg0: str,
    *,
    label: str,
    extra_argument: str | None = None,
    extra_before_service_run: str | None = None,
    config_path: str = "/tmp/config.toml",
    include_config: bool = True,
    module_invocation: bool = False,
) -> None:
    program_arguments = [program_arg0]
    if module_invocation:
        program_arguments.extend(["-m", "codex_obsidian_sync.cli"])
    if include_config:
        program_arguments.extend(["--config", config_path])
    if extra_before_service_run is not None:
        program_arguments.append(extra_before_service_run)
    program_arguments.append("service-run")
    if extra_argument is not None:
        program_arguments.append(extra_argument)
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


def launchctl_loaded_text(label: str) -> str:
    return f"gui/501/{label} = {{\n\tstate = running\n}}\n"


if __name__ == "__main__":
    unittest.main()
