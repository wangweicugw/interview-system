"""Adaptive weekly planning and privacy-safe algorithm learning reports."""

from __future__ import annotations

import json
import os
import tempfile
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from algorithm_progress import due_problem_ids, load_progress
from common import now_iso, read_json, write_json


MASTERED = {"independent_passed", "transferable"}


def _attempts(root: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for path in sorted((root / "algorithms" / "sessions").glob("*/attempts.jsonl")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except ValueError as error:
                raise ValueError(f"算法尝试记录损坏：{path.name}:{number}") from error
            item["session_id"] = path.parent.name
            result.append(item)
    return result


def build_week_plan(root: Path, bank: list[dict[str, Any]], week_start: date,
                    save: bool = False) -> dict[str, Any]:
    progress = load_progress(root)
    attempted = [item for item in progress["problems"].values() if item.get("attempt_count", 0)]
    mastered = [item for item in attempted if item.get("mastery") in MASTERED]
    records = [attempt for attempt in _attempts(root) if attempt.get("actual_tested")]
    independent_records = [
        attempt for attempt in records
        if attempt.get("passed") == attempt.get("total")
        and attempt.get("hint_level", 0) == 0 and not attempt.get("answer_revealed", False)
    ]
    assisted_records = [
        attempt for attempt in records
        if attempt.get("hint_level", 0) > 0 or attempt.get("answer_revealed", False)
    ]
    independent_rate = len(independent_records) / len(records) if records else 0.0
    repeated_errors = sum(
        mistake.get("count", 0)
        for item in attempted for mistake in item.get("mistakes", [])
        if mistake.get("count", 0) >= 2
    )
    if records and independent_rate >= 0.8 and not assisted_records and repeated_errors <= 2:
        new_target, adjustment = 6, "独立通过率较高，新增题量提高到 6 道"
    elif records and (independent_rate < 0.4 or repeated_errors >= 5):
        new_target, adjustment = 3, "独立通过率偏低或重复错误较多，题量降到 3 道并优先补弱项"
    else:
        new_target, adjustment = 5, "维持每周 5 道新题"

    weak_topics: Counter[str] = Counter()
    for item in attempted:
        weight = sum(mistake.get("count", 1) for mistake in item.get("mistakes", []))
        if item.get("mastery") not in MASTERED:
            weight += 2
        weak_topics[item.get("topic", "未分类")] += weight
    new_candidates = [problem for problem in bank if problem["id"] not in progress["problems"]]
    new_candidates.sort(key=lambda problem: (
        -weak_topics.get(problem["topic"], 0),
        problem.get("difficulty") != "easy", problem.get("leetcode_id", 999999), problem["id"],
    ))
    new_ids = [problem["id"] for problem in new_candidates[:new_target]]
    end = week_start + timedelta(days=6)
    review_ids = [problem_id for problem_id in due_problem_ids(progress, end)]
    weak_existing = sorted(
        progress["problems"],
        key=lambda problem_id: (
            progress["problems"][problem_id].get("mastery") in MASTERED,
            -sum(mistake.get("count", 1) for mistake in progress["problems"][problem_id].get("mistakes", [])),
            problem_id,
        ),
    )
    for problem_id in weak_existing:
        if problem_id not in review_ids and len(review_ids) < 5:
            review_ids.append(problem_id)

    days = []
    review_pool = list(dict.fromkeys(review_ids + new_ids))
    for offset in range(7):
        day = week_start + timedelta(days=offset)
        if offset < 5:
            tasks = ([{"type": "review", "problem_id": review_ids[offset]}]
                     if offset < len(review_ids) else [])
            tasks += [
                {"type": "new", "problem_id": problem_id}
                for index, problem_id in enumerate(new_ids) if index % 5 == offset
            ]
        elif offset == 5:
            tasks = [{"type": "weekly_review", "problem_id": problem_id} for problem_id in review_pool[:3]]
        else:
            pool = [
                problem_id for problem_id, item in progress["problems"].items()
                if item.get("mastery") in MASTERED
            ]
            pool = list(dict.fromkeys(pool + new_ids + review_ids))
            tasks = [{"type": "mock_interview", "problem_id": problem_id} for problem_id in pool[:2]]
        days.append({"date": day.isoformat(), "day": offset + 1, "tasks": tasks})

    plan = {
        "schema_version": 1, "generated_at": now_iso(), "week_start": week_start.isoformat(),
        "metrics": {
            "attempted_problems": len(attempted), "independent_mastered": len(mastered),
            "actual_submissions": len(records), "independent_submissions": len(independent_records),
            "assisted_submissions": len(assisted_records), "independent_rate": round(independent_rate, 3),
            "repeated_error_count": repeated_errors,
        },
        "adjustment": adjustment, "new_problem_target": new_target,
        "weak_topics": [{"topic": topic, "weight": weight} for topic, weight in weak_topics.most_common()],
        "days": days,
    }
    if save:
        path = root / "algorithms" / "plans" / f"week-{week_start.isoformat()}.json"
        write_json(path, plan)
        plan["saved_to"] = str(path)
    return plan


def _risk_rows(progress: dict[str, Any], on_date: date) -> list[tuple[int, str, dict[str, Any]]]:
    rows = []
    for problem_id, item in progress["problems"].items():
        due = item.get("next_review_date")
        overdue = bool(due and date.fromisoformat(due) <= on_date)
        mistakes = sum(value.get("count", 1) for value in item.get("mistakes", []))
        score = (5 if overdue else 0) + (3 if item.get("mastery") not in MASTERED else 0) + mistakes
        rows.append((score, problem_id, item))
    return sorted(rows, key=lambda row: (-row[0], row[1]))


def render_report(root: Path, kind: str, on_date: date, week_start: date | None = None) -> str:
    progress = load_progress(root)
    generated = now_iso()
    if kind == "mistakes":
        lines = ["# 算法错题摘要", "", f"生成时间：{generated}", "",
                 "> 依据本地实际记录汇总；不等同于能力认证。", ""]
        rows = _risk_rows(progress, on_date)
        for _, problem_id, item in rows:
            mistakes = item.get("mistakes", [])
            if not mistakes:
                continue
            lines += [f"## {item.get('title', problem_id)}", "",
                      f"掌握度：`{item.get('mastery')}`；下次复习：{item.get('next_review_date') or '未安排'}", ""]
            for mistake in sorted(mistakes, key=lambda value: (-value.get("count", 1), value["category"])):
                lines.append(f"- `{mistake['category']}`：{mistake.get('count', 1)} 次；来源 `{mistake.get('source', 'unknown')}`")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    if kind == "weekly":
        start = week_start or (on_date - timedelta(days=on_date.weekday()))
        end = start + timedelta(days=6)
        selected = []
        for attempt in _attempts(root):
            created = datetime.fromisoformat(attempt["created_at"]).date()
            if start <= created <= end:
                selected.append(attempt)
        passed = sum(1 for attempt in selected if attempt.get("passed") == attempt.get("total"))
        lines = ["# 算法学习周报", "", f"周期：{start.isoformat()} 至 {end.isoformat()}",
                 f"生成时间：{generated}", "", "## 真实记录", "",
                 f"- 提交次数：{len(selected)}", f"- 全部用例通过的提交：{passed}",
                 f"- 到期复习题：{len(due_problem_ids(progress, on_date))}", "", "## 当前薄弱点", ""]
        counts = Counter(
            mistake["category"] for item in progress["problems"].values()
            for mistake in item.get("mistakes", []) for _ in range(mistake.get("count", 1))
        )
        lines += [f"- `{category}`：{count} 次" for category, count in counts.most_common(8)] or ["- 暂无错误记录"]
        return "\n".join(lines).rstrip() + "\n"

    lines = ["# 面试前算法冲刺清单", "", f"生成时间：{generated}", "",
             "> 按到期、未独立掌握和重复错误排序；历史面试事实不会被改写。", ""]
    for score, problem_id, item in _risk_rows(progress, on_date)[:10]:
        sources = ", ".join(
            f"{link.get('case_id')}/{link.get('question_id')}" for link in item.get("source_links", [])
        ) or "无面试来源关联"
        lines += [f"- [ ] {item.get('title', problem_id)}（风险分 {score}）",
                  f"  - 掌握度：`{item.get('mastery')}`；复习日：{item.get('next_review_date') or '未安排'}",
                  f"  - 来源：{sources}"]
    return "\n".join(lines).rstrip() + "\n"


def export_report(root: Path, kind: str, output: Path, on_date: date,
                  week_start: date | None, force: bool) -> dict[str, Any]:
    output = output.expanduser().resolve()
    if output.exists() and not force:
        raise RuntimeError("输出文件已存在；如需覆盖请显式添加 --force")
    output.parent.mkdir(parents=True, exist_ok=True)
    body = render_report(root, kind, on_date, week_start)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=output.parent, delete=False) as handle:
        handle.write(body)
        temporary = handle.name
    os.replace(temporary, output)
    return {"status": "exported", "kind": kind, "output": str(output), "bytes": len(body.encode("utf-8"))}
