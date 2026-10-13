from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from common import (
    SCHEMA_VERSION,
    append_private_text,
    now_iso,
    read_json,
    sha256_text,
    write_json,
    write_private_text,
)


def event_log_path(root: Path) -> Path:
    return root / "events" / "profile-updates.jsonl"


def load_revisions(root: Path) -> list[dict[str, Any]]:
    path = event_log_path(root)
    if not path.is_file():
        return []
    revisions: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            revisions.append(json.loads(line))
        except ValueError as exc:
            raise RuntimeError(f"事件日志第 {line_number} 行不是有效 JSON：{exc}") from exc
    return revisions


def latest_events(revisions: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for event in revisions:
        event_id = event["event_id"]
        if event_id not in latest or event.get("revision", 0) > latest[event_id].get("revision", 0):
            latest[event_id] = event
    return latest


def append_event(root: Path, event: dict[str, Any]) -> None:
    config = root / "config.json"
    if config.is_file():
        read_json(config)
    state = root / "profile" / "state.json"
    if state.is_file():
        read_json(state)
    path = event_log_path(root)
    append_private_text(path, json.dumps(event, ensure_ascii=False) + "\n")


def content_hash(event: dict[str, Any]) -> str:
    comparable = {
        key: value
        for key, value in event.items()
        if key not in {"revision", "recorded_at", "content_hash", "revision_reason"}
    }
    return sha256_text(json.dumps(comparable, ensure_ascii=False, sort_keys=True))


def rebuild_profile(root: Path) -> dict[str, Any]:
    revisions = load_revisions(root)
    latest = latest_events(revisions)
    events = sorted(latest.values(), key=lambda item: (item.get("type", ""), item.get("key", ""), item["event_id"]))
    by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        by_type[event.get("type", "unknown")].append(event)

    state = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now_iso(),
        "event_count": len(events),
        "revision_count": len(revisions),
        "status_counts": dict(Counter(event.get("status", "unknown") for event in events)),
        "by_type": dict(by_type),
    }
    profile_dir = root / "profile"
    write_json(profile_dir / "state.json", state)

    lines = ["# 个人面试长期档案", "", f"> 更新时间：{state['generated_at']}", ""]
    counts = state["status_counts"]
    lines.append(
        "- 记录：{total}；已确认：{confirmed}；待确认：{pending}；已否定：{rejected}".format(
            total=len(events),
            confirmed=counts.get("confirmed", 0),
            pending=counts.get("pending", 0),
            rejected=counts.get("rejected", 0),
        )
    )
    labels = {
        "answer_version": "答案版本",
        "knowledge_state": "知识状态",
        "interview_pattern": "面试模式",
        "algorithm_weakness": "算法弱项",
        "resume_risk": "简历风险",
        "outcome": "面试结果",
    }
    for event_type, items in by_type.items():
        lines.extend(["", f"## {labels.get(event_type, event_type)}", ""])
        for item in items:
            if item.get("status") == "rejected":
                continue
            marker = "已确认" if item.get("status") == "confirmed" else "待确认"
            lines.append(
                f"- [{marker}] `{item.get('key', '')}` · {item.get('reason', '').strip() or '无说明'} · 来源 `{item.get('case_id', '')}`"
            )
    write_private_text(profile_dir / "summary.md", "\n".join(lines).rstrip() + "\n")
    return state
