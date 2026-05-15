from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from codex_obsidian_sync.discovery import (
    build_session_envelope,
    discover_rollout_candidates,
    load_session_index,
    parse_rollout_session_id,
    slugify,
)
from codex_obsidian_sync.parser import load_rollout_records_from_offset


class DiscoveryTests(unittest.TestCase):
    def test_parse_rollout_session_id_uses_last_uuid(self) -> None:
        path = Path(
            "/tmp/rollout-2026-03-25T15-21-17-019d23a7-9258-7810-93cc-c6833b3481cc.jsonl"
        )
        self.assertEqual(
            parse_rollout_session_id(path),
            "019d23a7-9258-7810-93cc-c6833b3481cc",
        )

    def test_build_session_envelope_prefers_matching_meta_and_filters_messages(self) -> None:
        child_session_id = "019d23a7-9258-7810-93cc-c6833b3481cc"
        parent_session_id = "019d2378-4ee8-7620-879c-ef43a2a89a3e"

        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            index_path = temp_path / "session_index.jsonl"
            rollout_path = (
                temp_path
                / f"rollout-2026-03-25T15-21-17-{child_session_id}.jsonl"
            )

            self._write_jsonl(
                index_path,
                [
                    {
                        "id": child_session_id,
                        "thread_name": "Deepen plan",
                        "updated_at": "2026-03-25T06:30:00Z",
                    }
                ],
            )
            self._write_jsonl(
                rollout_path,
                [
                    {
                        "timestamp": "2026-03-25T06:21:17Z",
                        "type": "session_meta",
                        "payload": {
                            "id": child_session_id,
                            "timestamp": "2026-03-25T06:21:17Z",
                            "cwd": "/tmp/demo-project",
                            "originator": "Codex Desktop",
                            "source": {
                                "subagent": {
                                    "thread_spawn": {
                                        "parent_thread_id": parent_session_id,
                                        "depth": 1,
                                    }
                                }
                            },
                        },
                    },
                    {
                        "timestamp": "2026-03-25T06:21:18Z",
                        "type": "session_meta",
                        "payload": {
                            "id": parent_session_id,
                            "timestamp": "2026-03-25T05:29:40Z",
                            "cwd": "/tmp/demo-project",
                            "originator": "Codex Desktop",
                            "source": "vscode",
                        },
                    },
                    self._message_record(
                        timestamp="2026-03-25T06:21:20Z",
                        role="developer",
                        text="ignore developer message",
                    ),
                    self._message_record(
                        timestamp="2026-03-25T06:21:21Z",
                        role="user",
                        text="Keep only this user question",
                    ),
                    self._message_record(
                        timestamp="2026-03-25T06:21:22Z",
                        role="assistant",
                        phase="commentary",
                        text="ignore commentary",
                    ),
                    self._message_record(
                        timestamp="2026-03-25T06:21:23Z",
                        role="assistant",
                        phase="final_answer",
                        text="Keep this final answer",
                    ),
                    self._message_record(
                        timestamp="2026-03-25T06:21:23Z",
                        role="assistant",
                        phase="final_answer",
                        text="Keep this final answer",
                    ),
                    self._message_record(
                        timestamp="2026-03-25T06:21:24Z",
                        role="assistant",
                        text="Keep this empty phase answer too",
                    ),
                ],
            )

            envelope = build_session_envelope(rollout_path, load_session_index(index_path))

        self.assertEqual(envelope.canonical_session_id, child_session_id)
        self.assertTrue(envelope.is_subagent)
        self.assertEqual(envelope.parent_session_id, parent_session_id)
        self.assertEqual(envelope.source_kind, "subagent.thread_spawn")
        self.assertEqual(envelope.project_slug, "demo-project")
        self.assertEqual(envelope.title_seed, "keep-only-this-user-question")
        self.assertEqual([message.role for message in envelope.messages], ["user", "assistant", "assistant"])
        self.assertEqual(
            [message.phase for message in envelope.messages],
            ["", "final_answer", ""],
        )

    def test_build_session_envelope_falls_back_to_thread_name_for_title(self) -> None:
        session_id = "019d23a7-9258-7810-93cc-c6833b3481cc"

        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            index_path = temp_path / "session_index.jsonl"
            rollout_path = (
                temp_path
                / f"rollout-2026-03-25T15-21-17-{session_id}.jsonl"
            )

            self._write_jsonl(
                index_path,
                [
                    {
                        "id": session_id,
                        "thread_name": "Fallback title from index",
                        "updated_at": "2026-03-25T06:30:00Z",
                    }
                ],
            )
            self._write_jsonl(
                rollout_path,
                [
                    {
                        "timestamp": "2026-03-25T06:21:17Z",
                        "type": "session_meta",
                        "payload": {
                            "id": session_id,
                            "timestamp": "2026-03-25T06:21:17Z",
                            "cwd": "/tmp/demo-project",
                            "originator": "codex_cli_rs",
                            "source": "cli",
                        },
                    },
                    self._message_record(
                        timestamp="2026-03-25T06:21:23Z",
                        role="assistant",
                        phase="final_answer",
                        text="Assistant only conversation",
                    ),
                ],
            )

            envelope = build_session_envelope(rollout_path, load_session_index(index_path))

        self.assertFalse(envelope.is_subagent)
        self.assertEqual(envelope.title_seed, "fallback-title-from-index")
        self.assertEqual(envelope.project_slug, "demo-project")

    def test_build_session_envelope_ignores_control_messages_for_title(self) -> None:
        session_id = "019d23a7-9258-7810-93cc-c6833b3481cd"

        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            index_path = temp_path / "session_index.jsonl"
            rollout_path = (
                temp_path
                / f"rollout-2026-03-25T15-21-17-{session_id}.jsonl"
            )

            self._write_jsonl(
                index_path,
                [
                    {
                        "id": session_id,
                        "thread_name": "Index fallback",
                        "updated_at": "2026-03-25T06:30:00Z",
                    }
                ],
            )
            self._write_jsonl(
                rollout_path,
                [
                    {
                        "timestamp": "2026-03-25T06:21:17Z",
                        "type": "session_meta",
                        "payload": {
                            "id": session_id,
                            "timestamp": "2026-03-25T06:21:17Z",
                            "cwd": "/tmp/demo-project",
                            "originator": "Codex Desktop",
                            "source": "vscode",
                        },
                    },
                    self._message_record(
                        timestamp="2026-03-25T06:21:18Z",
                        role="user",
                        text="# AGENTS.md instructions for /tmp/demo-project",
                    ),
                    self._message_record(
                        timestamp="2026-03-25T06:21:19Z",
                        role="user",
                        text="<environment_context>\n<cwd>/tmp/demo-project</cwd>\n</environment_context>",
                    ),
                    self._message_record(
                        timestamp="2026-03-25T06:21:20Z",
                        role="user",
                        text="/workflows:brainstorm codex 를 통해 대화한 내용을 저장하고 싶어",
                    ),
                ],
            )

            envelope = build_session_envelope(rollout_path, load_session_index(index_path))

        self.assertEqual(
            envelope.title_seed,
            "workflows-brainstorm-codex-를-통해-대화한-내용을-저장하고-싶어",
        )
        self.assertEqual([message.text for message in envelope.messages], ["/workflows:brainstorm codex 를 통해 대화한 내용을 저장하고 싶어"])

    def test_slugify_normalizes_whitespace_and_punctuation(self) -> None:
        self.assertEqual(
            slugify("  Plan 기반 구현!! / next step  "),
            "plan-기반-구현-next-step",
        )

    def test_discover_rollout_candidates_applies_batch_caps(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            codex_home = root / ".codex"
            sessions_dir = codex_home / "sessions" / "2026" / "03" / "25"
            sessions_dir.mkdir(parents=True)
            index_path = codex_home / "session_index.jsonl"

            first_session_id = "019d23a7-9258-7810-93cc-c6833b3481aa"
            second_session_id = "019d23a7-9258-7810-93cc-c6833b3481ab"
            self._write_jsonl(
                index_path,
                [
                    {
                        "id": first_session_id,
                        "thread_name": "first",
                        "updated_at": "2026-03-25T06:30:00Z",
                    },
                    {
                        "id": second_session_id,
                        "thread_name": "second",
                        "updated_at": "2026-03-24T06:30:00Z",
                    },
                ],
            )
            for session_id in (first_session_id, second_session_id):
                rollout_path = sessions_dir / f"rollout-2026-03-25T15-21-17-{session_id}.jsonl"
                self._write_jsonl(
                    rollout_path,
                    [
                        {
                            "timestamp": "2026-03-25T06:21:17Z",
                            "type": "session_meta",
                            "payload": {
                                "id": session_id,
                                "timestamp": "2026-03-25T06:21:17Z",
                                "cwd": "/tmp/demo-project",
                                "originator": "Codex Desktop",
                                "source": "vscode",
                            },
                        }
                    ],
                )

            candidates = discover_rollout_candidates(
                codex_home,
                load_session_index(index_path),
                {"files": {}},
                vault_root=root,
                include_subagents=False,
                recent_days=30,
                max_files=1,
                max_bytes=1024 * 1024,
            )

        self.assertEqual(len(candidates), 1)

    def test_discover_rollout_candidates_treats_malformed_index_timestamp_as_older(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            codex_home = root / ".codex"
            sessions_dir = codex_home / "sessions" / "2026" / "03" / "25"
            sessions_dir.mkdir(parents=True)
            index_path = codex_home / "session_index.jsonl"

            malformed_session_id = "019d23a7-9258-7810-93cc-c6833b3481ba"
            valid_session_id = "019d23a7-9258-7810-93cc-c6833b3481bb"
            self._write_jsonl(
                index_path,
                [
                    {
                        "id": malformed_session_id,
                        "thread_name": "malformed",
                        "updated_at": "not-a-date",
                    },
                    {
                        "id": valid_session_id,
                        "thread_name": "valid",
                        "updated_at": "2000-01-01T00:00:00Z",
                    },
                ],
            )
            malformed_path = sessions_dir / f"rollout-2026-03-25T15-21-17-{malformed_session_id}.jsonl"
            valid_path = sessions_dir / f"rollout-2026-03-25T15-21-17-{valid_session_id}.jsonl"
            malformed_path.write_text("", encoding="utf-8")
            valid_path.write_text("", encoding="utf-8")

            candidates = discover_rollout_candidates(
                codex_home,
                load_session_index(index_path),
                {"files": {}},
                vault_root=root,
                include_subagents=False,
                recent_days=30,
                max_files=1,
                max_bytes=1024 * 1024,
            )

        self.assertEqual(candidates, [valid_path])

    def test_discover_rollout_candidates_treats_timezone_less_index_timestamp_as_older(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            codex_home = root / ".codex"
            sessions_dir = codex_home / "sessions" / "2026" / "03" / "25"
            sessions_dir.mkdir(parents=True)
            index_path = codex_home / "session_index.jsonl"

            naive_session_id = "019d23a7-9258-7810-93cc-c6833b3481ca"
            valid_session_id = "019d23a7-9258-7810-93cc-c6833b3481cb"
            self._write_jsonl(
                index_path,
                [
                    {
                        "id": naive_session_id,
                        "thread_name": "naive",
                        "updated_at": "2099-01-02T00:00:00",
                    },
                    {
                        "id": valid_session_id,
                        "thread_name": "valid",
                        "updated_at": "2000-01-01T00:00:00Z",
                    },
                ],
            )
            naive_path = sessions_dir / f"rollout-2026-03-25T15-21-17-{naive_session_id}.jsonl"
            valid_path = sessions_dir / f"rollout-2026-03-25T15-21-17-{valid_session_id}.jsonl"
            naive_path.write_text("", encoding="utf-8")
            valid_path.write_text("", encoding="utf-8")

            candidates = discover_rollout_candidates(
                codex_home,
                load_session_index(index_path),
                {"files": {}},
                vault_root=root,
                include_subagents=False,
                recent_days=30,
                max_files=1,
                max_bytes=1024 * 1024,
            )

        self.assertEqual(candidates, [valid_path])

    def test_load_rollout_records_from_offset_waits_for_complete_line(self) -> None:
        with TemporaryDirectory() as temp_dir:
            rollout_path = Path(temp_dir) / "rollout-2026-03-25T15-21-17-019d23a7-9258-7810-93cc-c6833b3481cc.jsonl"
            first_record = {"timestamp": "2026-03-25T06:21:17Z", "type": "event_msg", "payload": {"type": "ok"}}
            partial_record_prefix = '{"timestamp":"2026-03-25T06:21:18Z","type":"event_msg"'

            with rollout_path.open("wb") as handle:
                handle.write((json.dumps(first_record) + "\n").encode("utf-8"))
                handle.write(partial_record_prefix.encode("utf-8"))

            records, next_offset = load_rollout_records_from_offset(rollout_path, 0)
            self.assertEqual(len(records), 1)
            self.assertLess(next_offset, rollout_path.stat().st_size)

            with rollout_path.open("ab") as handle:
                handle.write(b',"payload":{"type":"done"}}\n')

            delta_records, final_offset = load_rollout_records_from_offset(rollout_path, next_offset)
            self.assertEqual(len(delta_records), 1)
            self.assertEqual(delta_records[0]["payload"]["type"], "done")
            self.assertEqual(final_offset, rollout_path.stat().st_size)

    def test_build_session_envelope_ignores_trailing_partial_jsonl_record(self) -> None:
        session_id = "019d23a7-9258-7810-93cc-c6833b3481ce"

        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            index_path = temp_path / "session_index.jsonl"
            rollout_path = (
                temp_path
                / f"rollout-2026-03-25T15-21-17-{session_id}.jsonl"
            )
            self._write_jsonl(
                index_path,
                [{"id": session_id, "thread_name": "partial", "updated_at": "2026-03-25T06:30:00Z"}],
            )
            self._write_jsonl(
                rollout_path,
                [
                    {
                        "timestamp": "2026-03-25T06:21:17Z",
                        "type": "session_meta",
                        "payload": {
                            "id": session_id,
                            "timestamp": "2026-03-25T06:21:17Z",
                            "cwd": "/tmp/demo-project",
                            "originator": "Codex Desktop",
                            "source": "vscode",
                        },
                    },
                    self._message_record(timestamp="2026-03-25T06:21:19Z", role="user", text="complete message"),
                ],
            )
            with rollout_path.open("a", encoding="utf-8") as handle:
                handle.write('{"timestamp":"2026-03-25T06:21:20Z","type":"response_item","payload":')

            envelope = build_session_envelope(rollout_path, load_session_index(index_path))

        self.assertEqual([message.text for message in envelope.messages], ["complete message"])

    def test_load_session_index_skips_invalid_lines(self) -> None:
        with TemporaryDirectory() as temp_dir:
            index_path = Path(temp_dir) / "session_index.jsonl"
            index_path.write_text(
                "\n".join(
                    [
                        '{"id":"valid-session","thread_name":"ok","updated_at":"2026-03-25T06:30:00Z"}',
                        '{"thread_name":"missing id"}',
                        '{"id":',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            entries = load_session_index(index_path)

        self.assertEqual(list(entries.keys()), ["valid-session"])

    @staticmethod
    def _message_record(
        *,
        timestamp: str,
        role: str,
        text: str,
        phase: str | None = None,
    ) -> dict[str, object]:
        payload = {
            "type": "message",
            "role": role,
            "content": [{"type": "output_text" if role == "assistant" else "input_text", "text": text}],
        }
        if phase is not None:
            payload["phase"] = phase
        return {"timestamp": timestamp, "type": "response_item", "payload": payload}

    @staticmethod
    def _write_jsonl(path: Path, records: list[dict[str, object]]) -> None:
        with path.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False))
                handle.write("\n")


if __name__ == "__main__":
    unittest.main()
