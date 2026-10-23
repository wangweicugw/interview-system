from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import wave
import zipfile
import zlib
from html.parser import HTMLParser
from pathlib import Path
from unittest import mock


SKILL = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL / "scripts"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(SCRIPTS))

from check_target_interview_preps import validate_payload  # noqa: E402
from common import read_json, read_supported_text  # noqa: E402
from coach_runner import run_case as run_coach_case  # noqa: E402
from ingest_audio_transcript import normalize_segments, parse_artifact  # noqa: E402
from pdf_ocr import extract_pdf  # noqa: E402
from refresh_question_clusters import refresh as refresh_clusters  # noqa: E402
from render_interview_html import (  # noqa: E402
    SCRIPT,
    discover_cases,
    effective_question_preference,
    external_link,
    home_reference_view,
    home_answer_save_form,
    home_related_questions,
    jd_source,
    render_merge_builder,
    render_case,
    render_history_practice,
    render_interview_preparation,
    render_interview_preparations,
    render_question_bank,
    render_profile_event_rows,
    safe_url,
    split_priority_review,
    target_prep_evidence_href,
    write_text,
)


def run_script(
    name: str,
    *args: str,
    expect: int = 0,
    env: dict[str, str] | None = None,
) -> dict:
    process_env = os.environ.copy()
    if env:
        process_env.update(env)
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / name), *args],
        text=True,
        capture_output=True,
        check=False,
        env=process_env,
    )
    if result.returncode != expect:
        raise AssertionError(
            f"{name} exit={result.returncode}\nstdout={result.stdout}\nstderr={result.stderr}"
        )
    return json.loads(result.stdout.strip().splitlines()[-1])


class WorkflowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "interview-system"
        run_script("init_store.py", "--root", str(self.root))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_company_preparation_links_match_available_pages(self) -> None:
        payload = {
            "target": {"company": "虚构公司", "role": "AI 工程师"},
            "status": "pending",
            "questions": [{
                "question_id": "target-q-001",
                "source_type": "延伸练习题",
                "priority": "P0",
                "question": "请介绍相关项目。",
                "short_30s": "虚构回答。",
                "standard_2m": "虚构完整回答。",
                "risks": [],
                "followups": [],
                "sources": [],
                "evidence_refs": [],
            }],
            "reverse_questions": [],
        }
        prep_dir = self.root / "profile" / "target-interview-preps"
        prep_dir.mkdir(parents=True, exist_ok=True)
        (prep_dir / "fictional.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )

        listing = render_interview_preparations(self.root)
        detail = render_interview_preparation(self.root, {**payload, "_slug": "fictional"})
        self.assertNotIn('href="interview-history.html"', listing)
        self.assertNotIn('href="interview-question-bank.html"', listing)
        self.assertNotIn('href="interview-history.html"', detail)
        self.assertIn('href="interview-preparations.html"', detail)

        result, case_path = self.ingest()
        manifest = read_json(case_path / "manifest.json")
        self.assertEqual(manifest["case_id"], result["case_id"])
        listing = render_interview_preparations(self.root)
        detail = render_interview_preparation(self.root, {**payload, "_slug": "fictional"})
        self.assertIn('href="interview-history.html"', listing)
        self.assertIn('href="interview-question-bank.html"', detail)
        self.assertEqual(
            target_prep_evidence_href(self.root, result["case_id"], "q-001"),
            f"interviews/{case_path.name}/interview-review.html#practice-q-001",
        )

    def test_company_preparation_preserves_jd_provenance_and_escapes_text(self) -> None:
        payload = {
            "schema_version": 1,
            "status": "pending",
            "target": {"company": "虚构公司", "role": "数据库研发", "summary": "岗位摘要"},
            "jd": {
                "text": "职责：\n1. SQL <安全> & 稳定性",
                "source_type": "用户截图转写",
                "source_note": "职位名未显示，待核对截图。",
            },
            "questions": [{
                "question_id": "target-q-001",
                "source_type": "延伸练习题",
                "priority": "P0",
                "question": "如何设计？",
                "short_30s": "先明确边界。",
            }],
        }
        self.assertEqual(validate_payload(payload), [])
        detail = render_interview_preparation(self.root, {**payload, "_slug": "fictional"})
        self.assertIn('id="target-jd"', detail)
        self.assertIn("用户截图转写", detail)
        self.assertIn("SQL &lt;安全&gt; &amp; 稳定性", detail)
        self.assertLess(detail.index('id="target-jd"'), detail.index('id="target-q-001"'))
        payload["jd"]["source_type"] = "公开招聘页"
        self.assertTrue(any("source_url" in error for error in validate_payload(payload)))
        del payload["jd"]
        self.assertEqual(validate_payload(payload), [])
        self.assertIn("未保存岗位 JD 文字", render_interview_preparation(self.root, payload))

    def test_company_preparation_reports_saved_data_when_rendering_fails(self) -> None:
        _, case_path = self.ingest()
        manifest_path = case_path / "manifest.json"
        manifest = read_json(manifest_path)
        manifest["analysis_status"] = "stale"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        source = Path(self.temp.name) / "company-prep.json"
        source.write_text(json.dumps({
            "schema_version": 1,
            "status": "pending",
            "target": {"company": "虚构公司", "role": "AI 工程师", "summary": "虚构准备稿。"},
            "company_sources": [],
            "questions": [{
                "question_id": "target-q-001",
                "source_type": "延伸练习题",
                "priority": "P0",
                "question": "请介绍相关项目。",
                "short_30s": "虚构回答。",
                "standard_2m": "虚构完整回答。",
                "risks": [],
                "followups": [],
                "sources": [],
                "evidence_refs": [],
            }],
            "reverse_questions": [],
        }, ensure_ascii=False), encoding="utf-8")
        result = run_script(
            "upsert_target_interview_prep.py",
            "--root", str(self.root),
            "--slug", "fictional",
            "--input", str(source),
            "--render",
            expect=2,
        )
        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["data_saved"])
        self.assertFalse(result["rendered"])
        self.assertTrue((self.root / "profile" / "target-interview-preps" / "fictional.json").is_file())

    def test_cluster_refresh_rejects_invalid_changes_before_writing(self) -> None:
        for number in range(1, 4):
            case = self.root / "interviews" / f"case-{number}"
            case.mkdir()
            (case / "qa-tree.json").write_text(json.dumps({
                "case_id": f"case-{number}",
                "questions": [{"question_id": "q-001", "question": "请做自我介绍。"}],
            }, ensure_ascii=False), encoding="utf-8")
        cluster_path = self.root / "profile" / "question-clusters.json"
        cluster_path.write_text(json.dumps({"schema_version": 1, "clusters": [{
            "cluster_id": "self-intro", "label": "自我介绍", "topic": "self-intro",
            "status": "pending", "questions": [
                {"case_id": "case-1", "question_id": "q-001"},
                {"case_id": "case-2", "question_id": "q-001"},
            ],
        }]}, ensure_ascii=False), encoding="utf-8")
        before = cluster_path.read_bytes()
        with mock.patch("refresh_question_clusters.require_store_compatible"), \
             mock.patch("refresh_question_clusters.validate_question_clusters", return_value=["模拟校验失败"]):
            with self.assertRaisesRegex(RuntimeError, "模拟校验失败"):
                refresh_clusters(self.root, apply_high_confidence=True)
        self.assertEqual(cluster_path.read_bytes(), before)
        self.assertFalse((self.root / "profile" / "question-cluster-candidates.json").exists())
        with mock.patch("refresh_question_clusters.require_store_compatible"):
            result = refresh_clusters(self.root, apply_high_confidence=True)
        self.assertEqual(result["applied"], 1)
        updated = json.loads(cluster_path.read_text(encoding="utf-8"))
        self.assertIn(
            {"case_id": "case-3", "question_id": "q-001"},
            updated["clusters"][0]["questions"],
        )

    def test_generated_candidates_do_not_create_default_review_tasks(self) -> None:
        answers = [{"answer_id": "answer-1"}, {"answer_id": "answer-2"}]
        clusters = [
            {"cluster_id": f"cluster-{index}", "questions": [{}] * question_count}
            for index, question_count in enumerate((2, 9, 4, 8, 6, 7), start=1)
        ]
        clusters.sort(key=lambda item: (-len(item["questions"]), item["cluster_id"]))
        events = [
            {"event_id": "risk-1", "type": "resume_risk"},
            {"event_id": "state-1", "type": "knowledge_state"},
        ]

        priority_clusters, deferred_answers, deferred_clusters, deferred_events, priority_events = (
            split_priority_review(answers, clusters, events)
        )

        self.assertEqual(priority_clusters, [])
        self.assertEqual(deferred_answers, answers)
        self.assertEqual(
            [item["cluster_id"] for item in deferred_clusters],
            ["cluster-2", "cluster-4", "cluster-6", "cluster-5", "cluster-3", "cluster-1"],
        )
        self.assertEqual(priority_events, [])
        self.assertEqual(deferred_events, events)

    def test_profile_reminder_shows_question_and_candidate_answer(self) -> None:
        result, case_path = self.ingest()
        self.write_analysis(case_path, result["case_id"])
        cases = discover_cases(self.root)
        rows = render_profile_event_rows([{
            "event_id": "event-1",
            "case_id": result["case_id"],
            "type": "knowledge_state",
            "key": "cache/consistency",
            "evidence": ["s-0003", "s-0004"],
            "reason": "需要补失败边界。",
        }], cases)
        self.assertIn("原始问题：", rows)
        self.assertIn("为什么选择这个顺序？", rows)
        self.assertIn("我的现场回答：", rows)
        self.assertIn("AI 提醒：", rows)
        self.assertIn("知识点掌握情况", rows)

    def ingest(self) -> tuple[dict, Path]:
        result = run_script(
            "ingest_transcript.py",
            "--root",
            str(self.root),
            "--transcript",
            str(FIXTURES / "interview.md"),
            "--company",
            "示例公司",
            "--role",
            "后端工程师",
            "--round",
            "一面",
            "--date",
            "2026-09-28",
            "--jd",
            str(FIXTURES / "jd.txt"),
            "--resume",
            str(FIXTURES / "resume.txt"),
        )
        return result, Path(result["case_path"])

    def write_analysis(self, case_path: Path, case_id: str) -> None:
        qa = {
            "schema_version": 1,
            "case_id": case_id,
            "questions": [
                {
                    "question_id": "q-001",
                    "parent_id": None,
                    "kind": "main",
                    "category": "project",
                    "question": "介绍缓存项目",
                    "question_evidence": ["s-0001"],
                    "answer_evidence": ["s-0002"],
                    "relation_confidence": 1.0,
                },
                {
                    "question_id": "q-002",
                    "parent_id": "q-001",
                    "kind": "follow_up",
                    "category": "technical",
                    "question": "为什么选择这个顺序",
                    "question_evidence": ["s-0003"],
                    "answer_evidence": ["s-0004"],
                    "relation_confidence": 0.98,
                },
            ],
            "candidate_questions": [
                {"question": "如何衡量数据库稳定性", "evidence": ["s-0007"]}
            ],
        }
        claims = {
            "schema_version": 1,
            "case_id": case_id,
            "claims": [
                {
                    "claim_id": "c-001",
                    "question_id": "q-002",
                    "claim": "该顺序可以完全避免旧数据",
                    "version_context": None,
                    "verdict": "incorrect",
                    "evidence_segments": ["s-0004"],
                    "sources": [
                        {
                            "title": "示例官方文档",
                            "section": "缓存一致性限制",
                            "url": "https://example.com/official/cache",
                            "accessed_at": "2026-09-28",
                            "relation": "contradicts",
                            "summary": "并发窗口仍可能存在。",
                        }
                    ],
                    "explanation": "绝对化表述不成立。",
                }
            ],
        }
        scores = {
            "schema_version": 1,
            "case_id": case_id,
            "questions": [
                {
                    "question_id": "q-001",
                    "dimensions": {
                        "correctness": 4,
                        "completeness": 3,
                        "structure": 3,
                        "clarity": 4,
                    },
                    "total": 14,
                    "evidence": ["s-0002"],
                    "notes": "项目职责基本明确。",
                },
                {
                    "question_id": "q-002",
                    "dimensions": {
                        "correctness": 1,
                        "completeness": 2,
                        "structure": 3,
                        "clarity": 3,
                    },
                    "total": 9,
                    "evidence": ["s-0004"],
                    "notes": "存在绝对化技术判断。",
                },
            ],
            "overall": {
                "professional": 3,
                "credibility": 3,
                "structure": 3,
                "stability": 3,
            },
        }
        patch = {
            "schema_version": 1,
            "case_id": case_id,
            "changes": [
                {
                    "change_id": "knowledge-cache-consistency-001",
                    "type": "knowledge_state",
                    "key": "redis/cache-consistency",
                    "status": "pending",
                    "evidence": ["s-0004", "s-0006"],
                    "reason": "并发窗口和失败场景掌握不完整。",
                    "value": {
                        "grade": "D",
                        "scores": {
                            "accuracy": 1,
                            "completeness": 2,
                            "retrieval_stability": 3,
                            "expression": 3,
                        },
                    },
                }
            ],
        }
        matrix = {
            "schema_version": 1,
            "case_id": case_id,
            "requirements": [
                {
                    "requirement": "缓存一致性",
                    "coverage": "deep",
                    "performance": "weak",
                    "evidence_questions": ["q-001", "q-002"],
                    "next_priority": "high",
                    "inference_note": "本场已深挖。",
                }
            ],
        }
        for name, payload in (
            ("qa-tree.json", qa),
            ("claims.json", claims),
            ("scores.json", scores),
            ("profile-patch.json", patch),
            ("jd-matrix.json", matrix),
        ):
            (case_path / name).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        (case_path / "review.md").write_text(
            "# 复盘\n\n## 已确认事实\n有两级追问。\n\n## AI 判断\n缓存一致性存在缺口。\n",
            encoding="utf-8",
        )

    def test_ingest_deduplicate_validate_and_profile(self) -> None:
        result, case_path = self.ingest()
        self.assertEqual(result["status"], "created")
        self.assertEqual(result["speaker_status"], "confirmed")
        raw = read_json(case_path / "transcript.raw.json")
        self.assertEqual(len(raw["segments"]), 8)
        self.assertEqual(raw["segments"][0]["role"], "interviewer")
        self.assertEqual(raw["segments"][1]["role"], "candidate")

        duplicate, _ = self.ingest()
        self.assertEqual(duplicate["status"], "duplicate")
        ingest_validation = run_script(
            "validate_case.py", "--case", str(case_path), "--stage", "ingest"
        )
        self.assertEqual(ingest_validation["status"], "valid")

        self.write_analysis(case_path, result["case_id"])
        validation = run_script(
            "validate_case.py", "--case", str(case_path), "--stage", "analysis"
        )
        self.assertEqual(validation["status"], "valid")

        first = run_script(
            "record_profile_patch.py",
            "--root",
            str(self.root),
            "--case",
            str(case_path),
        )
        second = run_script(
            "record_profile_patch.py",
            "--root",
            str(self.root),
            "--case",
            str(case_path),
        )
        self.assertEqual(first["added"], 1)
        self.assertEqual(second["skipped"], 1)
        event_id = f"{result['case_id']}:knowledge-cache-consistency-001"
        run_script(
            "set_profile_status.py",
            "--root",
            str(self.root),
            "--event-id",
            event_id,
            "--status",
            "confirmed",
            "--reason",
            "人工确认",
        )
        state = read_json(self.root / "profile" / "state.json")
        self.assertEqual(state["status_counts"]["confirmed"], 1)
        self.assertEqual(state["revision_count"], 2)

    def test_build_next_interview_prep_from_profile_and_jd_matrix(self) -> None:
        result, case_path = self.ingest()
        self.write_analysis(case_path, result["case_id"])
        run_script(
            "validate_case.py", "--case", str(case_path), "--stage", "analysis"
        )
        run_script(
            "record_profile_patch.py",
            "--root",
            str(self.root),
            "--case",
            str(case_path),
        )
        run_script(
            "set_profile_status.py",
            "--root",
            str(self.root),
            "--event-id",
            f"{result['case_id']}:knowledge-cache-consistency-001",
            "--status",
            "confirmed",
            "--reason",
            "人工确认",
        )

        built = run_script(
            "build_next_prep.py", "--root", str(self.root), "--case", str(case_path)
        )
        self.assertEqual(built["status"], "created")
        brief = read_json(Path(built["json"]))
        self.assertEqual(brief["source_case"]["case_id"], result["case_id"])
        self.assertEqual(len(brief["requirements"]), 1)
        self.assertEqual(brief["requirements"][0]["state"], "已覆盖但有风险")
        self.assertEqual(brief["actions"][0]["priority"], "P0")
        self.assertEqual(len(brief["profile"]["confirmed"]), 1)
        markdown = Path(built["markdown"]).read_text(encoding="utf-8")
        self.assertIn("缓存一致性", markdown)
        self.assertIn("AI 推测", markdown)
        self.assertIn("| 高 | 缓存一致性 | 深入考察 | 表现较弱 |", markdown)
        self.assertIn("可直接复用的已确认资产", markdown)
        self.assertNotIn("| high |", markdown)

    def test_transcript_edit_preserves_raw(self) -> None:
        result, case_path = self.ingest()
        self.write_analysis(case_path, result["case_id"])
        raw_before = (case_path / "transcript.raw.json").read_text(encoding="utf-8")
        transcript_path = case_path / "transcript.md"
        content = transcript_path.read_text(encoding="utf-8")
        content = content.replace("我负责缓存一致性方案", "我主要负责缓存一致性方案")
        transcript_path.write_text(content, encoding="utf-8")
        sync = run_script("sync_transcript_edits.py", "--case", str(case_path))
        self.assertEqual(sync["changes"], 1)
        self.assertEqual(
            (case_path / "transcript.raw.json").read_text(encoding="utf-8"), raw_before
        )
        revised = read_json(case_path / "transcript.revised.json")
        self.assertIn("主要负责", revised["segments"][1]["text_corrected"])
        self.assertTrue((case_path / "transcript-revisions.jsonl").is_file())
        self.assertEqual(sync["analysis_status"], "stale")
        invalid = run_script(
            "validate_case.py",
            "--case",
            str(case_path),
            "--stage",
            "analysis",
            expect=1,
        )
        self.assertTrue(any("分析产物已过期" in item for item in invalid["errors"]))
        bypass = run_script(
            "validate_case.py", "--case", str(case_path), "--stage", "analysis",
            "--refresh-analysis", expect=1,
        )
        self.assertTrue(any("未重写" in item for item in bypass["errors"]))
        self.write_analysis(case_path, result["case_id"])
        with (case_path / "review.md").open("a", encoding="utf-8") as handle:
            handle.write("\n逐字稿已核对“主要负责”的修订。\n")
        refreshed = run_script(
            "validate_case.py",
            "--case",
            str(case_path),
            "--stage",
            "analysis",
            "--refresh-analysis",
        )
        self.assertEqual(refreshed["status"], "valid")
        self.assertEqual(refreshed["analysis_status"], "current")

    def test_validated_analysis_change_blocks_profile_and_render(self) -> None:
        result, case_path = self.ingest()
        self.write_analysis(case_path, result["case_id"])
        run_script("validate_case.py", "--case", str(case_path), "--stage", "analysis")
        with (case_path / "profile-patch.json").open("a", encoding="utf-8") as handle:
            handle.write(" ")
        with self.assertRaises(AssertionError):
            run_script("record_profile_patch.py", "--root", str(self.root), "--case", str(case_path))
        rendered = run_script(
            "render_interview_html.py", "--root", str(self.root), "--case", str(case_path), expect=1
        )
        self.assertTrue(any("发生变化" in item for item in rendered["errors"]))

    def test_case_metadata_update_keeps_revision(self) -> None:
        _, case_path = self.ingest()
        result = run_script(
            "update_case.py",
            "--case",
            str(case_path),
            "--result",
            "进入下一轮",
            "--hr-feedback",
            "技术面通过",
            "--reason",
            "收到 HR 反馈",
        )
        self.assertEqual(result["status"], "updated")
        manifest = read_json(case_path / "manifest.json")
        self.assertEqual(manifest["result"], "进入下一轮")
        self.assertTrue((case_path / "manifest-revisions.jsonl").is_file())
        self.assertEqual(result["outcome"]["status"], "updated")
        state = read_json(self.root / "profile" / "state.json")
        outcomes = state["by_type"]["outcome"]
        self.assertEqual(len(outcomes), 1)
        self.assertEqual(outcomes[0]["value"]["result"], "进入下一轮")

        revised = run_script(
            "update_case.py",
            "--case",
            str(case_path),
            "--result",
            "通过",
            "--hr-feedback",
            "技术面通过",
            "--reason",
            "收到最终反馈",
        )
        self.assertEqual(revised["outcome"]["revision"], 2)
        state = read_json(self.root / "profile" / "state.json")
        self.assertEqual(state["revision_count"], 2)
        self.assertEqual(
            state["by_type"]["outcome"][0]["value"]["result"], "通过"
        )

    def test_context_update_invalidates_analysis_and_render(self) -> None:
        result, case_path = self.ingest()
        self.write_analysis(case_path, result["case_id"])
        run_script(
            "validate_case.py", "--case", str(case_path), "--stage", "analysis"
        )
        updated = run_script(
            "update_case.py",
            "--case",
            str(case_path),
            "--role",
            "高级后端工程师",
            "--reason",
            "岗位信息更正",
        )
        self.assertEqual(updated["analysis_status"], "stale")
        with self.assertRaises(AssertionError):
            run_script("record_profile_patch.py", "--root", str(self.root), "--case", str(case_path))
        with self.assertRaises(AssertionError):
            run_script("build_next_prep.py", "--root", str(self.root), "--case", str(case_path))
        rendered = run_script(
            "render_interview_html.py", "--root", str(self.root), "--case", str(case_path), expect=1
        )
        self.assertIn("分析产物已过期", rendered["errors"][0])

    def test_docx_extraction_uses_standard_library(self) -> None:
        docx = Path(self.temp.name) / "sample.docx"
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
          <w:body><w:p><w:r><w:t>面试官：请介绍项目</w:t></w:r></w:p>
          <w:p><w:r><w:t>候选人：这是回答</w:t></w:r></w:p></w:body>
        </w:document>"""
        with zipfile.ZipFile(docx, "w") as archive:
            archive.writestr("word/document.xml", xml)
        text = read_supported_text(docx, allow_pdf=False)
        self.assertIn("面试官：请介绍项目", text)
        self.assertIn("候选人：这是回答", text)

    def test_ambiguous_speakers_require_confirmation(self) -> None:
        transcript = Path(self.temp.name) / "ambiguous.txt"
        transcript.write_text(
            "A：请介绍项目\nB：这是项目介绍\nA：为什么这样设计\nB：因为需要控制风险\n",
            encoding="utf-8",
        )
        result = run_script(
            "ingest_transcript.py",
            "--root",
            str(self.root),
            "--transcript",
            str(transcript),
            "--date",
            "2026-09-27",
        )
        self.assertEqual(result["speaker_status"], "needs_confirmation")
        case_path = Path(result["case_path"])
        for name in ("qa-tree.json", "claims.json", "scores.json", "review.md"):
            (case_path / name).write_text("placeholder", encoding="utf-8")
        confirmed = run_script(
            "set_speaker_roles.py",
            "--case",
            str(case_path),
            "--map",
            "spk-01=interviewer",
            "--map",
            "spk-02=candidate",
        )
        self.assertEqual(confirmed["status"], "confirmed")
        self.assertEqual(confirmed["analysis_status"], "stale")
        self.assertEqual(read_json(case_path / "manifest.json")["analysis_status"], "stale")
        revised = read_json(case_path / "transcript.revised.json")
        self.assertEqual(revised["segments"][0]["role"], "interviewer")
        self.assertEqual(revised["segments"][1]["role"], "candidate")

    def test_role_confirmation_preserves_prior_transcript_correction(self) -> None:
        transcript = Path(self.temp.name) / "ambiguous-correction.txt"
        transcript.write_text(
            "A：请介绍项目\nB：这是项目介绍\nA：为什么这样设计\nB：因为需要控制风险\n",
            encoding="utf-8",
        )
        result = run_script(
            "ingest_transcript.py", "--root", str(self.root), "--transcript", str(transcript),
            "--date", "2026-09-27",
        )
        case_path = Path(result["case_path"])
        editable = case_path / "transcript.md"
        editable.write_text(
            editable.read_text(encoding="utf-8").replace("这是项目介绍", "这是项目情况介绍"),
            encoding="utf-8",
        )
        run_script("sync_transcript_edits.py", "--case", str(case_path))
        run_script(
            "set_speaker_roles.py", "--case", str(case_path),
            "--map", "spk-01=interviewer", "--map", "spk-02=candidate",
        )
        revised = read_json(case_path / "transcript.revised.json")
        self.assertEqual(revised["segments"][1]["text_corrected"], "这是项目情况介绍")
        self.assertEqual(revised["segments"][1]["role"], "candidate")
        self.assertTrue((case_path / "transcript-revisions.jsonl").is_file())

    def test_render_offline_html_and_validate_presentation(self) -> None:
        result, case_path = self.ingest()
        self.write_analysis(case_path, result["case_id"])
        with (case_path / "review.md").open("a", encoding="utf-8") as handle:
            handle.write(
                "\n### 已确认事实与片段\n- 用户描述了缓存方案（`s-0002`）。\n"
                "\n### 本轮 AI 判断\n- 回答缺少失败恢复边界。\n"
                "\n### 下一步与验收\n- 做一次缓存失效演练并保留日志。\n"
                "- 复述幂等条件并完成边界测试。\n"
            )
        plan = self.root / "profile" / "after-2-interviews-todo-2026-09-29.md"
        plan.write_text(
            "# 两场后的待办\n\n## P0：先补缺口\n"
            "- [ ] **T01 做缓存故障演练**\n"
            "  - 交付：一次可复现的演练记录。\n"
            "  - 验收：保留日志与修复步骤。\n"
            "- [x] **T02 已完成的练习**\n"
            "  - 交付：已有记录。\n",
            encoding="utf-8",
        )

        second_transcript = Path(self.temp.name) / "second-interview.md"
        second_transcript.write_text(
            (FIXTURES / "interview.md")
            .read_text(encoding="utf-8")
            .replace("我负责缓存一致性方案", "我负责数据库中间件方案"),
            encoding="utf-8",
        )
        second = run_script(
            "ingest_transcript.py",
            "--root",
            str(self.root),
            "--transcript",
            str(second_transcript),
            "--company",
            "第二家公司",
            "--role",
            "架构师",
            "--round",
            "二面",
            "--date",
            "2026-09-29",
        )
        second_case = Path(second["case_path"])
        self.write_analysis(second_case, second["case_id"])

        missing = run_script(
            "validate_case.py",
            "--case",
            str(case_path),
            "--stage",
            "presentation",
            expect=1,
        )
        self.assertEqual(missing["status"], "invalid")
        self.assertIn("interview-review.html 缺失或为空", missing["errors"])

        for analyzed_case in (case_path, second_case):
            run_script(
                "validate_case.py",
                "--case",
                str(analyzed_case),
                "--stage",
                "analysis",
            )

        standard_answers = {
            "schema_version": 1,
            "answers": [{
                "answer_id": "other-standard",
                "topic": "other",
                "title": "缓存顺序标准答法",
                "status": "pending",
                "basis": "依据两场回答归并，待用户确认。",
                "source_questions": [{
                    "case_id": result["case_id"], "question_id": "q-002",
                }],
                "versions": {
                    "short_30s": "先更新数据库，再处理缓存。",
                    "standard_2m": "先说明一致性目标，再展开失败补偿与验证。",
                    "followups": [{
                        "question": "如何验证？", "answer": "并发重放并保留日志。",
                    }],
                },
                "limitations": ["缺少生产指标。"],
                "sources": [
                    {"title": "官方资料", "url": "https://example.com/cache"},
                ],
            }],
        }
        standard_answers["answers"].append({
            **standard_answers["answers"][0],
            "answer_id": "other-standard-second",
            "title": "缓存顺序的备选答法",
            "versions": {
                **standard_answers["answers"][0]["versions"],
                "short_30s": "先定义一致性目标。",
            },
        })
        standard_answers["answers"].append({
            **standard_answers["answers"][0],
            "answer_id": "project-introduction-answer",
            "title": "项目介绍参考答法",
            "source_questions": [
                {"case_id": result["case_id"], "question_id": "q-001"},
                {"case_id": second["case_id"], "question_id": "q-001"},
            ],
        })
        (self.root / "profile" / "standard-answers.json").write_text(
            json.dumps(standard_answers, ensure_ascii=False), encoding="utf-8"
        )
        question_clusters = {
            "schema_version": 1,
            "clusters": [{
                "cluster_id": "project-introduction",
                "label": "项目介绍与职责",
                "topic": "project-role",
                "status": "pending",
                "note": "按跨场相同面试意图归并。",
                "questions": [
                    {"case_id": result["case_id"], "question_id": "q-001"},
                    {"case_id": second["case_id"], "question_id": "q-001"},
                ],
            }],
        }
        (self.root / "profile" / "question-clusters.json").write_text(
            json.dumps(question_clusters, ensure_ascii=False), encoding="utf-8"
        )
        (self.root / "historical-reanalysis-test.html").write_text("旧版快照", encoding="utf-8")
        related_markup, related_prompt, related_cases = home_related_questions(
            self.root, discover_cases(self.root), result["case_id"], "q-001"
        )
        self.assertEqual(related_cases, 2)
        self.assertIn("同类问题分组", related_markup)
        self.assertIn("归在「项目介绍与职责」", related_markup)
        self.assertIn("两题题组移出任一道时，会解散整个题组", related_markup)
        self.assertIn("从题库搜索，加入这个题组", related_markup)
        self.assertIn('data-cluster-id="project-introduction"', related_markup)
        self.assertIn('data-cluster-add-search-input', related_markup)
        self.assertIn('data-question-id="q-002"', related_markup)
        self.assertIn('data-cluster-add-search="', related_markup)
        self.assertNotIn("参考答案来源题", related_markup)
        self.assertNotIn("项目介绍参考答法", related_prompt)
        self.assertIn("请简单介绍一下你做过的缓存项目", related_markup)
        self.assertIn("我负责数据库中间件方案", related_markup)
        self.assertEqual(related_markup.count('<details class="related-question">'), 2)
        self.assertGreaterEqual(related_markup.count('class="relation-remove"'), 1)
        self.assertIn('data-relation-dissolves-cluster="true"', related_markup)
        self.assertIn("同题组的原题与旧回答", related_markup)
        self.assertNotIn("其他场次的相关问法与旧回答", related_markup)
        self.assertIn("2 场 2 题", related_markup)
        self.assertNotIn("展开当时的完整回答", related_markup)
        self.assertIn("我负责数据库中间件方案", related_prompt)
        self.assertEqual(related_prompt.count("原题："), 2)
        clusters_path = self.root / "profile" / "question-clusters.json"
        grouped_clusters = {
            "schema_version": 1,
            "clusters": question_clusters["clusters"] + [{
                "cluster_id": "other-group", "label": "另一题组", "status": "pending",
                "questions": [
                    {"case_id": result["case_id"], "question_id": "q-002"},
                    {"case_id": second["case_id"], "question_id": "q-002"},
                ],
            }],
        }
        clusters_path.write_text(json.dumps(grouped_clusters, ensure_ascii=False), encoding="utf-8")
        grouped_markup, _, _ = home_related_questions(
            self.root, discover_cases(self.root), result["case_id"], "q-001"
        )
        self.assertIn("已归入「另一题组」", grouped_markup)
        self.assertIn("查看原题组", grouped_markup)
        self.assertNotIn('data-question-id="q-002"', grouped_markup)
        clusters_path.write_text(
            json.dumps({"schema_version": 1, "clusters": []}, ensure_ascii=False), encoding="utf-8"
        )
        self.assertEqual(
            home_related_questions(self.root, discover_cases(self.root), result["case_id"], "q-001"),
            ("", "", 0),
        )
        clusters_path.write_text(json.dumps(question_clusters, ensure_ascii=False), encoding="utf-8")
        grouped_home = render_history_practice(self.root, discover_cases(self.root))
        self.assertIn('<h3>项目介绍与职责</h3>', grouped_home)
        self.assertIn('查看同题组原题与旧回答 · 2 场', grouped_home)
        self.assertNotIn("代表原题来源", grouped_home)
        standard_answers["answers"].pop()
        (self.root / "profile" / "standard-answers.json").write_text(
            json.dumps(standard_answers, ensure_ascii=False), encoding="utf-8"
        )

        rendered = run_script(
            "render_interview_html.py", "--root", str(self.root), "--all"
        )
        self.assertEqual(rendered["status"], "ok")
        self.assertEqual(rendered["history_cases"], 2)
        self.assertEqual(rendered["pending_review"], str((self.root / "interview-pending-review.html").resolve()))

        case_html = (case_path / "interview-review.html").read_text(encoding="utf-8")
        history_html = (self.root / "interview-history.html").read_text(encoding="utf-8")
        question_bank_html = (self.root / "interview-question-bank.html").read_text(encoding="utf-8")
        answers_html = (self.root / "interview-answers.html").read_text(encoding="utf-8")
        pending_review_html = (self.root / "interview-pending-review.html").read_text(encoding="utf-8")
        HTMLParser().feed(case_html)
        HTMLParser().feed(history_html)
        HTMLParser().feed(question_bank_html)
        HTMLParser().feed(answers_html)
        HTMLParser().feed(pending_review_html)
        node = shutil.which("node")
        for scripted_html in (question_bank_html, answers_html, pending_review_html):
            script_match = re.search(r"<script>(.*?)</script>", scripted_html, re.DOTALL)
            self.assertIsNotNone(script_match)
            if node:
                syntax_check = subprocess.run(
                    [node, "--check"],
                    input=script_match.group(1),
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(syntax_check.returncode, 0, syntax_check.stderr)
        for rendered_html in (case_html, history_html, question_bank_html, answers_html, pending_review_html):
            self.assertIn('http-equiv="Content-Security-Policy"', rendered_html)
            self.assertIn('default-src \'none\'', rendered_html)
            self.assertIn('name="referrer" content="no-referrer"', rendered_html)
            self.assertIn('data-store-id="', rendered_html)
            self.assertNotRegex(rendered_html, r'<(?:script|img|link)[^>]+(?:src|href)="https?://')
        self.assertIn("sessionStorage", pending_review_html)
        self.assertIn("interview-system-review-actions-v2:${archiveId}", pending_review_html)
        self.assertIn("关闭页面后保留选择（当前本机入口；服务重启换端口后不保证恢复）", pending_review_html)
        self.assertIn('data-draft-persist', question_bank_html)
        self.assertIn("服务重启换端口后不保证恢复", question_bank_html)
        self.assertIn('data-action-message aria-live="polite" role="status"', pending_review_html)
        self.assertIn("确认应用审核结果", pending_review_html)
        self.assertIn("↗ 外部资料", history_html)
        self.assertIn("隐私提醒", case_html)
        self.assertIn("<td>深入考察</td>", case_html)
        self.assertIn("<td>高</td>", case_html)
        self.assertIn("表现较弱</span>", case_html)
        self.assertIn("说话人：已确认", case_html)
        self.assertIn("分析：已更新", case_html)
        self.assertIn('<section id="jd" class="material-card"><h3>岗位 JD 原文</h3>', case_html)
        self.assertIn("岗位要求：熟悉缓存一致性、数据库故障处理和可观测性。", case_html)
        self.assertLess(case_html.index('id="jd"'), case_html.index('id="jd-matrix"'))
        self.assertNotIn("<td>deep</td>", case_html)
        self.assertIn('data-interview-case="', case_html)
        self.assertIn('class="case-page"', case_html)
        self.assertIn('class="case-hero-result"', case_html)
        self.assertIn('id="performance"', case_html)
        self.assertIn('id="materials"', case_html)
        self.assertLess(case_html.index('id="focus"'), case_html.index('id="performance"'))
        self.assertLess(case_html.index('id="performance"'), case_html.index('id="practice"'))
        self.assertLess(case_html.index('id="practice"'), case_html.index('id="materials"'))
        self.assertIn('id="focus"', case_html)
        self.assertIn('id="practice"', case_html)
        self.assertIn('.practice-grid{display:block}', case_html)
        self.assertIn('id="practice-q-002"', case_html)
        self.assertIn('“为什么选择这个顺序？”', case_html)
        self.assertIn('原始问题：</strong>“为什么选择这个顺序？”', case_html)
        self.assertIn('我的现场回答：</strong>', case_html)
        self.assertIn('回答问题（AI 判断）：</strong>存在绝对化技术判断。', case_html)
        q002_card = case_html.split('id="practice-q-002"', 1)[1].split('</article>', 1)[0]
        self.assertIn('查看现场回答、AI 点评与参考答案', q002_card)
        self.assertIn('先更新数据库，再处理缓存。', q002_card)
        self.assertIn('href="../../interview-answers.html#standard-answer-other-standard"', q002_card)
        self.assertLess(q002_card.index('我的现场回答'), q002_card.index('回答问题（AI 判断）'))
        self.assertLess(q002_card.index('回答问题（AI 判断）'), q002_card.index('AI 参考答案'))
        self.assertIn('相关文档：</strong>', case_html)
        self.assertIn('提问：<a href="#s-0003">s-0003</a>', case_html)
        self.assertIn('回答：<a href="#s-0004">s-0004</a>', case_html)
        self.assertIn('问答树记录的追问', case_html)
        self.assertLess(case_html.index('id="practice-q-002"'), case_html.index('id="practice-q-001"'))
        self.assertIn("做一次缓存失效演练并保留日志。", case_html)
        self.assertIn('href="#s-0002"', case_html)
        self.assertLess(case_html.index('id="focus"'), case_html.index('id="jd"'))
        self.assertIn("面试练习", history_html)
        self.assertNotIn('id="next-prep"', history_html)
        self.assertNotIn("data-goal-card", history_html)
        self.assertNotIn("已完成的练习", history_html)
        self.assertLess(history_html.index('id="practice"'), history_html.index('id="history-archive"'))
        self.assertLess(history_html.index('id="history-archive"'), history_html.index('id="history"'))
        self.assertIn('先把这题答完整', history_html)
        self.assertNotIn('historical-reanalysis-test.html', history_html)
        self.assertIn('class="practice-card home-practice-card"', history_html)
        self.assertIn('<details class="practice-context"><summary>查看原题、旧回答与参考资料</summary>', history_html)
        self.assertIn('为什么选择这个顺序', history_html)
        self.assertIn('interview-review.html#practice-q-002', history_html)
        self.assertIn("data-history-row", history_html)
        self.assertIn('data-history-result-status="missing"', history_html)
        self.assertIn('data-history-result-filter', history_html)
        self.assertIn('data-search-clear', history_html)
        self.assertIn('aria-live="polite"', history_html)
        self.assertIn("轮次：", history_html)
        self.assertIn("结果：尚未记录结果", history_html)
        for rendered_html in (case_html, history_html, question_bank_html, pending_review_html):
            self.assertNotIn("练习偏好", rendered_html)
            self.assertNotIn("data-preference-choice", rendered_html)
        self.assertIn('id="history"', case_html)
        self.assertIn("示例公司", history_html)
        self.assertIn("第二家公司", case_html)
        self.assertIn("第二家公司", history_html)
        self.assertNotIn('id="question-bank"', history_html)
        self.assertIn("面试总览", history_html)
        self.assertIn('href="interview-pending-review.html"', history_html)
        home_nav = history_html.split('<nav class="topnav" aria-label="本页导航">', 1)[1].split('</nav>', 1)[0]
        self.assertEqual(home_nav.count('<a href='), 2)
        self.assertIn('<a href="#practice">练习</a>', home_nav)
        self.assertIn('<a href="#history-archive">历史面试</a>', home_nav)
        self.assertIn('<a href="interview-question-bank.html">打开完整题库</a>', history_html)
        self.assertIn('data-ai-reference hidden', history_html)
        self.assertNotIn('id="saved-answers"', history_html)
        reference_markup, reference_prompt = home_reference_view({}, [standard_answers["answers"][0]])
        self.assertIn('先更新数据库，再处理缓存。', reference_markup)
        self.assertIn('href="interview-answers.html#standard-answer-other-standard"', reference_markup)
        self.assertIn('未核对草稿', reference_markup)
        self.assertIn('先更新数据库，再处理缓存。', reference_prompt)
        blank_title_markup, _ = home_reference_view({}, [{
            "answer_id": "blank-title", "title": "   ", "status": "pending",
            "versions": {"short_30s": "短答", "standard_2m": "长答"},
        }])
        self.assertIn("跨场参考答案（标题未记录）", blank_title_markup)
        self.assertIn('data-interview-answers', answers_html)
        self.assertIn('data-answer-search', answers_html)
        self.assertIn('data-search-clear', answers_html)
        self.assertIn('aria-live="polite"', answers_html)
        self.assertIn('id="cross-case-answers"', answers_html)
        self.assertIn('id="question-answers"', answers_html)
        self.assertEqual(answers_html.count('class="standard-answer"'), 2)
        self.assertIn('id="standard-answer-other-standard"', answers_html)
        self.assertIn('id="standard-answer-other-standard-second"', answers_html)
        self.assertIn('>本场练习</a>', case_html)
        self.assertIn('>原始材料</a>', case_html)
        self.assertNotIn('>按需核对</a>', case_html)
        self.assertIn("data-interview-question-bank", question_bank_html)
        self.assertIn("data-interview-pending-review", pending_review_html)
        self.assertIn("完整面试题库", question_bank_html)
        self.assertIn("查看全部原题与长尾题", question_bank_html)
        self.assertIn('href="interview-history.html"', question_bank_html)
        self.assertIn('href="interview-answers.html"', question_bank_html)
        for marker in (
            "全部面试题", "我的现场回答", "推荐答案", "为什么选择这个顺序",
            "data-question-search", "data-question-row", 'class="bank-sidebar"',
            "搜索与书签", "按主题覆盖频率排序的书签", "覆盖 2 / 2 场",
            "主题仍按覆盖的面试场次数排序", "先写自己的版本，再用 AI 打磨", "2 份跨场参考答案",
            "相关跨场参考答案（2 份）", "缓存顺序的备选答法", "缓存顺序标准答法",
            "30 秒版", "展开 2 分钟版", "常见追问", "待补证据", "未核对草稿",
            "data-standard-answer", "https://example.com/cache", "1 组跨场高频问法",
            "高频重复问法", "项目介绍与职责", "覆盖 2 场 · 2 道真实问题",
            "AI 归并 · 未核对", "data-question-cluster", "跨场高频重复问法",
            'data-standard-library',
        ):
            self.assertIn(marker, question_bank_html)
        self.assertIn('data-search-clear', question_bank_html)
        self.assertIn('data-draft-key="supplement:', question_bank_html)
        self.assertIn('清除补充草稿', question_bank_html)
        self.assertIn('interview-system-session-draft-v1:${archiveId}', question_bank_html)
        self.assertIn('有未保存修改，请核对事实后点击保存。', question_bank_html)
        draft_form = home_answer_save_form("case-a", "q-001", {}, [{
            "answer_id": "draft-answer", "versions": {"short_30s": "短答", "standard_2m": "长答"},
        }])
        self.assertIn('data-draft-key="answer:standard:draft-answer"', draft_form)
        self.assertIn("保存并确认到档案", draft_form)
        self.assertNotIn('data-draft-persist', draft_form)
        self.assertNotIn('data-draft-clear', draft_form)
        self.assertEqual(question_bank_html.count('id="standard-answer-other-standard"'), 1)
        self.assertEqual(question_bank_html.count('id="standard-answer-other-standard-second"'), 1)
        self.assertIn('href="#standard-answer-other-standard"', question_bank_html)
        self.assertIn('href="#standard-answer-other-standard-second"', question_bank_html)
        self.assertIn('<summary class="standard-heading">', question_bank_html)
        self.assertIn('其中 2 份是未核对草稿，可先用于练习', question_bank_html)
        self.assertIn("需要纠错时再核对资料", history_html)
        self.assertNotIn("javascript:alert(1)", history_html)
        self.assertIn(f'interview-review.html#practice-q-002', history_html)
        self.assertNotIn('id="review-center"', question_bank_html)
        self.assertIn('data-practice-save>保存本次练习', question_bank_html)
        self.assertIn('已入库 · 待审核', question_bank_html)
        self.assertIn('data-ai-improve>复制给 AI 打磨答案', question_bank_html)
        self.assertIn('data-ai-past-answer hidden', question_bank_html)
        self.assertIn('data-ai-prompt-preview hidden readonly', question_bank_html)
        self.assertIn('需要时记录一次练习', question_bank_html)
        self.assertIn('不要编造职责、数据、事故和项目结果', question_bank_html)
        self.assertIn('服务暂时不可用时，才下载“待应用”文件', question_bank_html)
        self.assertNotIn('window.confirm(', question_bank_html)
        self.assertNotIn('<button type="button" data-export-actions', question_bank_html)
        self.assertNotIn("data-review-status=\"confirmed\"", question_bank_html)
        self.assertIn('id="review-center"', pending_review_html)
        self.assertIn("按需核对资料", pending_review_html)
        self.assertIn("这些内容是自动整理的候选资料，不是今天的待办", pending_review_html)
        self.assertNotIn('id="priority-review"', pending_review_html)
        self.assertNotIn("当前先确认", pending_review_html)
        self.assertIn("确认这些问题是否属于同一类", pending_review_html)
        self.assertIn(
            f'href="interview-question-bank.html#practice-{result["case_id"]}-q-001"',
            pending_review_html,
        )
        self.assertNotIn(
            f'href="#practice-{result["case_id"]}-q-001"',
            pending_review_html,
        )
        self.assertIn('class="pending-backlog"', pending_review_html)
        self.assertIn("本轮可以先跳过", pending_review_html)
        self.assertIn("AI 给你的复习提醒", pending_review_html)
        self.assertIn("哪些知识点可能要复习、哪些回答容易被追问", pending_review_html)
        self.assertIn("data-export-actions", pending_review_html)
        self.assertIn("data-review-status=\"confirmed\"", pending_review_html)
        self.assertIn("确认正确", pending_review_html)
        self.assertIn("暂不确定", pending_review_html)
        self.assertIn("不应采用", pending_review_html)
        self.assertIn("撤销选择", pending_review_html)
        self.assertNotIn("第 1 步：逐项判断", pending_review_html)
        self.assertIn('data-review-toolbar hidden', pending_review_html)
        self.assertIn('<details class="review-more"><summary>更多操作</summary>', pending_review_html)
        self.assertIn("应用刚导出的审核结果", pending_review_html)
        self.assertIn("下载选择", pending_review_html)
        self.assertIn("高级设置：2 道问题的原始引用（一般不用修改）", pending_review_html)
        self.assertIn('aria-pressed="false"', pending_review_html)
        self.assertIn("data-edit-short", pending_review_html)
        self.assertIn(r".value.split('\n')", pending_review_html)
        self.assertNotIn(".value.split('\n')", pending_review_html)
        self.assertIn(r"JSON.stringify(payload, null, 2) + '\n'", pending_review_html)
        self.assertNotIn("{html.escape(note)}", pending_review_html)
        self.assertIn("按跨场相同面试意图归并。", question_bank_html)
        self.assertIn("data-practice-widget", history_html)
        self.assertIn("和 AI 一起打磨回答", history_html)
        self.assertIn("需要时记录一次练习", history_html)
        second_html = (second_case / "interview-review.html").read_text(encoding="utf-8")
        self.assertIn("本场未提供 JD。", second_html)
        self.assertNotIn("岗位要求：熟悉缓存一致性", second_html)
        self.assertNotIn("<script src=", case_html)
        self.assertNotIn("<link rel=", case_html)

        focus = {"schema_version": 1, "groups": [{"title": "T01 故障排查", "questions": [
            {"case_id": result["case_id"], "question_id": "q-002",
             "knowledge_point": "缓存一致性", "followups": ["更新失败时如何补偿？", "怎样验证并发边界？"],
             "followup_answers": [{"steps": [{"heading": "检查", "body": "确认写入失败"}]},
                                  {"steps": [{"heading": "验证", "body": "并发重放"}]}],
             "reference_answer": {"scope": "仅供练习", "steps": [
                 {"heading": "先定位", "body": "核对 <旧数据>"}
             ], "code": "if (a < b) return;", "validation": "保留日志", "sources": [
                 {"title": "官方资料", "url": "https://example.com/official/cache"},
                 {"title": "不安全来源", "url": "javascript:alert(1)"}
             ]}}
        ]}]}
        (self.root / "profile" / "practice-focus.json").write_text(
            json.dumps(focus, ensure_ascii=False), encoding="utf-8"
        )
        focused = render_history_practice(
            self.root,
            [next(item for item in discover_cases(self.root) if item["case_id"] == result["case_id"])],
        )
        self.assertIn("为什么选择这个顺序", focused)
        self.assertIn("练习知识点：</strong>缓存一致性", focused)

        self.assertIn("更新失败时如何补偿？", focused)
        self.assertIn("再看 1 道延伸追问", focused)
        self.assertIn("练习题，非面试原话", focused)
        self.assertIn("AI 整理的参考答法，非当场回答", focused)
        self.assertEqual(focused.count("查看本题参考答案"), 2)
        self.assertIn("缓存顺序标准答法", focused)
        self.assertIn('data-saved-answer-kind="standard"', focused)
        self.assertNotIn("核对 &lt;旧数据&gt;", focused)
        self.assertIn('href="https://example.com/official/cache"', focused)
        self.assertNotIn("javascript:alert", focused)
        self.assertEqual(focused.count('class="practice-card home-practice-card"'), 1)
        focused_case = render_case(next(item for item in discover_cases(self.root)
                                        if item["case_id"] == result["case_id"]), discover_cases(self.root))
        self.assertIn("更新失败时如何补偿？", focused_case)
        self.assertIn('本题逐题稿', focused_case)

        main_answer = focus["groups"][0]["questions"][0].pop("reference_answer")
        (case_path / "practice-answers.json").write_text(
            json.dumps({"schema_version": 1, "questions": [
                {"question_id": "q-002", "reference_answer": main_answer}
            ]}, ensure_ascii=False), encoding="utf-8"
        )
        (self.root / "profile" / "practice-focus.json").write_text(
            json.dumps(focus, ensure_ascii=False), encoding="utf-8"
        )
        self.assertIn(
            "核对 &lt;旧数据&gt;",
            render_case(
                next(item for item in discover_cases(self.root)
                     if item["case_id"] == result["case_id"]),
                discover_cases(self.root),
            ),
        )

        coverage = run_script("check_practice_answers.py", "--root", str(self.root), "--scope", "focus")
        self.assertEqual((coverage["main_questions"], coverage["followup_questions"]), (1, 2))
        focus["groups"][0]["questions"][0]["followup_answers"].pop()
        (self.root / "profile" / "practice-focus.json").write_text(
            json.dumps(focus, ensure_ascii=False), encoding="utf-8"
        )
        missing_answer = run_script("check_practice_answers.py", "--root", str(self.root),
                                    "--scope", "focus", expect=1)
        self.assertIn("缺少延伸追问参考答案", missing_answer["errors"][0])
        focus["groups"][0]["questions"][0]["followup_answers"].append(
            {"steps": [{"heading": "验证", "body": "并发重放"}]}
        )
        (self.root / "profile" / "practice-focus.json").write_text(
            json.dumps(focus, ensure_ascii=False), encoding="utf-8"
        )

        presentation = run_script(
            "validate_case.py",
            "--case",
            str(case_path),
            "--stage",
            "presentation",
        )
        self.assertEqual(presentation["status"], "valid")

        standard_answers["answers"].append({
            **standard_answers["answers"][0],
            "answer_id": "project-introduction-answer",
            "title": "跨场项目介绍",
            "source_questions": [
                {"case_id": result["case_id"], "question_id": "q-001"},
                {"case_id": second["case_id"], "question_id": "q-001"},
            ],
        })
        (self.root / "profile" / "standard-answers.json").write_text(
            json.dumps(standard_answers, ensure_ascii=False), encoding="utf-8"
        )
        next_markup = render_history_practice(self.root, discover_cases(self.root))
        self.assertEqual(next_markup.count("data-home-question data-question-title="), 1)
        self.assertIn("data-home-next", next_markup)
        self.assertIn("data-home-previous", next_markup)
        self.assertIn("data-home-position", next_markup)
        self.assertIn("第 1 / 1 题", next_markup)
        self.assertIn("下一题", next_markup)
        self.assertIn("确认答案不代表已经掌握", next_markup)

    def test_render_write_cleans_temporary_file_when_replace_fails(self) -> None:
        target = self.root / "rendered.html"
        with mock.patch("render_interview_html.os.replace", side_effect=OSError("simulated replace failure")):
            with self.assertRaises(OSError):
                write_text(target, "fictional html")
        self.assertFalse(list(self.root.glob("tmp*")))

    def test_standard_answers_validate_real_question_references(self) -> None:
        result, case_path = self.ingest()
        self.write_analysis(case_path, result["case_id"])
        payload = {
            "schema_version": 1,
            "answers": [{
                "answer_id": "self-introduction",
                "topic": "self-intro",
                "title": "自我介绍",
                "status": "pending",
                "basis": "跨场整理，待确认。",
                "source_questions": [{
                    "case_id": result["case_id"], "question_id": "q-001",
                }],
                "versions": {
                    "short_30s": "三十秒答案。",
                    "standard_2m": "两分钟答案。",
                    "followups": [{"question": "为什么？", "answer": "说明取舍。"}],
                },
                "limitations": ["缺少量化结果。"],
                "sources": [{"title": "官方资料", "url": "https://example.com/docs"}],
            }],
        }
        path = self.root / "profile" / "standard-answers.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        valid = run_script("check_standard_answers.py", "--root", str(self.root))
        self.assertEqual(valid["status"], "valid")

        payload["answers"][0]["source_questions"][0]["question_id"] = "q-missing"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        invalid = run_script(
            "check_standard_answers.py", "--root", str(self.root), expect=1
        )
        self.assertEqual(invalid["status"], "invalid")
        self.assertIn("引用不存在", invalid["errors"][0])

    def test_standard_answer_status_appends_revision(self) -> None:
        result, case_path = self.ingest()
        self.write_analysis(case_path, result["case_id"])
        payload = {
            "schema_version": 1,
            "answers": [{
                "answer_id": "self-introduction",
                "topic": "self-intro",
                "title": "自我介绍",
                "status": "pending",
                "basis": "跨场整理，待确认。",
                "source_questions": [{
                    "case_id": result["case_id"], "question_id": "q-001",
                }],
                "versions": {"short_30s": "短版。", "standard_2m": "长版。", "followups": []},
                "limitations": ["量化数据待补。"],
                "sources": [],
            }],
        }
        path = self.root / "profile" / "standard-answers.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        updated = run_script(
            "set_standard_answer_status.py", "--root", str(self.root),
            "--answer-id", "self-introduction", "--status", "confirmed",
            "--reason", "用户确认当前版本",
        )
        self.assertEqual(updated["new_status"], "confirmed")
        saved = read_json(path)
        self.assertEqual(saved["answers"][0]["status"], "confirmed")
        revisions = (self.root / "profile" / "standard-answer-revisions.jsonl").read_text(encoding="utf-8")
        self.assertIn("用户确认当前版本", revisions)

    def test_rejected_question_cluster_can_preserve_split_history(self) -> None:
        case_ids = ("case-one", "case-two")
        for case_id in case_ids:
            case_path = self.root / "interviews" / case_id
            case_path.mkdir(parents=True)
            (case_path / "qa-tree.json").write_text(json.dumps({
                "schema_version": 1,
                "case_id": case_id,
                "questions": [{"question_id": "q-001", "question": "项目贡献"}],
            }, ensure_ascii=False), encoding="utf-8")
        refs = [{"case_id": case_id, "question_id": "q-001"} for case_id in case_ids]
        payload = {
            "schema_version": 1,
            "clusters": [
                {"cluster_id": "old-mixed", "label": "旧的混合问法", "topic": "project-role",
                 "status": "rejected", "note": "按项目拆分后保留历史。", "questions": refs},
                {"cluster_id": "new-split", "label": "新的项目问法", "topic": "project-role",
                 "status": "pending", "note": "拆分后的有效问法。", "questions": refs},
            ],
        }
        path = self.root / "profile" / "question-clusters.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        valid = run_script("check_question_clusters.py", "--root", str(self.root))
        self.assertEqual(valid["status"], "valid")

        payload["clusters"][0]["status"] = "confirmed"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        invalid = run_script("check_question_clusters.py", "--root", str(self.root), expect=1)
        self.assertTrue(any("已归入其他高频问法" in error for error in invalid["errors"]))

    def test_split_curated_entries_preserves_sources_and_revision_history(self) -> None:
        first, first_path = self.ingest()
        second_source = Path(self.temp.name) / "second-interview.md"
        second_source.write_text(
            (FIXTURES / "interview.md").read_text(encoding="utf-8") + "\n面试官：请补充项目贡献。\n",
            encoding="utf-8",
        )
        second = run_script(
            "ingest_transcript.py", "--root", str(self.root), "--transcript", str(second_source),
            "--company", "第二家公司", "--date", "2026-09-29",
        )
        case_paths = (first_path, Path(second["case_path"]))
        case_ids = (first["case_id"], second["case_id"])
        for case_path, case_id in zip(case_paths, case_ids):
            (case_path / "qa-tree.json").write_text(json.dumps({
                "schema_version": 1,
                "case_id": case_id,
                "questions": [{"question_id": "q-001", "question": "项目贡献"}],
            }, ensure_ascii=False), encoding="utf-8")
        refs = [{"case_id": case_id, "question_id": "q-001"} for case_id in case_ids]
        answer = {
            "answer_id": "mixed", "topic": "project-role", "title": "混合回答",
            "status": "pending", "basis": "两场回答。", "source_questions": refs,
            "versions": {"short_30s": "短版。", "standard_2m": "长版。", "followups": []},
            "limitations": ["待确认。"], "sources": [],
        }
        cluster = {
            "cluster_id": "mixed", "label": "混合问法", "topic": "project-role",
            "status": "pending", "note": "待拆分。", "questions": refs,
        }
        (self.root / "profile" / "standard-answers.json").write_text(json.dumps(
            {"schema_version": 1, "answers": [answer]}, ensure_ascii=False
        ), encoding="utf-8")
        (self.root / "profile" / "question-clusters.json").write_text(json.dumps(
            {"schema_version": 1, "clusters": [cluster]}, ensure_ascii=False
        ), encoding="utf-8")

        # 高频问法需跨场，因此构造四个引用，每个拆分组各覆盖两场。
        for qa_path in (path / "qa-tree.json" for path in case_paths):
            payload = json.loads(qa_path.read_text(encoding="utf-8"))
            payload["questions"].append({"question_id": "q-002", "question": "另一个项目贡献"})
            qa_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        answer["source_questions"] = [
            {"case_id": case_ids[0], "question_id": "q-001"},
            {"case_id": case_ids[1], "question_id": "q-001"},
            {"case_id": case_ids[0], "question_id": "q-002"},
            {"case_id": case_ids[1], "question_id": "q-002"},
        ]
        cluster["questions"] = [dict(ref) for ref in answer["source_questions"]]
        (self.root / "profile" / "standard-answers.json").write_text(json.dumps(
            {"schema_version": 1, "answers": [answer]}, ensure_ascii=False
        ), encoding="utf-8")
        (self.root / "profile" / "question-clusters.json").write_text(json.dumps(
            {"schema_version": 1, "clusters": [cluster]}, ensure_ascii=False
        ), encoding="utf-8")
        new_answers = [
            {**answer, "answer_id": "project-1", "title": "项目 1", "source_questions": answer["source_questions"][:2]},
            {**answer, "answer_id": "project-2", "title": "项目 2", "source_questions": answer["source_questions"][2:]},
        ]
        new_clusters = [
            {**cluster, "cluster_id": "project-1", "label": "项目 1", "questions": cluster["questions"][:2]},
            {**cluster, "cluster_id": "project-2", "label": "项目 2", "questions": cluster["questions"][2:]},
        ]
        spec_path = Path(self.temp.name) / "split.json"
        spec_path.write_text(json.dumps({
            "schema_version": 1,
            "reason": "按项目拆分",
            "original_answer_id": "mixed",
            "original_cluster_id": "mixed",
            "new_answers": new_answers,
            "new_clusters": new_clusters,
        }, ensure_ascii=False), encoding="utf-8")
        result = run_script(
            "split_curated_entries.py", "--root", str(self.root), "--spec", str(spec_path)
        )
        self.assertEqual(result["status"], "split")
        saved_answers = read_json(self.root / "profile" / "standard-answers.json")["answers"]
        self.assertEqual([item["status"] for item in saved_answers], ["rejected", "pending", "pending"])
        revisions = (self.root / "profile" / "standard-answer-revisions.jsonl").read_text(encoding="utf-8")
        self.assertIn('"previous_status": "pending"', revisions)
        self.assertEqual(run_script("check_question_clusters.py", "--root", str(self.root))["status"], "valid")

        cluster_actions = Path(self.temp.name) / "cluster-actions.json"
        cluster_actions.write_text(json.dumps({
            "schema_version": 1,
            "actions": [{
                "kind": "question_cluster", "id": "project-1", "expected_status": "pending",
                "status": "confirmed", "reason": "人工核对分组",
                "patch": {"label": "项目一贡献", "note": "已核对问题边界。",
                          "questions": cluster["questions"][:2]},
            }],
        }, ensure_ascii=False), encoding="utf-8")
        applied = run_script(
            "apply_review_actions.py", "--root", str(self.root), "--actions", str(cluster_actions)
        )
        self.assertEqual(applied["question_clusters"], 1)
        saved_clusters = read_json(self.root / "profile" / "question-clusters.json")["clusters"]
        edited = next(item for item in saved_clusters if item["cluster_id"] == "project-1")
        self.assertEqual((edited["status"], edited["label"]), ("confirmed", "项目一贡献"))
        cluster_revisions = (self.root / "profile" / "question-cluster-revisions.jsonl").read_text(encoding="utf-8")
        self.assertIn('"before":', cluster_revisions)
        self.assertIn('"after":', cluster_revisions)

    def test_manual_cluster_merge_validates_overlap_and_keeps_history(self) -> None:
        first, first_path = self.ingest()
        second_source = Path(self.temp.name) / "manual-second-interview.md"
        second_source.write_text(
            (FIXTURES / "interview.md").read_text(encoding="utf-8") + "\n面试官：请补充第二场项目边界。\n",
            encoding="utf-8",
        )
        second = run_script(
            "ingest_transcript.py", "--root", str(self.root), "--transcript", str(second_source),
            "--company", "第二家公司", "--role", "后端工程师", "--round", "二面", "--date", "2026-09-29",
            "--jd", str(FIXTURES / "jd.txt"), "--resume", str(FIXTURES / "resume.txt"),
        )
        second_path = Path(second["case_path"])
        self.write_analysis(first_path, first["case_id"])
        self.write_analysis(second_path, second["case_id"])
        refs_q1 = [
            {"case_id": first["case_id"], "question_id": "q-001"},
            {"case_id": second["case_id"], "question_id": "q-001"},
        ]
        refs_q2 = [
            {"case_id": first["case_id"], "question_id": "q-002"},
            {"case_id": second["case_id"], "question_id": "q-002"},
        ]
        (self.root / "profile" / "question-clusters.json").write_text(json.dumps({
            "schema_version": 1,
            "clusters": [{
                "cluster_id": "existing-cluster", "label": "已有题组", "topic": "project-role",
                "status": "pending", "note": "待复核。", "questions": refs_q1,
            }],
        }, ensure_ascii=False), encoding="utf-8")

        valid_file = Path(self.temp.name) / "manual-valid.json"
        valid_file.write_text(json.dumps({
            "schema_version": 1,
            "actions": [{
                "kind": "question_cluster_create", "id": "manual-new-cluster", "status": "pending",
                "label": "缓存项目追问", "topic": "project-role", "reason": "两场均直接追问项目选择顺序。",
                "questions": refs_q2,
            }],
        }, ensure_ascii=False), encoding="utf-8")
        applied = run_script("apply_review_actions.py", "--root", str(self.root), "--actions", str(valid_file))
        self.assertEqual(applied["question_clusters"], 1)
        saved = read_json(self.root / "profile" / "question-clusters.json")["clusters"]
        self.assertEqual(next(item for item in saved if item["cluster_id"] == "manual-new-cluster")["status"], "pending")
        revisions = (self.root / "profile" / "question-cluster-revisions.jsonl").read_text(encoding="utf-8")
        self.assertIn('"cluster_id": "manual-new-cluster"', revisions)
        self.assertIn('"before": null', revisions)

        refs_q3 = [
            {"case_id": first["case_id"], "question_id": "q-003"},
            {"case_id": second["case_id"], "question_id": "q-003"},
        ]
        for case_path in (first_path, second_path):
            qa_path = case_path / "qa-tree.json"
            qa_payload = read_json(qa_path)
            qa_payload["questions"].append({"question_id": "q-003", "question": "如何验证项目边界"})
            qa_path.write_text(json.dumps(qa_payload, ensure_ascii=False), encoding="utf-8")
        before_batch_conflict = (self.root / "profile" / "question-clusters.json").read_bytes()
        batch_conflict_file = Path(self.temp.name) / "manual-batch-conflict.json"
        batch_conflict_file.write_text(json.dumps({
            "schema_version": 1,
            "actions": [
                {"kind": "question_cluster_create", "id": "manual-batch-a", "status": "pending",
                 "label": "批量 A", "topic": "project-role", "reason": "测试批量冲突。", "questions": refs_q3},
                {"kind": "question_cluster_create", "id": "manual-batch-b", "status": "pending",
                 "label": "批量 B", "topic": "project-role", "reason": "测试批量冲突。", "questions": refs_q3},
            ],
        }, ensure_ascii=False), encoding="utf-8")
        batch_failed = run_script(
            "apply_review_actions.py", "--root", str(self.root), "--actions", str(batch_conflict_file), expect=1
        )
        self.assertIn("同一批次", batch_failed["error"])
        self.assertEqual((self.root / "profile" / "question-clusters.json").read_bytes(), before_batch_conflict)

        before_conflict = (self.root / "profile" / "question-clusters.json").read_bytes()
        conflict_file = Path(self.temp.name) / "manual-conflict.json"
        conflict_file.write_text(json.dumps({
            "schema_version": 1,
            "actions": [{
                "kind": "question_cluster_create", "id": "manual-conflict", "status": "pending",
                "label": "重叠题组", "topic": "project-role", "reason": "测试重叠校验。",
                "questions": refs_q1,
            }],
        }, ensure_ascii=False), encoding="utf-8")
        failed = run_script("apply_review_actions.py", "--root", str(self.root), "--actions", str(conflict_file), expect=1)
        self.assertIn("重叠", failed["error"])
        self.assertEqual((self.root / "profile" / "question-clusters.json").read_bytes(), before_conflict)

        merge_file = Path(self.temp.name) / "manual-supersede.json"
        merge_file.write_text(json.dumps({
            "schema_version": 1,
            "actions": [{
                "kind": "question_cluster_create", "id": "manual-merged", "status": "pending",
                "label": "项目问题合并组", "topic": "project-role", "reason": "人工确认旧题组全部成员后合并。",
                "supersedes": ["existing-cluster"],
                "expected_superseded_statuses": {"existing-cluster": "pending"},
                "questions": refs_q1,
            }],
        }, ensure_ascii=False), encoding="utf-8")
        merged = run_script("apply_review_actions.py", "--root", str(self.root), "--actions", str(merge_file))
        self.assertEqual(merged["question_clusters"], 2)
        saved = read_json(self.root / "profile" / "question-clusters.json")["clusters"]
        self.assertEqual(next(item for item in saved if item["cluster_id"] == "existing-cluster")["status"], "rejected")
        self.assertEqual(next(item for item in saved if item["cluster_id"] == "manual-merged")["status"], "pending")
        self.assertEqual(run_script("check_question_clusters.py", "--root", str(self.root))["status"], "valid")

        html = render_question_bank(self.root, discover_cases(self.root))
        self.assertIn("data-merge-builder", html)
        self.assertIn("data-merge-undo", html)
        self.assertIn("候选主题提示", html)
        self.assertIn('已归入题组：<a href="#cluster-manual-merged">项目问题合并组</a>（未核对）', html)
        pending_builder = render_merge_builder(
            self.root, discover_cases(self.root), builder_id="pending-manual-merge"
        )
        self.assertIn('已归入题组：<a href="interview-question-bank.html#cluster-manual-merged">项目问题合并组</a>（未核对）', pending_builder)
        self.assertNotIn("练习偏好", html)
        self.assertNotIn("data-preference-choice", html)

    def test_cluster_ignore_and_question_preference_override(self) -> None:
        first, first_path = self.ingest()
        second_source = Path(self.temp.name) / "preference-second-interview.md"
        second_source.write_text(
            (FIXTURES / "interview.md").read_text(encoding="utf-8") + "\n面试官：请补充第二场岗位边界。\n",
            encoding="utf-8",
        )
        second = run_script(
            "ingest_transcript.py", "--root", str(self.root), "--transcript", str(second_source),
            "--company", "第二家公司", "--role", "后端工程师", "--round", "二面", "--date", "2026-09-29",
            "--jd", str(FIXTURES / "jd.txt"), "--resume", str(FIXTURES / "resume.txt"),
        )
        second_path = Path(second["case_path"])
        self.write_analysis(first_path, first["case_id"])
        self.write_analysis(second_path, second["case_id"])
        refs = [
            {"case_id": first["case_id"], "question_id": "q-001"},
            {"case_id": second["case_id"], "question_id": "q-001"},
        ]
        (self.root / "profile" / "question-clusters.json").write_text(json.dumps({
            "schema_version": 1,
            "clusters": [{"cluster_id": "cluster-ignore", "label": "整组测试", "topic": "project-role",
                           "status": "pending", "note": "", "questions": refs}],
        }, ensure_ascii=False), encoding="utf-8")
        actions = Path(self.temp.name) / "preferences.json"
        actions.write_text(json.dumps({
            "schema_version": 1,
            "actions": [
                {"kind": "question_preference", "id": "cluster|cluster-ignore", "target_type": "cluster",
                 "target_id": "cluster-ignore", "preference": "ignored", "reason": "整组暂不练习。"},
                {"kind": "question_preference", "id": f"question|{first['case_id']}|q-001",
                 "target_type": "question", "case_id": first["case_id"], "question_id": "q-001",
                 "preference": "normal", "reason": "单题覆盖整组设置。"},
            ],
        }, ensure_ascii=False), encoding="utf-8")
        run_script("apply_review_actions.py", "--root", str(self.root), "--actions", str(actions))
        preferences = read_json(self.root / "profile" / "question-preferences.json")["preferences"]
        preference_map = {
            (
                item["target_type"],
                item.get("case_id", "") if item["target_type"] == "question" else "",
                item.get("question_id", "") if item["target_type"] == "question" else item.get("target_id", ""),
            ): item
            for item in preferences
        }
        self.assertEqual(effective_question_preference(self.root, preference_map, first["case_id"], "q-001"), "normal")
        self.assertEqual(effective_question_preference(self.root, preference_map, second["case_id"], "q-001"), "ignored")
        plan = run_script("build_review_plan.py", "--root", str(self.root), "--limit", "20")
        queue = read_json(self.root / "profile" / "review-plan.json")["queue"]
        self.assertTrue(any(item["case_id"] == first["case_id"] and item["question_id"] == "q-001" for item in queue))
        self.assertFalse(any(item["case_id"] == second["case_id"] and item["question_id"] == "q-001" for item in queue))

    def test_cluster_review_buttons_capture_editor_patch_in_dom_like_runtime(self) -> None:
        node_script = f"""
