#!/usr/bin/env python3
"""Read-only schema audit for a local interview data directory."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from common import SCHEMA_VERSION, schema_compatibility_issue


CORE_CASE_FILES = ("manifest.json", "transcript.raw.json", "speaker-map.json")
OPTIONAL_CASE_FILES = (
    "transcript.revised.json",
    "qa-tree.json",
    "claims.json",
    "scores.json",
    "profile-patch.json",
    "jd-matrix.json",
)


def scan_store(root: Path, *, include_optional: bool = True) -> dict[str, Any]:
    if root.is_symlink():
        return {
            "status": "blocked", "schema_version": SCHEMA_VERSION,
            "scope": "all" if include_optional else "core",
            "root": str(root), "case_count": 0, "scanned_files": 0,
            "issues": [{"path": ".", "reason": "数据目录不能是符号链接"}],
        }
    root = root.expanduser().resolve()
    issues: list[dict[str, str]] = []
    scanned_files = 0
    case_count = 0

    def report(path: Path, reason: str) -> None:
        issues.append({"path": path.relative_to(root).as_posix(), "reason": reason})

    def inspect(path: Path, *, required: bool = False) -> None:
        nonlocal scanned_files
        if path.is_symlink():
            report(path, "符号链接不可作为版本化数据文件")
            return
        if not path.exists():
            if required:
                report(path, "缺少必需文件")
            return
        if not path.is_file():
            report(path, "应为普通文件")
            return
        scanned_files += 1
        try:
            with path.open("r", encoding="utf-8") as handle:
                value = json.load(handle)
        except (OSError, UnicodeError, ValueError):
            report(path, "无法读取有效的 UTF-8 JSON")
            return
        problem = schema_compatibility_issue(path, value)
        if problem:
            report(path, problem)

    inspect(root / "config.json", required=True)
    inspect(root / "profile" / "state.json")
    interviews = root / "interviews"
    if interviews.is_symlink():
        report(interviews, "场次目录不能是符号链接")
    elif interviews.is_dir():
        for case in sorted(interviews.iterdir()):
            if case.name.startswith("."):
                continue
            if case.is_symlink():
                report(case, "场次不能是符号链接")
                continue
            if not case.is_dir():
                continue
            case_count += 1
            for name in CORE_CASE_FILES:
                inspect(case / name, required=True)
            if include_optional:
                for name in OPTIONAL_CASE_FILES:
                    inspect(case / name)
    algorithms = root / "algorithms"
    sessions = algorithms / "sessions"
    if algorithms.is_symlink():
        report(algorithms, "算法学习目录不能是符号链接")
    else:
        if sessions.is_symlink():
            report(sessions, "练习目录不能是符号链接")
        elif sessions.is_dir():
            for session in sorted(sessions.iterdir()):
                if session.is_symlink():
                    report(session, "练习会话不能是符号链接")
                elif session.is_dir():
                    inspect(session / "session.json", required=True)
        inspect(algorithms / "progress.json")
        plans = algorithms / "plans"
        if plans.is_symlink():
            report(plans, "算法周计划目录不能是符号链接")
        elif plans.is_dir():
            for plan in sorted(plans.glob("*.json")):
                inspect(plan)
    inspect(root / "profile" / "next-interview-prep.json")
    inspect(root / "profile" / "target-interview-prep.json")
    target_preps = root / "profile" / "target-interview-preps"
    if target_preps.is_symlink():
        report(target_preps, "目标公司准备目录不能是符号链接")
    elif target_preps.is_dir():
        for prep in sorted(target_preps.glob("*.json")):
            inspect(prep)
    if include_optional:
        for name in (
            "standard-answers.json",
            "question-clusters.json",
            "question-cluster-candidates.json",
            "standard-answer-audit.json",
            "review-plan.json",
            "reference-health.json",
        ):
            inspect(root / "profile" / name)
    return {
        "status": "compatible" if not issues else "blocked",
        "schema_version": SCHEMA_VERSION,
        "scope": "all" if include_optional else "core",
        "root": str(root),
        "case_count": case_count,
        "scanned_files": scanned_files,
        "issues": issues,
    }


def require_store_compatible(root: Path, *, include_optional: bool = False) -> None:
    result = scan_store(root, include_optional=include_optional)
    if result["status"] != "compatible":
        details = "；".join(
            f"{item['path']}：{item['reason']}" for item in result["issues"][:5]
        )
        raise RuntimeError("数据格式兼容性检查未通过：" + details)


def main() -> int:
    parser = argparse.ArgumentParser(description="只读检查本地面试数据的格式版本")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--core-only", action="store_true", help="只检查原始证据和根数据；用于修复旧分析产物前")
    args = parser.parse_args()
    result = scan_store(args.root.expanduser(), include_optional=not args.core_only)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "compatible" else 1


if __name__ == "__main__":
    raise SystemExit(main())
