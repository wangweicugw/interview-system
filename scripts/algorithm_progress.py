"""Persistent algorithm-learning progress and spaced-review scheduling."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

from common import now_iso, read_json, write_json


STAGES = (
    "understanding", "independent_thinking", "optimization", "independent_coding",
    "code_review", "interviewer_followup", "completed",
)
MASTERY = ("not_started", "in_progress", "concept_understood", "assisted", "independent_passed", "transferable")
REVIEW_INTERVALS = (1, 3, 7)


def progress_path(root: Path) -> Path:
    return root / "algorithms" / "progress.json"


def default_progress() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "updated_at": now_iso(),
        "preferences": {"language": "java", "daily_minutes": 60, "level": "基础"},
        "current_session": None,
        "problems": {},
    }


def validate_progress(progress: dict[str, Any]) -> None:
    if progress.get("schema_version") != 1:
        raise ValueError("算法进度 schema_version 不受支持")
    if not isinstance(progress.get("problems"), dict):
        raise ValueError("算法进度 problems 必须是对象")
    current = progress.get("current_session")
    if current is not None and not isinstance(current, dict):
        raise ValueError("算法进度 current_session 必须是对象或 null")
    for problem_id, item in progress["problems"].items():
        if not isinstance(problem_id, str) or not isinstance(item, dict):
            raise ValueError("算法进度题目记录格式无效")
        if item.get("stage") not in STAGES or item.get("mastery") not in MASTERY:
            raise ValueError(f"算法进度题目状态无效：{problem_id}")
        if not isinstance(item.get("mistakes", []), list) or not isinstance(item.get("source_links", []), list):
            raise ValueError(f"算法进度题目列表字段无效：{problem_id}")


def load_progress(root: Path) -> dict[str, Any]:
    path = progress_path(root)
    progress = read_json(path) if path.is_file() else default_progress()
    validate_progress(progress)
    return progress


def save_progress(root: Path, progress: dict[str, Any]) -> None:
    progress["updated_at"] = now_iso()
    validate_progress(progress)
    write_json(progress_path(root), progress)


def ensure_problem(progress: dict[str, Any], problem: dict[str, Any]) -> dict[str, Any]:
    return progress["problems"].setdefault(problem["id"], {
        "title": problem["title"],
        "topic": problem["topic"],
        "topic_key": problem.get("topic_key", problem["topic"]),
        "stage": "understanding",
        "mastery": "not_started",
        "hint_level": 0,
        "answer_revealed": False,
        "attempt_count": 0,
        "independent_pass_count": 0,
        "last_practiced_at": None,
        "next_review_date": None,
        "mistakes": [],
        "concepts": [],
        "source_links": [],
        "session_ids": [],
    })


def start_session(progress: dict[str, Any], problem: dict[str, Any], session_id: str, language: str) -> dict[str, Any]:
    item = ensure_problem(progress, problem)
    item.update({"stage": "understanding", "mastery": "in_progress", "hint_level": 0,
                 "answer_revealed": False, "last_practiced_at": now_iso()})
    if session_id not in item["session_ids"]:
        item["session_ids"].append(session_id)
    progress["current_session"] = {
        "session_id": session_id, "problem_id": problem["id"], "stage": "understanding",
        "language": language, "pending_action": "ask_approach",
    }
    return item


def record_stage(progress: dict[str, Any], problem: dict[str, Any], stage: str,
                 concepts: list[str], mistakes: list[str], pending_action: str) -> dict[str, Any]:
    if stage not in STAGES:
        raise ValueError(f"未知教学阶段：{stage}")
    item = ensure_problem(progress, problem)
    item["stage"] = stage
    item["last_practiced_at"] = now_iso()
    if stage in ("optimization", "independent_coding", "code_review", "interviewer_followup"):
        item["mastery"] = "concept_understood"
    for concept in concepts:
        if concept and concept not in item["concepts"]:
            item["concepts"].append(concept)
    for mistake in mistakes:
        add_mistake(item, mistake, "interactive", None)
    if progress.get("current_session"):
        progress["current_session"]["stage"] = stage
        progress["current_session"]["pending_action"] = pending_action
    return item


def add_mistake(item: dict[str, Any], category: str, source: str, attempt_id: str | None) -> None:
    if not category:
        return
    for existing in item["mistakes"]:
        if existing["category"] == category:
            existing["count"] += 1
            existing["last_seen_at"] = now_iso()
            if attempt_id:
                existing["attempt_id"] = attempt_id
            return
    item["mistakes"].append({
        "category": category, "source": source, "count": 1,
        "last_seen_at": now_iso(), "attempt_id": attempt_id,
    })


def record_hint(progress: dict[str, Any], problem: dict[str, Any], level: int) -> None:
    item = ensure_problem(progress, problem)
    item["hint_level"] = max(item.get("hint_level", 0), level)
    if progress.get("current_session"):
        progress["current_session"]["pending_action"] = "answer_current_hint"


def record_reveal(progress: dict[str, Any], problem: dict[str, Any]) -> None:
    item = ensure_problem(progress, problem)
    item["answer_revealed"] = True
    item["mastery"] = "assisted"
    item["hint_level"] = max(item.get("hint_level", 0), 4)
    if progress.get("current_session"):
        progress["current_session"]["pending_action"] = "explain_reference_solution"


def record_attempt(progress: dict[str, Any], problem: dict[str, Any], attempt: dict[str, Any],
                   mistake_categories: list[str], practiced_on: date | None = None) -> dict[str, Any]:
    item = ensure_problem(progress, problem)
    item["attempt_count"] += 1
    item["last_practiced_at"] = attempt["created_at"]
    for category in mistake_categories:
        add_mistake(item, category, "submission", attempt["attempt_id"])
    passed = attempt["actual_tested"] and attempt["passed"] == attempt["total"]
    independent = passed and item.get("hint_level", 0) == 0 and not item.get("answer_revealed", False)
    if independent:
        item["mastery"] = "independent_passed"
        item["stage"] = "interviewer_followup"
        item["independent_pass_count"] += 1
        if any(
            problem_id != problem["id"]
            and other.get("topic_key", other.get("topic")) == item.get("topic_key", item.get("topic"))
            and other.get("mastery") in ("independent_passed", "transferable")
            for problem_id, other in progress["problems"].items()
        ):
            item["mastery"] = "transferable"
    elif passed:
        item["mastery"] = "assisted"
        item["stage"] = "code_review"
    else:
        item["mastery"] = "in_progress"
        item["stage"] = "code_review"
    base = practiced_on or date.today()
    if independent:
        interval = REVIEW_INTERVALS[min(item["independent_pass_count"] - 1, len(REVIEW_INTERVALS) - 1)]
    else:
        interval = 1
    item["next_review_date"] = (base + timedelta(days=interval)).isoformat()
    if progress.get("current_session"):
        progress["current_session"]["stage"] = item["stage"]
        progress["current_session"]["pending_action"] = (
            "ask_interviewer_followup" if independent else "fix_first_reported_issue"
        )
    return item


def due_problem_ids(progress: dict[str, Any], on_date: date) -> list[str]:
    due = []
    for problem_id, item in progress["problems"].items():
        scheduled = item.get("next_review_date")
        if scheduled and date.fromisoformat(scheduled) <= on_date:
            due.append((scheduled, problem_id))
    return [problem_id for _, problem_id in sorted(due)]


def link_source(item: dict[str, Any], link: dict[str, str]) -> None:
    identity = (link.get("case_id"), link.get("question_id"), link.get("knowledge_key"), link.get("source_path"))
    if not any((old.get("case_id"), old.get("question_id"), old.get("knowledge_key"), old.get("source_path")) == identity
               for old in item["source_links"]):
        item["source_links"].append({key: value for key, value in link.items() if value})
