from __future__ import annotations

import unittest

from codex_obsidian_sync.redaction import redact_text


class RedactionTests(unittest.TestCase):
    def test_redacts_common_provider_token_formats(self) -> None:
        github_token = "ghp_" + ("a" * 36)
        github_fine_grained_token = "github_pat_" + ("A" * 22) + "_" + ("B" * 59)
        aws_access_key = "AKIA" + ("A" * 16)
        slack_token = "xox" + "b-" + "123456789012-123456789012-abcdefghijklmnopqrstuvwx"
        npm_token = "npm_" + ("b" * 36)
        google_api_key = "AIza" + ("C" * 35)
        stripe_key = "sk_live_" + ("d" * 24)
        jwt_token = f"eyJ{'e' * 20}.eyJ{'f' * 20}.{'g' * 20}"

        redacted = redact_text(
            "\n".join(
                [
                    github_token,
                    github_fine_grained_token,
                    aws_access_key,
                    slack_token,
                    npm_token,
                    google_api_key,
                    stripe_key,
                    jwt_token,
                ]
            )
        )

        for token in (
            github_token,
            github_fine_grained_token,
            aws_access_key,
            slack_token,
            npm_token,
            google_api_key,
            stripe_key,
            jwt_token,
        ):
            self.assertNotIn(token, redacted)
        self.assertIn("[REDACTED_GITHUB_TOKEN]", redacted)
        self.assertIn("[REDACTED_AWS_ACCESS_KEY]", redacted)
        self.assertIn("[REDACTED_SLACK_TOKEN]", redacted)
        self.assertIn("[REDACTED_NPM_TOKEN]", redacted)
        self.assertIn("[REDACTED_GOOGLE_API_KEY]", redacted)
        self.assertIn("[REDACTED_STRIPE_SECRET_KEY]", redacted)
        self.assertIn("[REDACTED_JWT]", redacted)

    def test_redacts_padded_bearer_tokens(self) -> None:
        bearer_token = "abc.def+/ghi=="

        redacted = redact_text(f"Authorization: Bearer {bearer_token}")

        self.assertNotIn(bearer_token, redacted)
        self.assertEqual(redacted, "Authorization: [REDACTED_BEARER_TOKEN]")

    def test_redacts_common_secret_assignment_names(self) -> None:
        secret_value = "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY"

        redacted = redact_text(f'AWS_SECRET_ACCESS_KEY="{secret_value}"')

        self.assertNotIn(secret_value, redacted)
        self.assertEqual(redacted, 'AWS_SECRET_ACCESS_KEY="[REDACTED_SECRET]"')


if __name__ == "__main__":
    unittest.main()
