#!/usr/bin/env python3
"""只读检查私人数据目录、公开源码与指定备份位置的明显风险。"""

from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import zipfile
from pathlib import Path
from typing import Any

from check_public_release import audit


SKILL_ROOT = Path(__file__).resolve().parents[1]
SKIP_NAMES = {".git", ".venv", ".venv-whisperx", "models", "__pycache__"}


def _issue(
    severity: str,
    code: str,
    path: Path | str,
    reason: str,
    fix: str,
) -> dict[str, str]:
    return {
        "severity": severity,
        "code": code,
        "path": str(path),
        "reason": reason,
        "fix": fix,
    }


def _git(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *arguments],
        text=True,
        capture_output=True,
        check=False,
    )


def _overlap(first: Path, second: Path) -> bool:
    first = first.resolve()
    second = second.resolve()
    return first == second or first in second.parents or second in first.parents


def _permission_issues(root: Path) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    for directory, subdirectories, files in os.walk(root, followlinks=False):
        base = Path(directory)
        relative_base = base.relative_to(root)
        subdirectories[:] = sorted(name for name in subdirectories if name not in SKIP_NAMES)
        candidates = [base / name for name in subdirectories] + [base / name for name in files]
        for path in candidates:
            relative = path.relative_to(root)
            if path.is_symlink():
                issues.append(_issue(
                    "high",
                    "data-symlink",
                    relative,
                    "私人数据目录包含符号链接，可能把读写引向目录外",
                    "人工核对链接目标后，将资料复制为普通文件并移除链接",
                ))
                continue
            try:
                mode = stat.S_IMODE(path.stat().st_mode)
            except OSError:
                issues.append(_issue(
                    "medium",
                    "unreadable-metadata",
                    relative,
                    "无法读取文件权限元数据",
                    "检查文件是否仍存在以及当前用户是否有权访问",
                ))
                continue
            if mode & 0o077:
                kind = "目录" if path.is_dir() else "文件"
                issues.append(_issue(
                    "high",
                    "broad-permissions",
                    relative_base / path.name,
                    f"敏感{kind}权限为 {mode:04o}，组用户或其他用户可能访问",
                    "将目录权限收紧为 0700、文件权限收紧为 0600，并核对共享需求",
                ))
    return issues


def _git_issues(root: Path) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    top = _git(root, "rev-parse", "--show-toplevel")
    if top.returncode != 0 or not top.stdout.strip():
        return issues
    git_root = Path(top.stdout.strip()).resolve()
    remotes = _git(root, "remote")
    remote_names = remotes.stdout.split() if remotes.returncode == 0 else []
    if remote_names:
        issues.append(_issue(
            "high",
            "data-git-remote",
            git_root,
            "私人数据目录位于配置了远程地址的 Git 工作树中",
            "不要推送；人工移除远程或把数据迁到独立的非远程目录",
        ))
    else:
        issues.append(_issue(
            "medium",
            "data-git-local",
            git_root,
            "私人数据目录位于本地 Git 工作树中；忽略规则仍可被强制添加绕过",
            "保持无远程并在提交前检查暂存区，或迁到非 Git 私有目录",
        ))
    tracked = _git(root, "ls-files", "-z", "--", ".")
    tracked_paths = [value for value in tracked.stdout.split("\0") if value] if tracked.returncode == 0 else []
    if tracked_paths:
        issues.append(_issue(
            "high" if remote_names else "medium",
            "data-git-tracked",
            git_root,
            f"Git 已跟踪私人数据目录范围内的 {len(tracked_paths)} 个文件",
            "先人工核对暂存区和历史；不要自动重写历史或强制推送",
        ))
    return issues


def _backup_candidates(paths: list[Path]) -> list[Path]:
    candidates: list[Path] = []
    for raw in paths:
        path = raw.expanduser().absolute()
        if path.is_symlink():
            candidates.append(path)
        elif path.is_file():
            candidates.append(path)
        elif path.is_dir():
            for child in path.rglob("*"):
                if child.is_file() or child.is_symlink():
                    candidates.append(child)
    return sorted(set(candidates))


