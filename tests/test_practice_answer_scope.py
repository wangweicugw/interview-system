from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from check_practice_answers import check  # noqa: E402


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def build_case(root: Path, name: str, case_id: str, question_ids: list[str]) -> Path:
    case = root / "interviews" / name
    case.mkdir(parents=True)
    write_json(case / "manifest.json", {"case_id": case_id, "date": "2026-01-01"})
    write_json(case / "qa-tree.json", {
        "schema_version": 1,
        "case_id": case_id,
        "questions": [
            {"question_id": qid, "question": f"问题 {qid}", "category": "technical"}
            for qid in question_ids
        ],
    })
    return case


def answer(heading: str, body: str, sources: list[dict] | None = None) -> dict:
    payload = {"scope": "适用前提", "steps": [{"heading": heading, "body": body}], "validation": "复述并验证"}
    if sources is not None:
        payload["sources"] = sources
    return payload


class PracticeAnswerScopeTest(unittest.TestCase):
    def test_case_scope_checks_only_the_named_case(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            answered = build_case(root, "case-a", "case-a", ["q-001"])
            build_case(root, "case-b", "case-b", ["q-001"])
            write_json(answered / "practice-answers.json", {
                "schema_version": 1,
                "questions": [{
                    "question_id": "q-001",
                    "reference_answer": answer("要点", "先讲边界再讲取舍", [
                        {"title": "官方文档", "url": "https://example.com/doc"},
                    ]),
                }],
            })

            whole_store = check(root, "all")
            self.assertEqual(whole_store["status"], "invalid")
            self.assertIn("case-b/q-001", " ".join(whole_store["errors"]))

            single = check(root, "all", "case-a")
            self.assertEqual(single["status"], "valid", single["errors"])
            self.assertEqual(single["scope"], "case")
            self.assertEqual(single["case"], "case-a")
            self.assertEqual(single["main_questions"], 1)

    def test_case_scope_ignores_invalid_focus_from_another_case(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            case = build_case(root, "case-a", "case-a", ["q-001"])
            write_json(case / "practice-answers.json", {
                "schema_version": 1,
                "questions": [{"question_id": "q-001", "reference_answer": answer("要点", "完整答法")}],
            })
            (root / "profile").mkdir()
            write_json(root / "profile" / "practice-focus.json", {
                "groups": [{"questions": [{"case_id": "other-case", "question_id": "missing"}]}],
            })

            single = check(root, "all", "case-a")
            self.assertEqual(single["status"], "valid", single["errors"])
            self.assertEqual(single["main_questions"], 1)
            whole_store = check(root, "all")
            self.assertEqual(whole_store["status"], "invalid")
            self.assertIn("other-case/missing", " ".join(whole_store["errors"]))

    def test_case_scope_accepts_directory_name_and_reports_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            case = build_case(root, "2026-01-01-某公司-后端-一面", "int-1", ["q-001"])
            write_json(case / "practice-answers.json", {
                "schema_version": 1,
                "questions": [{
                    "question_id": "q-001",
                    "reference_answer": answer("要点", "答法", [
                        {"title": "官方文档", "url": "https://example.com/doc"},
                    ]),
                }],
            })

            by_name = check(root, "all", "2026-01-01-某公司-后端-一面")
            self.assertEqual(by_name["status"], "valid", by_name["errors"])
            self.assertEqual(by_name["case"], "int-1")

            by_path = check(root, "all", str(case))
            self.assertEqual(by_path["case"], "int-1")

            unknown = check(root, "all", "不存在的场次")
            self.assertEqual(unknown["status"], "invalid")
            self.assertEqual(unknown["main_questions"], 0)
            self.assertIn("未知场次", unknown["errors"][0])

    def test_answers_without_sources_are_reported_separately(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            case = build_case(root, "case-a", "case-a", ["q-001", "q-002"])
            write_json(case / "practice-answers.json", {
                "schema_version": 1,
                "questions": [
                    {"question_id": "q-001", "reference_answer": answer("要点", "有来源", [
                        {"title": "官方文档", "url": "https://example.com/doc"},
                    ])},
                    {"question_id": "q-002", "reference_answer": answer("要点", "没来源")},
                ],
            })

            result = check(root, "all", "case-a")
            self.assertEqual(result["status"], "valid", result["errors"])
            self.assertEqual(result["answers_without_sources"], ["case-a/q-002"])


if __name__ == "__main__":
    unittest.main()
