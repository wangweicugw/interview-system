#!/usr/bin/env python3
"""Revise mixed question clusters with complete, auditable partitions."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any

from check_data_compatibility import require_store_compatible
from check_question_clusters import validate_question_clusters
from common import append_private_text, now_iso, write_json


def read_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RuntimeError(f"{label} 无法读取：{error}") from error
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise RuntimeError(f"{label} schema_version 必须为 1")
    return value


def refs(items: list[dict[str, Any]]) -> list[tuple[str, str]]:
    return [
        (str(item.get("case_id") or ""), str(item.get("question_id") or ""))
        for item in items
        if isinstance(item, dict) and item.get("case_id") and item.get("question_id")
    ]


def validate_staged(root: Path, payload: dict[str, Any]) -> None:
    with tempfile.TemporaryDirectory(prefix="interview-cluster-revision-") as temp_name:
        staging = Path(temp_name)
        (staging / "profile").mkdir()
        os.symlink(root / "interviews", staging / "interviews", target_is_directory=True)
        write_json(staging / "profile" / "question-clusters.json", payload)
        issues = validate_question_clusters(staging)
        if issues:
            raise RuntimeError("修订后的题目分组校验失败：" + "；".join(issues))


def revise(root: Path, spec_path: Path) -> dict[str, Any]:
    require_store_compatible(root)
    spec = read_object(spec_path, "分组修订规格")
    reason = str(spec.get("reason") or "").strip()
    revisions = spec.get("revisions")
    if not reason or not isinstance(revisions, list) or not revisions:
        raise RuntimeError("分组修订规格必须包含 reason 和非空 revisions")

    path = root / "profile" / "question-clusters.json"
    payload = read_object(path, "question-clusters.json")
    updated = deepcopy(payload)
    clusters = updated.get("clusters")
    if not isinstance(clusters, list):
        raise RuntimeError("question-clusters.json clusters 必须为数组")
    index = {
        str(item.get("cluster_id")): item
        for item in clusters
        if isinstance(item, dict) and item.get("cluster_id")
    }
    changed_at = now_iso()
    audit_rows: list[dict[str, Any]] = []
    new_ids: set[str] = set()
    for entry in revisions:
        if not isinstance(entry, dict):
            raise RuntimeError("revisions 条目必须为对象")
        original_id = str(entry.get("original_cluster_id") or "")
        original = index.get(original_id)
        new_clusters = entry.get("new_clusters")
        unassigned = sorted(refs(entry.get("unassigned", [])))
        if original is None or not isinstance(new_clusters, list):
            raise RuntimeError(f"修订条目无效：{original_id}")
        if original.get("status") == "rejected":
            raise RuntimeError(f"原分组已经 rejected，不能重复修订：{original_id}")
        old_refs = sorted(refs(original.get("questions", [])))
        replacement_refs: list[tuple[str, str]] = []
        for item in new_clusters:
            if not isinstance(item, dict):
                raise RuntimeError(f"新分组必须为对象：{original_id}")
            cluster_id = str(item.get("cluster_id") or "")
            if not cluster_id or cluster_id in index or cluster_id in new_ids:
                raise RuntimeError(f"新分组 ID 冲突或为空：{cluster_id}")
            if item.get("status") != "pending":
                raise RuntimeError(f"新分组必须保持 pending：{cluster_id}")
            replacement_refs.extend(refs(item.get("questions", [])))
            new_ids.add(cluster_id)
        expected_refs = sorted(set(old_refs) - set(unassigned))
        if sorted(replacement_refs) != expected_refs or len(replacement_refs) != len(set(replacement_refs)):
            missing = sorted(set(old_refs) - set(replacement_refs))
            extra = sorted(set(replacement_refs) - set(old_refs))
            raise RuntimeError(f"{original_id} 没有完整且不重复分区：missing={missing}, extra={extra}")
        if len(unassigned) != len(set(unassigned)) or any(ref not in old_refs for ref in unassigned):
            raise RuntimeError(f"{original_id} 的 unassigned 必须是原分组引用的无重复子集")
        before = deepcopy(original)
        original["status"] = "rejected"
        original["reviewed_at"] = changed_at
        original["superseded_by"] = [str(item["cluster_id"]) for item in new_clusters]
        original["revision_reason"] = reason
        clusters.extend(deepcopy(new_clusters))
        audit_rows.append({
            "schema_version": 1,
            "cluster_id": original_id,
            "previous_status": before.get("status"),
            "status": "rejected",
            "reason": reason,
            "changed_at": changed_at,
            "split_into": [str(item["cluster_id"]) for item in new_clusters],
            "partition_before": old_refs,
            "partition_after": replacement_refs,
            "unassigned": unassigned,
        })

    validate_staged(root, updated)
    write_json(path, updated)
    os.umask(0o077)
    append_private_text(
        root / "profile" / "question-cluster-revisions.jsonl",
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in audit_rows),
    )
    return {
        "status": "revised",
        "revised_clusters": len(audit_rows),
        "new_pending_clusters": len(new_ids),
        "new_cluster_ids": sorted(new_ids),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="按完整引用分区修订混合面试题聚类")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--spec", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(revise(args.root.expanduser().resolve(), args.spec.expanduser().resolve()), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False))
        raise SystemExit(1)
