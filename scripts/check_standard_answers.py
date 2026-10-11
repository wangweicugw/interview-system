#!/usr/bin/env python3
"""Validate private cross-case standard answers and their evidence references."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


TOPICS = {
    "self-intro", "career", "project-role", "middleware", "sharding", "mysql",
    "availability", "migration", "observability", "performance", "java",
    "algorithm", "system-design", "ai", "messaging", "collaboration", "product",
    "other",
}


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def http_url(value: Any) -> bool:
    parsed = urlparse(str(value or ""))
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def question_index(root: Path) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for path in (root / "interviews").glob("*/qa-tree.json"):
        payload = read_json(path)
        if not isinstance(payload, dict):
            continue
        case_id = str(payload.get("case_id") or "")
        if not case_id:
            continue
        result[case_id] = {
            str(item.get("question_id")) for item in payload.get("questions", [])
            if isinstance(item, dict) and item.get("question_id")
        }
    return result


def validate_standard_answers(root: Path) -> list[str]:
    path = root / "profile" / "standard-answers.json"
    if not path.exists():
        return []
    payload = read_json(path)
    if not isinstance(payload, dict):
        return ["profile/standard-answers.json 无法读取为 JSON 对象"]
    if payload.get("schema_version") != 1:
        return ["profile/standard-answers.json schema_version 必须为 1"]
    answers = payload.get("answers")
    if not isinstance(answers, list):
        return ["profile/standard-answers.json answers 必须为数组"]

    errors: list[str] = []
    ids: set[str] = set()
    known_questions = question_index(root)
    for index, answer in enumerate(answers, start=1):
        prefix = f"answers[{index}]"
        if not isinstance(answer, dict):
            errors.append(f"{prefix} 必须为对象")
            continue
        answer_id = str(answer.get("answer_id") or "").strip()
        topic = str(answer.get("topic") or "").strip()
        if not answer_id:
            errors.append(f"{prefix}.answer_id 缺失")
        elif answer_id in ids:
            errors.append(f"{prefix}.answer_id 重复：{answer_id}")
        ids.add(answer_id)
        if topic not in TOPICS:
            errors.append(f"{prefix}.topic 无效：{topic or '空'}")
        if answer.get("status") not in {"pending", "confirmed", "rejected"}:
            errors.append(f"{prefix}.status 只能是 pending、confirmed 或 rejected")
        for field in ("title", "basis"):
            if not str(answer.get(field) or "").strip():
                errors.append(f"{prefix}.{field} 缺失")
        versions = answer.get("versions")
        if not isinstance(versions, dict):
            errors.append(f"{prefix}.versions 必须为对象")
        else:
            for field in ("short_30s", "standard_2m"):
                if not str(versions.get(field) or "").strip():
                    errors.append(f"{prefix}.versions.{field} 缺失")
            followups = versions.get("followups", [])
            if not isinstance(followups, list):
                errors.append(f"{prefix}.versions.followups 必须为数组")
            else:
                for followup_index, followup in enumerate(followups, start=1):
                    if not isinstance(followup, dict) or not str(followup.get("question") or "").strip() or not str(followup.get("answer") or "").strip():
                        errors.append(f"{prefix}.versions.followups[{followup_index}] 必须同时包含 question 和 answer")
        refs = answer.get("source_questions")
        if not isinstance(refs, list) or not refs:
            errors.append(f"{prefix}.source_questions 至少引用一道真实问题")
        else:
            for ref_index, ref in enumerate(refs, start=1):
                if not isinstance(ref, dict):
                    errors.append(f"{prefix}.source_questions[{ref_index}] 必须为对象")
                    continue
                case_id = str(ref.get("case_id") or "")
                question_id = str(ref.get("question_id") or "")
                if question_id not in known_questions.get(case_id, set()):
                    errors.append(f"{prefix}.source_questions[{ref_index}] 引用不存在：{case_id}/{question_id}")
        limitations = answer.get("limitations", [])
        if not isinstance(limitations, list) or any(not isinstance(value, str) or not value.strip() for value in limitations):
            errors.append(f"{prefix}.limitations 必须为非空字符串数组")
        sources = answer.get("sources", [])
        if not isinstance(sources, list):
            errors.append(f"{prefix}.sources 必须为数组")
        else:
            for source_index, source in enumerate(sources, start=1):
                if not isinstance(source, dict) or not str(source.get("title") or "").strip() or not http_url(source.get("url")):
                    errors.append(f"{prefix}.sources[{source_index}] 必须包含标题和 HTTP(S) URL")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="校验跨场成熟答案及其真实问题引用")
    parser.add_argument("--root", required=True, type=Path)
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    errors = validate_standard_answers(root)
    print(json.dumps({
        "status": "valid" if not errors else "invalid",
        "path": str(root / "profile" / "standard-answers.json"),
        "errors": errors,
    }, ensure_ascii=False))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
