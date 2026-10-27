#!/usr/bin/env python3
"""把各场 qa-tree.json 的 category 归并到受控词表。

默认只报告；--apply 会先校验全部目标场次，全部有效后才写盘。
个案改判从 Skill 外的私有 JSON 文件传入，不写入公开源码。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import question_taxonomy
from common import read_json, write_json
from render_interview_html import discover_cases


def load_overrides(path: Path | None) -> dict[tuple[str, str], str]:
    if path is None:
        return {}
    if not path.is_absolute():
        raise ValueError("--overrides 必须是私有文件的绝对路径")
    source = path.expanduser().resolve()
    if source.is_relative_to(Path(__file__).resolve().parents[1]):
        raise ValueError("个案改判文件不得位于 Skill 源码目录内")
    payload = read_json(source)
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("个案改判文件的 schema_version 必须为 1")
    records = payload.get("overrides")
    if not isinstance(records, list):
        raise ValueError("个案改判文件的 overrides 必须为数组")
    result: dict[tuple[str, str], str] = {}
    for index, item in enumerate(records, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"第 {index} 条个案改判必须为对象")
        case_id = str(item.get("case_id") or "").strip()
        question_id = str(item.get("question_id") or "").strip()
        category = str(item.get("category") or "").strip()
        if not case_id or not question_id or not question_taxonomy.is_canonical(category):
            raise ValueError(f"第 {index} 条个案改判缺少场次、题号或有效分类")
        key = (case_id, question_id)
        if key in result:
            raise ValueError(f"第 {index} 条个案改判重复")
        result[key] = category
    return result


def resolve_case(cases: list[dict[str, Any]], case_ref: str | None) -> list[dict[str, Any]]:
    if not case_ref:
        return cases
    reference = case_ref.strip()
    for case in cases:
        if str(case["case_id"]) == reference or case["path"].name == reference:
            return [case]
    try:
        candidate = Path(reference).expanduser().resolve()
    except OSError:
        return []
    return [case for case in cases if case["path"].resolve() == candidate]


def target_category(case_id: str, question_id: str, current: str,
                    overrides: dict[tuple[str, str], str]) -> str:
    return overrides.get((case_id, question_id)) or question_taxonomy.normalize(current)


def planned_changes(case: dict[str, Any], overrides: dict[tuple[str, str], str]
                    ) -> tuple[list[dict[str, Any]], list[str]]:
    qa_path = case["path"] / "qa-tree.json"
    if not qa_path.is_file():
        return [], [f"缺少 qa-tree.json：{case['path'].name}"]
    payload = read_json(qa_path)
    if not isinstance(payload, dict) or not isinstance(payload.get("questions"), list):
        return [], [f"qa-tree.json 的 questions 无效：{case['path'].name}"]
    changes: list[dict[str, Any]] = []
    errors: list[str] = []
    case_id = str(case["case_id"])
    for question in payload["questions"]:
        if not isinstance(question, dict):
            errors.append(f"{case['path'].name} 存在非对象题目")
            continue
        qid = str(question.get("question_id") or "")
        current = str(question.get("category") or "").strip()
        target = target_category(case_id, qid, current, overrides)
        if not question_taxonomy.is_canonical(target):
            errors.append(f"{case['path'].name}/{qid} 的 category 无法归并：{current!r}")
            continue
        if target != current:
            changes.append({
                "case_id": case_id,
                "case": case["path"].name,
                "question_id": qid,
                "question": str(question.get("question") or "")[:60],
                "from": current,
                "to": target,
            })
    return changes, errors


def apply_changes(case: dict[str, Any], overrides: dict[tuple[str, str], str]) -> None:
    qa_path = case["path"] / "qa-tree.json"
    payload = read_json(qa_path)
    for question in payload["questions"]:
        qid = str(question.get("question_id") or "")
        current = str(question.get("category") or "").strip()
        question["category"] = target_category(str(case["case_id"]), qid, current, overrides)
    write_json(qa_path, payload)


def main() -> int:
    parser = argparse.ArgumentParser(description="把面试题分类归并到受控词表")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--case", help="只处理单场：case_id、场次目录名或绝对路径")
    parser.add_argument("--overrides", type=Path, help="Skill 外的私有个案改判 JSON 绝对路径")
    parser.add_argument("--apply", action="store_true", help="校验全部目标场次后写回；默认只报告")
    args = parser.parse_args()

    try:
        overrides = load_overrides(args.overrides)
    except (OSError, ValueError) as error:
        print(json.dumps({"status": "invalid", "applied": False, "errors": [str(error)]}, ensure_ascii=False))
        return 1
    root = args.root.expanduser().resolve()
    cases = resolve_case(discover_cases(root), args.case)
    if not cases:
        print(json.dumps({"status": "invalid", "applied": False, "errors": ["未找到匹配的场次"]}, ensure_ascii=False))
        return 1

    changes: list[dict[str, Any]] = []
    errors: list[str] = []
    cases_to_write: list[dict[str, Any]] = []
    for case in cases:
        case_changes, case_errors = planned_changes(case, overrides)
        changes.extend(case_changes)
        errors.extend(case_errors)
        if case_changes:
            cases_to_write.append(case)
    applied = bool(args.apply and not errors)
    if applied:
        for case in cases_to_write:
            apply_changes(case, overrides)

    remaining: dict[str, int] = {}
    for case in cases:
        qa_path = case["path"] / "qa-tree.json"
        if not qa_path.is_file():
            continue
        payload = read_json(qa_path)
        for question in payload.get("questions", []) if isinstance(payload, dict) else []:
            if not isinstance(question, dict):
                continue
            value = str(question.get("category") or "").strip()
            if not question_taxonomy.is_canonical(value):
                remaining[value or "(空)"] = remaining.get(value or "(空)", 0) + 1

    result = {
        "status": "valid" if not errors else "invalid",
        "applied": applied,
        "cases": len(cases),
        "changed": len(changes),
        "changes": changes,
        "remaining_non_canonical": remaining,
        "errors": errors,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
