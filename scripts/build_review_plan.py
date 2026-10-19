#!/usr/bin/env python3
"""Build a deterministic practice queue and cross-interview trend snapshot."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from check_data_compatibility import require_store_compatible
from common import now_iso, write_json, write_private_text
from render_interview_html import TOPIC_LABELS, question_topic


def _read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _attempts(root: Path) -> dict[tuple[str, str], list[dict[str, Any]]]:
    result: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    path = root / "practice" / "attempts.jsonl"
    if not path.is_file():
        return result
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            result[(str(item.get("case_id") or ""), str(item.get("question_id") or ""))].append(item)
    return result


def _parse_time(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _preference_key(item: dict[str, Any]) -> tuple[str, str, str]:
    target_type = str(item.get("target_type") or "")
    if target_type == "question":
        return target_type, str(item.get("case_id") or ""), str(item.get("question_id") or "")
    return target_type, "", str(item.get("target_id") or "")


def _goal_text_key(value: str) -> str:
    return "".join(str(value or "").lower().split()).replace("，", "").replace("。", "")


def _confirmed_cluster_map(payload: Any) -> dict[tuple[str, str], dict[str, str]]:
    result: dict[tuple[str, str], dict[str, str]] = {}
    clusters = payload.get("clusters", []) if isinstance(payload, dict) else []
    for cluster in clusters:
        if not isinstance(cluster, dict) or cluster.get("status") != "confirmed":
            continue
        cluster_id = str(cluster.get("cluster_id") or "")
        label = str(cluster.get("label") or cluster_id)
        for ref in cluster.get("questions", []):
            if isinstance(ref, dict):
                result[(str(ref.get("case_id") or ""), str(ref.get("question_id") or ""))] = {
                    "cluster_id": cluster_id,
                    "label": label,
                }
    return result


def build_training_goals(
    rows: list[dict[str, Any]],
    *,
    cluster_payload: Any,
    tag_map: dict[tuple[str, str], dict[str, Any]],
    limit: int = 3,
) -> list[dict[str, Any]]:
    """Collapse the per-question queue into a small, traceable practice inbox."""
    confirmed = _confirmed_cluster_map(cluster_payload)
    groups: dict[str, dict[str, Any]] = {}
    for row in rows:
        ref = confirmed.get((str(row.get("case_id") or ""), str(row.get("question_id") or "")))
        text = str(row.get("question") or "")
        topic = str(row.get("topic") or "other")
        if ref:
            key = f"confirmed:{ref['cluster_id']}"
            title = ref["label"]
            evidence_status = "已确认跨场归并"
        elif topic == "self-intro" or any(
            marker in _goal_text_key(text) for marker in ("自我介绍", "介绍自己", "个人经历", "个人背景", "近期工作", "近年负责", "概述近年")
        ):
            key = "suggested:self-intro"
            title = "建议主题：自我介绍与近年职责"
            evidence_status = "建议主题（不代表已确认同题）"
        else:
            tags = tag_map.get((str(row.get("case_id") or ""), str(row.get("question_id") or "")), {})
            intents = tags.get("intent", []) if isinstance(tags, dict) else []
            intent = str(intents[0]) if isinstance(intents, list) and intents else ""
            key = f"suggested:{topic}:{intent or _goal_text_key(text)[:28]}"
            title = f"建议主题：{row.get('topic_label') or topic}"
            if intent:
                title += f" · {intent}"
            evidence_status = "建议主题（不代表已确认同题）"
        group = groups.setdefault(key, {
            "goal_id": key,
            "title": title,
            "evidence_status": evidence_status,
            "priority_score": float("-inf"),
            "rows": [],
        })
        group["rows"].append(row)
        group["priority_score"] = max(group["priority_score"], float(row.get("priority_score") or 0))

    ordered = sorted(groups.values(), key=lambda item: (-item["priority_score"], item["goal_id"]))
    goals = []
    for group in ordered[:limit]:
        members = sorted(group["rows"], key=lambda item: (-float(item.get("priority_score") or 0), str(item.get("question_id") or "")))
        representative = members[0]
        sources = [
            {
                "case_id": str(item.get("case_id") or ""),
                "question_id": str(item.get("question_id") or ""),
                "company": str(item.get("company") or ""),
                "interview_date": str(item.get("interview_date") or ""),
                "question": str(item.get("question") or ""),
            }
            for item in members
        ]
        reasons = [str(value) for value in representative.get("priority_reasons", [])]
        reasons = [value for value in reasons if not value.startswith("跨 ")]
        if group["evidence_status"].startswith("建议"):
            reason_text = f"建议主题覆盖 {len(members)} 条原题"
        else:
            reason_text = f"已确认题组覆盖 {len(members)} 条原题"
        if reasons:
            reason_text += "；" + "、".join(reasons)
        title_key = _goal_text_key(str(group["title"]))
        if "自我介绍" in title_key or "近年职责" in title_key:
            deliverable = "写出一版岗位定制的 60–90 秒开场：职责、一个代表项目和一项可核验证据。"
            acceptance = "脱稿完成开场，并能在追问时补充个人边界、结果指标和证据来源。"
        elif any(marker in title_key for marker in ("数据库中间件", "vtdriver", "vitess", "dongdal")):
            deliverable = "画出 VtDriver、Vitess 网关、DongDAL 的职责边界和请求路径，补一段迁移取舍说明。"
            acceptance = "能说清三者分别解决什么问题、个人负责哪一段，以及迁移收益和代价；未确认内容保留为待核对。"
        elif any(marker in title_key for marker in ("跨分片", "分库分表")):
            deliverable = "用一道代表题讲清跨分片 Join 的执行路径，并写下大表、内存、失败或降级边界。"
            acceptance = "回答覆盖拆分、结果合并、资源边界和异常处置；不能确认的实现细节明确标为待核对。"
        else:
            deliverable = "完成一次围绕代表题的脱稿回答，并记录 1 条缺口和对应证据。"
            acceptance = "不看原稿完成回答，能说明个人职责、关键边界和可核验依据。"
        goals.append({
            "goal_id": group["goal_id"],
            "title": group["title"],
            "evidence_status": group["evidence_status"],
            "priority_score": round(group["priority_score"], 2),
            "question_count": len(members),
            "why_priority": reason_text or "进入当前复习队列",
            "deliverable": deliverable,
            "acceptance": acceptance,
            "representative": {
                "case_id": str(representative.get("case_id") or ""),
                "question_id": str(representative.get("question_id") or ""),
                "question": str(representative.get("question") or ""),
            },
            "sources": sources,
        })
    return goals


def build(root: Path, *, limit: int) -> dict[str, Any]:
    require_store_compatible(root)
    schedule = _read(root / "config.json", {}).get("review_schedule_days", [1, 7, 30])
    if not isinstance(schedule, list) or not schedule or any(type(value) is not int or value < 1 for value in schedule):
        schedule = [1, 7, 30]
    cluster_payload = _read(root / "profile" / "question-clusters.json", {})
    preference_payload = _read(root / "profile" / "question-preferences.json", {"preferences": []})
    tag_payload = _read(root / "profile" / "question-tags.json", {"questions": []})
    tag_map = {
        (str(item.get("case_id") or ""), str(item.get("question_id") or "")): item.get("tags", {})
        for item in tag_payload.get("questions", [])
        if isinstance(item, dict)
    }
    preferences = {
        _preference_key(item): str(item.get("preference") or "normal")
        for item in preference_payload.get("preferences", [])
        if isinstance(item, dict)
    }
    cluster_freq: dict[tuple[str, str], int] = {}
    cluster_pref_by_question: dict[tuple[str, str], list[str]] = defaultdict(list)
    for cluster in cluster_payload.get("clusters", []):
        if not isinstance(cluster, dict) or cluster.get("status") == "rejected":
            continue
        cluster_id = str(cluster.get("cluster_id") or "")
        cluster_preference = preferences.get(("cluster", "", cluster_id), "normal")
        refs = [ref for ref in cluster.get("questions", []) if isinstance(ref, dict)]
        case_count = len({str(ref.get("case_id") or "") for ref in refs})
        for ref in refs:
            key = (str(ref.get("case_id") or ""), str(ref.get("question_id") or ""))
            cluster_freq[key] = case_count
            cluster_pref_by_question[key].append(cluster_preference)
    attempts = _attempts(root)
    now = datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    trend_by_date: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for qa_path in sorted((root / "interviews").glob("*/qa-tree.json")):
        qa = _read(qa_path, {})
        manifest = _read(qa_path.parent / "manifest.json", {})
        scores_payload = _read(qa_path.parent / "scores.json", {})
        score_map = {
            str(item.get("question_id") or ""): item.get("total")
            for item in scores_payload.get("questions", []) if isinstance(item, dict)
        }
        matrix = _read(qa_path.parent / "jd-matrix.json", {})
        jd_high = {
            str(qid)
            for requirement in matrix.get("requirements", []) if isinstance(requirement, dict) and requirement.get("next_priority") == "high"
            for qid in requirement.get("evidence_questions", [])
        }
        case_id = str(qa.get("case_id") or "")
        interview_date = str(manifest.get("interview", {}).get("date") or manifest.get("date") or "")
        company = str(manifest.get("interview", {}).get("company") or manifest.get("company") or qa_path.parent.name)
        answer_map = {
            str(item.get("question_id") or ""): bool(item.get("reference_answer"))
            for item in _read(qa_path.parent / "practice-answers.json", {}).get("questions", [])
            if isinstance(item, dict)
        }
        for question in qa.get("questions", []):
            if not isinstance(question, dict):
                continue
            qid = str(question.get("question_id") or "")
            key = (case_id, qid)
            direct_preference = preferences.get(("question", case_id, qid))
            preference = direct_preference if direct_preference is not None else "normal"
            cluster_preferences = cluster_pref_by_question.get(key, [])
            if preference == "ignored" or (direct_preference is None and "ignored" in cluster_preferences):
                continue
            topic = question_topic(question)
            score = score_map.get(qid)
            history = sorted(attempts.get(key, []), key=lambda item: str(item.get("recorded_at") or ""))
            ratings = [int(item["self_rating"]) for item in history if type(item.get("self_rating")) is int]
            latest_at = _parse_time(str(history[-1].get("recorded_at") or "")) if history else None
            interval = schedule[min(max(len(history) - 1, 0), len(schedule) - 1)]
            days_since = (now - latest_at).days if latest_at else None
            due = not history or (days_since is not None and days_since >= interval)
            if not history:
                practice_status = "未练习"
            elif ratings[-1] <= 2:
                practice_status = "薄弱"
            elif len(ratings) >= 2 and min(ratings[-2:]) >= 4 and not due:
                practice_status = "暂时稳定"
            elif due:
                practice_status = "到期复习"
            else:
                practice_status = "学习中"
            reasons = []
            priority = cluster_freq.get(key, 0) * 5
            if preference == "low_priority":
                priority -= 30
                reasons.append("用户降低优先级")
            if direct_preference is None and "low_priority" in cluster_preferences:
                priority -= 30
                reasons.append("所属题组降低优先级")
            if cluster_freq.get(key, 0) >= 2:
                reasons.append(f"跨 {cluster_freq[key]} 场重复")
            if isinstance(score, (int, float)):
                priority += max(0, 20 - float(score)) * 2
                if score <= 10:
                    reasons.append(f"本场得分 {score}/20")
                trend_by_date[interview_date][topic].append(float(score))
            if qid in jd_high:
                priority += 8
                reasons.append("目标 JD 高优先级")
            if due:
                priority += 7
                reasons.append("未练习或已到复习日")
            if ratings and ratings[-1] <= 2:
                priority += 8
                reasons.append(f"最近自评 {ratings[-1]}/5")
            if not answer_map.get(qid):
                priority += 2
                reasons.append("尚无逐题参考答案")
            rows.append({
                "case_id": case_id,
                "question_id": qid,
                "question": str(question.get("question") or ""),
                "company": company,
                "interview_date": interview_date,
                "topic": topic,
                "topic_label": TOPIC_LABELS.get(topic, topic),
                "cluster_case_count": cluster_freq.get(key, 0),
                "preference": preference,
                "score": score,
                "attempt_count": len(history),
                "latest_self_rating": ratings[-1] if ratings else None,
                "practice_status": practice_status,
                "due": due,
                "priority_score": round(priority, 2),
                "priority_reasons": reasons or ["普通历史题"],
            })
    rows.sort(key=lambda item: (-item["priority_score"], item["interview_date"], item["case_id"], item["question_id"]))
    trends = []
    for date, topics in sorted(trend_by_date.items()):
        for topic, values in sorted(topics.items()):
            trends.append({
                "interview_date": date,
                "topic": topic,
                "topic_label": TOPIC_LABELS.get(topic, topic),
                "question_count": len(values),
                "average_score": round(sum(values) / len(values), 2),
            })
    payload = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "method": "频率、单场得分、JD 优先级和本地练习记录的确定性排序；不代表能力认证。",
        "review_schedule_days": schedule,
        "question_count": len(rows),
        "queue": rows[:limit],
        "training_goals": build_training_goals(rows, cluster_payload=cluster_payload, tag_map=tag_map),
        "trends": trends,
        "legacy_preference_compatibility": "继续读取 question-preferences.json 并影响排序/排除；页面不再提供编辑控件，历史记录不会被删除。",
    }
    output = root / "profile" / "review-plan.json"
    write_json(output, payload)
    lines = [
        "# 面试复习计划",
        "",
        f"> 生成时间：{payload['generated_at']}",
        "> 排序是练习建议，不是能力认证或通过率预测。",
        "",
        "## 优先练习",
        "",
    ]
    for index, item in enumerate(payload["queue"], start=1):
        lines.append(
            f"{index}. [{item['practice_status']}] {item['question']}｜{item['company']}｜"
            + "、".join(item["priority_reasons"])
        )
    lines.extend(["", "## 去重后的训练目标", ""])
    for index, goal in enumerate(payload["training_goals"], start=1):
        lines.append(
            f"{index}. {goal['title']}｜{goal['evidence_status']}｜{goal['why_priority']}｜"
            f"交付：{goal['deliverable']}｜验收：{goal['acceptance']}"
        )
    write_private_text(root / "profile" / "review-plan.md", "\n".join(lines).rstrip() + "\n")
    return {
        "status": "ok",
        "output": str(output),
        "questions": len(rows),
        "queue": len(payload["queue"]),
        "attempted_questions": sum(bool(attempts.get((item["case_id"], item["question_id"]))) for item in rows),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="生成按频率、失分、JD 和练习记录排序的复习计划")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--limit", type=int, default=30)
    args = parser.parse_args()
    if not 1 <= args.limit <= 500:
        raise RuntimeError("--limit 须在 1–500 之间")
    print(json.dumps(build(args.root.expanduser().resolve(), limit=args.limit), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False))
        raise SystemExit(1)
