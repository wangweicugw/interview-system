#!/usr/bin/env python3
"""Suggest or safely append high-confidence new questions to reviewed semantic clusters."""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from check_data_compatibility import require_store_compatible
from check_question_clusters import validate_question_clusters
from common import append_private_text, now_iso, sha256_text, write_json


def _normalize(value: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", value.lower())


def _grams(value: str) -> set[str]:
    text = _normalize(value)
    if len(text) < 2:
        return {text} if text else set()
    return {text[index:index + 2] for index in range(len(text) - 1)}


def _similarity(left: str, right: str) -> float:
    a, b = _normalize(left), _normalize(right)
    if not a or not b:
        return 0.0
    seq = SequenceMatcher(None, a, b).ratio()
    ga, gb = _grams(a), _grams(b)
    union = ga | gb
    jaccard = len(ga & gb) / len(union) if union else 0.0
    return round(seq * 0.55 + jaccard * 0.45, 4)


def _catalog(root: Path) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for path in sorted((root / "interviews").glob("*/qa-tree.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        case_id = str(payload.get("case_id") or "")
        for item in payload.get("questions", []):
            if not isinstance(item, dict) or not item.get("question_id"):
                continue
            result.append({
                "case_id": case_id,
                "question_id": str(item["question_id"]),
                "question": str(item.get("question") or ""),
            })
    return result


def refresh(root: Path, *, apply_high_confidence: bool) -> dict[str, Any]:
    require_store_compatible(root)
    path = root / "profile" / "question-clusters.json"
    if not path.is_file():
        raise RuntimeError("缺少 profile/question-clusters.json；首次语义归并仍需人工或 AI 建立")
    payload = json.loads(path.read_text(encoding="utf-8"))
    clusters = [item for item in payload.get("clusters", []) if isinstance(item, dict)]
    active = [item for item in clusters if item.get("status") != "rejected"]
    catalog = _catalog(root)
    by_key = {(item["case_id"], item["question_id"]): item for item in catalog}
    assigned = {
        (str(ref.get("case_id") or ""), str(ref.get("question_id") or ""))
        for cluster in active
        for ref in cluster.get("questions", [])
        if isinstance(ref, dict)
    }
    cluster_examples: dict[str, list[str]] = {}
    for cluster in active:
        values = [str(cluster.get("label") or ""), str(cluster.get("note") or "")]
        for ref in cluster.get("questions", []):
            if isinstance(ref, dict):
                item = by_key.get((str(ref.get("case_id") or ""), str(ref.get("question_id") or "")))
                if item:
                    values.append(item["question"])
        cluster_examples[str(cluster.get("cluster_id") or "")] = [value for value in values if value]

    candidates: list[dict[str, Any]] = []
    additions: dict[str, list[dict[str, str]]] = {}
    for item in catalog:
        key = (item["case_id"], item["question_id"])
        if key in assigned:
            continue
        ranked = []
        for cluster in active:
            cluster_id = str(cluster.get("cluster_id") or "")
            score = max((_similarity(item["question"], value) for value in cluster_examples.get(cluster_id, [])), default=0.0)
            ranked.append((score, cluster_id, str(cluster.get("label") or "")))
        ranked.sort(reverse=True)
        best = ranked[0] if ranked else (0.0, "", "")
        second = ranked[1][0] if len(ranked) > 1 else 0.0
        confidence = "high" if best[0] >= 0.72 and best[0] - second >= 0.12 else "medium" if best[0] >= 0.48 else "low"
        candidate = {
            **item,
            "suggested_cluster_id": best[1] or None,
            "suggested_cluster_label": best[2] or None,
            "score": best[0],
            "margin": round(best[0] - second, 4),
            "confidence": confidence,
            "requires_review": True,
        }
        candidates.append(candidate)
        if apply_high_confidence and confidence == "high" and best[1]:
            additions.setdefault(best[1], []).append({"case_id": item["case_id"], "question_id": item["question_id"]})

    revisions = []
    if additions:
        changed_at = now_iso()
        by_id = {str(item.get("cluster_id") or ""): item for item in clusters}
        for cluster_id, refs in additions.items():
            cluster = by_id[cluster_id]
            previous_status = str(cluster.get("status") or "pending")
            cluster.setdefault("questions", []).extend(refs)
            cluster["status"] = "pending"
            cluster["updated_at"] = changed_at
            revisions.append({
                "schema_version": 1,
                "cluster_id": cluster_id,
                "changed_at": changed_at,
                "action": "append_high_confidence_candidates",
                "previous_status": previous_status,
                "status": "pending",
                "added_questions": refs,
                "reason": "确定性相似度达到高阈值；仍降级为待确认，避免自动把语义判断写成事实。",
            })
        with tempfile.TemporaryDirectory(prefix="interview-cluster-refresh-") as temp_name:
            staging = Path(temp_name)
            (staging / "profile").mkdir()
            os.symlink(root / "interviews", staging / "interviews", target_is_directory=True)
            write_json(staging / "profile" / "question-clusters.json", payload)
            issues = validate_question_clusters(staging)
            if issues:
                raise RuntimeError("自动追加后聚类校验失败：" + "；".join(issues))
        write_json(path, payload)
        os.umask(0o077)
        log = root / "profile" / "question-cluster-revisions.jsonl"
        append_private_text(
            log,
            "".join(json.dumps(revision, ensure_ascii=False) + "\n" for revision in revisions),
        )

    source_digest = sha256_text(json.dumps(catalog, ensure_ascii=False, sort_keys=True))
    report = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "source_digest": source_digest,
        "question_count": len(catalog),
        "assigned_before": len(assigned),
        "unassigned_before": len(candidates),
        "applied": sum(len(values) for values in additions.values()),
        "candidates": candidates,
    }
    report_path = root / "profile" / "question-cluster-candidates.json"
    write_json(report_path, report)
    return {
        "status": "updated" if additions else "suggested",
        "report": str(report_path),
        "questions": len(catalog),
        "assigned": len(assigned) + sum(len(values) for values in additions.values()),
        "candidates": len(candidates),
        "high_confidence": sum(item["confidence"] == "high" for item in candidates),
        "applied": sum(len(values) for values in additions.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="为新增真实问题生成语义归并候选；默认不修改聚类")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--apply-high-confidence", action="store_true")
    args = parser.parse_args()
    result = refresh(args.root.expanduser().resolve(), apply_high_confidence=args.apply_high_confidence)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False))
        raise SystemExit(1)