const SCRIPT = {json.dumps(SCRIPT, ensure_ascii=False)};
const sessionStorage = {{value: null, getItem() {{ return this.value; }}, setItem(_k, value) {{ this.value = value; }}, removeItem() {{ this.value = null; }}}};
const localStorage = {{value: null, getItem() {{ return this.value; }}, setItem(_k, value) {{ this.value = value; }}, removeItem() {{ this.value = null; }}}};
const result = {{textContent: '', classList: {{toggle() {{}} }}}};
const fields = {{
  '[data-edit-cluster-label]': {{value: '用户改后的题组名'}},
  '[data-edit-cluster-note]': {{value: '用户改后的理由'}},
  '[data-edit-cluster-questions]': {{value: '[{{"case_id":"case-a","question_id":"q-001"}},{{"case_id":"case-b","question_id":"q-001"}}]'}},
}};
const cluster = {{dataset: {{}}, querySelector(selector) {{ return selector === '[data-review-result]' ? result : (fields[selector] || null); }}}};
let buttons = [];
const item = {{
  dataset: {{reviewKind: 'question_cluster', reviewId: 'cluster-a', reviewCurrent: 'pending'}},
  querySelectorAll(selector) {{ return selector === '[data-review-status]' ? buttons : []; }},
  querySelector() {{ return null; }},
  closest(selector) {{ return selector === '.answer-editor' ? null : (selector === '[data-question-cluster]' ? cluster : null); }},
}};
function makeButton(status) {{
  return {{dataset: {{reviewStatus: status}}, attrs: {{}}, addEventListener(_event, fn) {{ this.handler = fn; }},
    setAttribute(key, value) {{ this.attrs[key] = value; }}, closest(selector) {{ return selector === '[data-review-item]' ? item : null; }},
    click() {{ this.handler(); }}}};
}}
buttons = ['confirmed', 'pending', 'rejected'].map(makeButton);
globalThis.sessionStorage = sessionStorage;
globalThis.localStorage = localStorage;
globalThis.location = {{hash: ''}};
globalThis.window = {{prompt: () => '行为测试理由', confirm: () => true}};
globalThis.document = {{
  body: {{dataset: {{storeId: 'dom-test'}}}},
  addEventListener() {{}},
  querySelector() {{ return null; }},
  querySelectorAll(selector) {{ return selector === '[data-review-status]' ? buttons : []; }},
}};
globalThis.requestAnimationFrame = fn => fn();
eval(SCRIPT);
for (const [index, status] of ['confirmed', 'pending', 'rejected'].entries()) {{
  buttons[index].click();
  const payload = JSON.parse(sessionStorage.value);
  const action = payload.actions[payload.actions.length - 1];
  if (action.status !== status) throw new Error(`状态未入队: ${{status}}`);
  if (!action.patch || action.patch.label !== '用户改后的题组名' || action.patch.note !== '用户改后的理由') throw new Error('题组编辑 patch 未进入导出动作');
  if (action.patch.questions.length !== 2) throw new Error('题组引用 patch 未进入导出动作');
}}
console.log('dom-review-actions-ok');
"""
        script_path = Path(self.temp.name) / "dom-review-actions.js"
        script_path.write_text(node_script, encoding="utf-8")
        result = subprocess.run(["node", str(script_path)], text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("dom-review-actions-ok", result.stdout)

    def test_review_actions_practice_plan_backup_and_migration(self) -> None:
        result, case_path = self.ingest()
        self.write_analysis(case_path, result["case_id"])
        run_script("validate_case.py", "--case", str(case_path), "--stage", "analysis")
        run_script("record_profile_patch.py", "--root", str(self.root), "--case", str(case_path))
        answers = {
            "schema_version": 1,
            "answers": [{
                "answer_id": "project-answer",
                "topic": "project-role",
                "title": "项目回答",
                "status": "pending",
                "basis": "基于真实回答。",
                "source_questions": [{"case_id": result["case_id"], "question_id": "q-001"}],
                "versions": {"short_30s": "旧短版。", "standard_2m": "旧长版。", "followups": []},
                "limitations": ["缺少指标。"],
                "sources": [],
            }],
        }
        (self.root / "profile" / "standard-answers.json").write_text(
            json.dumps(answers, ensure_ascii=False), encoding="utf-8"
        )
        actions = {
            "schema_version": 1,
            "actions": [
                {"kind": "standard_answer", "id": "project-answer", "expected_status": "pending",
                 "status": "confirmed", "reason": "人工核对", "patch": {
                     "title": "项目标准回答", "basis": "已核对两次回答。",
                     "short_30s": "新短版。", "standard_2m": "新长版。",
                     "followups": [{"question": "追问？", "answer": "追问答案。"}],
                     "limitations": ["仍需指标。"],
                     "sources": [{"title": "官方资料", "url": "https://example.com/project"}],
                     "source_questions": [{"case_id": result["case_id"], "question_id": "q-001"}]}},
                {"kind": "profile_event",
                 "id": f'{result["case_id"]}:knowledge-cache-consistency-001',
                 "expected_status": "pending", "status": "confirmed", "reason": "人工核对"},
                {"kind": "practice_attempt", "case_id": result["case_id"], "question_id": "q-001",
                 "answer": "这是一次脱稿练习回答。", "duration_seconds": 42, "self_rating": 3,
                 "note": "需要补充指标"},
            ],
        }
        action_file = Path(self.temp.name) / "actions.json"
        action_file.write_text(json.dumps(actions, ensure_ascii=False), encoding="utf-8")
        applied = run_script(
            "apply_review_actions.py", "--root", str(self.root), "--actions", str(action_file)
        )
        self.assertEqual(applied["actions"], 3)
        saved_answer = read_json(self.root / "profile" / "standard-answers.json")["answers"][0]
        self.assertEqual(saved_answer["status"], "confirmed")
        self.assertEqual(saved_answer["title"], "项目标准回答")
        self.assertEqual(saved_answer["versions"]["followups"][0]["question"], "追问？")
        self.assertEqual(read_json(self.root / "profile" / "state.json")["status_counts"]["confirmed"], 1)
        self.assertTrue((self.root / "practice" / "attempts.jsonl").is_file())
        revisions = (self.root / "profile" / "standard-answer-revisions.jsonl").read_text(encoding="utf-8")
        self.assertIn('"before":', revisions)
        self.assertIn('"after":', revisions)

        before_invalid = (self.root / "profile" / "standard-answers.json").read_bytes()
        invalid_actions = Path(self.temp.name) / "invalid-actions.json"
        invalid_actions.write_text(json.dumps({
            "schema_version": 1,
            "actions": [{
                "kind": "standard_answer", "id": "project-answer",
                "expected_status": "confirmed", "status": "confirmed", "reason": "测试原子校验",
                "patch": {"source_questions": [{"case_id": result["case_id"], "question_id": "q-missing"}]},
            }],
        }, ensure_ascii=False), encoding="utf-8")
        failed = run_script(
            "apply_review_actions.py", "--root", str(self.root), "--actions", str(invalid_actions), expect=1
        )
        self.assertIn("校验失败", failed["error"])
        self.assertEqual((self.root / "profile" / "standard-answers.json").read_bytes(), before_invalid)

        review_plan = run_script("build_review_plan.py", "--root", str(self.root), "--limit", "10")
        self.assertEqual(review_plan["attempted_questions"], 1)
        plan_payload = read_json(self.root / "profile" / "review-plan.json")
        self.assertTrue(any(item["question_id"] == "q-001" for item in plan_payload["queue"]))
        health = run_script("check_reference_freshness.py", "--root", str(self.root))
        self.assertFalse(health["online_checked"])
        self.assertGreaterEqual(health["sources"], 1)

        backup = Path(self.temp.name) / "interview-backup.zip"
        created = run_script(
            "backup_store.py", "create", "--root", str(self.root), "--output", str(backup),
            "--allow-plaintext",
        )
        self.assertFalse(created["encrypted"])
        verified = run_script("backup_store.py", "verify", "--backup", str(backup))
        self.assertEqual(verified["status"], "valid")
        restored = Path(self.temp.name) / "restored"
        run_script("backup_store.py", "restore", "--backup", str(backup), "--target", str(restored),
                   "--confirm-empty-target")
        self.assertEqual(read_json(restored / "config.json")["schema_version"], 1)
        migration = run_script("migrate_store.py", "--root", str(self.root), "--target-version", "1")
        self.assertEqual(migration["status"], "no_change")

    def test_jd_source_escapes_document_text(self) -> None:
        case_path = self.root / "interviews" / "jd-escape"
        case_path.mkdir()
        (case_path / "jd.md").write_text("# JD\n\n<script>alert(1)</script>\n", encoding="utf-8")
        rendered = jd_source(case_path, {"inputs": {"jd": {
            "text": "jd.md", "extraction_status": "ok",
        }}})
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", rendered)
        self.assertNotIn("<script>", rendered)
        manual = jd_source(case_path, {"inputs": {"jd": {
            "text": "jd.md", "extraction_status": "manual_transcription_from_image",
        }}})
        self.assertIn("根据 JD 截图人工转写", manual)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", manual)

    def test_external_link_rejects_unsafe_urls_and_escapes_labels(self) -> None:
        for value in (
            "javascript:alert(1)",
            "data:text/html,unsafe",
            "https://user:secret@" + "example.invalid/path",
            "https://example.com/path\nunsafe",
        ):
            self.assertIsNone(safe_url(value))
        valid = "https://example.com/reference"
        self.assertEqual(safe_url(valid), valid)
        rendered = external_link(valid, '<script>alert("x")</script>')
        self.assertNotIn("<script>", rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertIn("↗ 外部资料", rendered)

    def test_release_package_keeps_canonical_entrypoint_without_private_data(self) -> None:
        output = Path(self.temp.name) / "interview-system.zip"
        built = run_script(
            "build_release_package.py",
            "--source",
            str(SKILL),
            "--output",
            str(output),
        )
        self.assertEqual(built["status"], "created")
        self.assertFalse(built["contains_interview_cases"])
        self.assertFalse(built["host_specific_rewrite"])
        self.assertEqual(built["package_sha256"], hashlib.sha256(output.read_bytes()).hexdigest())
        with zipfile.ZipFile(output) as archive:
            names = archive.namelist()
            packaged_skill = archive.read("interview-system/SKILL.md")
            manifest = json.loads(archive.read("interview-system/MANIFEST.json"))
            for entry in manifest["files"]:
                body = archive.read(f"interview-system/{entry['path']}")
                self.assertEqual(entry["size"], len(body))
                self.assertEqual(entry["sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(packaged_skill, (SKILL / "SKILL.md").read_bytes())
        self.assertFalse(manifest["host_specific_rewrite"])
        self.assertEqual(manifest["entrypoint"], "SKILL.md")
        self.assertEqual(built["file_count"], len(manifest["files"]) + 1)
        self.assertIn(
            "interview-system/scripts/render_interview_html.py", names
        )
        self.assertIn(
            "interview-system/scripts/ingest_audio_transcript.py", names
        )
        self.assertIn("interview-system/scripts/transcribe_local_audio.py", names)
        self.assertIn("interview-system/scripts/pdf_ocr.py", names)
        self.assertIn("interview-system/scripts/ocr_image.swift", names)
        self.assertIn("interview-system/scripts/algorithm_coach.py", names)
        self.assertIn("interview-system/assets/algorithm-problems.json", names)
        self.assertIn("interview-system/assets/demo-case.json", names)
        self.assertIn("interview-system/scripts/demo_smoke.py", names)
        self.assertIn("interview-system/LICENSE", names)
        self.assertIn("interview-system/agents/openai.yaml", names)
        self.assertIn(
            "interview-system/scripts/register_reference_transcript.py", names
        )
        self.assertIn("interview-system/scripts/build_next_prep.py", names)
        self.assertIn(
            "interview-system/references/html-presentation.md", names
        )
        self.assertFalse(any("tests/" in name or "private/" in name for name in names))
        blocked = run_script(
            "build_release_package.py",
            "--source",
            str(SKILL),
            "--output",
            str(output),
            expect=1,
        )
        self.assertIn("--force", blocked["error"])
        rebuilt = run_script(
            "build_release_package.py",
            "--source",
            str(SKILL),
            "--output",
            str(output),
            "--force",
        )
        self.assertEqual(rebuilt["status"], "created")

    def test_ingest_local_audio_transcript_preserves_raw_evidence(self) -> None:
        audio = Path(self.temp.name) / "sample.wav"
        with wave.open(str(audio), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(8000)
            handle.writeframes(b"\x00\x00" * 800)

        raw_payload = {
            "segments": [
                {
                    "id": 7,
                    "start": 0.0,
                    "end": 0.04,
                    "speaker": "面试官",
                    "text": "请介绍项目。",
                    "confidence": 0.93,
                },
                {
                    "id": 8,
                    "start": 0.04,
                    "end": 0.1,
                    "speaker": "候选人",
                    "text": "我负责数据库中间件。",
                    "confidence": 0.91,
                },
            ]
        }
        transcript = Path(self.temp.name) / "whisper.json"
        transcript.write_text(
            json.dumps(raw_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        subtitle = Path(self.temp.name) / "whisper.srt"
        subtitle.write_text(
            "1\n00:00:00,000 --> 00:00:00,040\n面试官：请介绍项目。\n",
            encoding="utf-8",
        )

        created = run_script(
            "ingest_audio_transcript.py",
            "--root",
            str(self.root),
            "--audio",
            str(audio),
            "--transcript",
            str(transcript),
            "--raw-output",
            str(subtitle),
            "--engine",
            "local-test-engine",
            "--model",
            "fixture-model",
            "--language",
            "zh",
            "--speaker-method",
            "source_labels",
            "--company",
            "音频示例公司",
            "--role",
            "架构师",
            "--round",
            "一面",
            "--date",
            "2026-09-29",
        )
        self.assertEqual(created["status"], "created")
        self.assertEqual(created["speaker_status"], "confirmed")
        case_path = Path(created["case_path"])

        manifest = read_json(case_path / "manifest.json")
        self.assertEqual(manifest["inputs"]["audio"]["processing"], "local_only")
        self.assertEqual(manifest["inputs"]["audio"]["probe_method"], "python-wave")
        self.assertAlmostEqual(
            manifest["inputs"]["audio"]["duration_seconds"], 0.1, places=3
        )
        self.assertEqual(manifest["transcription"]["engine"], "local-test-engine")
        self.assertEqual(len(manifest["transcription"]["raw_outputs"]), 2)
        self.assertEqual(
            (case_path / "private" / "transcription.raw.json").read_bytes(),
            transcript.read_bytes(),
        )

        raw = read_json(case_path / "transcript.raw.json")
        self.assertEqual(raw["source_kind"], "local_audio_transcription")
        self.assertEqual(raw["segments"][0]["source_segment_id"], "7")
        self.assertEqual(raw["segments"][1]["role"], "candidate")
        validation = run_script(
            "validate_case.py", "--case", str(case_path), "--stage", "ingest"
        )
        self.assertEqual(validation["status"], "valid")

        duplicate = run_script(
            "ingest_audio_transcript.py",
            "--root",
            str(self.root),
            "--audio",
            str(audio),
            "--transcript",
            str(transcript),
            "--engine",
            "local-test-engine",
            "--model",
            "fixture-model",
            "--date",
            "2026-09-29",
        )
        self.assertEqual(duplicate["status"], "duplicate")
        self.assertEqual(duplicate["case_id"], created["case_id"])

    def test_audio_transcript_formats_and_timeline_validation(self) -> None:
        srt = Path(self.temp.name) / "sample.srt"
        srt.write_text(
            "1\n00:00:01,000 --> 00:00:02,500\n候选人：这是回答。\n",
            encoding="utf-8",
        )
        vtt = Path(self.temp.name) / "sample.vtt"
        vtt.write_text(
            "WEBVTT\n\n00:03.000 --> 00:04.250\n<v 面试官>这是追问。\n",
            encoding="utf-8",
        )
        text = Path(self.temp.name) / "sample.txt"
        text.write_text("[00:05] 没有说话人标签。\n", encoding="utf-8")

        self.assertEqual(parse_artifact(srt)[0]["start"], 1.0)
        self.assertEqual(parse_artifact(srt)[0]["speaker_label"], "候选人")
        self.assertEqual(parse_artifact(vtt)[0]["end"], 4.25)
        self.assertEqual(parse_artifact(vtt)[0]["speaker_label"], "面试官")
        self.assertEqual(parse_artifact(text)[0]["start"], 5.0)

        invalid = Path(self.temp.name) / "invalid.json"
        invalid.write_text(
            json.dumps({"segments": [{"start": 2, "end": 1, "text": "错误时间线"}]}),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "end 早于 start"):
            normalize_segments(parse_artifact(invalid))

    def test_register_reference_transcript_keeps_immutable_snapshots(self) -> None:
        result, case_path = self.ingest()
        raw_before = (case_path / "transcript.raw.json").read_bytes()
        first = Path(self.temp.name) / "reference.txt"
        first.write_text(
            "候选人：我主要负责缓存一致性方案。\n", encoding="utf-8"
        )
        registered = run_script(
            "register_reference_transcript.py",
            "--case",
            str(case_path),
            "--reference",
            str(first),
            "--timestamp-coverage",
            "none",
            "--speaker-labels",
            "none",
        )
        self.assertEqual(registered["status"], "registered")
        self.assertEqual(registered["count"], 1)
        manifest = read_json(case_path / "manifest.json")
        references = manifest["inputs"]["reference_transcripts"]
        self.assertEqual(len(references), 1)
        self.assertEqual(references[0]["timestamp_coverage"], "none")
        self.assertEqual(references[0]["speaker_labels"], "none")
        self.assertTrue((case_path / references[0]["original"]).is_file())
        self.assertEqual((case_path / "transcript.raw.json").read_bytes(), raw_before)

        duplicate = run_script(
            "register_reference_transcript.py",
            "--case",
            str(case_path),
            "--reference",
            str(first),
        )
        self.assertEqual(duplicate["status"], "duplicate")

        second = Path(self.temp.name) / "reference.md"
        second.write_text("# 修订参考稿\n\n候选人：缓存方案。\n", encoding="utf-8")
        newer = run_script(
            "register_reference_transcript.py",
            "--case",
            str(case_path),
            "--reference",
            str(second),
            "--timestamp-coverage",
            "partial",
            "--speaker-labels",
            "partial",
        )
        self.assertEqual(newer["status"], "registered")
        self.assertEqual(newer["count"], 2)
        self.assertEqual(
            len((case_path / "reference-transcript-revisions.jsonl").read_text(encoding="utf-8").splitlines()),
            2,
        )
        validation = run_script(
            "validate_case.py", "--case", str(case_path), "--stage", "ingest"
        )
        self.assertEqual(validation["status"], "valid")

    def test_local_asr_wrapper_imports_diarized_output_without_fake_confidence(self) -> None:
        audio = Path(self.temp.name) / "speech.wav"
        with wave.open(str(audio), "wb") as target:
            target.setnchannels(1)
            target.setsampwidth(2)
            target.setframerate(16000)
            target.writeframes(b"\0\0" * 1600)
        model = Path(self.temp.name) / "asr-model"
        diarize_model = Path(self.temp.name) / "diarize-model"
        align_model = Path(self.temp.name) / "align-model"
        model.mkdir()
        diarize_model.mkdir()
        align_model.mkdir()
        engine = Path(self.temp.name) / "fake-whisperx"
        engine.write_text(
            f"#!{sys.executable}\n"
            "import json, pathlib, sys\n"
            "args=sys.argv[1:]\n"
            "out=pathlib.Path(args[args.index('--output_dir')+1])\n"
            "assert '--diarize' in args\n"
            "assert '--align_model' in args\n"
            "assert __import__('os').environ['HF_HUB_OFFLINE']=='1'\n"
            "(out/'speech.json').write_text(json.dumps({'segments':["
            "{'start':0,'end':2.02,'text':'请介绍项目。这是我的回答。','speaker':'SPEAKER_00','words':["
            "{'start':0,'end':1,'word':'请介绍项目','speaker':'SPEAKER_00'},"
            "{'start':1,'end':1.02,'word':'。','speaker':'SPEAKER_01'},"
            "{'start':1.02,'end':2,'word':'这是我的回答','speaker':'SPEAKER_01'},"
            "{'start':2,'end':2.02,'word':'。'}]}]}),encoding='utf-8')\n",
            encoding="utf-8",
        )
        engine.chmod(0o755)
        missing_alignment = run_script(
            "transcribe_local_audio.py", "--root", str(self.root), "--audio", str(audio),
            "--engine", "whisperx", "--engine-executable", str(engine),
            "--model", str(model), "--diarize", "--diarize-model", str(diarize_model),
            expect=1,
        )
        self.assertIn("--align-model", missing_alignment["error"])
        created = run_script(
            "transcribe_local_audio.py", "--root", str(self.root), "--audio", str(audio),
            "--engine", "whisperx", "--engine-executable", str(engine),
            "--model", str(model), "--diarize", "--diarize-model", str(diarize_model),
            "--align-model", str(align_model),
            "--date", "2026-10-04",
        )
        case_path = Path(created["case_path"])
        manifest = read_json(case_path / "manifest.json")
        self.assertEqual(manifest["transcription"]["speaker_method"], "diarization")
        self.assertIsNone(manifest["transcription"]["speaker_confidence"])
        self.assertEqual(manifest["speaker_status"], "needs_confirmation")
        transcript = read_json(case_path / "transcript.raw.json")
        self.assertEqual([part["speaker_label"] for part in transcript["segments"]], ["SPEAKER_00", "SPEAKER_01"])
        self.assertEqual([part["text_raw"] for part in transcript["segments"]], ["请介绍项目。", "这是我的回答。"])
        self.assertEqual(
            run_script("validate_case.py", "--case", str(case_path), "--stage", "ingest")["status"],
            "valid",
        )

    def test_algorithm_practice_records_tests_and_pending_review(self) -> None:
        listing = run_script("algorithm_coach.py", "--root", str(self.root), "list")
        self.assertGreaterEqual(len(listing["problems"]), 11)
        started = run_script(
            "algorithm_coach.py", "--root", str(self.root), "start", "--problem", "max-subarray"
        )
        solution = Path(started["solution"])
        solution.write_text(
            "def solve(data):\n"
            "    best = current = data['nums'][0]\n"
            "    for value in data['nums'][1:]:\n"
            "        current = max(value, current + value)\n"
            "        best = max(best, current)\n"
            "    return best\n",
            encoding="utf-8",
        )
        hint = run_script(
            "algorithm_coach.py", "--root", str(self.root), "hint",
            "--session", started["session_id"],
        )
        self.assertEqual(hint["level"], 1)
        attempt = run_script(
            "algorithm_coach.py", "--root", str(self.root), "submit",
            "--session", started["session_id"], "--explanation", "以 i 结尾的最优和",
            "--runner", "local", "--trust-solution",
        )
        self.assertEqual(attempt["passed"], attempt["total"])
        self.assertEqual(attempt["human_review_status"], "pending")
        event = read_json(self.root / "profile" / "state.json")["by_type"]["algorithm_weakness"][0]
        self.assertEqual(event["status"], "pending")
        reviewed = run_script(
            "algorithm_coach.py", "--root", str(self.root), "review",
            "--session", started["session_id"],
        )
        self.assertEqual(len(reviewed["attempts"]), 1)
        solution.write_text("def solve(data):\n    return 0\n", encoding="utf-8")
        failed = run_script(
            "algorithm_coach.py", "--root", str(self.root), "submit",
            "--session", started["session_id"], "--runner", "local", "--trust-solution",
        )
        self.assertLess(failed["passed"], failed["total"])
        self.assertEqual(len(run_script(
            "algorithm_coach.py", "--root", str(self.root), "review", "--session", started["session_id"]
        )["attempts"]), 2)

    @unittest.skipUnless(shutil.which("javac") and shutil.which("java"), "缺少 JDK")
    def test_java_algorithm_and_local_trust_gate(self) -> None:
        started = run_script(
            "algorithm_coach.py", "--root", str(self.root), "start",
            "--problem", "max-subarray", "--language", "java",
        )
        solution = Path(started["solution"])
        self.assertEqual(solution.name, "Solution.java")
        solution.write_text(
            "import java.util.*;\n"
            "public class Solution {\n"
            "  public static Object solve(Map<String,Object> data) {\n"
            "    List<?> nums=(List<?>)data.get(\"nums\");\n"
            "    long best=Long.MIN_VALUE, current=0;\n"
            "    for(Object n:nums){long v=((Number)n).longValue();current=Math.max(v,current+v);best=Math.max(best,current);}\n"
            "    return best;\n"
            "  }\n}\n",
            encoding="utf-8",
        )
        blocked = run_script(
            "algorithm_coach.py", "--root", str(self.root), "submit", "--session", started["session_id"],
            "--runner", "local", expect=1,
        )
        self.assertIn("--trust-solution", blocked["error"])
        attempt = run_script(
            "algorithm_coach.py", "--root", str(self.root), "submit", "--session", started["session_id"],
            "--runner", "local", "--trust-solution",
        )
        self.assertEqual(attempt["passed"], attempt["total"])
        self.assertEqual(attempt["language"], "java")
        self.assertEqual(attempt["runner"], "local")

    def test_audio_setup_check_distinguishes_ready_from_real_test(self) -> None:
        engine = Path(self.temp.name) / "fake-whisperx"
        engine.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        engine.chmod(0o755)
        fake_bin = Path(self.temp.name) / "fake-bin"
        fake_bin.mkdir()
        ffprobe = fake_bin / "ffprobe"
        ffprobe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        ffprobe.chmod(0o755)
        for name in ("model", "align", "diarize", "nltk"):
            (Path(self.temp.name) / name).mkdir()
        checked = run_script(
            "audio_setup.py", "--root", str(self.root), "check", "--engine", "whisperx", "--diarize",
            "--engine-executable", str(engine), "--model", str(Path(self.temp.name) / "model"),
            "--align-model", str(Path(self.temp.name) / "align"),
            "--diarize-model", str(Path(self.temp.name) / "diarize"),
            "--nltk-data", str(Path(self.temp.name) / "nltk"),
            env={"PATH": str(fake_bin) + os.pathsep + os.environ.get("PATH", "")},
        )
        self.assertEqual(checked["status"], "ready")
        self.assertFalse(checked["actual_recognition_tested"])
        planned = run_script(
            "audio_setup.py", "--root", str(self.root), "plan", "--engine", "whisperx", "--diarize"
        )
        self.assertEqual(planned["status"], "plan")
        self.assertTrue(planned["requires_authorization"])
        self.assertTrue(planned["commands"])

    def test_container_runner_mounts_only_submission_and_disables_network(self) -> None:
        source = Path(self.temp.name) / "solution.py"
        source.write_text("def solve(data): return 1\n", encoding="utf-8")
        secret = Path(self.temp.name) / "secret.txt"
        secret.write_text("private", encoding="utf-8")

        def inspect(command, data, timeout, *, container_name):
            self.assertIn("--network", command)
            self.assertEqual(command[command.index("--network") + 1], "none")
            self.assertIn("--read-only", command)
            self.assertIn("-i", command)
            self.assertTrue(container_name.startswith("interview-coach-"))
            self.assertIn("--cap-drop", command)
            self.assertIn("--pull", command)
            self.assertEqual(command[command.index("--pull") + 1], "never")
            mounted = command[command.index("--mount") + 1]
            self.assertTrue(mounted.endswith(",target=/work,readonly"))
            work = Path(mounted.split(",")[1].split("=", 1)[1])
            self.assertEqual([item.name for item in work.iterdir()], ["solution.py"])
            self.assertEqual(data, {"nums": [1]})
            return {"result": 1, "elapsed_ms": 1}

        with mock.patch("coach_runner._execute", side_effect=inspect):
            result = run_coach_case(source, {"nums": [1]}, "python", "docker")
        self.assertEqual(result["result"], 1)

        java_source = Path(self.temp.name) / "Solution.java"
        java_source.write_text("public class Solution {}\n", encoding="utf-8")

        def inspect_java(command, data, timeout, *, container_name):
            mounted = command[command.index("--mount") + 1]
            work = Path(mounted.split(",")[1].split("=", 1)[1])
            self.assertEqual(sorted(item.name for item in work.iterdir()), ["CoachHarness.java", "Solution.java"])
            self.assertIn("javac -d /tmp", command[-1])
            self.assertEqual(command[command.index("--memory") + 1], "512m")
            return {"result": 1, "elapsed_ms": 1}

        with mock.patch("coach_runner._execute", side_effect=inspect_java):
            self.assertEqual(run_coach_case(java_source, {"nums": [1]}, "java", "docker")["result"], 1)

    @unittest.skipUnless(shutil.which("pdftoppm") and shutil.which("tesseract"), "缺少本地 OCR 命令")
    def test_scanned_pdf_uses_ocr_and_keeps_page_provenance(self) -> None:
        try:
            import pypdf  # noqa: F401
        except ImportError:
            self.skipTest("缺少 pypdf")
        content = b"BT /F1 36 Tf 50 250 Td (HELLO OCR TEST) Tj ET"
        objects = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 400] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        ]
        output = bytearray(b"%PDF-1.4\n")
        offsets = [0]
        for index, obj in enumerate(objects, 1):
            offsets.append(len(output))
            output.extend(f"{index} 0 obj\n".encode() + obj + b"\nendobj\n")
        xref = len(output)
        output.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
        for offset in offsets[1:]:
            output.extend(f"{offset:010d} 00000 n \n".encode())
        output.extend(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
        vector = Path(self.temp.name) / "vector.pdf"
        vector.write_bytes(output)
        prefix = Path(self.temp.name) / "rendered"
        subprocess.run(
            [shutil.which("pdftoppm"), "-q", "-f", "1", "-l", "1", "-singlefile", "-r", "150", str(vector), str(prefix)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True,
        )
        ppm = prefix.with_suffix(".ppm").read_bytes()
        header = re.match(rb"P6\s+(\d+)\s+(\d+)\s+255\s", ppm)
        self.assertIsNotNone(header)
        width, height = map(int, header.groups())
        pixels = ppm[header.end():]
        self.assertEqual(len(pixels), width * height * 3)
        compressed = zlib.compress(pixels)
        image = (
            f"<< /Type /XObject /Subtype /Image /Width {width} /Height {height} "
            f"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode /Length {len(compressed)} >>\nstream\n"
        ).encode() + compressed + b"\nendstream"
        draw = b"q 600 0 0 400 0 0 cm /Im0 Do Q"
        scanned_objects = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 400] /Resources << /XObject << /Im0 4 0 R >> >> /Contents 5 0 R >>",
            image,
            b"<< /Length " + str(len(draw)).encode() + b" >>\nstream\n" + draw + b"\nendstream",
        ]
        scan_output = bytearray(b"%PDF-1.4\n")
        scan_offsets = [0]
        for index, obj in enumerate(scanned_objects, 1):
            scan_offsets.append(len(scan_output))
            scan_output.extend(f"{index} 0 obj\n".encode() + obj + b"\nendobj\n")
        scan_xref = len(scan_output)
        scan_output.extend(f"xref\n0 {len(scan_offsets)}\n0000000000 65535 f \n".encode())
        for offset in scan_offsets[1:]:
            scan_output.extend(f"{offset:010d} 00000 n \n".encode())
        scan_output.extend(
            f"trailer\n<< /Size {len(scan_offsets)} /Root 1 0 R >>\nstartxref\n{scan_xref}\n%%EOF\n".encode()
        )
        scanned = Path(self.temp.name) / "scan.pdf"
        scanned.write_bytes(scan_output)
        with mock.patch.dict(os.environ, {"INTERVIEW_OCR_ENGINE": "tesseract"}):
            text, pages = extract_pdf(scanned)
        self.assertIn("HELLO OCR", text)
        self.assertEqual(pages[0]["page"], 1)
        self.assertTrue(pages[0]["method"].startswith("tesseract"))
        with mock.patch.dict(os.environ, {"INTERVIEW_OCR_ENGINE": "tesseract"}):
            imported = run_script(
                "ingest_transcript.py", "--root", str(self.root), "--transcript", str(scanned),
                "--date", "2026-10-04", "--jd", str(scanned),
            )
        manifest = read_json(Path(imported["case_path"]) / "manifest.json")
        self.assertEqual(manifest["inputs"]["transcript_pdf_pages"][0]["page"], 1)
        self.assertTrue(manifest["inputs"]["jd"]["pdf_pages"][0]["method"].startswith("tesseract"))


if __name__ == "__main__":
    unittest.main()
