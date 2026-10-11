from __future__ import annotations

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

from common import read_json, read_supported_text  # noqa: E402
from coach_runner import run_case as run_coach_case  # noqa: E402
from ingest_audio_transcript import normalize_segments, parse_artifact  # noqa: E402
from pdf_ocr import extract_pdf  # noqa: E402
from render_interview_html import discover_cases, jd_source, render_case, render_history_practice  # noqa: E402


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

        rendered = run_script(
            "render_interview_html.py", "--root", str(self.root), "--all"
        )
        self.assertEqual(rendered["status"], "ok")
        self.assertEqual(rendered["history_cases"], 2)

        case_html = (case_path / "interview-review.html").read_text(encoding="utf-8")
        history_html = (self.root / "interview-history.html").read_text(encoding="utf-8")
        question_bank_html = (self.root / "interview-question-bank.html").read_text(encoding="utf-8")
        HTMLParser().feed(case_html)
        HTMLParser().feed(history_html)
        HTMLParser().feed(question_bank_html)
        self.assertIn("<td>深入考察</td>", case_html)
        self.assertIn("<td>高</td>", case_html)
        self.assertIn("表现较弱</span>", case_html)
        self.assertIn("说话人：已确认", case_html)
        self.assertIn("分析：已更新", case_html)
        self.assertIn('<section id="jd"><h2>岗位 JD 原文</h2>', case_html)
        self.assertIn("岗位要求：熟悉缓存一致性、数据库故障处理和可观测性。", case_html)
        self.assertLess(case_html.index('id="jd"'), case_html.index('id="jd-matrix"'))
        self.assertNotIn("<td>deep</td>", case_html)
        self.assertIn('data-interview-case="', case_html)
        self.assertIn('id="focus"', case_html)
        self.assertIn('id="practice"', case_html)
        self.assertIn('.practice-grid{display:block}', case_html)
        self.assertIn('id="practice-q-002"', case_html)
        self.assertIn('“为什么选择这个顺序？”', case_html)
        self.assertIn('原始问题：</strong>“为什么选择这个顺序？”', case_html)
        self.assertIn('我的现场回答：</strong>', case_html)
        self.assertIn('回答问题（AI 判断）：</strong>存在绝对化技术判断。', case_html)
        self.assertIn('相关文档：</strong>', case_html)
        self.assertIn('提问：<a href="#s-0003">s-0003</a>', case_html)
        self.assertIn('回答：<a href="#s-0004">s-0004</a>', case_html)
        self.assertIn('问答树记录的追问', case_html)
        self.assertLess(case_html.index('id="practice-q-002"'), case_html.index('id="practice-q-001"'))
        self.assertIn("做一次缓存失效演练并保留日志。", case_html)
        self.assertIn('href="#s-0002"', case_html)
        self.assertLess(case_html.index('id="focus"'), case_html.index('id="jd"'))
        self.assertIn("做缓存故障演练", history_html)
        self.assertIn("保留日志与修复步骤", history_html)
        self.assertNotIn("已完成的练习", history_html)
        self.assertLess(history_html.index('id="next-actions"'), history_html.index('id="history"'))
        self.assertIn('先用面试问题练习', history_html)
        self.assertIn('为什么选择这个顺序', history_html)
        self.assertIn('interview-review.html#practice-q-002', history_html)
        self.assertLess(history_html.index('id="practice"'), history_html.index('id="history"'))
        self.assertIn("data-history-row", history_html)
        self.assertIn('id="history"', case_html)
        self.assertIn("示例公司", history_html)
        self.assertIn("第二家公司", case_html)
        self.assertIn("第二家公司", history_html)
        self.assertIn('href="interview-question-bank.html"', history_html)
        self.assertIn("data-interview-question-bank", question_bank_html)
        self.assertIn('id="question-bank"', question_bank_html)
        self.assertIn("全部面试题", question_bank_html)
        self.assertIn("我的现场回答", question_bank_html)
        self.assertIn("推荐答案", question_bank_html)
        self.assertIn("为什么选择这个顺序", question_bank_html)
        self.assertIn("data-question-search", question_bank_html)
        self.assertIn("data-question-row", question_bank_html)
        self.assertIn('class="bank-sidebar"', question_bank_html)
        self.assertIn("搜索与书签", question_bank_html)
        self.assertIn("按主题覆盖频率排序的书签", question_bank_html)
        self.assertIn("覆盖 2 / 2 场", question_bank_html)
        self.assertIn("主题仍按覆盖的面试场次数排序", question_bank_html)
        self.assertEqual(question_bank_html.count("此说明只展示一次"), 1)
        self.assertNotIn("查看练习方法", question_bank_html)
        self.assertIn("2 份跨场成熟答案", question_bank_html)
        self.assertIn("相关跨场成熟答案（2 份）", question_bank_html)
        self.assertIn("查看：缓存顺序的备选答法", question_bank_html)
        self.assertIn("缓存顺序标准答法", question_bank_html)
        self.assertIn("30 秒版", question_bank_html)
        self.assertIn("2 分钟版", question_bank_html)
        self.assertIn("常见追问", question_bank_html)
        self.assertIn("待补证据", question_bank_html)
        self.assertIn("待确认", question_bank_html)
        self.assertIn("data-standard-answer", question_bank_html)
        self.assertIn("https://example.com/cache", question_bank_html)
        self.assertNotIn("javascript:alert(1)", question_bank_html)
        self.assertIn(f'interview-review.html#practice-q-002', question_bank_html)
        self.assertIn("1 组跨场高频问法", question_bank_html)
        self.assertIn("高频重复问法", question_bank_html)
        self.assertIn("项目介绍与职责", question_bank_html)
        self.assertIn("覆盖 2 场 · 2 道真实问题", question_bank_html)
        self.assertIn("AI 归并 · 待确认", question_bank_html)
        self.assertIn("data-question-cluster", question_bank_html)
        self.assertIn("跨场高频重复问法", question_bank_html)
        self.assertIn("确认方式", question_bank_html)
        self.assertIn('id="review-center"', question_bank_html)
        self.assertIn("事实与答案确认中心", question_bank_html)
        self.assertIn("data-export-actions", question_bank_html)
        self.assertIn("data-review-status=\"confirmed\"", question_bank_html)
        self.assertIn("data-edit-short", question_bank_html)
        self.assertIn("data-practice-widget", question_bank_html)
        self.assertIn("脱稿练习并记录", question_bank_html)
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
        focused = render_history_practice(self.root, discover_cases(self.root))
        self.assertIn("T01 故障排查", focused)
        self.assertIn("为什么选择这个顺序", focused)
        self.assertIn("练习知识点：</strong>缓存一致性", focused)

        self.assertIn("更新失败时如何补偿？", focused)
        self.assertIn("再看 1 道延伸追问", focused)
        self.assertIn("练习题，非面试原话", focused)
        self.assertIn("AI 整理的参考答法，非当场回答", focused)
        self.assertEqual(focused.count("查看本题参考答案"), 2)
        self.assertIn("核对 &lt;旧数据&gt;", focused)
        self.assertIn("if (a &lt; b) return;", focused)
        self.assertIn('href="https://example.com/official/cache"', focused)
        self.assertNotIn("javascript:alert", focused)
        self.assertEqual(focused.count('class="practice-card"'), 1)
        focused_case = render_case(next(item for item in discover_cases(self.root)
                                        if item["case_id"] == result["case_id"]), discover_cases(self.root))
        self.assertIn("更新失败时如何补偿？", focused_case)

        main_answer = focus["groups"][0]["questions"][0].pop("reference_answer")
        (case_path / "practice-answers.json").write_text(
            json.dumps({"schema_version": 1, "questions": [
                {"question_id": "q-002", "reference_answer": main_answer}
            ]}, ensure_ascii=False), encoding="utf-8"
        )
        (self.root / "profile" / "practice-focus.json").write_text(
            json.dumps(focus, ensure_ascii=False), encoding="utf-8"
        )
        self.assertIn("核对 &lt;旧数据&gt;", render_history_practice(self.root, discover_cases(self.root)))

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
        created = run_script("backup_store.py", "create", "--root", str(self.root), "--output", str(backup))
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

    def test_workbuddy_package_contains_renderer_without_private_data(self) -> None:
        output = Path(self.temp.name) / "interview-system-workbuddy.zip"
        built = run_script(
            "build_workbuddy_package.py",
            "--source",
            str(SKILL),
            "--output",
            str(output),
        )
        self.assertEqual(built["status"], "created")
        self.assertFalse(built["contains_interview_cases"])
        with zipfile.ZipFile(output) as archive:
            names = archive.namelist()
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
        self.assertIn(
            "interview-system/scripts/register_reference_transcript.py", names
        )
        self.assertIn("interview-system/scripts/build_next_prep.py", names)
        self.assertIn(
            "interview-system/references/html-presentation.md", names
        )
        self.assertFalse(any("tests/" in name or "private/" in name for name in names))

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
