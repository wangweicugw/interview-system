#!/usr/bin/env python3
"""Plan versioned store migrations; never invent a conversion for an unknown schema."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Callable

from common import SCHEMA_VERSION, now_iso


Migration = Callable[[Path], list[str]]
MIGRATIONS: dict[tuple[int, int], Migration] = {}


def _version(root: Path) -> int:
    path = root / "config.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RuntimeError(f"无法读取 config.json：{error}") from error
    version = payload.get("schema_version") if isinstance(payload, dict) else None
    if type(version) is not int:
        raise RuntimeError("config.json 缺少整数 schema_version；不能猜测来源版本")
    return version


def plan(root: Path, target: int) -> dict:
    source = _version(root)
    if target < source:
        raise RuntimeError("不支持降级迁移；请从已验证备份恢复")
    steps = []
    current = source
    while current < target:
        next_version = current + 1
        if (current, next_version) not in MIGRATIONS:
            raise RuntimeError(f"没有已实现并验收的迁移：v{current} -> v{next_version}")
        steps.append({"from": current, "to": next_version})
        current = next_version
    return {
        "status": "no_change" if not steps else "planned",
        "root": str(root),
        "source_version": source,
        "target_version": target,
        "steps": steps,
        "requires_verified_backup": bool(steps),
    }


def apply(root: Path, target: int, backup: Path | None) -> dict:
    result = plan(root, target)
    if not result["steps"]:
        return result
    if backup is None or not backup.is_file():
        raise RuntimeError("实际迁移前必须提供已创建的 --backup")
    from backup_store import verify

    verified = verify(backup)
    if verified["status"] != "valid":
        raise RuntimeError("迁移前备份校验失败")
    completed = []
    for step in result["steps"]:
        changed = MIGRATIONS[(step["from"], step["to"])](root)
        completed.append({**step, "changed_files": changed})
    log = root / "events" / "store-migrations.jsonl"
    with log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "schema_version": SCHEMA_VERSION,
            "migrated_at": now_iso(),
            "backup": str(backup.resolve()),
            "steps": completed,
        }, ensure_ascii=False) + "\n")
    return {**result, "status": "migrated", "completed": completed}


def main() -> int:
    parser = argparse.ArgumentParser(description="规划或执行显式注册的数据格式迁移")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--target-version", required=True, type=int)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup", type=Path)
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    result = apply(root, args.target_version, args.backup.expanduser().resolve() if args.backup else None) if args.apply else plan(root, args.target_version)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False))
        raise SystemExit(1)
