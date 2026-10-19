#!/usr/bin/env python3
"""Validate auditable question and cluster preference state."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from check_standard_answers import question_index


STATES = {"normal", "low_priority", "ignored"}


def validate_question_preferences(root: Path) -> list[str]:
    path = root / "profile" / "question-preferences.json"
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return [f"question-preferences.json 无法读取：{error}"]
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        return ["question-preferences.json schema_version 必须为 1"]
    preferences = payload.get("preferences")
    if not isinstance(preferences, list):
        return ["question-preferences.json preferences 必须为数组"]
    known_questions = question_index(root)
    try:
        clusters_payload = json.loads((root / "profile" / "question-clusters.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        clusters_payload = {}
    known_clusters = {
        str(item.get("cluster_id"))
        for item in clusters_payload.get("clusters", [])
        if isinstance(item, dict) and item.get("cluster_id")
    }
    seen: set[tuple[str, str, str]] = set()
    errors: list[str] = []
    for index, item in enumerate(preferences, start=1):
        if not isinstance(item, dict):
            errors.append(f"preferences[{index}] 必须为对象")
            continue
        target_type = str(item.get("target_type") or "")
        case_id = str(item.get("case_id") or "")
        question_id = str(item.get("question_id") or "")
        target_id = str(item.get("target_id") or "")
        key = (target_type, case_id + "/" + question_id, target_id)
        if key in seen:
            errors.append(f"preferences[{index}] 重复")
        seen.add(key)
        if target_type not in {"question", "cluster"}:
            errors.append(f"preferences[{index}].target_type 无效")
        if item.get("preference") not in STATES:
            errors.append(f"preferences[{index}].preference 无效")
        if target_type == "question" and question_id not in known_questions.get(case_id, set()):
            errors.append(f"preferences[{index}] 引用不存在：{case_id}/{question_id}")
        if target_type == "cluster" and target_id not in known_clusters:
            errors.append(f"preferences[{index}] 分组不存在：{target_id}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="校验题目优先级与忽略状态")
    parser.add_argument("--root", required=True, type=Path)
    args = parser.parse_args()
    errors = validate_question_preferences(args.root.expanduser().resolve())
    print(json.dumps({"status": "valid" if not errors else "invalid", "errors": errors}, ensure_ascii=False))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
