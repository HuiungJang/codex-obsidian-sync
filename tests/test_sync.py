from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from codex_obsidian_sync.config import resolve_sync_config
from codex_obsidian_sync.sync import sync_once
from codex_obsidian_sync.writer import CONVERSATION_NOTE_MARKER, TRANSCRIPT_MARKER


class SyncTests(unittest.TestCase):
    def test_sync_once_writes_notes_and_state(self) -> None:
        session_id = "019d23a7-9258-7810-93cc-c6833b3481cc"

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            codex_home = root / ".codex"
            sessions_dir = codex_home / "sessions" / "2026" / "03" / "25"
            sessions_dir.mkdir(parents=True)
            vault = root / "vault"
            vault.mkdir()
            state_file = codex_home / "obsidian-sync" / "sync-state.json"
            index_path = codex_home / "session_index.jsonl"
            rollout_path = sessions_dir / f"rollout-2026-03-25T15-21-17-{session_id}.jsonl"

            self._write_jsonl(
                index_path,
                [
                    {
                        "id": session_id,
                        "thread_name": "브레인스토밍 대화 Markdown 저장",
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
                        text="/workflows:brainstorm codex 를 통해 대화한 내용을 자동으로 markdown 으로 남겨서 obsidian 에서 볼 수 있도록 하고싶어.",
                    ),
                    self._message_record(
                        timestamp="2026-03-25T06:21:20Z",
                        role="assistant",
                        phase="commentary",
                        text="ignore commentary",
                    ),
                    self._message_record(
                        timestamp="2026-03-25T06:21:21Z",
                        role="assistant",
                        phase="final_answer",
                        text="Bearer abcdefghijklmnopqrstuvwxyz and sk-abcdefghijklmnopqrstuvwxyz123456",
                    ),
                ],
            )

            config = self._config(codex_home=codex_home, vault=vault, state_file=state_file)
            first_summary = sync_once(config=config)
            second_summary = sync_once(config=config)

            conversation_note = next((vault / "Codex" / "Conversations" / "2026").glob("*.md"))
            daily_note = vault / "Codex" / "Daily" / "2026-03-25.md"
            project_note = vault / "Codex" / "Projects" / "demo-project.md"

            conversation_text = conversation_note.read_text(encoding="utf-8")
            daily_text = daily_note.read_text(encoding="utf-8")
            project_text = project_note.read_text(encoding="utf-8")
            state = json.loads(state_file.read_text(encoding="utf-8"))

        self.assertEqual(first_summary["processed"], 1)
        self.assertEqual(second_summary["processed"], 0)
        self.assertIn("/workflows:brainstorm codex 를 통해 대화한 내용을 자동으로 markdown 으로 남겨서 obsidian 에서 볼 수 있도록 하고싶어.", conversation_text)
        self.assertNotIn("# AGENTS.md instructions", conversation_text)
        self.assertNotIn("ignore commentary", conversation_text)
        self.assertIn("[REDACTED_BEARER_TOKEN]", conversation_text)
        self.assertIn("[REDACTED_API_KEY]", conversation_text)
        self.assertIn("[[Codex/Daily/2026-03-25]]", conversation_text)
        self.assertIn("[[Codex/Projects/demo-project]]", conversation_text)
        self.assertIn(CONVERSATION_NOTE_MARKER, conversation_text)
        self.assertIn(TRANSCRIPT_MARKER, conversation_text)
        self.assertIn("<!-- BEGIN CODEX MANAGED SECTION -->", daily_text)
        self.assertIn("Conversation", daily_text)
        self.assertIn("project_slug: demo-project", project_text)
        self.assertEqual(len(state["files"]), 1)
        entry = next(iter(state["files"].values()))
        self.assertIn("offset", entry)
        self.assertNotIn("envelope_cache", entry)
        self.assertIn("last_written_message_count", entry)
        self.assertIn("last_written_message_key", entry)
        self.assertIn("header_hash", entry)
        self.assertIn("conversation_note_fingerprint", entry)

    def test_sync_once_updates_existing_note_from_appended_rollout_lines(self) -> None:
        session_id = "019d23a7-9258-7810-93cc-c6833b3481ce"

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            codex_home = root / ".codex"
            sessions_dir = codex_home / "sessions" / "2026" / "03" / "25"
            sessions_dir.mkdir(parents=True)
            vault = root / "vault"
            vault.mkdir()
            state_file = codex_home / "obsidian-sync" / "sync-state.json"
            index_path = codex_home / "session_index.jsonl"
            rollout_path = sessions_dir / f"rollout-2026-03-25T15-21-17-{session_id}.jsonl"

            self._write_jsonl(
                index_path,
                [
                    {
                        "id": session_id,
                        "thread_name": "증분 동기화 테스트",
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
                        timestamp="2026-03-25T06:21:19Z",
                        role="user",
                        text="첫 질문",
                    ),
                ],
            )

            config = self._config(codex_home=codex_home, vault=vault, state_file=state_file)
            first_summary = sync_once(config=config)
            conversation_note = next((vault / "Codex" / "Conversations" / "2026").glob("*.md"))
            first_inode = conversation_note.stat().st_ino
            first_state = json.loads(state_file.read_text(encoding="utf-8"))
            first_entry = first_state["files"][str(rollout_path)]

            with rollout_path.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        self._message_record(
                            timestamp="2026-03-25T06:21:22Z",
                            role="assistant",
                            phase="final_answer",
                            text="추가 응답",
                        ),
                        ensure_ascii=False,
                    )
                )
                handle.write("\n")

            second_summary = sync_once(config=config)
            second_state = json.loads(state_file.read_text(encoding="utf-8"))
            second_entry = second_state["files"][str(rollout_path)]
            conversation_text = conversation_note.read_text(encoding="utf-8")
            second_inode = conversation_note.stat().st_ino

        self.assertEqual(first_summary["processed"], 1)
        self.assertEqual(second_summary["processed"], 1)
        self.assertEqual(second_summary["appended"], 1)
        self.assertEqual(second_summary["rewritten"], 1)
        self.assertGreater(second_entry["offset"], first_entry["offset"])
        self.assertNotEqual(first_inode, second_inode)
        self.assertEqual(second_entry["last_written_message_count"], 2)
        self.assertIn("추가 응답", conversation_text)

    def test_sync_once_appends_without_rewrite_when_header_is_stable(self) -> None:
        session_id = "019d23a7-9258-7810-93cc-c6833b3481de"

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            codex_home = root / ".codex"
            sessions_dir = codex_home / "sessions" / "2026" / "04" / "03"
            sessions_dir.mkdir(parents=True)
            vault = root / "vault"
            vault.mkdir()
            state_file = codex_home / "obsidian-sync" / "sync-state.json"
            index_path = codex_home / "session_index.jsonl"
            rollout_path = sessions_dir / f"rollout-2026-04-03T10-05-00-{session_id}.jsonl"

            self._write_jsonl(
                index_path,
                [{"id": session_id, "thread_name": "append only", "updated_at": "2026-04-03T01:05:00Z"}],
            )
            self._write_jsonl(
                rollout_path,
                [
                    {
                        "timestamp": "2026-04-03T01:05:00Z",
                        "type": "session_meta",
                        "payload": {
                            "id": session_id,
                            "timestamp": "2026-04-03T01:05:00Z",
                            "cwd": "/tmp/demo-project",
                            "originator": "Codex Desktop",
                            "source": "vscode",
                        },
                    },
                    self._message_record(timestamp="2026-04-03T01:05:01Z", role="user", text="첫 질문"),
                ],
            )

            config = self._config(codex_home=codex_home, vault=vault, state_file=state_file)
            sync_once(config=config)
            conversation_note = next((vault / "Codex" / "Conversations" / "2026").glob("*.md"))
            first_inode = conversation_note.stat().st_ino

            with rollout_path.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        self._message_record(
                            timestamp="2026-04-03T01:05:02Z",
                            role="user",
                            text="두 번째 질문",
                        ),
                        ensure_ascii=False,
                    )
                )
                handle.write("\n")

            summary = sync_once(config=config)
            second_inode = conversation_note.stat().st_ino
            conversation_text = conversation_note.read_text(encoding="utf-8")

        self.assertEqual(summary["processed"], 1)
        self.assertEqual(summary["appended"], 1)
        self.assertEqual(summary["rewritten"], 0)
        self.assertEqual(first_inode, second_inode)
        self.assertIn("두 번째 질문", conversation_text)

    def test_sync_once_falls_back_to_full_rebuild_when_incremental_offset_is_stale(self) -> None:
        session_id = "019d23a7-9258-7810-93cc-c6833b3481e6"

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            codex_home = root / ".codex"
            sessions_dir = codex_home / "sessions" / "2026" / "04" / "03"
            sessions_dir.mkdir(parents=True)
            vault = root / "vault"
            vault.mkdir()
            state_file = codex_home / "obsidian-sync" / "sync-state.json"
            index_path = codex_home / "session_index.jsonl"
            rollout_path = sessions_dir / f"rollout-2026-04-03T10-06-00-{session_id}.jsonl"

            self._write_jsonl(
                index_path,
                [{"id": session_id, "thread_name": "stale offset", "updated_at": "2026-04-03T01:06:00Z"}],
            )
            self._write_jsonl(
                rollout_path,
                [
                    self._session_meta(session_id=session_id, timestamp="2026-04-03T01:06:00Z"),
                    self._message_record(timestamp="2026-04-03T01:06:01Z", role="user", text="첫 질문"),
                ],
            )

            config = self._config(codex_home=codex_home, vault=vault, state_file=state_file)
            sync_once(config=config)
            conversation_note = next((vault / "Codex" / "Conversations" / "2026").glob("*.md"))

            with rollout_path.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        self._message_record(
                            timestamp="2026-04-03T01:06:02Z",
                            role="assistant",
                            phase="final_answer",
                            text="offset 복구 응답",
                        ),
                        ensure_ascii=False,
                    )
                )
                handle.write("\n")

            state = json.loads(state_file.read_text(encoding="utf-8"))
            state["files"][str(rollout_path)]["offset"] += 1
            state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

            summary = sync_once(config=config)
            conversation_text = conversation_note.read_text(encoding="utf-8")

        self.assertEqual(summary["processed"], 1)
        self.assertEqual(summary["skipped_invalid"], 0)
        self.assertIn("offset 복구 응답", conversation_text)

    def test_sync_once_recreates_deleted_conversation_note(self) -> None:
        session_id = "019d23a7-9258-7810-93cc-c6833b3481cf"

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            codex_home = root / ".codex"
            sessions_dir = codex_home / "sessions" / "2026" / "03" / "25"
            sessions_dir.mkdir(parents=True)
            vault = root / "vault"
            vault.mkdir()
            state_file = codex_home / "obsidian-sync" / "sync-state.json"
            index_path = codex_home / "session_index.jsonl"
            rollout_path = sessions_dir / f"rollout-2026-03-25T15-21-17-{session_id}.jsonl"

            self._write_jsonl(
                index_path,
                [
                    {
                        "id": session_id,
                        "thread_name": "삭제 복구 테스트",
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
                        timestamp="2026-03-25T06:21:19Z",
                        role="user",
                        text="삭제 후 복구되는지 보자",
                    ),
                ],
            )

            config = self._config(codex_home=codex_home, vault=vault, state_file=state_file)
            sync_once(config=config)
            conversation_note = next((vault / "Codex" / "Conversations" / "2026").glob("*.md"))
            conversation_note.unlink()

            second_summary = sync_once(config=config)
            recreated_exists = conversation_note.exists()

        self.assertEqual(second_summary["processed"], 1)
        self.assertTrue(recreated_exists)

    def test_sync_once_rewrites_note_when_thread_name_changes_without_new_messages(self) -> None:
        session_id = "019d23a7-9258-7810-93cc-c6833b3481d6"

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            codex_home = root / ".codex"
            sessions_dir = codex_home / "sessions" / "2026" / "04" / "03"
            sessions_dir.mkdir(parents=True)
            vault = root / "vault"
            vault.mkdir()
            state_file = codex_home / "obsidian-sync" / "sync-state.json"
            index_path = codex_home / "session_index.jsonl"
            rollout_path = sessions_dir / f"rollout-2026-04-03T10-00-00-{session_id}.jsonl"

            self._write_jsonl(
                index_path,
                [{"id": session_id, "thread_name": "old title", "updated_at": "2026-04-03T01:00:00Z"}],
            )
            self._write_jsonl(
                rollout_path,
                [
                    {
                        "timestamp": "2026-04-03T01:00:00Z",
                        "type": "session_meta",
                        "payload": {
                            "id": session_id,
                            "timestamp": "2026-04-03T01:00:00Z",
                            "cwd": "/tmp/demo-project",
                            "originator": "Codex Desktop",
                            "source": "vscode",
                        },
                    },
                    self._message_record(timestamp="2026-04-03T01:00:01Z", role="user", text="same transcript"),
                ],
            )

            config = self._config(codex_home=codex_home, vault=vault, state_file=state_file)
            sync_once(config=config)
            conversation_note = next((vault / "Codex" / "Conversations" / "2026").glob("*.md"))
            first_inode = conversation_note.stat().st_ino

            self._write_jsonl(
                index_path,
                [{"id": session_id, "thread_name": "new title", "updated_at": "2026-04-03T01:05:00Z"}],
            )

            summary = sync_once(config=config)
            rewritten_text = conversation_note.read_text(encoding="utf-8")
            second_inode = conversation_note.stat().st_ino

        self.assertEqual(summary["processed"], 1)
        self.assertEqual(summary["rewritten"], 1)
        self.assertNotEqual(first_inode, second_inode)
        self.assertIn("# new title", rewritten_text)
        self.assertIn("same transcript", rewritten_text)

    def test_sync_once_rebuilds_when_conversation_note_fingerprint_mismatches(self) -> None:
        session_id = "019d23a7-9258-7810-93cc-c6833b3481d7"

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            codex_home = root / ".codex"
            sessions_dir = codex_home / "sessions" / "2026" / "04" / "03"
            sessions_dir.mkdir(parents=True)
            vault = root / "vault"
            vault.mkdir()
            state_file = codex_home / "obsidian-sync" / "sync-state.json"
            index_path = codex_home / "session_index.jsonl"
            rollout_path = sessions_dir / f"rollout-2026-04-03T10-10-00-{session_id}.jsonl"

            self._write_jsonl(
                index_path,
                [{"id": session_id, "thread_name": "fingerprint", "updated_at": "2026-04-03T01:10:00Z"}],
            )
            self._write_jsonl(
                rollout_path,
                [
                    {
                        "timestamp": "2026-04-03T01:10:00Z",
                        "type": "session_meta",
                        "payload": {
                            "id": session_id,
                            "timestamp": "2026-04-03T01:10:00Z",
                            "cwd": "/tmp/demo-project",
                            "originator": "Codex Desktop",
                            "source": "vscode",
                        },
                    },
                    self._message_record(timestamp="2026-04-03T01:10:01Z", role="user", text="original body"),
                ],
            )

            config = self._config(codex_home=codex_home, vault=vault, state_file=state_file)
            sync_once(config=config)
            conversation_note = next((vault / "Codex" / "Conversations" / "2026").glob("*.md"))
            first_inode = conversation_note.stat().st_ino
            with conversation_note.open("a", encoding="utf-8") as handle:
                handle.write("\nmanual edit\n")

            summary = sync_once(config=config)
            rebuilt_text = conversation_note.read_text(encoding="utf-8")
            second_inode = conversation_note.stat().st_ino

        self.assertEqual(summary["processed"], 1)
        self.assertEqual(summary["rewritten"], 1)
        self.assertNotEqual(first_inode, second_inode)
        self.assertNotIn("manual edit", rebuilt_text)
        self.assertIn("original body", rebuilt_text)

    def test_sync_once_backfills_append_metadata_when_old_state_is_missing_fields(self) -> None:
        session_id = "019d23a7-9258-7810-93cc-c6833b3481d8"

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            codex_home = root / ".codex"
            sessions_dir = codex_home / "sessions" / "2026" / "04" / "03"
            sessions_dir.mkdir(parents=True)
            vault = root / "vault"
            vault.mkdir()
            state_file = codex_home / "obsidian-sync" / "sync-state.json"
            index_path = codex_home / "session_index.jsonl"
            rollout_path = sessions_dir / f"rollout-2026-04-03T10-20-00-{session_id}.jsonl"

            self._write_jsonl(
                index_path,
                [{"id": session_id, "thread_name": "old state", "updated_at": "2026-04-03T01:20:00Z"}],
            )
            self._write_jsonl(
                rollout_path,
                [
                    {
                        "timestamp": "2026-04-03T01:20:00Z",
                        "type": "session_meta",
                        "payload": {
                            "id": session_id,
                            "timestamp": "2026-04-03T01:20:00Z",
                            "cwd": "/tmp/demo-project",
                            "originator": "Codex Desktop",
                            "source": "vscode",
                        },
                    },
                    self._message_record(timestamp="2026-04-03T01:20:01Z", role="user", text="legacy state"),
                ],
            )

            config = self._config(codex_home=codex_home, vault=vault, state_file=state_file)
            sync_once(config=config)
            conversation_note = next((vault / "Codex" / "Conversations" / "2026").glob("*.md"))
            first_inode = conversation_note.stat().st_ino

            state = json.loads(state_file.read_text(encoding="utf-8"))
            entry = state["files"][str(rollout_path)]
            entry.pop("last_written_message_count", None)
            entry.pop("last_written_message_key", None)
            entry.pop("header_hash", None)
            entry.pop("conversation_note_fingerprint", None)
            state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

            with rollout_path.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        self._message_record(
                            timestamp="2026-04-03T01:20:02Z",
                            role="assistant",
                            phase="final_answer",
                            text="backfill response",
                        ),
                        ensure_ascii=False,
                    )
                )
                handle.write("\n")

            summary = sync_once(config=config)
            refreshed_state = json.loads(state_file.read_text(encoding="utf-8"))
            refreshed_entry = refreshed_state["files"][str(rollout_path)]
            second_inode = conversation_note.stat().st_ino

        self.assertEqual(summary["processed"], 1)
        self.assertEqual(summary["rewritten"], 1)
        self.assertNotEqual(first_inode, second_inode)
        self.assertIn("last_written_message_count", refreshed_entry)
        self.assertIn("conversation_note_fingerprint", refreshed_entry)

    def test_sync_once_recovers_when_note_write_succeeds_but_state_save_fails(self) -> None:
        session_id = "019d23a7-9258-7810-93cc-c6833b3481d9"

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            codex_home = root / ".codex"
            sessions_dir = codex_home / "sessions" / "2026" / "04" / "03"
            sessions_dir.mkdir(parents=True)
            vault = root / "vault"
            vault.mkdir()
            state_file = codex_home / "obsidian-sync" / "sync-state.json"
            index_path = codex_home / "session_index.jsonl"
            rollout_path = sessions_dir / f"rollout-2026-04-03T10-30-00-{session_id}.jsonl"

            self._write_jsonl(
                index_path,
                [{"id": session_id, "thread_name": "state save", "updated_at": "2026-04-03T01:30:00Z"}],
            )
            self._write_jsonl(
                rollout_path,
                [
                    {
                        "timestamp": "2026-04-03T01:30:00Z",
                        "type": "session_meta",
                        "payload": {
                            "id": session_id,
                            "timestamp": "2026-04-03T01:30:00Z",
                            "cwd": "/tmp/demo-project",
                            "originator": "Codex Desktop",
                            "source": "vscode",
                        },
                    },
                    self._message_record(timestamp="2026-04-03T01:30:01Z", role="user", text="before append"),
                ],
            )

            config = self._config(codex_home=codex_home, vault=vault, state_file=state_file)
            sync_once(config=config)
            conversation_note = next((vault / "Codex" / "Conversations" / "2026").glob("*.md"))

            with rollout_path.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        self._message_record(
                            timestamp="2026-04-03T01:30:02Z",
                            role="assistant",
                            phase="final_answer",
                            text="single appended reply",
                        ),
                        ensure_ascii=False,
                    )
                )
                handle.write("\n")

            with patch("codex_obsidian_sync.sync.save_state", side_effect=RuntimeError("boom")):
                with self.assertRaises(RuntimeError):
                    sync_once(config=config)

            recovery_summary = sync_once(config=config)
            recovered_text = conversation_note.read_text(encoding="utf-8")

        self.assertEqual(recovery_summary["processed"], 1)
        self.assertEqual(recovered_text.count("single appended reply"), 1)

    def test_sync_once_indexes_old_session_under_latest_activity_day(self) -> None:
        session_id = "019d23a7-9258-7810-93cc-c6833b3481d5"

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            codex_home = root / ".codex"
            sessions_dir = codex_home / "sessions" / "2026" / "03" / "26"
            sessions_dir.mkdir(parents=True)
            vault = root / "vault"
            vault.mkdir()
            state_file = codex_home / "obsidian-sync" / "sync-state.json"
            index_path = codex_home / "session_index.jsonl"
            rollout_path = sessions_dir / f"rollout-2026-03-26T10-00-00-{session_id}.jsonl"

            self._write_jsonl(
                index_path,
                [
                    {
                        "id": session_id,
                        "thread_name": "장기 세션",
                        "updated_at": "2026-03-26T01:00:00Z",
                    }
                ],
            )
            self._write_jsonl(
                rollout_path,
                [
                    {
                        "timestamp": "2026-02-20T06:00:00Z",
                        "type": "session_meta",
                        "payload": {
                            "id": session_id,
                            "timestamp": "2026-02-20T06:00:00Z",
                            "cwd": "/tmp/demo-project",
                            "originator": "Codex Desktop",
                            "source": "vscode",
                        },
                    },
                    self._message_record(
                        timestamp="2026-02-20T06:00:10Z",
                        role="user",
                        text="한 달 전 질문",
                    ),
                    self._message_record(
                        timestamp="2026-03-26T01:00:00Z",
                        role="assistant",
                        phase="final_answer",
                        text="오늘 이어진 답변",
                    ),
                ],
            )

            sync_once(config=self._config(codex_home=codex_home, vault=vault, state_file=state_file))
            started_daily = vault / "Codex" / "Daily" / "2026-02-20.md"
            latest_daily = vault / "Codex" / "Daily" / "2026-03-26.md"
            conversation_note = next((vault / "Codex" / "Conversations" / "2026").glob("*.md"))
            started_daily_exists = started_daily.exists()
            latest_daily_exists = latest_daily.exists()
            latest_daily_text = latest_daily.read_text(encoding="utf-8")
            conversation_note_name = conversation_note.name

        self.assertTrue(started_daily_exists)
        self.assertTrue(latest_daily_exists)
        self.assertIn("장기 세션", latest_daily_text)
        self.assertTrue(conversation_note_name.startswith("2026-02-20-"))

    def test_sync_once_skips_invalid_rollout_without_blocking_valid_sessions(self) -> None:
        valid_session_id = "019d23a7-9258-7810-93cc-c6833b3481d0"
        invalid_session_id = "019d23a7-9258-7810-93cc-c6833b3481d1"

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            codex_home = root / ".codex"
            sessions_dir = codex_home / "sessions" / "2026" / "03" / "25"
            sessions_dir.mkdir(parents=True)
            vault = root / "vault"
            vault.mkdir()
            state_file = codex_home / "obsidian-sync" / "sync-state.json"
            index_path = codex_home / "session_index.jsonl"
            valid_rollout = sessions_dir / f"rollout-2026-03-25T15-21-17-{valid_session_id}.jsonl"
            invalid_rollout = sessions_dir / f"rollout-2026-03-25T15-22-17-{invalid_session_id}.jsonl"

            self._write_jsonl(
                index_path,
                [
                    {"id": valid_session_id, "thread_name": "valid", "updated_at": "2026-03-25T06:30:00Z"},
                    {"id": invalid_session_id, "thread_name": "invalid", "updated_at": "2026-03-25T06:29:00Z"},
                ],
            )
            self._write_jsonl(
                valid_rollout,
                [
                    {
                        "timestamp": "2026-03-25T06:21:17Z",
                        "type": "session_meta",
                        "payload": {
                            "id": valid_session_id,
                            "timestamp": "2026-03-25T06:21:17Z",
                            "cwd": "/tmp/demo-project",
                            "originator": "Codex Desktop",
                            "source": "vscode",
                        },
                    },
                    self._message_record(
                        timestamp="2026-03-25T06:21:19Z",
                        role="user",
                        text="정상 세션",
                    ),
                ],
            )
            invalid_rollout.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "timestamp": "2026-03-25T06:21:17Z",
                                "type": "session_meta",
                                "payload": {
                                    "id": invalid_session_id,
                                    "timestamp": "2026-03-25T06:21:17Z",
                                    "cwd": "/tmp/demo-project",
                                    "originator": "Codex Desktop",
                                    "source": "vscode",
                                },
                            },
                            ensure_ascii=False,
                        ),
                        '{"timestamp":"2026-03-25T06:21:19Z","type":"response_item","payload":',
                    ]
                    + [""]
                ),
                encoding="utf-8",
            )

            summary = sync_once(config=self._config(codex_home=codex_home, vault=vault, state_file=state_file))
            conversation_notes = list((vault / "Codex" / "Conversations" / "2026").glob("*.md"))

        self.assertEqual(summary["processed"], 1)
        self.assertEqual(summary["skipped_invalid"], 1)
        self.assertEqual(len(conversation_notes), 1)

    def test_sync_once_supports_pause_and_resume_when_vault_is_temporarily_missing(self) -> None:
        session_id = "019d23a7-9258-7810-93cc-c6833b3481d2"

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            codex_home = root / ".codex"
            sessions_dir = codex_home / "sessions" / "2026" / "03" / "25"
            sessions_dir.mkdir(parents=True)
            vault = root / "vault"
            state_file = codex_home / "obsidian-sync" / "sync-state.json"
            index_path = codex_home / "session_index.jsonl"
            rollout_path = sessions_dir / f"rollout-2026-03-25T15-21-17-{session_id}.jsonl"

            self._write_jsonl(
                index_path,
                [{"id": session_id, "thread_name": "resume", "updated_at": "2026-03-25T06:30:00Z"}],
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
                    self._message_record(timestamp="2026-03-25T06:21:19Z", role="user", text="복구 테스트"),
                ],
            )

            config = self._config(codex_home=codex_home, vault=vault, state_file=state_file)
            paused_summary = sync_once(config=config, allow_pause=True)
            vault.mkdir()
            resumed_summary = sync_once(config=config, allow_pause=True)

        self.assertEqual(paused_summary["paused"], 1)
        self.assertEqual(resumed_summary["processed"], 1)

    def test_sync_once_uses_fast_path_when_sources_are_unchanged(self) -> None:
        session_id = "019d23a7-9258-7810-93cc-c6833b3481d3"

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            codex_home = root / ".codex"
            sessions_dir = codex_home / "sessions" / "2026" / "03" / "25"
            sessions_dir.mkdir(parents=True)
            vault = root / "vault"
            vault.mkdir()
            state_file = codex_home / "obsidian-sync" / "sync-state.json"
            index_path = codex_home / "session_index.jsonl"
            rollout_path = sessions_dir / f"rollout-2026-03-25T15-21-17-{session_id}.jsonl"

            self._write_jsonl(
                index_path,
                [{"id": session_id, "thread_name": "fast path", "updated_at": "2026-03-25T06:30:00Z"}],
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
                    self._message_record(timestamp="2026-03-25T06:21:19Z", role="user", text="빠른 경로"),
                ],
            )

            config = self._config(codex_home=codex_home, vault=vault, state_file=state_file)
            first_summary = sync_once(config=config)
            second_summary = sync_once(config=config)

        self.assertEqual(first_summary["processed"], 1)
        self.assertEqual(second_summary["fast_path"], 1)

    def test_sync_once_drains_capped_batches_before_fast_path(self) -> None:
        first_session_id = "019d23a7-9258-7810-93cc-c6833b3481e0"
        second_session_id = "019d23a7-9258-7810-93cc-c6833b3481e1"

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            codex_home = root / ".codex"
            sessions_dir = codex_home / "sessions" / "2026" / "04" / "03"
            sessions_dir.mkdir(parents=True)
            vault = root / "vault"
            vault.mkdir()
            state_file = codex_home / "obsidian-sync" / "sync-state.json"
            index_path = codex_home / "session_index.jsonl"
            first_rollout = sessions_dir / f"rollout-2026-04-03T10-40-00-{first_session_id}.jsonl"
            second_rollout = sessions_dir / f"rollout-2026-04-03T10-41-00-{second_session_id}.jsonl"

            self._write_jsonl(
                index_path,
                [
                    {"id": first_session_id, "thread_name": "first capped", "updated_at": "2026-04-03T01:41:00Z"},
                    {"id": second_session_id, "thread_name": "second capped", "updated_at": "2026-04-03T01:40:00Z"},
                ],
            )
            self._write_jsonl(
                first_rollout,
                [
                    self._session_meta(session_id=first_session_id, timestamp="2026-04-03T01:41:00Z"),
                    self._message_record(timestamp="2026-04-03T01:41:01Z", role="user", text="first capped body"),
                ],
            )
            self._write_jsonl(
                second_rollout,
                [
                    self._session_meta(session_id=second_session_id, timestamp="2026-04-03T01:40:00Z"),
                    self._message_record(timestamp="2026-04-03T01:40:01Z", role="user", text="second capped body"),
                ],
            )

            config = self._config(codex_home=codex_home, vault=vault, state_file=state_file, candidate_file_limit=1)
            first_summary = sync_once(config=config)
            second_summary = sync_once(config=config)
            notes = list((vault / "Codex" / "Conversations" / "2026").glob("*.md"))

        self.assertEqual(first_summary["processed"], 1)
        self.assertEqual(first_summary["fast_path"], 0)
        self.assertEqual(second_summary["processed"], 1)
        self.assertEqual(second_summary["fast_path"], 0)
        self.assertEqual(len(notes), 2)

    def test_sync_once_reapplies_subagent_opt_out(self) -> None:
        session_id = "019d23a7-9258-7810-93cc-c6833b3481e2"

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            codex_home = root / ".codex"
            sessions_dir = codex_home / "sessions" / "2026" / "04" / "03"
            sessions_dir.mkdir(parents=True)
            vault = root / "vault"
            vault.mkdir()
            state_file = codex_home / "obsidian-sync" / "sync-state.json"
            index_path = codex_home / "session_index.jsonl"
            rollout_path = sessions_dir / f"rollout-2026-04-03T10-42-00-{session_id}.jsonl"

            self._write_jsonl(
                index_path,
                [{"id": session_id, "thread_name": "subagent title", "updated_at": "2026-04-03T01:42:00Z"}],
            )
            self._write_jsonl(
                rollout_path,
                [
                    self._session_meta(
                        session_id=session_id,
                        timestamp="2026-04-03T01:42:00Z",
                        source={"subagent": {"thread_spawn": {"parent_thread_id": "parent-session"}}},
                    ),
                    self._message_record(timestamp="2026-04-03T01:42:01Z", role="user", text="subagent body"),
                ],
            )

            sync_once(
                config=self._config(
                    codex_home=codex_home,
                    vault=vault,
                    state_file=state_file,
                    include_subagents=True,
                )
            )
            sync_once(
                config=self._config(
                    codex_home=codex_home,
                    vault=vault,
                    state_file=state_file,
                    include_subagents=False,
                )
            )
            state = json.loads(state_file.read_text(encoding="utf-8"))
            entry = state["files"][str(rollout_path)]
            daily_text = (vault / "Codex" / "Daily" / "2026-04-03.md").read_text(encoding="utf-8")
            project_text = (vault / "Codex" / "Projects" / "demo-project.md").read_text(encoding="utf-8")

        self.assertFalse(entry["included"])
        self.assertNotIn("subagent title", daily_text)
        self.assertNotIn("subagent title", project_text)

    def test_sync_once_surfaces_note_write_failures(self) -> None:
        session_id = "019d23a7-9258-7810-93cc-c6833b3481e3"

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            codex_home = root / ".codex"
            sessions_dir = codex_home / "sessions" / "2026" / "04" / "03"
            sessions_dir.mkdir(parents=True)
            vault = root / "vault"
            vault.mkdir()
            state_file = codex_home / "obsidian-sync" / "sync-state.json"
            index_path = codex_home / "session_index.jsonl"
            rollout_path = sessions_dir / f"rollout-2026-04-03T10-43-00-{session_id}.jsonl"

            self._write_jsonl(
                index_path,
                [{"id": session_id, "thread_name": "write failure", "updated_at": "2026-04-03T01:43:00Z"}],
            )
            self._write_jsonl(
                rollout_path,
                [
                    self._session_meta(session_id=session_id, timestamp="2026-04-03T01:43:00Z"),
                    self._message_record(timestamp="2026-04-03T01:43:01Z", role="user", text="write failure body"),
                ],
            )

            with patch("codex_obsidian_sync.sync.write_conversation_note", side_effect=PermissionError("denied")):
                with self.assertRaises(PermissionError):
                    sync_once(config=self._config(codex_home=codex_home, vault=vault, state_file=state_file))

    def test_sync_state_does_not_cache_raw_transcript_text(self) -> None:
        session_id = "019d23a7-9258-7810-93cc-c6833b3481e4"
        raw_secret = "ghp_" + "1234567890abcdefghijklmnopqrstuvwxyz1234"

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            codex_home = root / ".codex"
            sessions_dir = codex_home / "sessions" / "2026" / "04" / "03"
            sessions_dir.mkdir(parents=True)
            vault = root / "vault"
            vault.mkdir()
            state_file = codex_home / "obsidian-sync" / "sync-state.json"
            index_path = codex_home / "session_index.jsonl"
            rollout_path = sessions_dir / f"rollout-2026-04-03T10-44-00-{session_id}.jsonl"

            self._write_jsonl(
                index_path,
                [{"id": session_id, "thread_name": "cache privacy", "updated_at": "2026-04-03T01:44:00Z"}],
            )
            self._write_jsonl(
                rollout_path,
                [
                    self._session_meta(session_id=session_id, timestamp="2026-04-03T01:44:00Z"),
                    self._message_record(timestamp="2026-04-03T01:44:01Z", role="user", text="cache privacy body"),
                    self._message_record(
                        timestamp="2026-04-03T01:44:02Z",
                        role="assistant",
                        phase="final_answer",
                        text=f"secret {raw_secret}",
                    ),
                ],
            )

            sync_once(config=self._config(codex_home=codex_home, vault=vault, state_file=state_file))
            state_text = state_file.read_text(encoding="utf-8")

        self.assertNotIn("envelope_cache", state_text)
        self.assertNotIn(raw_secret, state_text)

    def test_sync_once_refreshes_thread_name_during_append(self) -> None:
        session_id = "019d23a7-9258-7810-93cc-c6833b3481e5"

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            codex_home = root / ".codex"
            sessions_dir = codex_home / "sessions" / "2026" / "04" / "03"
            sessions_dir.mkdir(parents=True)
            vault = root / "vault"
            vault.mkdir()
            state_file = codex_home / "obsidian-sync" / "sync-state.json"
            index_path = codex_home / "session_index.jsonl"
            rollout_path = sessions_dir / f"rollout-2026-04-03T10-45-00-{session_id}.jsonl"

            self._write_jsonl(
                index_path,
                [{"id": session_id, "thread_name": "old append title", "updated_at": "2026-04-03T01:45:00Z"}],
            )
            self._write_jsonl(
                rollout_path,
                [
                    self._session_meta(session_id=session_id, timestamp="2026-04-03T01:45:00Z"),
                    self._message_record(timestamp="2026-04-03T01:45:01Z", role="user", text="append title body"),
                ],
            )

            config = self._config(codex_home=codex_home, vault=vault, state_file=state_file)
            sync_once(config=config)
            conversation_note = next((vault / "Codex" / "Conversations" / "2026").glob("*.md"))

            self._write_jsonl(
                index_path,
                [{"id": session_id, "thread_name": "new append title", "updated_at": "2026-04-03T01:46:00Z"}],
            )
            with rollout_path.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        self._message_record(
                            timestamp="2026-04-03T01:46:01Z",
                            role="assistant",
                            phase="final_answer",
                            text="append title response",
                        ),
                        ensure_ascii=False,
                    )
                )
                handle.write("\n")

            summary = sync_once(config=config)
            conversation_text = conversation_note.read_text(encoding="utf-8")

        self.assertEqual(summary["processed"], 1)
        self.assertIn("# new append title", conversation_text)
        self.assertIn("append title response", conversation_text)

    def test_sync_once_does_not_modify_codex_source_files(self) -> None:
        session_id = "019d23a7-9258-7810-93cc-c6833b3481d4"

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            codex_home = root / ".codex"
            sessions_dir = codex_home / "sessions" / "2026" / "03" / "25"
            sessions_dir.mkdir(parents=True)
            vault = root / "vault"
            vault.mkdir()
            state_file = codex_home / "obsidian-sync" / "sync-state.json"
            index_path = codex_home / "session_index.jsonl"
            rollout_path = sessions_dir / f"rollout-2026-03-25T15-21-17-{session_id}.jsonl"

            self._write_jsonl(
                index_path,
                [{"id": session_id, "thread_name": "immutable", "updated_at": "2026-03-25T06:30:00Z"}],
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
                    self._message_record(timestamp="2026-03-25T06:21:19Z", role="user", text="원본 불변"),
                ],
            )

            before_index = index_path.read_bytes()
            before_rollout = rollout_path.read_bytes()
            sync_once(config=self._config(codex_home=codex_home, vault=vault, state_file=state_file))
            after_index = index_path.read_bytes()
            after_rollout = rollout_path.read_bytes()

        self.assertEqual(before_index, after_index)
        self.assertEqual(before_rollout, after_rollout)

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
    def _session_meta(
        *,
        session_id: str,
        timestamp: str,
        source: object = "vscode",
    ) -> dict[str, object]:
        return {
            "timestamp": timestamp,
            "type": "session_meta",
            "payload": {
                "id": session_id,
                "timestamp": timestamp,
                "cwd": "/tmp/demo-project",
                "originator": "Codex Desktop",
                "source": source,
            },
        }

    @staticmethod
    def _write_jsonl(path: Path, records: list[dict[str, object]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False))
                handle.write("\n")

    @staticmethod
    def _config(
        *,
        codex_home: Path,
        vault: Path,
        state_file: Path,
        include_subagents: bool = False,
        candidate_file_limit: int = 100,
    ):
        return resolve_sync_config(
            vault=vault,
            codex_home=codex_home,
            state_file=state_file,
            lock_file=state_file.with_suffix(".lock"),
            include_subagents=include_subagents,
            interval_seconds=10,
            recent_days=30,
            candidate_file_limit=candidate_file_limit,
            candidate_bytes_limit=500 * 1024 * 1024,
            log_level="ERROR",
            config_data={},
        )


if __name__ == "__main__":
    unittest.main()
