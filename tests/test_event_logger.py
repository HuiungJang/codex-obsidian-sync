from __future__ import annotations

import io
import json
import unittest
from pathlib import Path

from codex_obsidian_sync.event_logger import EventLogger, REDACTED


class EventLoggerTests(unittest.TestCase):
    def test_logger_redacts_content_fields(self) -> None:
        stream = io.StringIO()
        logger = EventLogger(level="DEBUG", stream=stream)

        logger.info(
            "sync_complete",
            transcript_text="secret body",
            message_preview="hello",
            rollout_path=Path("/tmp/demo.jsonl"),
            processed=1,
        )

        payload = json.loads(stream.getvalue())
        self.assertEqual(payload["transcript_text"], REDACTED)
        self.assertEqual(payload["message_preview"], REDACTED)
        self.assertEqual(payload["rollout_path"], "/tmp/demo.jsonl")
        self.assertEqual(payload["processed"], 1)


if __name__ == "__main__":
    unittest.main()
