#!/usr/bin/env python3
"""Validate question-tags.json without loading transcript content."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from check_standard_answers import question_index


def validate_question_tags(root: Path) -> list[str]:
    path = root / "profile" / "question-tags.json"
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return [f"question-tags.json 无法读取：{error}"]
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        return ["question-tags.json schema_version 必须为 1"]
    rows = payload.get("questions")
    if not isinstance(rows, list):
        return ["question-tags.json questions 必须为数组"]
    known = question_index(root)
    seen: set[tuple[str, str]] = set()
    errors: list[str] = []
    allowed = {"high", "medium", "low"}
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            errors.append(f"questions[{index}] 必须为对象")
            continue
        key = (str(row.get("case_id") or ""), str(row.get("question_id") or ""))
        if key in seen:
            errors.append(f"questions[{index}] 重复：{key[0]}/{key[1]}")
        seen.add(key)
        if key[1] not in known.get(key[0], set()):
            errors.append(f"questions[{index}] 引用不存在：{key[0]}/{key[1]}")
        tags = row.get("tags")
        if not isinstance(tags, dict):
            errors.append(f"questions[{index}].tags 必须为对象")
            continue
        for field in ("domain", "intent", "object_scene", "constraints"):
            values = tags.get(field)
            if not isinstance(values, list) or any(not isinstance(value, str) or not value.strip() for value in values):
                errors.append(f"questions[{index}].tags.{field} 必须为非空字符串数组")
        if tags.get("reusability") not in allowed:
            errors.append(f"questions[{index}].tags.reusability 无效")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="校验面试题标签")
    parser.add_argument("--root", required=True, type=Path)
    args = parser.parse_args()
    errors = validate_question_tags(args.root.expanduser().resolve())
    print(json.dumps({"status": "valid" if not errors else "invalid", "errors": errors}, ensure_ascii=False))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
