#!/usr/bin/env python3
"""Run a complete text-only workflow on explicitly fictional interview data."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


SKILL_DIR = Path(__file__).resolve().parents[1]
FIXTURE = SKILL_DIR / "assets" / "demo-case.json"


def run_script(name: str, *arguments: str) -> dict[str, Any]:
    result = subprocess.run(
        [sys.executable, str(SKILL_DIR / "scripts" / name), *arguments],
        text=True,
        capture_output=True,
        check=False,
        timeout=120,
    )
    if result.returncode:
        raise RuntimeError(f"{name} 失败：{result.stderr.strip() or result.stdout.strip()}")
    try:
        return json.loads(result.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as error:
        raise RuntimeError(f"{name} 未返回有效 JSON") from error


def run_demo(root_argument: Path) -> dict[str, Any]:
    if root_argument.is_symlink():
        raise ValueError("演示目录不能是符号链接")
    root = root_argument.expanduser().resolve()
    if root == SKILL_DIR or root.is_relative_to(SKILL_DIR) or SKILL_DIR.is_relative_to(root):
        raise ValueError("演示目录必须位于 Skill 源码目录之外")
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise ValueError("演示目录必须不存在或为空，避免覆盖已有面试资料")

    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="interview-demo-input-") as temporary:
        input_dir = Path(temporary)
        paths = {}
        for name, suffix in (("transcript", ".md"), ("jd", ".txt"), ("resume", ".txt")):
            path = input_dir / f"{name}{suffix}"
            path.write_text(fixture[name], encoding="utf-8")
            paths[name] = path

        run_script("init_store.py", "--root", str(root))
        imported = run_script(
            "ingest_transcript.py",
            "--root", str(root),
            "--transcript", str(paths["transcript"]),
            "--jd", str(paths["jd"]),
            "--resume", str(paths["resume"]),
            "--company", "虚构公司",
            "--role", "后端工程师",
            "--round", "一面",
            "--date", "2026-01-01",
        )
        case_path = Path(imported["case_path"])
        if imported["status"] != "created":
            raise RuntimeError("演示场次未能创建")
        run_script("validate_case.py", "--case", str(case_path), "--stage", "ingest")

        for filename, payload in fixture["analysis"].items():
            artifact = dict(payload)
            artifact["case_id"] = imported["case_id"]
            (case_path / filename).write_text(
                json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        (case_path / "review.md").write_text(fixture["review"], encoding="utf-8")
        run_script("validate_case.py", "--case", str(case_path), "--stage", "analysis")
        run_script("record_profile_patch.py", "--root", str(root), "--case", str(case_path))
        run_script("render_interview_html.py", "--root", str(root), "--all")
        run_script("validate_case.py", "--case", str(case_path), "--stage", "presentation")

    case_html = case_path / "interview-review.html"
    history_html = root / "interview-history.html"
    question_bank_html = root / "interview-question-bank.html"
    if not case_html.is_file() or not history_html.is_file() or not question_bank_html.is_file():
        raise RuntimeError("演示 HTML 未生成")
    if "示例岗位要求" not in case_html.read_text(encoding="utf-8"):
        raise RuntimeError("演示报告缺少 JD 原文")
    return {
        "status": "passed",
        "scope": "synthetic_text_only",
        "root": str(root),
        "case_html": str(case_html),
        "history_html": str(history_html),
        "question_bank_html": str(question_bank_html),
        "limits": "只验证虚构文字稿流程；不验证真实分析质量、语音模型或扫描件 OCR。",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="用虚构资料验收面试文稿闭环")
    parser.add_argument("--root", required=True, type=Path, help="不存在或为空的演示输出目录")
    args = parser.parse_args()
    try:
        result = run_demo(args.root)
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
