from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

from common import SCHEMA_VERSION, now_iso, read_json, write_json


GITIGNORE = """# 仅本地版本管理；原始敏感文件不进入 Git
.venv/
.venv-whisperx/
models/
.env
**/private/
logs/
*.m4a
*.mp3
*.wav
*.aac
*.flac
*.pdf
*.docx
__pycache__/
*.pyc
"""


def run_git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=False,
        text=True,
        capture_output=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="初始化个人面试系统数据目录")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--init-git", action="store_true")
    args = parser.parse_args()

    os.umask(0o077)
    root = args.root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    if (root / "config.json").exists():
        read_json(root / "config.json")
    root.chmod(0o700)

    for relative in (
        "interviews",
        "profile",
        "events",
        "knowledge/mysql",
        "knowledge/redis",
        "knowledge/kafka",
        "knowledge/distributed-system",
        "knowledge/algorithms",
        "algorithms",
        "logs",
    ):
        (root / relative).mkdir(parents=True, exist_ok=True)

    gitignore = root / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(GITIGNORE, encoding="utf-8")

    config = root / "config.json"
    if not config.exists():
        write_json(
            config,
            {
                "schema_version": SCHEMA_VERSION,
                "created_at": now_iso(),
                "language": "zh-CN",
                "local_only": True,
                "allow_git_remote": False,
                "telemetry_enabled": False,
                "audio_enabled": False,
                "review_schedule_days": [1, 7, 30],
            },
        )

    events = root / "events" / "profile-updates.jsonl"
    events.touch(exist_ok=True)

    git_status = "not_requested"
    if args.init_git:
        if not (root / ".git").exists():
            result = run_git(root, "init")
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or "git init 失败")
            git_status = "initialized"
        else:
            git_status = "existing"
        remote = run_git(root, "remote")
        if remote.returncode == 0 and remote.stdout.strip():
            raise RuntimeError("检测到 Git 远程仓库；当前配置要求仅本地 Git，请先人工处理")

    print(
        json.dumps(
            {"status": "ok", "root": str(root), "git": git_status},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
