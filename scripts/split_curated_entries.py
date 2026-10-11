#!/usr/bin/env python3
"""Split one mixed standard answer and question cluster into focused entries."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any

from check_data_compatibility import require_store_compatible
from check_question_clusters import validate_question_clusters
from check_standard_answers import validate_standard_answers
from common import now_iso, write_json


def _read_object(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RuntimeError(f"{label}无法读取：{error}") from error
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise RuntimeError(f"{label} schema_version 必须为 1")
    return payload


def _index(items: Any, field: str, label: str) -> dict[str, dict[str, Any]]:
    if not isinstance(items, list):
        raise RuntimeError(f"{label}必须为数组")
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict) or not str(item.get(field) or "").strip():
            raise RuntimeError(f"{label}存在缺少 {field} 的条目")
        item_id = str(item[field])
        if item_id in result:
            raise RuntimeError(f"{label} {field} 重复：{item_id}")
        result[item_id] = item
    return result


def _refs(items: list[dict[str, Any]], field: str) -> Counter[tuple[str, str]]:
    refs: Counter[tuple[str, str]] = Counter()
    for item in items:
        values = item.get(field)
        if not isinstance(values, list):
            raise RuntimeError(f"{item.get('answer_id') or item.get('cluster_id')} 缺少 {field}")
        for ref in values:
            if not isinstance(ref, dict):
                raise RuntimeError(f"{field} 引用必须为对象")
            key = (str(ref.get("case_id") or ""), str(ref.get("question_id") or ""))
            if not all(key):
                raise RuntimeError(f"{field} 存在空引用")
            refs[key] += 1
    return refs


def _require_partition(original: dict[str, Any], replacements: list[dict[str, Any]], field: str) -> None:
    before = _refs([original], field)
    after = _refs(replacements, field)
    if before != after:
        missing = sorted((before - after).elements())
        extra = sorted((after - before).elements())
        raise RuntimeError(f"{field} 必须完整且不重复地拆分；缺少={missing}，多出={extra}")


def _stage_validate(root: Path, answers: dict[str, Any], clusters: dict[str, Any]) -> None:
    with tempfile.TemporaryDirectory(prefix="interview-split-") as temp_name:
        staging = Path(temp_name)
        (staging / "profile").mkdir()
        os.symlink(root / "interviews", staging / "interviews", target_is_directory=True)
        write_json(staging / "profile" / "standard-answers.json", answers)
        write_json(staging / "profile" / "question-clusters.json", clusters)
        issues = validate_standard_answers(staging) + validate_question_clusters(staging)
        if issues:
            raise RuntimeError("拆分结果校验失败：" + "；".join(issues))


def split_entries(root: Path, spec_path: Path) -> dict[str, Any]:
    require_store_compatible(root)
    spec = _read_object(spec_path, "拆分规格")
    reason = str(spec.get("reason") or "").strip()
    if not reason:
        raise RuntimeError("拆分规格缺少 reason")

    answers_path = root / "profile" / "standard-answers.json"
    clusters_path = root / "profile" / "question-clusters.json"
    answers_payload = _read_object(answers_path, "成熟答案")
    clusters_payload = _read_object(clusters_path, "高频问法")
    answers_copy = deepcopy(answers_payload)
    clusters_copy = deepcopy(clusters_payload)
    answers = _index(answers_copy.get("answers"), "answer_id", "answers")
    clusters = _index(clusters_copy.get("clusters"), "cluster_id", "clusters")

    original_answer_id = str(spec.get("original_answer_id") or "")
    original_cluster_id = str(spec.get("original_cluster_id") or "")
    original_answer = answers.get(original_answer_id)
    original_cluster = clusters.get(original_cluster_id)
    if original_answer is None:
        raise RuntimeError(f"找不到待拆分成熟答案：{original_answer_id}")
    if original_cluster is None:
        raise RuntimeError(f"找不到待拆分高频问法：{original_cluster_id}")
    if original_answer.get("status") == "rejected" or original_cluster.get("status") == "rejected":
        raise RuntimeError("待拆分条目已是 rejected，不能重复拆分")

    new_answers = deepcopy(spec.get("new_answers"))
    new_clusters = deepcopy(spec.get("new_clusters"))
    new_answer_index = _index(new_answers, "answer_id", "new_answers")
    new_cluster_index = _index(new_clusters, "cluster_id", "new_clusters")
    if len(new_answers) < 2 or len(new_clusters) < 2:
        raise RuntimeError("拆分至少需要两条新成熟答案和两组新高频问法")
    if set(new_answer_index) & set(answers):
        raise RuntimeError("新成熟答案 ID 与已有条目冲突")
    if set(new_cluster_index) & set(clusters):
        raise RuntimeError("新高频问法 ID 与已有条目冲突")
    if any(item.get("status") != "pending" for item in new_answers + new_clusters):
        raise RuntimeError("新拆分条目必须保持 pending，不能自动确认")

    _require_partition(original_answer, new_answers, "source_questions")
    _require_partition(original_cluster, new_clusters, "questions")

    changed_at = now_iso()
    previous_answer_status = str(original_answer.get("status") or "")
    previous_cluster_status = str(original_cluster.get("status") or "")
    original_answer["status"] = "rejected"
    original_answer["reviewed_at"] = changed_at
    original_cluster["status"] = "rejected"
    original_cluster["reviewed_at"] = changed_at
    answers_copy["answers"].extend(new_answers)
    clusters_copy["clusters"].extend(new_clusters)
    _stage_validate(root, answers_copy, clusters_copy)

    write_json(answers_path, answers_copy)
    write_json(clusters_path, clusters_copy)
    os.umask(0o077)
    revision_sets = (
        (root / "profile" / "standard-answer-revisions.jsonl", [{
            "schema_version": 1, "answer_id": original_answer_id,
            "previous_status": previous_answer_status,
            "status": "rejected", "reason": reason, "changed_at": changed_at,
            "split_into": list(new_answer_index),
        }] + [{
            "schema_version": 1, "answer_id": item_id, "previous_status": None,
            "status": "pending", "reason": f"由 {original_answer_id} 拆分创建",
            "changed_at": changed_at,
        } for item_id in new_answer_index]),
        (root / "profile" / "question-cluster-revisions.jsonl", [{
            "schema_version": 1, "cluster_id": original_cluster_id,
            "previous_status": previous_cluster_status,
            "status": "rejected", "reason": reason, "changed_at": changed_at,
            "split_into": list(new_cluster_index),
        }] + [{
            "schema_version": 1, "cluster_id": item_id, "previous_status": None,
            "status": "pending", "reason": f"由 {original_cluster_id} 拆分创建",
            "changed_at": changed_at,
        } for item_id in new_cluster_index]),
    )
    for path, revisions in revision_sets:
        with path.open("a", encoding="utf-8") as handle:
            for revision in revisions:
                handle.write(json.dumps(revision, ensure_ascii=False) + "\n")

    return {
        "status": "split",
        "original_answer_id": original_answer_id,
        "original_cluster_id": original_cluster_id,
        "new_answer_ids": list(new_answer_index),
        "new_cluster_ids": list(new_cluster_index),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="将混合成熟答案和高频问法按项目或语义拆分")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--spec", required=True, type=Path)
    args = parser.parse_args()
    result = split_entries(args.root.expanduser().resolve(), args.spec.expanduser().resolve())
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False))
        raise SystemExit(1)
