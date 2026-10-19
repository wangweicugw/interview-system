#!/usr/bin/env python3
"""Flag possible interviewer questions missing from the curated question tree."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from check_data_compatibility import require_store_compatible
from common import now_iso, write_json


QUESTION_CUE = re.compile(r"[?？]|为什么|怎么|如何|有哪些|是什么|能不能|有没有|讲一下|说一下|介绍一下|聊一下")


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def audit_case(case_path: Path) -> list[dict[str, Any]]:
    qa = read_json(case_path / "qa-tree.json")
    transcript_path = case_path / "transcript.revised.json"
    if not transcript_path.is_file():
        transcript_path = case_path / "transcript.raw.json"
    transcript = read_json(transcript_path)
    questions = [item for item in qa.get("questions", []) if isinstance(item, dict)]
    segments = [item for item in transcript.get("segments", []) if isinstance(item, dict)]
    covered = {
        str(segment_id) for question in questions
        for segment_id in question.get("question_evidence", [])
    }
    answer_to_questions: dict[str, list[str]] = {}
    for question in questions:
        for segment_id in question.get("answer_evidence", []):
            answer_to_questions.setdefault(str(segment_id), []).append(str(question.get("question_id") or ""))
    candidates: list[dict[str, Any]] = []
    for index, segment in enumerate(segments):
        segment_id = str(segment.get("segment_id") or "")
        text = str(segment.get("text_corrected") or segment.get("text_raw") or "").strip()
        if segment.get("role") != "interviewer" or segment_id in covered or len(text) < 12 or not QUESTION_CUE.search(text):
            continue
        following = next((
            item for item in segments[index + 1:index + 4]
            if item.get("role") == "candidate"
        ), None)
        answer_id = str(following.get("segment_id") or "") if following else ""
        answer_length = len(str(following.get("text_corrected") or following.get("text_raw") or "").strip()) if following else 0
        linked_questions = sorted(set(answer_to_questions.get(answer_id, [])))
        candidates.append({
            "case_id": str(qa.get("case_id") or ""),
            "segment_id": segment_id,
            "excerpt": text[:220],
            "following_answer_segment_id": answer_id,
            "existing_answer_question_ids": linked_questions,
            "review_hint": "可能漏标提问证据" if linked_questions else "可能漏建原题",
            "priority": "先复核" if answer_length >= 30 else "低优先级线索",
            "status": "candidate_only",
        })
    return candidates


def audit(root: Path, *, write_report: bool = False) -> dict[str, Any]:
    require_store_compatible(root)
    cases = sorted((root / "interviews").glob("*/qa-tree.json"))
    candidates = [item for path in cases for item in audit_case(path.parent)]
    summary = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "cases_checked": len(cases),
        "candidate_count": len(candidates),
        "review_first_count": sum(item["priority"] == "先复核" for item in candidates),
        "possible_evidence_gaps": sum(bool(item["existing_answer_question_ids"]) for item in candidates),
        "without_existing_answer_link": sum(not item["existing_answer_question_ids"] for item in candidates),
        "warning": "仅按问句词和后续回答排序；包含寒暄、确认及转写错误，不代表已核实漏题。",
    }
    if write_report:
        path = root / "profile" / "question-coverage-audit.json"
        write_json(path, {**summary, "candidates": candidates})
        summary["report"] = str(path)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="扫描问答树可能遗漏的面试官提问，不自动新增题目")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--write-report", action="store_true")
    args = parser.parse_args()
    print(json.dumps(audit(args.root.expanduser().resolve(), write_report=args.write_report), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