def _backup_issues(paths: list[Path]) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    for path in _backup_candidates(paths):
        if path.is_symlink():
            issues.append(_issue(
                "medium",
                "backup-symlink",
                path,
                "备份检查目标是符号链接，未跟随读取",
                "直接指定普通备份文件并核对其保存位置",
            ))
            continue
        try:
            plaintext = zipfile.is_zipfile(path)
        except OSError:
            plaintext = False
        if plaintext:
            issues.append(_issue(
                "high",
                "plaintext-backup",
                path,
                "检测到未加密 ZIP 备份；SHA-256 完整性不能提供机密性",
                "迁移到 age 加密备份并在确认可恢复后人工处置旧明文备份",
            ))
    return issues


def check(
    root: Path,
    *,
    skill_dir: Path = SKILL_ROOT,
    backup_paths: list[Path] | None = None,
) -> dict[str, Any]:
    root = root.expanduser().absolute()
    skill_dir = skill_dir.expanduser().absolute()
    issues: list[dict[str, str]] = []
    if not root.is_dir() or root.is_symlink():
        issues.append(_issue(
            "high",
            "data-root-invalid",
            root,
            "私人数据目录不存在、不是目录或本身是符号链接",
            "指定由 init_store.py 创建的普通本地目录",
        ))
    else:
        if _overlap(root, skill_dir):
            issues.append(_issue(
                "critical",
                "source-data-overlap",
                root,
                "私人数据目录与公开 Skill 源码目录重叠",
                "停止发布并把私人数据迁到源码仓库之外；不要自动删除原目录",
            ))
        mode = stat.S_IMODE(root.stat().st_mode)
        if mode & 0o077:
            issues.append(_issue(
                "high",
                "data-root-permissions",
                root,
                f"私人数据根目录权限为 {mode:04o}",
                "将目录权限收紧为 0700，并逐项核对共享需求",
            ))
        issues.extend(_permission_issues(root))
        issues.extend(_git_issues(root))

    if not skill_dir.is_dir() or skill_dir.is_symlink():
        issues.append(_issue(
            "high",
            "skill-root-invalid",
            skill_dir,
            "Skill 源码目录不存在、不是目录或本身是符号链接",
            "指定实际的公开 Skill 源码目录",
        ))
    else:
        source_audit = audit(skill_dir, privacy_only=True, scopes=("workspace",))
        for message in source_audit["blockers"]:
            issues.append(_issue(
                "high",
                "public-source-risk",
                skill_dir,
                str(message),
                "停止发布并按发布审计定位；不要把命中内容复制到公开 Issue",
            ))
        for message in source_audit["warnings"]:
            issues.append(_issue(
                "medium",
                "public-source-review",
                skill_dir,
                str(message),
                "发布前人工复核该位置是否为虚构资料",
            ))

    issues.extend(_backup_issues(backup_paths or []))
    counts = {
        severity: sum(1 for item in issues if item["severity"] == severity)
        for severity in ("critical", "high", "medium")
    }
    if counts["critical"] or counts["high"]:
        status = "blocked"
    elif counts["medium"]:
        status = "warning"
    else:
        status = "ready"
    return {
        "status": status,
        "read_only": True,
        "data_root": str(root),
        "skill_dir": str(skill_dir),
        "counts": counts,
        "issues": issues,
        "limits": [
            "只检查用户明确指定的目录和备份路径，不扫描整台电脑",
            "不验证云端宿主的数据保留、训练或网络隔离政策",
            "不自动修改权限、Git 配置、历史或备份文件",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="只读检查 Interview System 的隐私与安全配置")
    parser.add_argument("--root", required=True, type=Path, help="私人数据目录")
    parser.add_argument("--skill-dir", type=Path, default=SKILL_ROOT, help="公开 Skill 源码目录")
    parser.add_argument(
        "--backup-path",
        action="append",
        default=[],
        type=Path,
        help="要检查的备份文件或目录；可重复指定，不会扫描未授权位置",
    )
    args = parser.parse_args()
    result = check(args.root, skill_dir=args.skill_dir, backup_paths=args.backup_path)
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result["status"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
