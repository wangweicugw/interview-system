#!/usr/bin/env python3
"""Apply offline review-center actions with stale-state checks and append-only logs."""

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
from check_standard_answers import validate_standard_answers
from common import append_private_text, now_iso, write_json
from profile_store import append_event, content_hash, latest_events, load_revisions, rebuild_profile
from record_practice_attempt import _question, record_attempt


STATUSES = {"pending", "confirmed", "rejected"}


def _load(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RuntimeError(f"操作文件无法读取：{error}") from error
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise RuntimeError("操作文件 schema_version 必须为 1")
    actions = payload.get("actions")
    if not isinstance(actions, list) or not actions:
        raise RuntimeError("操作文件 actions 必须是非空数组")
    if len(actions) > 500:
        raise RuntimeError("单次最多应用 500 项操作")
    return payload


def _index(items: list[dict[str, Any]], field: str) -> dict[str, dict[str, Any]]:
    return {str(item.get(field)): item for item in items if isinstance(item, dict) and item.get(field)}


def _validate_status_action(action: dict[str, Any], current: dict[str, Any], label: str) -> None:
    status = str(action.get("status") or "")
    reason = str(action.get("reason") or "").strip()
    expected = str(action.get("expected_status") or "")
    if status not in STATUSES:
        raise RuntimeError(f"{label} 的状态无效：{status or '空'}")
    if not reason:
        raise RuntimeError(f"{label} 缺少修订原因")
    if expected and str(current.get("status") or "") != expected:
        raise RuntimeError(
            f"{label} 已变化：操作基于 {expected}，当前为 {current.get('status')}；请刷新页面后重试"
        )


def _require_text(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise RuntimeError(f"{label}不能为空")
    return text


def _require_string_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise RuntimeError(f"{label}必须为非空字符串数组")
    return [item.strip() for item in value]


def _require_object_list(value: Any, fields: tuple[str, ...], label: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise RuntimeError(f"{label}必须为数组")
    result: list[dict[str, Any]] = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            raise RuntimeError(f"{label}[{index}]必须为对象")
        copied = deepcopy(item)
        for field in fields:
            copied[field] = _require_text(copied.get(field), f"{label}[{index}].{field}")
        result.append(copied)
    return result


def _validate_staged(root: Path, answers: dict[str, Any], clusters: dict[str, Any]) -> None:
    """Validate both curated files before either live file is replaced."""
    with tempfile.TemporaryDirectory(prefix="interview-review-") as temp_name:
        staging = Path(temp_name)
        (staging / "profile").mkdir()
        os.symlink(root / "interviews", staging / "interviews", target_is_directory=True)
        write_json(staging / "profile" / "standard-answers.json", answers)
        write_json(staging / "profile" / "question-clusters.json", clusters)
        issues = validate_standard_answers(staging) + validate_question_clusters(staging)
        if issues:
            raise RuntimeError("确认操作校验失败：" + "；".join(issues))


def apply_actions(root: Path, action_file: Path) -> dict[str, Any]:
    require_store_compatible(root)
    payload = _load(action_file)
    answers_path = root / "profile" / "standard-answers.json"
    clusters_path = root / "profile" / "question-clusters.json"
    answers_payload = json.loads(answers_path.read_text(encoding="utf-8")) if answers_path.is_file() else {"schema_version": 1, "answers": []}
    clusters_payload = json.loads(clusters_path.read_text(encoding="utf-8")) if clusters_path.is_file() else {"schema_version": 1, "clusters": []}
    answers_copy = deepcopy(answers_payload)
    clusters_copy = deepcopy(clusters_payload)
    answers = _index(answers_copy.get("answers", []), "answer_id")
    clusters = _index(clusters_copy.get("clusters", []), "cluster_id")
    answer_originals = deepcopy(answers)
    cluster_originals = deepcopy(clusters)
    profile_latest = latest_events(load_revisions(root))
    normalized: list[dict[str, Any]] = []
    action_ids: set[tuple[str, str]] = set()

    for index, raw in enumerate(payload["actions"], start=1):
        if not isinstance(raw, dict):
            raise RuntimeError(f"actions[{index}] 必须为对象")
        action = deepcopy(raw)
        kind = str(action.get("kind") or "")
        identity = (kind, str(action.get("id") or action.get("case_id") or "") + ":" + str(action.get("question_id") or ""))
        if identity in action_ids:
            raise RuntimeError(f"actions[{index}] 与前面的操作重复：{kind}/{identity[1]}")
        action_ids.add(identity)
        if kind == "standard_answer":
            item_id = str(action.get("id") or "")
            target = answers.get(item_id)
            if target is None:
                raise RuntimeError(f"找不到成熟答案：{item_id}")
            _validate_status_action(action, target, f"成熟答案 {item_id}")
            patch = action.get("patch", {})
            if patch and not isinstance(patch, dict):
                raise RuntimeError(f"成熟答案 {item_id} 的 patch 必须为对象")
            for field in ("title", "basis"):
                if field in patch:
                    target[field] = _require_text(patch[field], f"成熟答案 {item_id} 的 {field}")
            versions = target.setdefault("versions", {})
            for field in ("short_30s", "standard_2m"):
                if field in patch:
                    value = str(patch[field]).strip()
                    if not value:
                        raise RuntimeError(f"成熟答案 {item_id} 的 {field} 不能为空")
                    versions[field] = value
            if "followups" in patch:
                versions["followups"] = _require_object_list(
                    patch["followups"], ("question", "answer"),
                    f"成熟答案 {item_id} 的 followups",
                )
            if "limitations" in patch:
                target["limitations"] = _require_string_list(
                    patch["limitations"], f"成熟答案 {item_id} 的 limitations"
                )
            if "sources" in patch:
                target["sources"] = _require_object_list(
                    patch["sources"], ("title", "url"), f"成熟答案 {item_id} 的 sources"
                )
            if "source_questions" in patch:
                target["source_questions"] = _require_object_list(
                    patch["source_questions"], ("case_id", "question_id"),
                    f"成熟答案 {item_id} 的 source_questions",
                )
            normalized.append(action)
        elif kind == "question_cluster":
            item_id = str(action.get("id") or "")
            target = clusters.get(item_id)
            if target is None:
                raise RuntimeError(f"找不到高频问法：{item_id}")
            _validate_status_action(action, target, f"高频问法 {item_id}")
            patch = action.get("patch", {})
            if patch and not isinstance(patch, dict):
                raise RuntimeError(f"高频问法 {item_id} 的 patch 必须为对象")
            if "label" in patch:
                target["label"] = _require_text(patch["label"], f"高频问法 {item_id} 的 label")
            if "note" in patch:
                target["note"] = str(patch["note"] or "").strip()
            if "questions" in patch:
                target["questions"] = _require_object_list(
                    patch["questions"], ("case_id", "question_id"),
                    f"高频问法 {item_id} 的 questions",
                )
            normalized.append(action)
        elif kind == "profile_event":
            item_id = str(action.get("id") or "")
            target = profile_latest.get(item_id)
            if target is None:
                raise RuntimeError(f"找不到长期档案事件：{item_id}")
            _validate_status_action(action, target, f"长期档案事件 {item_id}")
            normalized.append(action)
        elif kind == "practice_attempt":
            case_id = str(action.get("case_id") or "")
            question_id = str(action.get("question_id") or "")
            answer = str(action.get("answer") or "").strip()
            duration = action.get("duration_seconds")
            rating = action.get("self_rating")
            if _question(root, case_id, question_id) is None:
                raise RuntimeError(f"找不到真实问题：{case_id}/{question_id}")
            if not answer or len(answer) > 100_000:
                raise RuntimeError(f"{case_id}/{question_id} 的练习回答为空或过长")
            if type(duration) is not int or not 1 <= duration <= 3600:
                raise RuntimeError(f"{case_id}/{question_id} 的练习时长无效")
            if type(rating) is not int or not 1 <= rating <= 5:
                raise RuntimeError(f"{case_id}/{question_id} 的自评分无效")
            normalized.append(action)
        else:
            raise RuntimeError(f"actions[{index}] 的 kind 不受支持：{kind or '空'}")

    changed_at = now_iso()
    answer_revisions: list[dict[str, Any]] = []
    cluster_revisions: list[dict[str, Any]] = []
    profile_revisions: list[dict[str, Any]] = []
    practice_events: list[dict[str, Any]] = []
    for action in normalized:
        kind = action["kind"]
        if kind == "standard_answer":
            target = answers[str(action["id"])]
            before = answer_originals[str(action["id"])]
            previous = str(before.get("status"))
            target["status"] = action["status"]
            target["reviewed_at"] = changed_at
            answer_revisions.append({
                "schema_version": 1,
                "answer_id": action["id"],
                "previous_status": previous,
                "status": action["status"],
                "reason": str(action["reason"]).strip(),
                "changed_at": changed_at,
                "edited_fields": sorted((action.get("patch") or {}).keys()),
                "before": before,
                "after": deepcopy(target),
            })
        elif kind == "question_cluster":
            target = clusters[str(action["id"])]
            before = cluster_originals[str(action["id"])]
            previous = str(before.get("status"))
            target["status"] = action["status"]
            target["reviewed_at"] = changed_at
            cluster_revisions.append({
                "schema_version": 1,
                "cluster_id": action["id"],
                "previous_status": previous,
                "status": action["status"],
                "reason": str(action["reason"]).strip(),
                "changed_at": changed_at,
                "edited_fields": sorted((action.get("patch") or {}).keys()),
                "before": before,
                "after": deepcopy(target),
            })
        elif kind == "profile_event":
            previous = profile_latest[str(action["id"])]
            event = {
                **previous,
                "revision": int(previous.get("revision", 0)) + 1,
                "recorded_at": changed_at,
                "status": action["status"],
                "revision_reason": str(action["reason"]).strip(),
            }
            event["content_hash"] = content_hash(event)
            profile_revisions.append(event)
        elif kind == "practice_attempt":
            practice_events.append(action)

    if answer_revisions or cluster_revisions:
        _validate_staged(root, answers_copy, clusters_copy)
    if answer_revisions:
        write_json(answers_path, answers_copy)
    if cluster_revisions:
        write_json(clusters_path, clusters_copy)

    os.umask(0o077)
    for relative, revisions in (
        ("profile/standard-answer-revisions.jsonl", answer_revisions),
        ("profile/question-cluster-revisions.jsonl", cluster_revisions),
    ):
        if revisions:
            path = root / relative
            append_private_text(
                path,
                "".join(json.dumps(revision, ensure_ascii=False) + "\n" for revision in revisions),
            )
    for event in profile_revisions:
        append_event(root, event)
    if profile_revisions:
        rebuild_profile(root)
    for action in practice_events:
        record_attempt(
            root,
            case_id=str(action["case_id"]),
            question_id=str(action["question_id"]),
            answer=str(action["answer"]),
            duration_seconds=int(action["duration_seconds"]),
            self_rating=int(action["self_rating"]),
            note=str(action.get("note") or ""),
        )
    return {
        "status": "applied",
        "actions": len(normalized),
        "standard_answers": len(answer_revisions),
        "question_clusters": len(cluster_revisions),
        "profile_events": len(profile_revisions),
        "practice_attempts": len(practice_events),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="应用题库页面导出的确认、修订和练习操作")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--actions", required=True, type=Path)
    args = parser.parse_args()
    result = apply_actions(args.root.expanduser().resolve(), args.actions.expanduser().resolve())
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False))
        raise SystemExit(1)
