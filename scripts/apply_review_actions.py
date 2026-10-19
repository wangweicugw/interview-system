#!/usr/bin/env python3
"""Apply offline review-center actions with stale-state checks and append-only logs."""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any

from check_data_compatibility import require_store_compatible
from check_question_clusters import validate_question_clusters
from check_question_preferences import validate_question_preferences
from check_standard_answers import TOPICS, validate_standard_answers
from common import append_private_text, now_iso, write_json
from profile_store import append_event, content_hash, latest_events, load_revisions, rebuild_profile
from record_practice_attempt import _question, record_attempt


STATUSES = {"pending", "confirmed", "rejected"}
PREFERENCES = {"normal", "low_priority", "ignored"}
CLUSTER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,80}$")


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


def _preference_key(item: dict[str, Any]) -> tuple[str, str, str]:
    target_type = str(item.get("target_type") or "")
    if target_type == "question":
        return target_type, str(item.get("case_id") or ""), str(item.get("question_id") or "")
    return target_type, "", str(item.get("target_id") or item.get("id") or "")


def _question_ref_key(ref: dict[str, Any]) -> tuple[str, str]:
    return str(ref.get("case_id") or ""), str(ref.get("question_id") or "")


def _active_cluster_question_refs(clusters: dict[str, dict[str, Any]]) -> set[tuple[str, str]]:
    result: set[tuple[str, str]] = set()
    for cluster in clusters.values():
        if str(cluster.get("status") or "") not in {"pending", "confirmed"}:
            continue
        for ref in cluster.get("questions", []):
            if isinstance(ref, dict):
                result.add(_question_ref_key(ref))
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
    preference_issues = validate_question_preferences(root)
    if preference_issues:
        raise RuntimeError("已有题目偏好状态校验失败：" + "；".join(preference_issues))
    payload = _load(action_file)
    answers_path = root / "profile" / "standard-answers.json"
    clusters_path = root / "profile" / "question-clusters.json"
    preferences_path = root / "profile" / "question-preferences.json"
    answers_payload = json.loads(answers_path.read_text(encoding="utf-8")) if answers_path.is_file() else {"schema_version": 1, "answers": []}
    clusters_payload = json.loads(clusters_path.read_text(encoding="utf-8")) if clusters_path.is_file() else {"schema_version": 1, "clusters": []}
    preferences_payload = json.loads(preferences_path.read_text(encoding="utf-8")) if preferences_path.is_file() else {"schema_version": 1, "preferences": []}
    if not isinstance(preferences_payload, dict) or preferences_payload.get("schema_version") != 1 or not isinstance(preferences_payload.get("preferences"), list):
        raise RuntimeError("question-preferences.json 格式无效")
    answers_copy = deepcopy(answers_payload)
    clusters_copy = deepcopy(clusters_payload)
    preferences_copy = deepcopy(preferences_payload)
    answers = _index(answers_copy.get("answers", []), "answer_id")
    clusters = _index(clusters_copy.get("clusters", []), "cluster_id")
    answer_originals = deepcopy(answers)
    cluster_originals = deepcopy(clusters)
    preference_items = preferences_copy["preferences"]
    preferences = {_preference_key(item): item for item in preference_items if isinstance(item, dict)}
    preference_originals = deepcopy(preferences)
    profile_latest = latest_events(load_revisions(root))
    normalized: list[dict[str, Any]] = []
    action_ids: set[tuple[str, str]] = set()
    active_cluster_refs = _active_cluster_question_refs(clusters)
    created_cluster_ids: set[str] = set()
    created_cluster_refs: set[tuple[str, str]] = set()
    superseded_cluster_ids: set[str] = set()

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
        elif kind == "question_cluster_create":
            item_id = _require_text(action.get("id"), f"actions[{index}].id")
            if not CLUSTER_ID_RE.fullmatch(item_id):
                raise RuntimeError(f"actions[{index}] 的新题组 ID 格式无效")
            if item_id in clusters or item_id in created_cluster_ids:
                raise RuntimeError(f"新题组 ID 已存在：{item_id}")
            if str(action.get("status") or "pending") != "pending":
                raise RuntimeError("手动合并的新题组必须保持 pending，不能直接确认")
            label = _require_text(action.get("label"), f"新题组 {item_id} 的 label")
            reason = _require_text(action.get("reason"), f"新题组 {item_id} 的 reason")
            topic = str(action.get("topic") or "other")
            if topic not in TOPICS:
                raise RuntimeError(f"新题组 {item_id} 的 topic 无效：{topic}")
            questions = _require_object_list(
                action.get("questions"), ("case_id", "question_id"),
                f"新题组 {item_id} 的 questions",
            )
            if len(questions) < 2:
                raise RuntimeError(f"新题组 {item_id} 至少需要选择两道题")
            ref_keys = [_question_ref_key(ref) for ref in questions]
            if len(ref_keys) != len(set(ref_keys)):
                raise RuntimeError(f"新题组 {item_id} 的题目引用重复")
            supersedes = action.get("supersedes", [])
            if not isinstance(supersedes, list) or any(not isinstance(item, str) or not item.strip() for item in supersedes):
                raise RuntimeError(f"新题组 {item_id} 的 supersedes 必须为字符串数组")
            supersedes = [item.strip() for item in supersedes]
            if len(supersedes) != len(set(supersedes)):
                raise RuntimeError(f"新题组 {item_id} 的 supersedes 重复")
            expected_superseded = action.get("expected_superseded_statuses", {})
            if not isinstance(expected_superseded, dict):
                raise RuntimeError(f"新题组 {item_id} 的 expected_superseded_statuses 必须为对象")
            selected_refs = set(ref_keys)
            for superseded_id in supersedes:
                if superseded_id in superseded_cluster_ids:
                    raise RuntimeError(f"新题组 {item_id} 重复合并旧题组：{superseded_id}")
                if superseded_id in created_cluster_ids:
                    raise RuntimeError(f"新题组 {item_id} 不能合并同一批次新建题组：{superseded_id}")
                old = clusters.get(superseded_id)
                if old is None:
                    raise RuntimeError(f"新题组 {item_id} 要合并的旧题组不存在：{superseded_id}")
                old_status = str(old.get("status") or "")
                if old_status not in {"pending", "confirmed"}:
                    raise RuntimeError(f"新题组 {item_id} 只能合并待确认/已确认题组：{superseded_id}")
                expected_old = str(expected_superseded.get(superseded_id) or "")
                if expected_old and expected_old != old_status:
                    raise RuntimeError(f"旧题组状态已变化：{superseded_id}；请刷新页面后重试")
                old_refs = {
                    _question_ref_key(ref) for ref in old.get("questions", [])
                    if isinstance(ref, dict)
                }
                if not old_refs.issubset(selected_refs):
                    raise RuntimeError(f"新题组 {item_id} 只选择了旧题组 {superseded_id} 的部分成员")
            for case_id, question_id in ref_keys:
                if _question(root, case_id, question_id) is None:
                    raise RuntimeError(f"新题组 {item_id} 引用不存在：{case_id}/{question_id}")
                if (case_id, question_id) in created_cluster_refs:
                    raise RuntimeError(f"新题组 {item_id} 与同一批次的其他新题组重叠：{case_id}/{question_id}")
                overlapping = [
                    cluster_id for cluster_id, cluster in clusters.items()
                    if str(cluster.get("status") or "") in {"pending", "confirmed"}
                    and (case_id, question_id) in {
                        _question_ref_key(ref) for ref in cluster.get("questions", [])
                        if isinstance(ref, dict)
                    }
                ]
                if any(cluster_id not in supersedes for cluster_id in overlapping):
                    raise RuntimeError(
                        f"新题组 {item_id} 与现有待确认/已确认题组重叠：{case_id}/{question_id}；请先刷新并处理原题组"
                    )
            action["label"] = label
            action["topic"] = topic
            action["status"] = "pending"
            action["questions"] = questions
            action["supersedes"] = supersedes
            action["expected_superseded_statuses"] = {
                key: str(value) for key, value in expected_superseded.items()
                if key in supersedes
            }
            active_cluster_refs.difference_update(
                ref for superseded_id in supersedes
                for ref in {
                    _question_ref_key(item) for item in clusters[superseded_id].get("questions", [])
                    if isinstance(item, dict)
                }
            )
            active_cluster_refs.update(ref_keys)
            created_cluster_ids.add(item_id)
            created_cluster_refs.update(ref_keys)
            superseded_cluster_ids.update(supersedes)
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
        elif kind == "question_preference":
            target_type = str(action.get("target_type") or "")
            preference = str(action.get("preference") or "")
            reason = str(action.get("reason") or "").strip()
            if target_type not in {"question", "cluster"}:
                raise RuntimeError(f"actions[{index}] 的题目偏好 target_type 无效")
            if preference not in PREFERENCES:
                raise RuntimeError(f"actions[{index}] 的题目偏好无效：{preference or '空'}")
            if not reason:
                raise RuntimeError(f"actions[{index}] 的题目偏好缺少修订原因")
            if target_type == "question":
                case_id = str(action.get("case_id") or "")
                question_id = str(action.get("question_id") or "")
                if _question(root, case_id, question_id) is None:
                    raise RuntimeError(f"找不到真实问题：{case_id}/{question_id}")
                key = (target_type, case_id, question_id)
            else:
                target_id = str(action.get("target_id") or action.get("id") or "")
                if target_id not in clusters:
                    raise RuntimeError(f"找不到题目分组：{target_id}")
                key = (target_type, "", target_id)
            current = preferences.get(key, {"preference": "normal"})
            expected = str(action.get("expected_preference") or "")
            if expected and str(current.get("preference") or "normal") != expected:
                raise RuntimeError(f"题目偏好已变化：操作基于 {expected}，当前为 {current.get('preference', 'normal')}；请刷新页面后重试")
            action["_preference_key"] = list(key)
            normalized.append(action)
        else:
            raise RuntimeError(f"actions[{index}] 的 kind 不受支持：{kind or '空'}")

    changed_at = now_iso()
    answer_revisions: list[dict[str, Any]] = []
    cluster_revisions: list[dict[str, Any]] = []
    profile_revisions: list[dict[str, Any]] = []
    practice_events: list[dict[str, Any]] = []
    preference_revisions: list[dict[str, Any]] = []
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
        elif kind == "question_cluster_create":
            item_id = str(action["id"])
            superseded_ids = [str(value) for value in action.get("supersedes", [])]
            for superseded_id in superseded_ids:
                old = clusters[superseded_id]
                before = cluster_originals[superseded_id]
                old["status"] = "rejected"
                old["reviewed_at"] = changed_at
                old["superseded_by"] = [item_id]
                old["revision_reason"] = str(action["reason"]).strip()
                cluster_revisions.append({
                    "schema_version": 1,
                    "cluster_id": superseded_id,
                    "previous_status": str(before.get("status") or ""),
                    "status": "rejected",
                    "reason": str(action["reason"]).strip(),
                    "changed_at": changed_at,
                    "edited_fields": ["superseded_by", "status"],
                    "before": before,
                    "after": deepcopy(old),
                })
            target = {
                "cluster_id": item_id,
                "label": action["label"],
                "topic": action["topic"],
                "status": "pending",
                "note": str(action.get("note") or "").strip(),
                "questions": deepcopy(action["questions"]),
                "created_at": changed_at,
            }
            clusters_copy.setdefault("clusters", []).append(target)
            clusters[item_id] = target
            cluster_revisions.append({
                "schema_version": 1,
                "cluster_id": item_id,
                "previous_status": None,
                "status": "pending",
                "reason": str(action["reason"]).strip(),
                "changed_at": changed_at,
                "edited_fields": ["create"],
                "before": None,
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
        elif kind == "question_preference":
            key = tuple(action["_preference_key"])
            before = preference_originals.get(key)
            item = deepcopy(before) if before else {
                "schema_version": 1,
                "target_type": action["target_type"],
                "case_id": str(action.get("case_id") or ""),
                "question_id": str(action.get("question_id") or ""),
                "target_id": str(action.get("target_id") or action.get("id") or ""),
            }
            item["preference"] = action["preference"]
            item["updated_at"] = changed_at
            item["reason"] = str(action["reason"]).strip()
            existing_index = next((i for i, value in enumerate(preference_items) if isinstance(value, dict) and _preference_key(value) == key), None)
            if existing_index is None:
                preference_items.append(item)
            else:
                preference_items[existing_index] = item
            preference_revisions.append({
                "schema_version": 1,
                "target_type": item["target_type"],
                "case_id": item.get("case_id", ""),
                "question_id": item.get("question_id", ""),
                "target_id": item.get("target_id", ""),
                "previous_preference": str((before or {}).get("preference") or "normal"),
                "preference": item["preference"],
                "reason": item["reason"],
                "changed_at": changed_at,
            })

    if answer_revisions or cluster_revisions:
        _validate_staged(root, answers_copy, clusters_copy)
    if answer_revisions:
        write_json(answers_path, answers_copy)
    if cluster_revisions:
        write_json(clusters_path, clusters_copy)
    if preference_revisions:
        write_json(preferences_path, preferences_copy)

    os.umask(0o077)
    for relative, revisions in (
        ("profile/standard-answer-revisions.jsonl", answer_revisions),
        ("profile/question-cluster-revisions.jsonl", cluster_revisions),
        ("profile/question-preference-revisions.jsonl", preference_revisions),
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
            attempt_id=str(action.get("attempt_id") or "") or None,
        )
    return {
        "status": "applied",
        "actions": len(normalized),
        "standard_answers": len(answer_revisions),
        "question_clusters": len(cluster_revisions),
        "question_preferences": len(preference_revisions),
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
