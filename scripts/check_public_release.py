#!/usr/bin/env python3
"""Audit the skill source before publishing; reports paths, never secret values."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path


ROOT_FILES = {
    ".gitignore", "SKILL.md", "README.md", "LICENSE", "LICENSE.md",
    "CONTRIBUTING.md", "SECURITY.md", "requirements.lock", "requirements-optional.txt",
}
SKIP_DIRECTORIES = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".venv", ".venv-whisperx"}
PRIVATE_DIRECTORIES = {"private", "interviews", "profile", "events", "logs", "models"}
SECRET_PATTERNS = (
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bhf_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)
PERSONAL_PATH = re.compile(r"/(?:Users|home)/[^/\s'\"]+")


def allowed(relative: Path) -> bool:
    parts = relative.parts
    if len(parts) == 1:
        return parts[0] in ROOT_FILES
    if len(parts) == 2:
        parent, name = parts
        return (
            (parent == "agents" and name == "openai.yaml")
            or (parent == "assets" and name in {"algorithm-problems.json", "demo-case.json"})
            or (parent == "templates" and name == "workbuddy-SKILL.md")
            or (parent == "references" and name.endswith(".md"))
            or (parent == "scripts" and (name.endswith(".py") or name in {"ocr_image.swift", "CoachHarness.java"}))
            or (parent == "tests" and name.startswith("test_") and name.endswith(".py"))
        )
    if len(parts) == 3:
        return (
            (parts[0] == "tests" and parts[1] == "fixtures" and parts[2] in {"interview.md", "jd.txt", "resume.txt"})
            or (parts[0] == ".github" and parts[1] == "workflows" and parts[2].endswith(('.yml', '.yaml')))
        )
    return False


def audit(source: Path, *, privacy_only: bool = False) -> dict[str, object]:
    source = source.expanduser().resolve()
    blockers: list[str] = []
    warnings: list[str] = []
    public_files: list[str] = []
    skipped_local_environments: list[str] = []
    if not privacy_only:
        if not (source / "SKILL.md").is_file():
            blockers.append("缺少 SKILL.md")
        if not (source / "README.md").is_file():
            blockers.append("缺少 GitHub 使用说明 README.md")
        if not (source / "LICENSE").is_file() and not (source / "LICENSE.md").is_file():
            blockers.append("缺少开源许可证 LICENSE；需由维护者选择")
        if not (source / ".github/workflows/tests.yml").is_file():
            warnings.append("缺少自动测试工作流")

    for directory, subdirs, files in os.walk(source, followlinks=False):
        base = Path(directory)
        kept = []
        for name in subdirs:
            path = base / name
            relative = path.relative_to(source)
            if path.is_symlink():
                blockers.append(f"符号链接目录不可发布：{relative}")
            elif name in PRIVATE_DIRECTORIES:
                blockers.append(f"包含本地数据目录：{relative}")
            elif name in SKIP_DIRECTORIES:
                if name in {".venv", ".venv-whisperx"}:
                    skipped_local_environments.append(relative.as_posix())
            else:
                kept.append(name)
        subdirs[:] = sorted(kept)
        for name in sorted(files):
            path = base / name
            relative = path.relative_to(source)
            if name == ".DS_Store" or name.endswith(".pyc"):
                continue
            if path.is_symlink():
                blockers.append(f"符号链接文件不可发布：{relative}")
                continue
            if not allowed(relative):
                blockers.append(f"未列入公开文件清单：{relative}")
                continue
            if path.stat().st_size > 2_000_000:
                blockers.append(f"公开文件超过 2 MB，需人工核对：{relative}")
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                blockers.append(f"公开文件不是 UTF-8 文本：{relative}")
                continue
            if any(pattern.search(content) for pattern in SECRET_PATTERNS):
                blockers.append(f"疑似密钥：{relative}")
            if PERSONAL_PATH.search(content):
                blockers.append(f"疑似个人绝对路径：{relative}")
            public_files.append(relative.as_posix())
    return {
        "status": "ready" if not blockers else "blocked",
        "mode": "privacy_only" if privacy_only else "release",
        "source": str(source),
        "public_file_count": len(public_files),
        "blockers": sorted(set(blockers)),
        "warnings": warnings
        + (["本地虚拟环境已排除，确认未被 Git 强制添加：" + ", ".join(skipped_local_environments)] if skipped_local_environments else [])
        + ["未扫描未来 Git 提交历史；首次公开前还需检查实际提交集。"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="公开发布前的本地文件与密钥审计")
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--privacy-only", action="store_true", help="只检查文件与隐私边界，供持续集成使用")
    parser.add_argument("--strict", action="store_true", help="存在阻断项时返回非零状态")
    args = parser.parse_args()
    result = audit(args.source, privacy_only=args.privacy_only)
    print(json.dumps(result, ensure_ascii=False))
    return 1 if args.strict and result["blockers"] else 0


if __name__ == "__main__":
    sys.exit(main())
