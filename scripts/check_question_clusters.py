#!/usr/bin/env python3
"""Validate curated repeated-question clusters and their case references."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from check_standard_answers import TOPICS, question_index, read_json


def validate_question_clusters(root: Path) -> list[str]:
    path = root / "profile" / "question-clusters.json"
    if not path.exists():
        return []
    payload = read_json(path)
    if not isinstance(payload, dict):
        return ["profile/question-clusters.json 无法读取为 JSON 对象"]
    if payload.get("schema_version") != 1:
        return ["profile/question-clusters.json schema_version 必须为 1"]
    clusters = payload.get("clusters")
    if not isinstance(clusters, list):
        return ["profile/question-clusters.json clusters 必须为数组"]

    errors: list[str] = []
    ids: set[str] = set()
    assigned: set[tuple[str, str]] = set()
    known_questions = question_index(root)
    for index, cluster in enumerate(clusters, start=1):
        prefix = f"clusters[{index}]"
        if not isinstance(cluster, dict):
            errors.append(f"{prefix} 必须为对象")
            continue
        cluster_id = str(cluster.get("cluster_id") or "").strip()
        if not cluster_id:
            errors.append(f"{prefix}.cluster_id 缺失")
        elif cluster_id in ids:
            errors.append(f"{prefix}.cluster_id 重复：{cluster_id}")
        ids.add(cluster_id)
        if not str(cluster.get("label") or "").strip():
            errors.append(f"{prefix}.label 缺失")
        if str(cluster.get("topic") or "") not in TOPICS:
            errors.append(f"{prefix}.topic 无效：{cluster.get('topic') or '空'}")
        status = cluster.get("status")
        if status not in {"pending", "confirmed", "rejected"}:
            errors.append(f"{prefix}.status 只能是 pending、confirmed 或 rejected")
        questions = cluster.get("questions")
        if not isinstance(questions, list) or len(questions) < 2:
            errors.append(f"{prefix}.questions 至少引用两道真实问题")
            continue
        local: set[tuple[str, str]] = set()
        case_ids: set[str] = set()
        for ref_index, ref in enumerate(questions, start=1):
            if not isinstance(ref, dict):
                errors.append(f"{prefix}.questions[{ref_index}] 必须为对象")
                continue
            case_id = str(ref.get("case_id") or "")
            question_id = str(ref.get("question_id") or "")
            key = (case_id, question_id)
            if question_id not in known_questions.get(case_id, set()):
                errors.append(f"{prefix}.questions[{ref_index}] 引用不存在：{case_id}/{question_id}")
            if key in local:
                errors.append(f"{prefix}.questions[{ref_index}] 在组内重复：{case_id}/{question_id}")
            if status != "rejected" and key in assigned:
                errors.append(f"{prefix}.questions[{ref_index}] 已归入其他高频问法：{case_id}/{question_id}")
            local.add(key)
            if status != "rejected":
                assigned.add(key)
            case_ids.add(case_id)
        if len(case_ids) < 2:
            errors.append(f"{prefix} 只覆盖一场面试，不能作为跨场高频问法")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="校验跨场重复问法聚类及其真实问题引用")
    parser.add_argument("--root", required=True, type=Path)
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    errors = validate_question_clusters(root)
    print(json.dumps({
        "status": "valid" if not errors else "invalid",
        "path": str(root / "profile" / "question-clusters.json"),
        "errors": errors,
    }, ensure_ascii=False))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
