#!/usr/bin/env python3
"""把各场被判错的 claim 汇总成错题本。

只收录 `verdict=incorrect` 的 claim：这些是现场说错、且 claim 自身已带对照来源的技术点。
「正确说法」「记忆点」「复述提示」来自 `<root>/profile/mistake-book-notes.json`（人工整理，
可选）。没有整理过的条目只展示 claim 自带的说明和来源，不用模型补写结论。

错题本内容是派生展示，不替代 claims.json；修改判定请回到对应场次。
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import quote

from check_data_compatibility import require_store_compatible
from common import now_iso, read_json, write_json
from render_interview_html import TOPIC_LABELS, discover_cases, mistake_book_source_digest, question_topic

NOTES_PATH = Path("profile") / "mistake-book-notes.json"
OUTPUT_PATH = Path("profile") / "mistake-book.json"
CASE_HTML = "interview-review.html"


def _read(path: Path, default: Any) -> Any:
    try:
        return read_json(path)
    except (OSError, ValueError):
        return default


def load_notes(root: Path) -> dict[str, dict[str, Any]]:
    """读取人工整理的正确说法；文件不存在时返回空表。"""
    payload = _read(root / NOTES_PATH, {})
    if not isinstance(payload, dict):
        return {}
    entries = payload.get("entries")
    if not isinstance(entries, dict):
        return {}
    return {
        str(key): value
        for key, value in entries.items()
        if isinstance(value, dict)
    }


def _text(value: Any) -> str:
    return str(value or "").strip()


def _sources(raw: Any) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        result.append({
            "title": _text(item.get("title")),
            "section": _text(item.get("section")),
            "url": _text(item.get("url")),
            "relation": _text(item.get("relation")),
            "summary": _text(item.get("summary")),
            "accessed_at": _text(item.get("accessed_at")),
        })
    return result


def collect(root: Path) -> list[dict[str, Any]]:
    notes = load_notes(root)
    entries: list[dict[str, Any]] = []
    for case in discover_cases(root):
        claims = _read(case["path"] / "claims.json", {})
        questions = {
            str(item.get("question_id")): item
            for item in _read(case["path"] / "qa-tree.json", {}).get("questions", [])
            if isinstance(item, dict)
        }
        for claim in claims.get("claims", []):
            if not isinstance(claim, dict) or _text(claim.get("verdict")) != "incorrect":
                continue
            claim_id = _text(claim.get("claim_id"))
            question_id = _text(claim.get("question_id"))
            question = questions.get(question_id, {})
            entry_id = f'{case["case_id"]}/{claim_id}'
            note = notes.get(entry_id, {})
            # 题面主题按题面推断；整理时若发现知识点与题面主题不符，可在 notes 里覆盖。
            topic = _text(note.get("topic")) or (question_topic(question) if question else "other")
            if topic not in TOPIC_LABELS:
                topic = "other"
            sources = _sources(claim.get("sources"))
            entries.append({
                "entry_id": entry_id,
                "case_id": case["case_id"],
                "case_name": case["name"],
                "date": case["date"],
                "company": case["company"],
                "role": case["role"],
                "round": case["round"],
                "question_id": question_id,
                "question": _text(question.get("question")),
                "category": _text(question.get("category")),
                "topic": topic,
                "topic_label": TOPIC_LABELS.get(topic, topic),
                "claim": _text(claim.get("claim")),
                "version_context": _text(claim.get("version_context")),
                "why_wrong": _text(claim.get("explanation")),
                "correction": _text(note.get("correction")),
                "memory_hook": _text(note.get("memory_hook")),
                "recall_prompt": _text(note.get("recall_prompt")),
                "sources": sources,
                "evidence_segments": [str(item) for item in claim.get("evidence_segments", [])],
                "case_href": (
                    f'interviews/{quote(case["name"])}/{CASE_HTML}'
                    f'#practice-{quote(question_id)}'
                ),
            })
    return entries


def build(root: Path) -> dict[str, Any]:
    require_store_compatible(root)
    entries = collect(root)
    topic_order = {name: index for index, name in enumerate(TOPIC_LABELS)}
    # 三次稳定排序：先按主题固定顺序分组，组内新的场次在前，同场按 entry_id 保持可复现。
    entries.sort(key=lambda item: item["entry_id"])
    entries.sort(key=lambda item: item["date"], reverse=True)
    entries.sort(key=lambda item: topic_order.get(item["topic"], 999))
    by_topic = Counter(item["topic_label"] for item in entries)
    by_case = Counter(f'{item["company"]}（{item["date"]}）' for item in entries)
    payload = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "source_digest": mistake_book_source_digest(root, discover_cases(root)),
        "method": "汇总各场 claims.json 中 verdict=incorrect 的结论；正确说法仅来自人工整理的 mistake-book-notes.json。",
        "stats": {
            "total": len(entries),
            "with_correction": sum(bool(item["correction"]) for item in entries),
            "by_topic": dict(by_topic),
            "by_case": dict(by_case),
            "dates": sorted({item["date"] for item in entries}),
        },
        "entries": entries,
    }
    output = root / OUTPUT_PATH
    write_json(output, payload)
    return {
        "status": "ok",
        "output": str(output),
        "entries": len(entries),
        "with_correction": payload["stats"]["with_correction"],
        "topics": len(by_topic),
        "missing_correction": [
            item["entry_id"] for item in entries if not item["correction"]
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="把判错的 claim 汇总成错题本")
    parser.add_argument("--root", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.root.expanduser().resolve()), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False))
        raise SystemExit(1)
