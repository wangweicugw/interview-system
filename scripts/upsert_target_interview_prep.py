#!/usr/bin/env python3
"""Create or update one company preparation file with validation and revision history."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from check_data_compatibility import require_store_compatible
from check_target_interview_preps import SLUG_RE, duplicate_errors, load_existing, question_index, validate_payload
from common import append_private_text, now_iso, read_json, write_json


def digest(payload: dict) -> str:
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def render_preparations_only(root: Path) -> None:
    from render_interview_html import (
        PREPARATIONS_HTML,
        discover_target_interview_preps,
        render_interview_preparation,
        render_interview_preparations,
        target_interview_prep_filename,
        write_text,
    )

    write_text(root / PREPARATIONS_HTML, render_interview_preparations(root))
    for payload in discover_target_interview_preps(root):
        write_text(
            root / target_interview_prep_filename(payload),
            render_interview_preparation(root, payload),
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="创建或更新单家公司面试准备稿")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--slug", required=True)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--render", action="store_true")
    args = parser.parse_args()

    os.umask(0o077)
    root = args.root.expanduser().resolve()
    require_store_compatible(root)
    slug = args.slug.strip()
    if not SLUG_RE.fullmatch(slug):
        raise RuntimeError("--slug 只能使用小写英文、数字和连字符")
    source = args.input.expanduser().resolve()
    payload = json.loads(source.read_text(encoding="utf-8"))
    errors = validate_payload(
        payload,
        known_questions=question_index(root),
        require_historical_evidence=True,
    )
    if errors:
        raise RuntimeError("准备稿校验失败：" + "；".join(errors))

    prep_dir = root / "profile" / "target-interview-preps"
    prep_dir.mkdir(parents=True, exist_ok=True)
    destination = prep_dir / f"{slug}.json"
    rows = [(path, row) for path, row in load_existing(root) if path.resolve() != destination.resolve()]
    rows.append((destination, payload))
    duplicates = duplicate_errors(rows)
    if duplicates:
        raise RuntimeError("；".join(duplicates))

    before = read_json(destination) if destination.is_file() else None
    now = now_iso()
    payload["updated_at"] = now
    write_json(destination, payload)
    append_private_text(
        prep_dir / "revisions.jsonl",
        json.dumps(
            {
                "recorded_at": now,
                "action": "updated" if before else "created",
                "slug": slug,
                "before_sha256": digest(before) if before else None,
                "after_sha256": digest(payload),
                "before": before,
                "after": payload,
            },
            ensure_ascii=False,
        )
        + "\n",
    )

    rendered = False
    warning = ""
    if args.render:
        script = Path(__file__).with_name("render_interview_html.py")
        result = subprocess.run(
            [sys.executable, str(script), "--root", str(root), "--all"],
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip()
            if "没有可呈现的面试场次" in message:
                render_preparations_only(root)
            else:
                warning = (
                    "准备稿和修订记录已保存，但页面重建失败；请修复渲染问题后重新生成页面。"
                    + (f" 原因：{message}" if message else "")
                )
                print(json.dumps({
                    "status": "partial",
                    "path": str(destination),
                    "action": "updated" if before else "created",
                    "data_saved": True,
                    "rendered": False,
                    "warning": warning,
                }, ensure_ascii=False))
                return 2
        rendered = True
    print(json.dumps({
        "status": "ok",
        "path": str(destination),
        "action": "updated" if before else "created",
        "data_saved": True,
        "rendered": rendered,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
