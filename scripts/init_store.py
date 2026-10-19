from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

from common import SCHEMA_VERSION, now_iso, read_json, write_json


SKILL_ROOT = Path(__file__).resolve().parents[1]

GITIGNORE = """# 私人数据目录：默认不允许 Git 跟踪任何内容。
# .gitignore 不能阻止 git add -f，也不能清除已提交历史。
*
!.gitignore
"""


def run_git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=False,
        text=True,
        capture_output=True,
    )


def paths_overlap(first: Path, second: Path) -> bool:
    first = first.resolve()
    second = second.resolve()
    return first == second or first in second.parents or second in first.parents


def git_context(root: Path) -> tuple[Path | None, list[str]]:
    top = run_git(root, "rev-parse", "--show-toplevel")
    if top.returncode != 0 or not top.stdout.strip():
        return None, []
    top_level = Path(top.stdout.strip()).resolve()
    remotes = run_git(root, "remote")
    names = remotes.stdout.split() if remotes.returncode == 0 else []
    return top_level, names


def main() -> int:
    parser = argparse.ArgumentParser(description="初始化个人面试系统数据目录")
    parser.add_argument("--root", required=True, type=Path, help="用户明确指定的私有档案绝对路径；无默认位置")
    parser.add_argument("--init-git", action="store_true")
    args = parser.parse_args()

    os.umask(0o077)
    requested_root = args.root.expanduser()
    if not requested_root.is_absolute():
        parser.error("--root 必须是用户指定的绝对路径，不会按当前工作目录推定保存位置")
    if requested_root.is_symlink():
        raise RuntimeError("数据目录不能是符号链接")
    root = requested_root.resolve()
    if paths_overlap(root, SKILL_ROOT):
        raise RuntimeError(
            "数据目录不得位于 Skill 源码目录内，也不得包含源码目录；"
            "请选择独立的私有路径"
        )
    root.mkdir(parents=True, exist_ok=True)
    git_root, remotes = git_context(root)
    if remotes:
        raise RuntimeError(
            f"数据目录位于已配置远程的 Git 工作树中：{git_root}；"
            "为避免误上传，未写入任何面试数据"
        )
    if (root / "config.json").exists():
        read_json(root / "config.json")
    root.chmod(0o700)

    protected_paths = (
        ".gitignore", "config.json", "interviews", "profile", "events", "practice",
        "knowledge", "algorithms", "logs",
    )
    for relative in protected_paths:
        if (root / relative).is_symlink():
            raise RuntimeError(f"数据目录内不允许受保护路径为符号链接：{relative}")

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
    warnings: list[str] = []
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
        warnings.append(
            "已按显式请求初始化本地 Git；默认忽略全部数据。"
            ".gitignore 不能阻止强制添加，请勿配置远程仓库"
        )
    elif git_root is not None:
        warnings.append(f"数据目录位于本地 Git 工作树中：{git_root}；请勿强制添加敏感文件")

    print(
        json.dumps(
            {"status": "ok", "root": str(root), "git": git_status, "warnings": warnings},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
