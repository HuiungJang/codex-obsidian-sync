from __future__ import annotations

import unittest
from pathlib import Path


class ReleaseWorkflowTests(unittest.TestCase):
    def test_publish_job_generates_formula_before_release(self) -> None:
        workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")

        formula_step = workflow.index("- name: Generate Homebrew formula")
        publish_step = workflow.index("- name: Publish release")
        smoke_step = workflow.index("- name: Smoke Homebrew formula")

        self.assertLess(formula_step, publish_step)
        self.assertLess(publish_step, smoke_step)
        self.assertIn("actions/checkout@v4", workflow)
        self.assertIn("scripts/generate_homebrew_formula.py", workflow)
        self.assertIn("scripts/smoke_homebrew_formula.py", workflow)
        self.assertIn("--aarch64-checksum dist/codex-obsidian-sync-aarch64-apple-darwin.tar.gz.sha256", workflow)
        self.assertIn("--x86-64-checksum dist/codex-obsidian-sync-x86_64-apple-darwin.tar.gz.sha256", workflow)
        self.assertIn("--output dist/codex-obsidian-sync.rb", workflow)
        self.assertIn("ruby -c dist/codex-obsidian-sync.rb", workflow)
        self.assertIn('gh release create "${GITHUB_REF_NAME}" dist/*', workflow)
        self.assertIn("--formula dist/codex-obsidian-sync.rb", workflow)
        self.assertIn("--expected-version \"${GITHUB_REF_NAME}\"", workflow)
        self.assertIn("--output dist/homebrew-smoke-summary.json", workflow)


if __name__ == "__main__":
    unittest.main()
