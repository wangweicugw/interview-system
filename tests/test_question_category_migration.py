from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL = Path(__file__).resolve().parents[1]
SCRIPT = SKILL / "scripts" / "normalize_question_categories.py"


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def run(root: Path, *options: str) -> tuple[subprocess.CompletedProcess[str], dict]:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), *options],
        text=True, capture_output=True, check=False,
    )
    return result, json.loads(result.stdout)


class CategoryMigrationTest(unittest.TestCase):
    def test_invalid_category_blocks_all_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            case = root / "interviews" / "case-a"
            write_json(case / "manifest.json", {"schema_version": 1, "case_id": "case-a", "date": "2026-01-01"})
            qa_path = case / "qa-tree.json"
            write_json(qa_path, {"schema_version": 1, "questions": [
                {"question_id": "q-001", "question": "系统设计", "category": "design"},
                {"question_id": "q-002", "question": "未知题型", "category": "unknown-kind"},
            ]})
            before = qa_path.read_bytes()

            result, report = run(root, "--apply")
            self.assertEqual(result.returncode, 1)
            self.assertEqual(report["status"], "invalid")
            self.assertFalse(report["applied"])
            self.assertEqual(qa_path.read_bytes(), before)
            self.assertEqual(report["remaining_non_canonical"], {"design": 1, "unknown-kind": 1})

    def test_private_overrides_are_applied_only_when_explicitly_passed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "archive"
            case = root / "interviews" / "case-a"
            write_json(case / "manifest.json", {"schema_version": 1, "case_id": "case-a", "date": "2026-01-01"})
            qa_path = case / "qa-tree.json"
            write_json(qa_path, {"schema_version": 1, "questions": [
                {"question_id": "q-001", "question": "职业规划", "category": "behavioral"},
            ]})
            overrides = Path(temporary) / "private-overrides.json"
            write_json(overrides, {"schema_version": 1, "overrides": [
                {"case_id": "case-a", "question_id": "q-001", "category": "career"},
            ]})

            dry_run, report = run(root, "--overrides", str(overrides))
            self.assertEqual(dry_run.returncode, 0)
            self.assertFalse(report["applied"])
            self.assertEqual(json.loads(qa_path.read_text())["questions"][0]["category"], "behavioral")

            applied, report = run(root, "--overrides", str(overrides), "--apply")
            self.assertEqual(applied.returncode, 0)
            self.assertTrue(report["applied"])
            self.assertEqual(json.loads(qa_path.read_text())["questions"][0]["category"], "career")


if __name__ == "__main__":
    unittest.main()
