from __future__ import annotations

import json
import plistlib
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory, gettempdir

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import smoke_real_config_launchagent


class SmokeRealConfigLaunchAgentTests(unittest.TestCase):
    def test_runs_real_config_dry_run_under_isolated_label(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-real-launchagent-test-") as temp_dir:
            root = Path(temp_dir)
            binary = write_fake_binary(root)
            launchctl = write_fake_launchctl(root)
            config = root / "config.toml"
            config.write_text("vault = \"/tmp/example\"\n", encoding="utf-8")
            work_dir = Path(gettempdir()) / f"codex-obsidian-sync-real-launchagent-smoke-{root.name}"
            self.addCleanup(shutil.rmtree, work_dir, ignore_errors=True)
            output = root / "summary.json"
            label = f"{smoke_real_config_launchagent.LABEL_PREFIX}{root.name.replace('_', '-')}"

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/smoke_real_config_launchagent.py",
                    "--binary",
                    str(binary),
                    "--config",
                    str(config),
                    "--launchctl",
                    str(launchctl),
                    "--work-dir",
                    str(work_dir),
                    "--label",
                    label,
                    "--timeout-seconds",
                    "5",
                    "--cleanup-dry-run-output",
                    "--output",
                    str(output),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(output.read_text(encoding="utf-8"))
            log = (root / "launchctl.log").read_text(encoding="utf-8")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(report["ok"], report["no_go_reasons"])
        self.assertEqual(report["details"]["label"], label)
        self.assertEqual(report["details"]["dry_run"], True)
        self.assertEqual(report["details"]["processed"], 1)
        self.assertEqual(report["details"]["note_files"], 1)
        self.assertTrue(report["details"]["temp_state_exists"])
        self.assertFalse(report["details"]["dry_run_output_exists_after_cleanup"])
        self.assertFalse(report["details"]["loaded_after_bootout"])
        self.assertIn("bootstrap", log)
        self.assertIn("kickstart -k", log)
        self.assertIn("bootout", log)

    def test_reports_timeout_and_bootouts_label(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-real-launchagent-test-") as temp_dir:
            root = Path(temp_dir)
            binary = write_fake_binary(root)
            launchctl = write_fake_launchctl(root, timeout=True)
            config = root / "config.toml"
            config.write_text("vault = \"/tmp/example\"\n", encoding="utf-8")
            work_dir = Path(gettempdir()) / f"codex-obsidian-sync-real-launchagent-smoke-{root.name}"
            self.addCleanup(shutil.rmtree, work_dir, ignore_errors=True)
            label = f"{smoke_real_config_launchagent.LABEL_PREFIX}{root.name.replace('_', '-')}"

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/smoke_real_config_launchagent.py",
                    "--binary",
                    str(binary),
                    "--config",
                    str(config),
                    "--launchctl",
                    str(launchctl),
                    "--work-dir",
                    str(work_dir),
                    "--label",
                    label,
                    "--timeout-seconds",
                    "0.1",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)
            log = (root / "launchctl.log").read_text(encoding="utf-8")

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertIn("sync-once dry-run timed out", report["no_go_reasons"])
        self.assertFalse(report["details"]["loaded_after_bootout"])
        self.assertIn("bootout", log)

    def test_passes_read_trace_flag_and_reports_trace_excerpt(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-real-launchagent-test-") as temp_dir:
            root = Path(temp_dir)
            binary = write_fake_binary(root)
            launchctl = write_fake_launchctl(root)
            config = root / "config.toml"
            config.write_text("vault = \"/tmp/example\"\n", encoding="utf-8")
            work_dir = Path(gettempdir()) / f"codex-obsidian-sync-real-launchagent-smoke-{root.name}"
            self.addCleanup(shutil.rmtree, work_dir, ignore_errors=True)
            label = f"{smoke_real_config_launchagent.LABEL_PREFIX}{root.name.replace('_', '-')}"

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/smoke_real_config_launchagent.py",
                    "--binary",
                    str(binary),
                    "--config",
                    str(config),
                    "--launchctl",
                    str(launchctl),
                    "--work-dir",
                    str(work_dir),
                    "--label",
                    label,
                    "--timeout-seconds",
                    "5",
                    "--trace-read-paths",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(report["ok"], report["no_go_reasons"])
        self.assertTrue(report["details"]["trace_read_paths"])
        self.assertTrue(report["details"]["read_trace_exists"])
        self.assertEqual(len(report["details"]["read_trace_head"]), 1)
        self.assertIn('"source": "vault"', report["details"]["read_trace_head"][0])
        self.assertEqual(report["details"]["read_trace_head"], report["details"]["read_trace_tail"])

    def test_records_codesign_identity_for_smoked_binary(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-real-launchagent-test-") as temp_dir:
            root = Path(temp_dir)
            binary = write_fake_binary(root)
            launchctl = write_fake_launchctl(root)
            codesign = write_fake_codesign(root, identifier="com.codex.obsidian-sync")
            config = root / "config.toml"
            config.write_text("vault = \"/tmp/example\"\n", encoding="utf-8")
            work_dir = Path(gettempdir()) / f"codex-obsidian-sync-real-launchagent-smoke-{root.name}"
            self.addCleanup(shutil.rmtree, work_dir, ignore_errors=True)
            label = f"{smoke_real_config_launchagent.LABEL_PREFIX}{root.name.replace('_', '-')}"

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/smoke_real_config_launchagent.py",
                    "--binary",
                    str(binary),
                    "--config",
                    str(config),
                    "--launchctl",
                    str(launchctl),
                    "--codesign",
                    str(codesign),
                    "--work-dir",
                    str(work_dir),
                    "--label",
                    label,
                    "--timeout-seconds",
                    "5",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(report["details"]["codesign"]["checked"])
        self.assertEqual(report["details"]["codesign"]["identifier"], "com.codex.obsidian-sync")
        self.assertEqual(report["details"]["codesign"]["signature"], "adhoc")
        self.assertEqual(report["details"]["codesign"]["team_identifier"], "not set")
        self.assertEqual(report["details"]["codesign"]["cdhash"], "abc123")

    def test_records_sample_excerpt_on_timeout_when_requested(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-real-launchagent-test-") as temp_dir:
            root = Path(temp_dir)
            binary = write_fake_binary(root)
            launchctl = write_fake_launchctl(root, timeout=True)
            sample = write_fake_sample(root)
            config = root / "config.toml"
            config.write_text("vault = \"/tmp/example\"\n", encoding="utf-8")
            work_dir = Path(gettempdir()) / f"codex-obsidian-sync-real-launchagent-smoke-{root.name}"
            self.addCleanup(shutil.rmtree, work_dir, ignore_errors=True)
            label = f"{smoke_real_config_launchagent.LABEL_PREFIX}{root.name.replace('_', '-')}"

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/smoke_real_config_launchagent.py",
                    "--binary",
                    str(binary),
                    "--config",
                    str(config),
                    "--launchctl",
                    str(launchctl),
                    "--work-dir",
                    str(work_dir),
                    "--label",
                    label,
                    "--timeout-seconds",
                    "0.1",
                    "--sample-on-timeout",
                    "--sample",
                    str(sample),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertEqual(report["details"]["pid"], 12345)
        self.assertEqual(report["details"]["sample_returncode"], 0)
        self.assertTrue(Path(report["details"]["sample_output"]).is_file())
        self.assertIn("DryRunOutput::read_relative", "\n".join(report["details"]["sample_excerpt"]))

    def test_diagnoses_vault_open_timeout_when_trace_and_sample_match(self) -> None:
        diagnosis = smoke_real_config_launchagent.timeout_diagnosis(
            {
                "sample_excerpt": [
                    "DryRunOutput::read_relative",
                    "std::fs::read_to_string",
                    "open",
                    "__open",
                ]
            },
            ['{"event":"read_attempt","source":"vault","relative_path":"Codex/Daily/2026-05-14.md"}'],
        )

        self.assertIsNotNone(diagnosis)
        self.assertIn("Full Disk Access", diagnosis)

    def test_timeout_diagnosis_requires_trace_evidence(self) -> None:
        diagnosis = smoke_real_config_launchagent.timeout_diagnosis(
            {"sample_excerpt": ["std::fs::read_to_string", "open"]},
            [],
        )

        self.assertIsNone(diagnosis)

    def test_diagnoses_dry_run_open_timeout_without_trace(self) -> None:
        diagnosis = smoke_real_config_launchagent.timeout_diagnosis(
            {"sample_excerpt": ["DryRunOutput::read_relative", "std::fs::read_to_string", "open"]},
            [],
        )

        self.assertIsNotNone(diagnosis)
        self.assertIn("Full Disk Access", diagnosis)
        self.assertIn("--trace-read-paths", diagnosis)

    def test_rejects_real_service_label(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-real-launchagent-test-") as temp_dir:
            root = Path(temp_dir)
            binary = write_fake_binary(root)
            launchctl = write_fake_launchctl(root)
            config = root / "config.toml"
            config.write_text("vault = \"/tmp/example\"\n", encoding="utf-8")
            work_dir = Path(gettempdir()) / f"codex-obsidian-sync-real-launchagent-smoke-{root.name}"
            self.addCleanup(shutil.rmtree, work_dir, ignore_errors=True)

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/smoke_real_config_launchagent.py",
                    "--binary",
                    str(binary),
                    "--config",
                    str(config),
                    "--launchctl",
                    str(launchctl),
                    "--work-dir",
                    str(work_dir),
                    "--label",
                    "com.codex.obsidian-sync",
                ],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(result.returncode, 1)
        self.assertIn("label must start", result.stdout)

    def test_refuses_symlinked_output_report_path(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-real-launchagent-test-") as temp_dir:
            root = Path(temp_dir)
            output_target = root / "target-summary.json"
            output = root / "summary.json"
            output_target.write_text("keep\n", encoding="utf-8")
            output.symlink_to(output_target)

            with self.assertRaisesRegex(RuntimeError, "Output path is a symlink"):
                smoke_real_config_launchagent.resolve_output_path(output)

            self.assertEqual(output_target.read_text(encoding="utf-8"), "keep\n")


def write_fake_binary(root: Path) -> Path:
    binary = root / "codex-obsidian-sync"
    binary.write_text(
        """#!/usr/bin/env python3
import json
import sys
from pathlib import Path

args = sys.argv[1:]
if len(args) in {5, 7} and args[0] == "--config" and args[2:4] == ["sync-once", "--dry-run-output"]:
    output = Path(args[4])
    trace = None
    if len(args) == 7:
        if args[5] != "--trace-read-paths":
            print("unexpected trace args: " + " ".join(args), file=sys.stderr)
            raise SystemExit(2)
        trace = Path(args[6])
    note = output / "Codex" / "Conversations" / "2026" / "smoke.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("# Smoke\\n", encoding="utf-8")
    (output / "sync-state.json").write_text(json.dumps({"files": {}}) + "\\n", encoding="utf-8")
    if trace is not None:
        trace.write_text(json.dumps({
            "event": "read_attempt",
            "source": "vault",
            "relative_path": "Codex/Daily/2026-04-04.md",
        }) + "\\n", encoding="utf-8")
    print(json.dumps({
        "processed": 1,
        "appended": 0,
        "rewritten": 1,
        "skipped_invalid": 0,
        "dry_run": True,
        "planned_writes": 2,
        "temp_state_file": str(output / "sync-state.json"),
    }))
    raise SystemExit(0)
print("unexpected args: " + " ".join(args), file=sys.stderr)
raise SystemExit(2)
""",
        encoding="utf-8",
    )
    binary.chmod(0o755)
    return binary


def write_fake_launchctl(root: Path, *, timeout: bool = False) -> Path:
    launchctl = root / "launchctl"
    launchctl.write_text(
        f"""#!/usr/bin/env python3
import plistlib
import subprocess
import sys
from pathlib import Path

root = Path({str(root)!r})
state_dir = root / "launchctl-state"
state_dir.mkdir(exist_ok=True)
timeout = {timeout!r}
log = root / "launchctl.log"
log.write_text(log.read_text(encoding="utf-8") + " ".join(sys.argv[1:]) + "\\n" if log.exists() else " ".join(sys.argv[1:]) + "\\n", encoding="utf-8")


def state_path(target):
    return state_dir / target.replace("/", "_")


args = sys.argv[1:]
if len(args) == 2 and args[0] == "print":
    state = state_path(args[1])
    if not state.exists():
        print("could not find service", file=sys.stderr)
        raise SystemExit(113)
    exit_code = state.read_text(encoding="utf-8")
    print("state = running")
    print("pid = 12345")
    print(f"last exit code = {{exit_code}}")
    raise SystemExit(0)
if len(args) == 3 and args[0] == "bootstrap":
    plist = Path(args[2])
    payload = plistlib.loads(plist.read_bytes())
    label = payload["Label"]
    target = args[1] + "/" + label
    state_path(target).write_text("(never exited)", encoding="utf-8")
    (state_path(target).with_suffix(".plist")).write_text(str(plist), encoding="utf-8")
    raise SystemExit(0)
if len(args) == 3 and args[0:2] == ["kickstart", "-k"]:
    state = state_path(args[2])
    if not state.exists():
        raise SystemExit(113)
    if timeout:
        raise SystemExit(0)
    plist = Path(state.with_suffix(".plist").read_text(encoding="utf-8"))
    payload = plistlib.loads(plist.read_bytes())
    result = subprocess.run(payload["ProgramArguments"], capture_output=True, text=True, check=False)
    Path(payload["StandardOutPath"]).write_text(result.stdout, encoding="utf-8")
    Path(payload["StandardErrorPath"]).write_text(result.stderr, encoding="utf-8")
    state.write_text(str(result.returncode), encoding="utf-8")
    raise SystemExit(0)
if len(args) == 2 and args[0] == "bootout":
    state = state_path(args[1])
    if state.exists():
        state.unlink()
    plist = state.with_suffix(".plist")
    if plist.exists():
        plist.unlink()
    raise SystemExit(0)
print("unexpected launchctl args: " + " ".join(args), file=sys.stderr)
raise SystemExit(2)
""",
        encoding="utf-8",
    )
    launchctl.chmod(0o755)
    return launchctl


def write_fake_codesign(root: Path, *, identifier: str) -> Path:
    codesign = root / "codesign"
    codesign.write_text(
        f"""#!/usr/bin/env python3
import sys

print("Executable=" + sys.argv[-1], file=sys.stderr)
print("Identifier={identifier}", file=sys.stderr)
print("Format=Mach-O thin (arm64)", file=sys.stderr)
print("CDHash=abc123", file=sys.stderr)
print("Signature=adhoc", file=sys.stderr)
print("TeamIdentifier=not set", file=sys.stderr)
""",
        encoding="utf-8",
    )
    codesign.chmod(0o755)
    return codesign


def write_fake_sample(root: Path) -> Path:
    sample = root / "sample"
    sample.write_text(
        """#!/usr/bin/env python3
import sys
from pathlib import Path

args = sys.argv[1:]
if len(args) == 4 and args[2] == "-file":
    Path(args[3]).write_text(
        "codex_obsidian_sync_rs::sync::run_sync\\n"
        "codex_obsidian_sync_rs::dry_run_output::DryRunOutput::read_relative\\n"
        "open\\n",
        encoding="utf-8",
    )
    raise SystemExit(0)
print("unexpected sample args: " + " ".join(args), file=sys.stderr)
raise SystemExit(2)
""",
        encoding="utf-8",
    )
    sample.chmod(0o755)
    return sample


if __name__ == "__main__":
    unittest.main()
