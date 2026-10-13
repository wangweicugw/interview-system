#!/usr/bin/env python3
"""Record one private spoken/written practice attempt without changing interview evidence."""

from __future__ import annotations

import argparse
import json
import os
import uuid
from pathlib import Path
from typing import Any

from check_data_compatibility import require_store_compatible
from common import append_private_text, now_iso, sha256_text, write_private_text


def _question(root: Path, case_id: str, question_id: str) -> dict[str, Any] | None:
    for path in (root / "interviews").glob("*/qa-tree.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if payload.get("case_id") != case_id:
            continue
        return next(
            (
                item for item in payload.get("questions", [])
                if isinstance(item, dict) and item.get("question_id") == question_id
            ),
            None,
        )
    return None


def record_attempt(
    root: Path,
    *,
    case_id: str,
    question_id: str,
    answer: str,
    duration_seconds: int,
    self_rating: int,
    note: str = "",
) -> dict[str, Any]:
    require_store_compatible(root)
    question = _question(root, case_id, question_id)
    if question is None:
        raise RuntimeError(f"找不到真实问题：{case_id}/{question_id}")
    answer = answer.strip()
    if not answer:
        raise RuntimeError("练习回答不能为空")
    if len(answer) > 100_000:
        raise RuntimeError("练习回答超过 100000 字符")
    if not 1 <= duration_seconds <= 3600:
        raise RuntimeError("练习时长须在 1–3600 秒之间")
    if not 1 <= self_rating <= 5:
        raise RuntimeError("自评分须在 1–5 之间")

    os.umask(0o077)
    attempt_id = f"practice-{uuid.uuid4().hex[:16]}"
    private_dir = root / "practice" / "private"
    private_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    answer_path = private_dir / f"{attempt_id}.txt"
    write_private_text(answer_path, answer + "\n")
    event = {
        "schema_version": 1,
        "attempt_id": attempt_id,
        "recorded_at": now_iso(),
        "case_id": case_id,
        "question_id": question_id,
        "question": str(question.get("question") or ""),
        "duration_seconds": duration_seconds,
        "self_rating": self_rating,
        "note": note.strip(),
        "answer_sha256": sha256_text(answer + "\n"),
        "answer_path": str(answer_path.relative_to(root)),
        "review_status": "pending",
    }
    log = root / "practice" / "attempts.jsonl"
    append_private_text(log, json.dumps(event, ensure_ascii=False) + "\n")
    return event


def main() -> int:
    parser = argparse.ArgumentParser(description="保存一次脱稿练习及计时、自评")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--question-id", required=True)
    parser.add_argument("--answer-file", required=True, type=Path)
    parser.add_argument("--duration-seconds", required=True, type=int)
    parser.add_argument("--self-rating", required=True, type=int)
    parser.add_argument("--note", default="")
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    answer_file = args.answer_file.expanduser().resolve()
    if not answer_file.is_file():
        raise RuntimeError("练习回答文件不存在")
    event = record_attempt(
        root,
        case_id=args.case_id,
        question_id=args.question_id,
        answer=answer_file.read_text(encoding="utf-8"),
        duration_seconds=args.duration_seconds,
        self_rating=args.self_rating,
        note=args.note,
    )
    print(json.dumps({"status": "recorded", **event}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False))
        raise SystemExit(1)
