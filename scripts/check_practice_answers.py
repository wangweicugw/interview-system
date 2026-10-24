#!/usr/bin/env python3
"""Check reference-answer coverage for focused or all interview questions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import question_taxonomy
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


TECHNICAL_CATEGORIES = {"technical", "algorithm", "system_design"}


def has_sources(value: Any) -> bool:
    """参考答案的核验资料：至少一条带标题和 URL 的来源。"""
    if not isinstance(value, dict):
        return False
    for source in value.get("sources", []) or []:
        if (isinstance(source, dict)
                and str(source.get("title") or "").strip()
                and str(source.get("url") or "").strip()):
            return True
    return False


def resolve_case(cases: list[dict[str, Any]], case_ref: str) -> dict[str, Any] | None:
    """按 case_id、目录名或绝对路径定位单场。"""
    reference = case_ref.strip()
    if not reference:
        return None
    for case in cases:
        if str(case["case_id"]) == reference or case["path"].name == reference:
            return case
    try:
        candidate = Path(reference).expanduser().resolve()
    except OSError:
        return None
    for case in cases:
        if case["path"].resolve() == candidate:
            return case
    return None


def check(root: Path, scope: str, case_ref: str = "") -> dict[str, Any]:
    cases = discover_cases(root)
    by_case = {str(case["case_id"]): case for case in cases}
    focus = read_json(root / "profile" / "practice-focus.json", {})
    groups = focus.get("groups", []) if isinstance(focus, dict) else []
    if not isinstance(groups, list):
        groups = []
    errors: list[str] = []
    without_sources: list[str] = []
    target_case: dict[str, Any] | None = None
    if case_ref.strip():
        target_case = resolve_case(cases, case_ref)
        if target_case is None:
            return {
                "status": "invalid",
                "scope": "case",
                "case": case_ref.strip(),
                "cases": len(cases),
                "main_questions": 0,
                "followup_questions": 0,
                "answers_without_sources": [],
                "errors": [f"未知场次：{case_ref.strip()}"],
            }
    focused: dict[tuple[str, str], dict[str, Any]] = {}
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("questions"), list):
            continue
        for ref in group["questions"]:
            if not isinstance(ref, dict):
                continue
            case_id = str(ref.get("case_id") or "")
            if target_case is not None and case_id != str(target_case["case_id"]):
                continue
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
    categories: dict[str, dict[str, str]] = {}
    for case in cases:
        categories[str(case["case_id"])] = {
            str(item["question"].get("question_id")): str(item["question"].get("category") or "")
            for item in practice_items(case["path"])
        }
    if target_case is not None:
        targets = [(str(target_case["case_id"]), str(item["question"].get("question_id")))
                   for item in practice_items(target_case["path"])]
        scope = "case"
    elif scope == "focus":
        targets = [(case_id, qid) for case_id, qid in focused]
    else:
        targets = [(str(case["case_id"]), str(item["question"].get("question_id")))
                   for case in cases for item in practice_items(case["path"])]
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
        elif (not curated_complete
              and question_taxonomy.normalize(categories.get(case_id, {}).get(qid, ""))
              in TECHNICAL_CATEGORIES
              and not has_sources(answer)):
            without_sources.append(f"{case_id}/{qid}")
    for (case_id, qid), ref in focused.items():
        if target_case is not None and case_id != str(target_case["case_id"]):
            continue
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
            elif not has_sources(answers[index]):
                without_sources.append(f"{case_id}/{qid}/追问{index + 1}")
        if len(answers) > len(followups):
            errors.append(f"延伸追问答案多于题目：{case_id}/{qid}")
    return {
        "status": "valid" if not errors else "invalid",
        "scope": scope,
        "case": str(target_case["case_id"]) if target_case is not None else None,
        "cases": len(cases),
        "main_questions": checked_main,
        "followup_questions": checked_followups,
        "answers_without_sources": without_sources,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="校验面试练习题参考答案覆盖率")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--scope", choices=("focus", "all"), default="focus")
    parser.add_argument(
        "--case",
        default="",
        help="只校验单场：case_id、场次目录名或绝对路径；提供时忽略 --scope",
    )
    args = parser.parse_args()
    result = check(args.root.expanduser().resolve(), args.scope, args.case)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "valid" else 1


if __name__ == "__main__":
    raise SystemExit(main())
