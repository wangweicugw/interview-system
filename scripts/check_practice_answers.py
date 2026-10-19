#!/usr/bin/env python3
"""Check reference-answer coverage for focused or all interview questions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from render_interview_html import case_answer_map, discover_cases, practice_items, read_json


def has_answer(value: Any) -> bool:
    if not isinstance(value, dict) or not isinstance(value.get("steps"), list):
        return False
    return any(
        isinstance(step, dict)
        and str(step.get("heading") or "").strip()
        and str(step.get("body") or "").strip()
        for step in value["steps"]
    )


def check(root: Path, scope: str) -> dict[str, Any]:
    cases = discover_cases(root)
    by_case = {str(case["case_id"]): case for case in cases}
    focus = read_json(root / "profile" / "practice-focus.json", {})
    groups = focus.get("groups", []) if isinstance(focus, dict) else []
    if not isinstance(groups, list):
        groups = []
    errors: list[str] = []
    focused: dict[tuple[str, str], dict[str, Any]] = {}
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("questions"), list):
            continue
        for ref in group["questions"]:
            if not isinstance(ref, dict):
                continue
            case_id = str(ref.get("case_id") or "")
            qid = str(ref.get("question_id") or "")
            key = (case_id, qid)
            case = by_case.get(case_id)
            if not case or qid not in {str(item["question"].get("question_id")) for item in practice_items(case["path"])}:
                errors.append(f"未知重点题：{case_id}/{qid}")
                continue
            if key in focused:
                errors.append(f"重点题重复：{case_id}/{qid}")
            focused[key] = ref
    checked_main = 0
    checked_followups = 0
    targets = (
        [(case_id, qid) for case_id, qid in focused]
        if scope == "focus"
        else [(str(case["case_id"]), str(item["question"].get("question_id")))
              for case in cases for item in practice_items(case["path"])]
    )
    for case_id, qid in targets:
        case = by_case[case_id]
        ref = focused.get((case_id, qid), {})
        stored = case_answer_map(case["path"]).get(qid, {})
        curated = stored.get("curated_answer")
        versions = curated.get("versions", {}) if isinstance(curated, dict) else {}
        curated_complete = (isinstance(versions, dict)
                            and bool(str(versions.get("short_30s") or "").strip())
                            and bool(str(versions.get("standard_2m") or "").strip()))
        answer = ref.get("reference_answer") or stored.get("reference_answer")
        checked_main += 1
        if not curated_complete and not has_answer(answer):
            errors.append(f"缺少原题参考答案：{case_id}/{qid}")
    for (case_id, qid), ref in focused.items():
        followups = ref.get("followups", [])
        followups = followups if isinstance(followups, list) else []
        answers = ref.get("followup_answers", [])
        answers = answers if isinstance(answers, list) else []
        for index, question in enumerate(followups):
            if not isinstance(question, str) or not question.strip():
                errors.append(f"延伸追问题面为空：{case_id}/{qid}/{index + 1}")
            checked_followups += 1
            if index >= len(answers) or not has_answer(answers[index]):
                errors.append(f"缺少延伸追问参考答案：{case_id}/{qid}/{index + 1}")
        if len(answers) > len(followups):
            errors.append(f"延伸追问答案多于题目：{case_id}/{qid}")
    return {
        "status": "valid" if not errors else "invalid",
        "scope": scope,
        "cases": len(cases),
        "main_questions": checked_main,
        "followup_questions": checked_followups,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="校验面试练习题参考答案覆盖率")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--scope", choices=("focus", "all"), default="focus")
    args = parser.parse_args()
    result = check(args.root.expanduser().resolve(), args.scope)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "valid" else 1


if __name__ == "__main__":
    raise SystemExit(main())
