from __future__ import annotations

import unittest
from pathlib import Path


class ReleaseWorkflowTests(unittest.TestCase):
    def test_build_job_uses_native_macos_runners_for_each_release_target(self) -> None:
        workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")

        self.assertIn("runs-on: ${{ matrix.runner }}", workflow)
        self.assertIn("target: aarch64-apple-darwin", workflow)
        self.assertIn("runner: macos-15", workflow)
        self.assertIn("target: x86_64-apple-darwin", workflow)
        self.assertIn("runner: macos-15-intel", workflow)

    def test_publish_job_generates_formula_before_release(self) -> None:
        workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")

        formula_step = workflow.index("- name: Generate Homebrew formula")
        publish_step = workflow.index("- name: Publish release")
        smoke_step = workflow.index("- name: Smoke Homebrew formula")
        audit_step = workflow.index("- name: Audit release evidence")
        summaries_step = workflow.index("- name: Publish smoke summaries")
        published_audit_step = workflow.index("- name: Audit published release assets")
        publish_published_audit_step = workflow.index("- name: Publish published-release audit")

        self.assertLess(formula_step, publish_step)
        self.assertLess(publish_step, smoke_step)
        self.assertLess(smoke_step, audit_step)
        self.assertLess(audit_step, summaries_step)
        self.assertLess(summaries_step, published_audit_step)
        self.assertLess(published_audit_step, publish_published_audit_step)
        self.assertIn("actions/checkout@v4", workflow)
        self.assertIn("scripts/generate_homebrew_formula.py", workflow)
        self.assertIn("scripts/smoke_homebrew_formula.py", workflow)
        self.assertIn("scripts/audit_release_evidence.py", workflow)
        self.assertIn("scripts/audit_published_release.py", workflow)
        self.assertIn("--aarch64-checksum dist/codex-obsidian-sync-aarch64-apple-darwin.tar.gz.sha256", workflow)
        self.assertIn("--x86-64-checksum dist/codex-obsidian-sync-x86_64-apple-darwin.tar.gz.sha256", workflow)
        self.assertIn("--output dist/codex-obsidian-sync.rb", workflow)
        self.assertIn("ruby -c dist/codex-obsidian-sync.rb", workflow)
        self.assertIn('gh release create "${GITHUB_REF_NAME}" dist/*', workflow)
        self.assertIn("--formula dist/codex-obsidian-sync.rb", workflow)
        self.assertIn("--expected-version \"${GITHUB_REF_NAME}\"", workflow)
        self.assertIn("--output dist/homebrew-smoke-summary.json", workflow)
        self.assertIn("--output dist/release-evidence-summary.json", workflow)
        self.assertIn("dist/*.smoke-summary.json", workflow)
        self.assertIn(
            'gh release upload "${GITHUB_REF_NAME}" dist/*smoke-summary.json dist/release-evidence-summary.json --clobber',
            workflow,
        )
        self.assertIn("GITHUB_TOKEN: ${{ github.token }}", workflow)
        self.assertIn("--download-dir \"/tmp/codex-obsidian-sync-published-release-${GITHUB_REF_NAME}\"", workflow)
        self.assertIn("--output dist/published-release-audit.json", workflow)
        self.assertIn('gh release upload "${GITHUB_REF_NAME}" dist/published-release-audit.json --clobber', workflow)


if __name__ == "__main__":
    unittest.main()
