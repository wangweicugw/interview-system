#!/usr/bin/env python3
"""Set a mature answer review status and append a private revision event."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from check_standard_answers import validate_standard_answers


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = handle.name
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description="更新跨场成熟答案的人工确认状态")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--answer-id", required=True)
    parser.add_argument("--status", required=True, choices=("pending", "confirmed", "rejected"))
    parser.add_argument("--reason", required=True)
    args = parser.parse_args()
    os.umask(0o077)
    root = args.root.expanduser().resolve()
    path = root / "profile" / "standard-answers.json"
    if not path.is_file():
        print(json.dumps({"status": "error", "errors": ["成熟答案文件不存在"]}, ensure_ascii=False))
        return 1
    issues = validate_standard_answers(root)
    if issues:
        print(json.dumps({"status": "error", "errors": issues}, ensure_ascii=False))
        return 1
    payload = read_json(path)
    target = next((item for item in payload["answers"] if item.get("answer_id") == args.answer_id), None)
    if not target:
        print(json.dumps({"status": "error", "errors": [f"成熟答案不存在：{args.answer_id}"]}, ensure_ascii=False))
        return 1
    previous = str(target.get("status"))
    changed_at = datetime.now(timezone.utc).isoformat()
    target["status"] = args.status
    target["reviewed_at"] = changed_at
    write_json(path, payload)
    revision = {
        "schema_version": 1,
        "answer_id": args.answer_id,
        "previous_status": previous,
        "status": args.status,
        "reason": args.reason,
        "changed_at": changed_at,
    }
    log = root / "profile" / "standard-answer-revisions.jsonl"
    with log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(revision, ensure_ascii=False) + "\n")
    print(json.dumps({
        "status": "updated",
        "answer_id": args.answer_id,
        "previous_status": previous,
        "new_status": args.status,
        "revision_log": str(log),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
