from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tarfile
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import audit_published_release
from audit_release_evidence import audit_release_evidence


REPOSITORY = "HuiungJang/codex-obsidian-sync"
API_URL = "https://api.example.test"


class AuditPublishedReleaseTests(unittest.TestCase):
    def test_downloads_release_assets_and_runs_evidence_audit(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-published-release-") as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "assets"
            download_dir = root / "downloaded"
            assets = write_release_asset_set(source_dir)
            assets["published-release-audit.json"] = b'{"ok": true}\n'
            opener = FakeGitHubOpener(assets)

            result = audit_published_release.audit_published_release(
                version="0.1.0",
                repository=REPOSITORY,
                download_dir=download_dir,
                github_api_url=API_URL,
                opener=opener,
            )

        self.assertTrue(result["ok"], result["no_go_reasons"])
        self.assertEqual(result["tag"], "v0.1.0")
        self.assertEqual(result["missing_assets"], [])
        self.assertEqual(result["extra_assets"], [])
        self.assertTrue(result["local_evidence_audit"]["ok"])
        self.assertTrue(result["uploaded_evidence_summary"]["ok"])
        self.assertEqual(len(result["downloaded_assets"]), len(audit_published_release.required_asset_names()))

    def test_fails_when_required_release_asset_is_missing(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-published-release-") as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "assets"
            download_dir = root / "downloaded"
            assets = write_release_asset_set(source_dir)
            del assets["homebrew-smoke-summary.json"]
            opener = FakeGitHubOpener(assets)

            result = audit_published_release.audit_published_release(
                version="0.1.0",
                repository=REPOSITORY,
                download_dir=download_dir,
                github_api_url=API_URL,
                opener=opener,
            )

        self.assertFalse(result["ok"])
        self.assertIn("missing release asset: homebrew-smoke-summary.json", result["no_go_reasons"])
        self.assertIn("homebrew-smoke-summary.json", result["missing_assets"])

    def test_fails_when_release_is_draft_or_prerelease(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-published-release-") as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "assets"
            download_dir = root / "downloaded"
            assets = write_release_asset_set(source_dir)
            opener = FakeGitHubOpener(assets, draft=True, prerelease=True)

            result = audit_published_release.audit_published_release(
                version="0.1.0",
                repository=REPOSITORY,
                download_dir=download_dir,
                github_api_url=API_URL,
                opener=opener,
            )

        self.assertFalse(result["ok"])
        self.assertIn("GitHub release is still a draft", result["no_go_reasons"])
        self.assertIn("GitHub release is marked as a prerelease", result["no_go_reasons"])

    def test_fails_when_release_publication_flags_are_missing(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-published-release-") as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "assets"
            download_dir = root / "downloaded"
            assets = write_release_asset_set(source_dir)
            opener = FakeGitHubOpener(assets, include_publication_flags=False)

            result = audit_published_release.audit_published_release(
                version="0.1.0",
                repository=REPOSITORY,
                download_dir=download_dir,
                github_api_url=API_URL,
                opener=opener,
            )

        self.assertFalse(result["ok"])
        self.assertIn("GitHub release draft flag is missing or not false", result["no_go_reasons"])
        self.assertIn("GitHub release prerelease flag is missing or not false", result["no_go_reasons"])

    def test_fails_when_release_publication_flags_are_not_booleans(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-published-release-") as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "assets"
            download_dir = root / "downloaded"
            assets = write_release_asset_set(source_dir)
            opener = FakeGitHubOpener(assets, draft="false", prerelease="false")

            result = audit_published_release.audit_published_release(
                version="0.1.0",
                repository=REPOSITORY,
                download_dir=download_dir,
                github_api_url=API_URL,
                opener=opener,
            )

        self.assertFalse(result["ok"])
        self.assertIn("GitHub release draft flag is missing or not false", result["no_go_reasons"])
        self.assertIn("GitHub release prerelease flag is missing or not false", result["no_go_reasons"])

    def test_fails_when_release_html_url_points_to_another_repository(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-published-release-") as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "assets"
            download_dir = root / "downloaded"
            assets = write_release_asset_set(source_dir)
            opener = FakeGitHubOpener(
                assets,
                html_url="https://github.example.test/Other/repo/releases/tag/v0.1.0",
            )

            result = audit_published_release.audit_published_release(
                version="0.1.0",
                repository=REPOSITORY,
                download_dir=download_dir,
                github_api_url=API_URL,
                opener=opener,
            )

        self.assertFalse(result["ok"])
        self.assertIn(
            "GitHub release html_url does not match requested repository and tag",
            result["no_go_reasons"],
        )

    def test_fails_when_release_contains_unexpected_asset(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-published-release-") as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "assets"
            download_dir = root / "downloaded"
            assets = write_release_asset_set(source_dir)
            assets["debug.log"] = b"unexpected\n"
            opener = FakeGitHubOpener(assets)

            result = audit_published_release.audit_published_release(
                version="0.1.0",
                repository=REPOSITORY,
                download_dir=download_dir,
                github_api_url=API_URL,
                opener=opener,
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["extra_assets"], ["debug.log"])
        self.assertIn("unexpected release asset: debug.log", result["no_go_reasons"])

    def test_fails_when_uploaded_evidence_summary_omits_no_go_reasons(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-published-release-") as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "assets"
            download_dir = root / "downloaded"
            assets = write_release_asset_set(source_dir)
            summary = json.loads(assets["release-evidence-summary.json"].decode("utf-8"))
            del summary["no_go_reasons"]
            assets["release-evidence-summary.json"] = json.dumps(summary).encode("utf-8")
            opener = FakeGitHubOpener(assets)

            result = audit_published_release.audit_published_release(
                version="0.1.0",
                repository=REPOSITORY,
                download_dir=download_dir,
                github_api_url=API_URL,
                opener=opener,
            )

        self.assertFalse(result["ok"])
        self.assertIn(
            "uploaded release evidence summary no_go_reasons is not an empty list",
            result["no_go_reasons"],
        )

    def test_fails_when_uploaded_evidence_summary_version_does_not_match(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-published-release-") as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "assets"
            download_dir = root / "downloaded"
            assets = write_release_asset_set(source_dir)
            summary = json.loads(assets["release-evidence-summary.json"].decode("utf-8"))
            summary["version"] = "0.2.0"
            assets["release-evidence-summary.json"] = json.dumps(summary).encode("utf-8")
            opener = FakeGitHubOpener(assets)

            result = audit_published_release.audit_published_release(
                version="0.1.0",
                repository=REPOSITORY,
                download_dir=download_dir,
                github_api_url=API_URL,
                opener=opener,
            )

        self.assertFalse(result["ok"])
        self.assertIn(
            "uploaded release evidence summary version does not match requested version",
            result["no_go_reasons"],
        )

    def test_fails_when_uploaded_evidence_summary_generated_at_is_missing(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-published-release-") as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "assets"
            download_dir = root / "downloaded"
            assets = write_release_asset_set(source_dir)
            summary = json.loads(assets["release-evidence-summary.json"].decode("utf-8"))
            del summary["generated_at"]
            assets["release-evidence-summary.json"] = json.dumps(summary).encode("utf-8")
            opener = FakeGitHubOpener(assets)

            result = audit_published_release.audit_published_release(
                version="0.1.0",
                repository=REPOSITORY,
                download_dir=download_dir,
                github_api_url=API_URL,
                opener=opener,
            )

        self.assertFalse(result["ok"])
        self.assertIn(
            "uploaded release evidence summary generated_at is missing or invalid",
            result["no_go_reasons"],
        )

    def test_fails_when_uploaded_evidence_summary_generated_at_is_invalid(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-published-release-") as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "assets"
            download_dir = root / "downloaded"
            assets = write_release_asset_set(source_dir)
            summary = json.loads(assets["release-evidence-summary.json"].decode("utf-8"))
            summary["generated_at"] = "2026-05-15 12:00:00"
            assets["release-evidence-summary.json"] = json.dumps(summary).encode("utf-8")
            opener = FakeGitHubOpener(assets)

            result = audit_published_release.audit_published_release(
                version="0.1.0",
                repository=REPOSITORY,
                download_dir=download_dir,
                github_api_url=API_URL,
                opener=opener,
            )

        self.assertFalse(result["ok"])
        self.assertIn(
            "uploaded release evidence summary generated_at is missing or invalid",
            result["no_go_reasons"],
        )

    def test_fails_when_uploaded_evidence_summary_omits_checks(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-published-release-") as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "assets"
            download_dir = root / "downloaded"
            assets = write_release_asset_set(source_dir)
            summary = json.loads(assets["release-evidence-summary.json"].decode("utf-8"))
            del summary["checks"]
            assets["release-evidence-summary.json"] = json.dumps(summary).encode("utf-8")
            opener = FakeGitHubOpener(assets)

            result = audit_published_release.audit_published_release(
                version="0.1.0",
                repository=REPOSITORY,
                download_dir=download_dir,
                github_api_url=API_URL,
                opener=opener,
            )

        self.assertFalse(result["ok"])
        self.assertIn(
            "uploaded release evidence summary checks are missing",
            result["no_go_reasons"],
        )

    def test_fails_when_uploaded_evidence_summary_contains_failed_check(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-published-release-") as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "assets"
            download_dir = root / "downloaded"
            assets = write_release_asset_set(source_dir)
            summary = json.loads(assets["release-evidence-summary.json"].decode("utf-8"))
            summary["checks"][0]["ok"] = False
            assets["release-evidence-summary.json"] = json.dumps(summary).encode("utf-8")
            opener = FakeGitHubOpener(assets)

            result = audit_published_release.audit_published_release(
                version="0.1.0",
                repository=REPOSITORY,
                download_dir=download_dir,
                github_api_url=API_URL,
                opener=opener,
            )

        self.assertFalse(result["ok"])
        self.assertIn(
            "uploaded release evidence summary contains failed checks",
            result["no_go_reasons"],
        )

    def test_fails_when_uploaded_evidence_summary_check_has_no_go_reasons(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-published-release-") as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "assets"
            download_dir = root / "downloaded"
            assets = write_release_asset_set(source_dir)
            summary = json.loads(assets["release-evidence-summary.json"].decode("utf-8"))
            summary["checks"][0]["no_go_reasons"] = ["stale failure"]
            assets["release-evidence-summary.json"] = json.dumps(summary).encode("utf-8")
            opener = FakeGitHubOpener(assets)

            result = audit_published_release.audit_published_release(
                version="0.1.0",
                repository=REPOSITORY,
                download_dir=download_dir,
                github_api_url=API_URL,
                opener=opener,
            )

        self.assertFalse(result["ok"])
        self.assertIn(
            "uploaded release evidence summary contains check no-go reasons",
            result["no_go_reasons"],
        )

    def test_fails_when_uploaded_evidence_summary_omits_required_check_names(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-published-release-") as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "assets"
            download_dir = root / "downloaded"
            assets = write_release_asset_set(source_dir)
            summary = json.loads(assets["release-evidence-summary.json"].decode("utf-8"))
            summary["checks"] = [{"name": "release artifact:aarch64-apple-darwin", "ok": True}]
            assets["release-evidence-summary.json"] = json.dumps(summary).encode("utf-8")
            opener = FakeGitHubOpener(assets)

            result = audit_published_release.audit_published_release(
                version="0.1.0",
                repository=REPOSITORY,
                download_dir=download_dir,
                github_api_url=API_URL,
                opener=opener,
            )

        self.assertFalse(result["ok"])
        self.assertTrue(
            any(
                reason.startswith("uploaded release evidence summary is missing checks:")
                for reason in result["no_go_reasons"]
            )
        )

    def test_fails_when_uploaded_evidence_summary_contains_unexpected_check(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-published-release-") as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "assets"
            download_dir = root / "downloaded"
            assets = write_release_asset_set(source_dir)
            summary = json.loads(assets["release-evidence-summary.json"].decode("utf-8"))
            summary["checks"].append({"name": "manual smoke note", "ok": True, "no_go_reasons": []})
            assets["release-evidence-summary.json"] = json.dumps(summary).encode("utf-8")
            opener = FakeGitHubOpener(assets)

            result = audit_published_release.audit_published_release(
                version="0.1.0",
                repository=REPOSITORY,
                download_dir=download_dir,
                github_api_url=API_URL,
                opener=opener,
            )

        self.assertFalse(result["ok"])
        self.assertIn(
            "uploaded release evidence summary contains unexpected checks: ['manual smoke note']",
            result["no_go_reasons"],
        )

    def test_fails_when_uploaded_evidence_summary_contains_duplicate_check(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-published-release-") as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "assets"
            download_dir = root / "downloaded"
            assets = write_release_asset_set(source_dir)
            summary = json.loads(assets["release-evidence-summary.json"].decode("utf-8"))
            summary["checks"].append(dict(summary["checks"][0]))
            assets["release-evidence-summary.json"] = json.dumps(summary).encode("utf-8")
            opener = FakeGitHubOpener(assets)

            result = audit_published_release.audit_published_release(
                version="0.1.0",
                repository=REPOSITORY,
                download_dir=download_dir,
                github_api_url=API_URL,
                opener=opener,
            )

        self.assertFalse(result["ok"])
        self.assertIn(
            "uploaded release evidence summary contains duplicate checks: ['release artifact:aarch64-apple-darwin']",
            result["no_go_reasons"],
        )

    def test_fails_when_uploaded_evidence_summary_contains_malformed_check_name(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-published-release-") as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "assets"
            download_dir = root / "downloaded"
            assets = write_release_asset_set(source_dir)
            summary = json.loads(assets["release-evidence-summary.json"].decode("utf-8"))
            summary["checks"].append({"name": "", "ok": True, "no_go_reasons": []})
            assets["release-evidence-summary.json"] = json.dumps(summary).encode("utf-8")
            opener = FakeGitHubOpener(assets)

            result = audit_published_release.audit_published_release(
                version="0.1.0",
                repository=REPOSITORY,
                download_dir=download_dir,
                github_api_url=API_URL,
                opener=opener,
            )

        self.assertFalse(result["ok"])
        self.assertTrue(
            any(
                reason.startswith("uploaded release evidence summary contains malformed check names:")
                for reason in result["no_go_reasons"]
            )
        )

    def test_fails_when_uploaded_evidence_summary_check_omits_details(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-published-release-") as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "assets"
            download_dir = root / "downloaded"
            assets = write_release_asset_set(source_dir)
            summary = json.loads(assets["release-evidence-summary.json"].decode("utf-8"))
            del summary["checks"][0]["details"]
            assets["release-evidence-summary.json"] = json.dumps(summary).encode("utf-8")
            opener = FakeGitHubOpener(assets)

            result = audit_published_release.audit_published_release(
                version="0.1.0",
                repository=REPOSITORY,
                download_dir=download_dir,
                github_api_url=API_URL,
                opener=opener,
            )

        self.assertFalse(result["ok"])
        self.assertIn(
            "uploaded release evidence summary contains malformed check details: "
            "['release artifact:aarch64-apple-darwin']",
            result["no_go_reasons"],
        )

    def test_fails_when_uploaded_evidence_summary_check_target_detail_does_not_match(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-published-release-") as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "assets"
            download_dir = root / "downloaded"
            assets = write_release_asset_set(source_dir)
            summary = json.loads(assets["release-evidence-summary.json"].decode("utf-8"))
            for check in summary["checks"]:
                if check["name"] == "release smoke summary:aarch64-apple-darwin":
                    check["details"]["target"] = "x86_64-apple-darwin"
                    break
            assets["release-evidence-summary.json"] = json.dumps(summary).encode("utf-8")
            opener = FakeGitHubOpener(assets)

            result = audit_published_release.audit_published_release(
                version="0.1.0",
                repository=REPOSITORY,
                download_dir=download_dir,
                github_api_url=API_URL,
                opener=opener,
            )

        self.assertFalse(result["ok"])
        self.assertIn(
            "uploaded release evidence summary check target mismatch: release smoke summary:aarch64-apple-darwin",
            result["no_go_reasons"],
        )

    def test_fails_when_uploaded_evidence_summary_formula_details_do_not_match(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-published-release-") as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "assets"
            download_dir = root / "downloaded"
            assets = write_release_asset_set(source_dir)
            summary = json.loads(assets["release-evidence-summary.json"].decode("utf-8"))
            for check in summary["checks"]:
                if check["name"] == "homebrew formula":
                    check["details"]["version"] = "0.2.0"
                    check["details"]["repository"] = "Other/repo"
                elif check["name"] == "homebrew smoke summary":
                    check["details"]["expected_version"] = "0.2.0"
            assets["release-evidence-summary.json"] = json.dumps(summary).encode("utf-8")
            opener = FakeGitHubOpener(assets)

            result = audit_published_release.audit_published_release(
                version="0.1.0",
                repository=REPOSITORY,
                download_dir=download_dir,
                github_api_url=API_URL,
                opener=opener,
            )

        self.assertFalse(result["ok"])
        self.assertIn(
            "uploaded release evidence summary formula version does not match requested version",
            result["no_go_reasons"],
        )
        self.assertIn(
            "uploaded release evidence summary formula repository does not match requested repository",
            result["no_go_reasons"],
        )
        self.assertIn(
            "uploaded release evidence summary Homebrew smoke version does not match requested version",
            result["no_go_reasons"],
        )

    def test_fails_when_uploaded_evidence_summary_artifact_checksum_does_not_match_downloaded_asset(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-published-release-") as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "assets"
            download_dir = root / "downloaded"
            assets = write_release_asset_set(source_dir)
            summary = json.loads(assets["release-evidence-summary.json"].decode("utf-8"))
            for check in summary["checks"]:
                if check["name"] == "release artifact:aarch64-apple-darwin":
                    check["details"]["checksum"] = "0" * 64
                    break
            assets["release-evidence-summary.json"] = json.dumps(summary).encode("utf-8")
            opener = FakeGitHubOpener(assets)

            result = audit_published_release.audit_published_release(
                version="0.1.0",
                repository=REPOSITORY,
                download_dir=download_dir,
                github_api_url=API_URL,
                opener=opener,
            )

        self.assertFalse(result["ok"])
        self.assertIn(
            "uploaded release evidence summary check detail mismatch: "
            "release artifact:aarch64-apple-darwin.checksum",
            result["no_go_reasons"],
        )

    def test_fails_when_uploaded_evidence_summary_homebrew_formula_digest_does_not_match_downloaded_asset(
        self,
    ) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-published-release-") as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "assets"
            download_dir = root / "downloaded"
            assets = write_release_asset_set(source_dir)
            summary = json.loads(assets["release-evidence-summary.json"].decode("utf-8"))
            for check in summary["checks"]:
                if check["name"] == "homebrew smoke summary":
                    check["details"]["formula_sha256"] = "0" * 64
                    break
            assets["release-evidence-summary.json"] = json.dumps(summary).encode("utf-8")
            opener = FakeGitHubOpener(assets)

            result = audit_published_release.audit_published_release(
                version="0.1.0",
                repository=REPOSITORY,
                download_dir=download_dir,
                github_api_url=API_URL,
                opener=opener,
            )

        self.assertFalse(result["ok"])
        self.assertIn(
            "uploaded release evidence summary check detail mismatch: homebrew smoke summary.formula_sha256",
            result["no_go_reasons"],
        )

    def test_fails_when_release_asset_entry_is_not_an_object(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-published-release-") as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "assets"
            download_dir = root / "downloaded"
            assets = write_release_asset_set(source_dir)
            opener = FakeGitHubOpener(assets, release_assets=["not an asset"])

            result = audit_published_release.audit_published_release(
                version="0.1.0",
                repository=REPOSITORY,
                download_dir=download_dir,
                github_api_url=API_URL,
                opener=opener,
            )

        self.assertFalse(result["ok"])
        self.assertIn("GitHub release has malformed asset metadata: #0 is not an object", result["no_go_reasons"])

    def test_fails_when_release_asset_metadata_is_incomplete(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-published-release-") as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "assets"
            download_dir = root / "downloaded"
            assets = write_release_asset_set(source_dir)
            asset_name = "codex-obsidian-sync-aarch64-apple-darwin.tar.gz"
            opener = FakeGitHubOpener(
                assets,
                release_assets=[
                    release_asset_metadata(assets, asset_name, url="", size="123"),
                ],
            )

            result = audit_published_release.audit_published_release(
                version="0.1.0",
                repository=REPOSITORY,
                download_dir=download_dir,
                github_api_url=API_URL,
                opener=opener,
            )

        self.assertFalse(result["ok"])
        self.assertIn(
            "GitHub release has malformed asset metadata: "
            "codex-obsidian-sync-aarch64-apple-darwin.tar.gz is missing a download URL; "
            "codex-obsidian-sync-aarch64-apple-darwin.tar.gz is missing a non-negative size",
            result["no_go_reasons"],
        )

    def test_fails_when_release_asset_url_is_outside_requested_repository(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-published-release-") as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "assets"
            download_dir = root / "downloaded"
            assets = write_release_asset_set(source_dir)
            asset_name = "codex-obsidian-sync-aarch64-apple-darwin.tar.gz"
            opener = FakeGitHubOpener(
                assets,
                release_assets=[
                    release_asset_metadata(
                        assets,
                        asset_name,
                        url=f"{API_URL}/repos/Other/repo/releases/assets/123",
                    ),
                ],
            )

            result = audit_published_release.audit_published_release(
                version="0.1.0",
                repository=REPOSITORY,
                download_dir=download_dir,
                github_api_url=API_URL,
                opener=opener,
            )

        self.assertFalse(result["ok"])
        self.assertIn(
            "GitHub release has malformed asset metadata: "
            "codex-obsidian-sync-aarch64-apple-darwin.tar.gz download URL is outside the requested repository",
            result["no_go_reasons"],
        )

    def test_fails_when_release_asset_url_has_no_asset_id(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-published-release-") as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "assets"
            download_dir = root / "downloaded"
            assets = write_release_asset_set(source_dir)
            asset_name = "codex-obsidian-sync-aarch64-apple-darwin.tar.gz"
            opener = FakeGitHubOpener(
                assets,
                release_assets=[
                    release_asset_metadata(
                        assets,
                        asset_name,
                        url=f"{API_URL}/repos/{REPOSITORY}/releases/assets/",
                    ),
                ],
            )

            result = audit_published_release.audit_published_release(
                version="0.1.0",
                repository=REPOSITORY,
                download_dir=download_dir,
                github_api_url=API_URL,
                opener=opener,
            )

        self.assertFalse(result["ok"])
        self.assertIn(
            "GitHub release has malformed asset metadata: "
            "codex-obsidian-sync-aarch64-apple-darwin.tar.gz download URL has an invalid asset id",
            result["no_go_reasons"],
        )

    def test_fails_when_release_asset_id_is_missing(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-published-release-") as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "assets"
            download_dir = root / "downloaded"
            assets = write_release_asset_set(source_dir)
            asset_name = "codex-obsidian-sync-aarch64-apple-darwin.tar.gz"
            asset = release_asset_metadata(assets, asset_name)
            del asset["id"]
            opener = FakeGitHubOpener(assets, release_assets=[asset])

            result = audit_published_release.audit_published_release(
                version="0.1.0",
                repository=REPOSITORY,
                download_dir=download_dir,
                github_api_url=API_URL,
                opener=opener,
            )

        self.assertFalse(result["ok"])
        self.assertIn(
            "GitHub release has malformed asset metadata: "
            "codex-obsidian-sync-aarch64-apple-darwin.tar.gz is missing a positive asset id",
            result["no_go_reasons"],
        )

    def test_fails_when_release_asset_url_does_not_match_asset_id(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-published-release-") as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "assets"
            download_dir = root / "downloaded"
            assets = write_release_asset_set(source_dir)
            asset_name = "codex-obsidian-sync-aarch64-apple-darwin.tar.gz"
            opener = FakeGitHubOpener(
                assets,
                release_assets=[
                    release_asset_metadata(
                        assets,
                        asset_name,
                        asset_id=17,
                        url=f"{API_URL}/repos/{REPOSITORY}/releases/assets/18",
                    ),
                ],
            )

            result = audit_published_release.audit_published_release(
                version="0.1.0",
                repository=REPOSITORY,
                download_dir=download_dir,
                github_api_url=API_URL,
                opener=opener,
            )

        self.assertFalse(result["ok"])
        self.assertIn(
            "GitHub release has malformed asset metadata: "
            "codex-obsidian-sync-aarch64-apple-darwin.tar.gz download URL does not match asset id",
            result["no_go_reasons"],
        )

    def test_fails_when_release_asset_url_has_nested_path(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-published-release-") as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "assets"
            download_dir = root / "downloaded"
            assets = write_release_asset_set(source_dir)
            asset_name = "codex-obsidian-sync-aarch64-apple-darwin.tar.gz"
            opener = FakeGitHubOpener(
                assets,
                release_assets=[
                    release_asset_metadata(
                        assets,
                        asset_name,
                        url=f"{API_URL}/repos/{REPOSITORY}/releases/assets/{asset_name}/redirect",
                    ),
                ],
            )

            result = audit_published_release.audit_published_release(
                version="0.1.0",
                repository=REPOSITORY,
                download_dir=download_dir,
                github_api_url=API_URL,
                opener=opener,
            )

        self.assertFalse(result["ok"])
        self.assertIn(
            "GitHub release has malformed asset metadata: "
            "codex-obsidian-sync-aarch64-apple-darwin.tar.gz download URL has an invalid asset id",
            result["no_go_reasons"],
        )

    def test_fails_when_release_asset_state_is_not_uploaded(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-published-release-") as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "assets"
            download_dir = root / "downloaded"
            assets = write_release_asset_set(source_dir)
            asset_name = "codex-obsidian-sync-aarch64-apple-darwin.tar.gz"
            opener = FakeGitHubOpener(
                assets,
                release_assets=[
                    release_asset_metadata(assets, asset_name, state="starter"),
                ],
            )

            result = audit_published_release.audit_published_release(
                version="0.1.0",
                repository=REPOSITORY,
                download_dir=download_dir,
                github_api_url=API_URL,
                opener=opener,
            )

        self.assertFalse(result["ok"])
        self.assertIn(
            "GitHub release has malformed asset metadata: "
            "codex-obsidian-sync-aarch64-apple-darwin.tar.gz asset state is not uploaded",
            result["no_go_reasons"],
        )

    def test_fails_when_release_asset_digest_is_missing(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-published-release-") as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "assets"
            download_dir = root / "downloaded"
            assets = write_release_asset_set(source_dir)
            asset_name = "codex-obsidian-sync-aarch64-apple-darwin.tar.gz"
            asset = release_asset_metadata(assets, asset_name)
            del asset["digest"]
            opener = FakeGitHubOpener(assets, release_assets=[asset])

            result = audit_published_release.audit_published_release(
                version="0.1.0",
                repository=REPOSITORY,
                download_dir=download_dir,
                github_api_url=API_URL,
                opener=opener,
            )

        self.assertFalse(result["ok"])
        self.assertIn(
            "GitHub release has malformed asset metadata: "
            "codex-obsidian-sync-aarch64-apple-darwin.tar.gz is missing a sha256 digest",
            result["no_go_reasons"],
        )

    def test_fails_when_release_asset_digest_is_not_sha256(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-published-release-") as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "assets"
            download_dir = root / "downloaded"
            assets = write_release_asset_set(source_dir)
            asset_name = "codex-obsidian-sync-aarch64-apple-darwin.tar.gz"
            opener = FakeGitHubOpener(
                assets,
                release_assets=[
                    release_asset_metadata(assets, asset_name, digest="md5:" + ("0" * 32)),
                ],
            )

            result = audit_published_release.audit_published_release(
                version="0.1.0",
                repository=REPOSITORY,
                download_dir=download_dir,
                github_api_url=API_URL,
                opener=opener,
            )

        self.assertFalse(result["ok"])
        self.assertIn(
            "GitHub release has malformed asset metadata: "
            "codex-obsidian-sync-aarch64-apple-darwin.tar.gz is missing a sha256 digest",
            result["no_go_reasons"],
        )

    def test_fails_when_release_asset_digest_does_not_match_payload(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-published-release-") as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "assets"
            download_dir = root / "downloaded"
            assets = write_release_asset_set(source_dir)
            asset_name = "codex-obsidian-sync-aarch64-apple-darwin.tar.gz"
            opener = FakeGitHubOpener(
                assets,
                release_assets=[
                    release_asset_metadata(assets, asset_name, digest="sha256:" + ("0" * 64)),
                ],
            )

            result = audit_published_release.audit_published_release(
                version="0.1.0",
                repository=REPOSITORY,
                download_dir=download_dir,
                github_api_url=API_URL,
                opener=opener,
            )

        self.assertFalse(result["ok"])
        self.assertIn(
            "release asset digest mismatch for codex-obsidian-sync-aarch64-apple-darwin.tar.gz",
            result["no_go_reasons"],
        )

    def test_fails_when_release_asset_browser_download_url_is_wrong(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-published-release-") as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "assets"
            download_dir = root / "downloaded"
            assets = write_release_asset_set(source_dir)
            asset_name = "codex-obsidian-sync-aarch64-apple-darwin.tar.gz"
            opener = FakeGitHubOpener(
                assets,
                release_assets=[
                    release_asset_metadata(
                        assets,
                        asset_name,
                        browser_download_url=(
                            "https://github.example.test/Other/repo/releases/download/v0.1.0/"
                            "codex-obsidian-sync-aarch64-apple-darwin.tar.gz"
                        ),
                    ),
                ],
            )

            result = audit_published_release.audit_published_release(
                version="0.1.0",
                repository=REPOSITORY,
                download_dir=download_dir,
                github_api_url=API_URL,
                opener=opener,
            )

        self.assertFalse(result["ok"])
        self.assertIn(
            "GitHub release has malformed asset metadata: "
            "codex-obsidian-sync-aarch64-apple-darwin.tar.gz "
            "browser download URL does not match the requested repository, tag, and asset name",
            result["no_go_reasons"],
        )

    def test_fails_when_release_asset_names_are_duplicated(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-published-release-") as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "assets"
            download_dir = root / "downloaded"
            assets = write_release_asset_set(source_dir)
            asset_name = "codex-obsidian-sync-aarch64-apple-darwin.tar.gz"
            duplicate_asset = release_asset_metadata(assets, asset_name)
            opener = FakeGitHubOpener(
                assets,
                release_assets=[
                    duplicate_asset,
                    dict(duplicate_asset),
                ],
            )

            result = audit_published_release.audit_published_release(
                version="0.1.0",
                repository=REPOSITORY,
                download_dir=download_dir,
                github_api_url=API_URL,
                opener=opener,
            )

        self.assertFalse(result["ok"])
        self.assertIn(
            "GitHub release has duplicate asset names: codex-obsidian-sync-aarch64-apple-darwin.tar.gz",
            result["no_go_reasons"],
        )

    def test_fails_when_release_asset_download_urls_are_duplicated(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-published-release-") as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "assets"
            download_dir = root / "downloaded"
            assets = write_release_asset_set(source_dir)
            tarball_name = "codex-obsidian-sync-aarch64-apple-darwin.tar.gz"
            checksum_name = f"{tarball_name}.sha256"
            duplicate_url = f"{API_URL}/repos/{REPOSITORY}/releases/assets/1"
            opener = FakeGitHubOpener(
                assets,
                release_assets=[
                    release_asset_metadata(assets, tarball_name, asset_id=1, url=duplicate_url),
                    release_asset_metadata(assets, checksum_name, asset_id=1, url=duplicate_url),
                ],
            )

            result = audit_published_release.audit_published_release(
                version="0.1.0",
                repository=REPOSITORY,
                download_dir=download_dir,
                github_api_url=API_URL,
                opener=opener,
            )

        self.assertFalse(result["ok"])
        self.assertTrue(
            any(
                reason.startswith("GitHub release has duplicate asset download URLs:")
                for reason in result["no_go_reasons"]
            )
        )

    def test_refuses_non_empty_download_directory(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-published-release-") as temp_dir:
            root = Path(temp_dir)
            download_dir = root / "downloaded"
            download_dir.mkdir()
            (download_dir / "existing").write_text("keep\n", encoding="utf-8")

            result = audit_published_release.audit_published_release(
                version="0.1.0",
                repository=REPOSITORY,
                download_dir=download_dir,
                github_api_url=API_URL,
                opener=FakeGitHubOpener({}),
            )

        self.assertFalse(result["ok"])
        self.assertIn("download directory is not empty", result["no_go_reasons"][0])

    def test_refuses_symlinked_download_directory(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-published-release-") as temp_dir:
            root = Path(temp_dir)
            target_dir = root / "target"
            download_dir = root / "downloaded"
            target_dir.mkdir()
            download_dir.symlink_to(target_dir, target_is_directory=True)

            result = audit_published_release.audit_published_release(
                version="0.1.0",
                repository=REPOSITORY,
                download_dir=download_dir,
                github_api_url=API_URL,
                opener=FakeGitHubOpener({}),
            )

        self.assertFalse(result["ok"])
        self.assertIn("download path is a symlink", result["no_go_reasons"][0])

    def test_refuses_symlinked_output_report_path(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-published-release-") as temp_dir:
            root = Path(temp_dir)
            output_target = root / "target-report.json"
            output = root / "published-release-audit.json"
            output_target.write_text("keep\n", encoding="utf-8")
            output.symlink_to(output_target)

            with self.assertRaisesRegex(RuntimeError, "output path is a symlink"):
                audit_published_release.resolve_output_path(output)

            self.assertEqual(output_target.read_text(encoding="utf-8"), "keep\n")


class FakeGitHubOpener:
    def __init__(
        self,
        assets: dict[str, bytes],
        *,
        draft: object = False,
        prerelease: object = False,
        include_publication_flags: bool = True,
        html_url: object = "https://github.example.test/HuiungJang/codex-obsidian-sync/releases/tag/v0.1.0",
        release_assets: list[Any] | None = None,
    ) -> None:
        self.assets = assets
        self.draft = draft
        self.prerelease = prerelease
        self.include_publication_flags = include_publication_flags
        self.html_url = html_url
        self.release_assets = release_assets
        self.release_url = f"{API_URL}/repos/{REPOSITORY}/releases/tags/v0.1.0"

    def __call__(self, request: Any) -> "FakeResponse":
        if request.full_url == self.release_url:
            return FakeResponse(json.dumps(self.release_json()).encode("utf-8"))
        for asset in self.release_json()["assets"]:
            if not isinstance(asset, dict):
                continue
            if request.full_url == asset.get("url"):
                name = asset.get("name")
                if name in self.assets:
                    return FakeResponse(self.assets[name])
        raise RuntimeError(f"unexpected URL: {request.full_url}")

    def release_json(self) -> dict[str, Any]:
        release_assets = self.release_assets if self.release_assets is not None else [
            {
                "id": index,
                "name": name,
                "url": f"{API_URL}/repos/{REPOSITORY}/releases/assets/{index}",
                "browser_download_url": browser_download_url(name),
                "state": "uploaded",
                "digest": f"sha256:{hashlib.sha256(payload).hexdigest()}",
                "size": len(payload),
            }
            for index, (name, payload) in enumerate(sorted(self.assets.items()), start=1)
        ]
        release = {
            "tag_name": "v0.1.0",
            "html_url": self.html_url,
            "assets": release_assets,
        }
        if self.include_publication_flags:
            release["draft"] = self.draft
            release["prerelease"] = self.prerelease
        return release


class FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


def browser_download_url(name: str) -> str:
    return f"https://github.example.test/{REPOSITORY}/releases/download/v0.1.0/{name}"


def release_asset_metadata(
    assets: dict[str, bytes],
    name: str,
    *,
    asset_id: int = 1,
    **overrides: Any,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "id": asset_id,
        "name": name,
        "url": f"{API_URL}/repos/{REPOSITORY}/releases/assets/{asset_id}",
        "browser_download_url": browser_download_url(name),
        "state": "uploaded",
        "digest": f"sha256:{hashlib.sha256(assets[name]).hexdigest()}",
        "size": len(assets[name]),
    }
    metadata.update(overrides)
    return metadata


def write_release_asset_set(release_dir: Path) -> dict[str, bytes]:
    release_dir.mkdir(parents=True)
    checksums = write_release_artifacts(release_dir)
    formula = write_formula(release_dir, checksums)
    write_release_smoke_summaries(release_dir)
    write_homebrew_smoke_summary(release_dir, formula)
    evidence = audit_release_evidence(
        release_dir=release_dir,
        formula_path=formula,
        version="0.1.0",
        repository=REPOSITORY,
    )
    (release_dir / "release-evidence-summary.json").write_text(
        json.dumps(evidence, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return {
        asset_name: (release_dir / asset_name).read_bytes()
        for asset_name in audit_published_release.required_asset_names()
    }


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
        shutil.rmtree(source_dir)
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
    return path


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
                        },
                    ],
                }
            )
            + "\n",
            encoding="utf-8",
        )


def write_homebrew_smoke_summary(release_dir: Path, formula: Path) -> None:
    installed_binary = "/tmp/codex-obsidian-sync/bin/codex-obsidian-sync"
    (release_dir / "homebrew-smoke-summary.json").write_text(
        json.dumps(
            {
                "ok": True,
                "generated_at": "2026-05-16T00:00:00+00:00",
                "formula": str(formula.resolve()),
                "formula_sha256": hashlib.sha256(formula.resolve().read_bytes()).hexdigest(),
                "expected_version": "0.1.0",
                "version": "codex-obsidian-sync 0.1.0",
                "installed_binary": installed_binary,
                "installed_after": False,
                "status_configured": True,
                "status_json_parsed": True,
                "dry_run": True,
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


if __name__ == "__main__":
    unittest.main()
