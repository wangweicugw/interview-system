#!/usr/bin/env python3
"""Record one private spoken/written practice attempt without changing interview evidence."""

from __future__ import annotations

import argparse
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any

from check_data_compatibility import require_store_compatible
from common import (
    commit_write_transaction,
    data_root_write_lock,
    finalize_write_transaction,
    now_iso,
    recover_write_transactions,
    sha256_text,
)


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


def _record_attempt_unlocked(
    root: Path,
    *,
    case_id: str,
    question_id: str,
    answer: str,
    duration_seconds: int,
    self_rating: int,
    note: str = "",
    attempt_id: str | None = None,
    transaction_updates: dict[Path, bytes] | None = None,
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
    if attempt_id is None:
        attempt_id = f"practice-{uuid.uuid4().hex[:16]}"
    elif not re.fullmatch(r"practice-[a-f0-9]{16,64}", attempt_id):
        raise RuntimeError("练习记录编号无效，请刷新页面后重试")
    private_dir = root / "practice" / "private"
    private_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    answer_hash = sha256_text(answer + "\n")
    answer_path = private_dir / f"{attempt_id}.txt"
    log = root / "practice" / "attempts.jsonl"
    if attempt_id and log.is_file():
        try:
            existing_events = [
                json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        except (OSError, ValueError) as error:
            raise RuntimeError("练习记录索引无法读取，请先检查档案后重试") from error
        existing = next(
            (item for item in existing_events
             if isinstance(item, dict) and item.get("attempt_id") == attempt_id),
            None,
        )
        if existing is not None:
            same_payload = (
                existing.get("case_id") == case_id
                and existing.get("question_id") == question_id
                and existing.get("duration_seconds") == duration_seconds
                and existing.get("self_rating") == self_rating
                and existing.get("note") == note.strip()
                and existing.get("answer_sha256") == answer_hash
            )
            if not same_payload:
                raise RuntimeError("练习记录编号已存在但内容不同，请刷新页面后重试")
            try:
                existing_text = answer_path.read_text(encoding="utf-8")
            except (OSError, ValueError) as error:
                raise RuntimeError("练习记录正文缺失，请重新保存本次练习") from error
            if sha256_text(existing_text) != existing.get("answer_sha256"):
                raise RuntimeError("练习记录正文已损坏，请重新保存本次练习")
            return {**existing, "deduplicated": True}
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
        "answer_sha256": answer_hash,
        "answer_path": str(answer_path.relative_to(root)),
        "review_status": "pending",
    }
    previous = transaction_updates.get(log) if transaction_updates is not None else None
    if previous is None:
        previous = log.read_bytes() if log.is_file() else b""
    prepared = {
        answer_path: (answer + "\n").encode("utf-8"),
        log: previous + (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8"),
    }
    if transaction_updates is not None:
        transaction_updates.update(prepared)
    else:
        transaction_id = f"practice-{attempt_id.removeprefix('practice-')}"
        commit_write_transaction(root, prepared, transaction_id, finalize=False)
        finalize_write_transaction(root, transaction_id, {"status": "recorded", **event})
    return event


def record_attempt(
    root: Path,
    *,
    case_id: str,
    question_id: str,
    answer: str,
    duration_seconds: int,
    self_rating: int,
    note: str = "",
    attempt_id: str | None = None,
) -> dict[str, Any]:
    with data_root_write_lock(root):
        recover_write_transactions(root)
        return _record_attempt_unlocked(
            root, case_id=case_id, question_id=question_id, answer=answer,
            duration_seconds=duration_seconds, self_rating=self_rating,
            note=note, attempt_id=attempt_id,
        )


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
