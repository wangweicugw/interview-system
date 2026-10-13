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


def build(root: Path, *, limit: int) -> dict[str, Any]:
    require_store_compatible(root)
    schedule = _read(root / "config.json", {}).get("review_schedule_days", [1, 7, 30])
    if not isinstance(schedule, list) or not schedule or any(type(value) is not int or value < 1 for value in schedule):
        schedule = [1, 7, 30]
    cluster_payload = _read(root / "profile" / "question-clusters.json", {})
    cluster_freq: dict[tuple[str, str], int] = {}
    for cluster in cluster_payload.get("clusters", []):
        if not isinstance(cluster, dict) or cluster.get("status") == "rejected":
            continue
        refs = [ref for ref in cluster.get("questions", []) if isinstance(ref, dict)]
        case_count = len({str(ref.get("case_id") or "") for ref in refs})
        for ref in refs:
            cluster_freq[(str(ref.get("case_id") or ""), str(ref.get("question_id") or ""))] = case_count
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
        "trends": trends,
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
