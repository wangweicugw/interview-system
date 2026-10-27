from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_mistake_book import build  # noqa: E402
from render_interview_html import discover_cases, render_mistake_book  # noqa: E402


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def build_case(root: Path, name: str, case_id: str, claims: list[dict]) -> Path:
    case = root / "interviews" / name
    case.mkdir(parents=True)
    write_json(case / "manifest.json", {"schema_version": 1, "case_id": case_id, "date": "2026-01-01"})
    write_json(case / "transcript.raw.json", {"schema_version": 1, "segments": []})
    write_json(case / "speaker-map.json", {"schema_version": 1})
    write_json(case / "qa-tree.json", {
        "schema_version": 1,
        "case_id": case_id,
        "questions": [{
            "question_id": "q-001",
            "question": "ConcurrentHashMap 在 JDK 8 如何保证线程安全？",
            "category": "technical",
        }],
    })
    write_json(case / "claims.json", {"schema_version": 1, "case_id": case_id, "claims": claims})
    return case


def claim(claim_id: str, verdict: str, text: str, *, sources: list[dict] | None = None) -> dict:
    return {
        "claim_id": claim_id,
        "question_id": "q-001",
        "claim": text,
        "version_context": "JDK 8",
        "verdict": verdict,
        "evidence_segments": [],
        "sources": sources if sources is not None else [],
        "explanation": "说明",
    }


def make_root(root: Path) -> None:
    write_json(root / "config.json", {"schema_version": 1, "created_at": "2026-01-01T00:00:00+00:00"})


class MistakeBookBuildTest(unittest.TestCase):
    def test_only_incorrect_claims_are_collected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            make_root(root)
            build_case(root, "case-a", "int-a", [
                claim("c-001", "incorrect", "JDK 8 的 CHM 仍以 Segment 为主", sources=[{
                    "title": "OpenJDK 8u", "section": "Implementation", "url": "https://example.com/chm",
                    "accessed_at": "2026-01-01", "relation": "refute", "summary": "JDK 8 改为 CAS 加 synchronized。",
                }]),
                claim("c-002", "unverified", "某阈值大约是一万 TPS"),
                claim("c-003", "correct", "HashMap 不是线程安全的"),
            ])
            build_case(root, "case-b", "int-b", [
                claim("c-001", "incorrect", "G1 的 Mixed GC 等同 Full GC"),
            ])

            result = build(root)
            self.assertEqual(result["entries"], 2)
            self.assertEqual(result["with_correction"], 0)
            self.assertEqual(sorted(result["missing_correction"]), ["int-a/c-001", "int-b/c-001"])

            payload = json.loads((root / "profile" / "mistake-book.json").read_text(encoding="utf-8"))
            self.assertEqual([item["claim"] for item in payload["entries"]], [
                "JDK 8 的 CHM 仍以 Segment 为主",
                "G1 的 Mixed GC 等同 Full GC",
            ])
            self.assertEqual(payload["entries"][0]["topic"], "java")

    def test_notes_supply_correction_and_topic_override(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            make_root(root)
            build_case(root, "case-a", "int-a", [
                claim("c-001", "incorrect", "MySQL 5.7.44 升级使 SSL 默认开启"),
            ])
            write_json(root / "profile" / "mistake-book-notes.json", {
                "schema_version": 1,
                "entries": {
                    "int-a/c-001": {
                        "topic": "mysql",
                        "correction": "默认启用 SSL 早于 5.7.44。",
                        "memory_hook": "5.7.5 服务端默认开 SSL",
                        "recall_prompt": "先确认客户端版本与 sslMode。",
                    },
                },
            })

            build(root)
            payload = json.loads((root / "profile" / "mistake-book.json").read_text(encoding="utf-8"))
            entry = payload["entries"][0]
            self.assertEqual(entry["topic"], "mysql")
            self.assertEqual(entry["topic_label"], "MySQL 原理与实践")
            self.assertEqual(entry["correction"], "默认启用 SSL 早于 5.7.44。")
            self.assertEqual(entry["memory_hook"], "5.7.5 服务端默认开 SSL")

    def test_unknown_topic_override_falls_back_to_other(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            make_root(root)
            build_case(root, "case-a", "int-a", [claim("c-001", "incorrect", "某结论")])
            write_json(root / "profile" / "mistake-book-notes.json", {
                "schema_version": 1,
                "entries": {"int-a/c-001": {"topic": "不存在的主题", "correction": "更正"}},
            })

            build(root)
            payload = json.loads((root / "profile" / "mistake-book.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["entries"][0]["topic"], "other")


class MistakeBookRenderTest(unittest.TestCase):
    def test_page_warns_when_claims_changed_after_build(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            make_root(root)
            case = build_case(root, "case-a", "int-a", [claim("c-001", "incorrect", "某结论")])
            build(root)

            fresh = render_mistake_book(root, discover_cases(root))
            self.assertNotIn("不一致", fresh)
            self.assertIn("data-mistake-entry", fresh)

            write_json(case / "claims.json", {"schema_version": 1, "case_id": "int-a", "claims": [
                claim("c-001", "incorrect", "某结论"),
                claim("c-002", "incorrect", "新发现的错误"),
            ]})
            stale = render_mistake_book(root, discover_cases(root))
            self.assertIn("不一致", stale)
            self.assertIn("build_mistake_book.py", stale)

    def test_page_warns_when_claim_changes_without_count_change(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            make_root(root)
            case = build_case(root, "case-a", "int-a", [claim("c-001", "incorrect", "旧说法")])
            build(root)
            write_json(case / "claims.json", {"schema_version": 1, "case_id": "int-a", "claims": [
                claim("c-001", "incorrect", "已修改的新说法"),
            ]})

            page = render_mistake_book(root, discover_cases(root))
            self.assertIn("不一致", page)
            self.assertIn("旧说法", page)
            self.assertNotIn("已修改的新说法", page)

    def test_page_without_book_points_to_builder(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            make_root(root)
            build_case(root, "case-a", "int-a", [claim("c-001", "incorrect", "某结论")])

            page = render_mistake_book(root, discover_cases(root))
            self.assertIn("尚未生成", page)
            self.assertIn("错题本", page)


if __name__ == "__main__":
    unittest.main()
