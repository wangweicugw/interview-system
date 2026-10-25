from __future__ import annotations

import multiprocessing
import json
import subprocess
import tempfile
import time
import unittest
from unittest import mock
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from common import (  # noqa: E402
    commit_write_transaction,
    data_root_write_lock,
    recover_write_transactions,
)
from apply_review_actions import apply_actions  # noqa: E402
from demo_smoke import run_demo  # noqa: E402
from profile_store import load_revisions  # noqa: E402
from render_interview_html import practice_attempt_history  # noqa: E402
from record_practice_attempt import _record_attempt_unlocked  # noqa: E402


def _hold_lock(root_text: str, ready: multiprocessing.Queue[str]) -> None:
    root = Path(root_text)
    with data_root_write_lock(root):
        ready.put("locked")
        time.sleep(0.35)


class WriteTransactionTest(unittest.TestCase):
    def test_unfinished_transaction_restores_previous_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "profile" / "standard-answers.json"
            target.parent.mkdir(parents=True)
            target.write_text("old\n", encoding="utf-8")
            commit_write_transaction(root, {target: b"new\n"}, "case-crash", finalize=False)
            self.assertEqual(target.read_text(encoding="utf-8"), "new\n")
            recover_write_transactions(root)
            self.assertEqual(target.read_text(encoding="utf-8"), "old\n")
            self.assertFalse((root / "logs" / "write-transactions" / "case-crash.json").exists())

    def test_lock_is_shared_across_processes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ready: multiprocessing.Queue[str] = multiprocessing.Queue()
            process = multiprocessing.Process(target=_hold_lock, args=(str(root), ready))
            process.start()
            self.assertEqual(ready.get(timeout=3), "locked")
            started = time.monotonic()
            with data_root_write_lock(root):
                elapsed = time.monotonic() - started
            process.join(timeout=3)
            self.assertEqual(process.exitcode, 0)
            self.assertGreaterEqual(elapsed, 0.25)

    def test_practice_batch_rolls_back_after_second_file_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(
                [sys.executable, str(Path(__file__).resolve().parents[1] / "scripts" / "init_store.py"), "--root", str(root)],
                check=True, capture_output=True, text=True,
            )
            case_id = "2026-01-01-demo"
            case = root / "interviews" / "case"
            case.mkdir(parents=True)
            (case / "qa-tree.json").write_text(json.dumps({
                "schema_version": 1, "case_id": case_id,
                "questions": [{"question_id": "q-001", "question": "虚构问题"}],
            }), encoding="utf-8")
            (case / "manifest.json").write_text(json.dumps({"schema_version": 1, "case_id": case_id}), encoding="utf-8")
            (case / "transcript.raw.json").write_text(json.dumps({"schema_version": 1, "case_id": case_id, "segments": []}), encoding="utf-8")
            (case / "speaker-map.json").write_text(json.dumps({"schema_version": 1, "case_id": case_id, "status": "confirmed", "speakers": []}), encoding="utf-8")
            import common
            original_replace = common.os.replace

            def fail_attempt_index(source, destination):
                if str(destination).endswith("attempts.jsonl"):
                    raise OSError("simulated crash")
                return original_replace(source, destination)

            with mock.patch.object(common.os, "replace", side_effect=fail_attempt_index):
                with self.assertRaises(OSError):
                    _record_attempt_unlocked(
                        root, case_id=case_id, question_id="q-001", answer="虚构回答",
                        duration_seconds=30, self_rating=3, attempt_id="practice-0123456789abcdef",
                    )
            self.assertFalse((root / "practice" / "private" / "practice-0123456789abcdef.txt").exists())
            self.assertFalse((root / "practice" / "attempts.jsonl").exists())

    def test_retry_refreshes_derived_outputs_after_core_commit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "store"
            run_demo(root)
            event = load_revisions(root)[0]
            action_file = root / "actions.json"
            action_file.write_text(json.dumps({"schema_version": 1, "actions": [{
                "kind": "profile_event", "id": event["event_id"], "status": "confirmed",
                "expected_status": event["status"], "reason": "虚构故障注入测试",
            }]}), encoding="utf-8")
            with mock.patch("apply_review_actions.rebuild_profile", side_effect=RuntimeError("injected")):
                partial = apply_actions(root, action_file)
            self.assertEqual(partial["status"], "partial")
            self.assertTrue(partial["derived_refresh_required"])
            recovered = apply_actions(root, action_file)
            self.assertEqual(recovered["status"], "applied")
            self.assertFalse(recovered["derived_refresh_required"])

    def test_practice_history_reports_corruption_and_escapes_answer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = root / "interviews" / "case"
            case.mkdir(parents=True)
            (case / "qa-tree.json").write_text(json.dumps({
                "schema_version": 1, "case_id": "fictional-case",
                "questions": [{"question_id": "q-001", "question": "虚构问题"}],
            }), encoding="utf-8")
            private = root / "practice" / "private"
            private.mkdir(parents=True)
            answer_path = private / "practice-0123456789abcdef.txt"
            answer_path.write_text("<script>alert('x')</script>", encoding="utf-8")
            log = root / "practice" / "attempts.jsonl"
            log.write_text("坏 JSON\n" + json.dumps({
                "case_id": "fictional-case", "question_id": "q-001",
                "recorded_at": "2026-01-01T00:00:00+00:00", "self_rating": 4,
                "answer_path": "practice/private/practice-0123456789abcdef.txt",
            }, ensure_ascii=False) + "\n", encoding="utf-8")
            html = practice_attempt_history(case, "q-001")
            self.assertIn("练习记录索引需检查", html)
            self.assertIn("&lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt;", html)
            self.assertNotIn("<script>alert", html)


if __name__ == "__main__":
    unittest.main()
