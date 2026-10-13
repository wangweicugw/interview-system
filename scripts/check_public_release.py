#!/usr/bin/env python3
"""发布前审计工作区、Git 暂存区与历史；只报告位置，不回显敏感值。"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Iterable


ROOT_FILES = {
    ".gitignore", "SKILL.md", "README.md", "LICENSE", "LICENSE.md",
    "CONTRIBUTING.md", "SECURITY.md", "requirements.lock", "requirements-optional.txt",
}
SKIP_DIRECTORIES = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".venv", ".venv-whisperx"}
PRIVATE_DIRECTORIES = {"private", "interviews", "profile", "events", "logs", "models", "practice"}
SUSPICIOUS_BINARY_SUFFIXES = {
    ".aac", ".docx", ".flac", ".html", ".m4a", ".mp3", ".mp4", ".pdf", ".wav",
}
MAX_TEXT_BYTES = 2_000_000
MAX_HISTORY_BLOBS = 50_000

SECRET_PATTERNS = (
    ("GitHub Token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b")),
    ("GitHub Fine-grained Token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
    ("OpenAI API Key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b")),
    ("Hugging Face Token", re.compile(r"\bhf_[A-Za-z0-9]{20,}\b")),
    ("Slack Token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")),
    ("AWS Access Key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("Google API Key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b")),
    ("私钥", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("Bearer 凭据", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{20,}=*", re.IGNORECASE)),
)
GENERIC_SECRET = re.compile(
    r"(?i)\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret|password)\b"
    r"\s*[:=]\s*['\"]([^'\"\s]{20,})['\"]"
)
PERSONAL_PATH = re.compile(r"/(?:Users|home)/[^/\s'\"]+")
EMAIL = re.compile(r"(?i)(?<![\w.+-])[\w.+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![\w.-])")
PHONE_CN = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
ID_CN = re.compile(
    r"(?<!\d)[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])"
    r"(?:0[1-9]|[12]\d|3[01])\d{3}[0-9Xx](?!\d)"
)


def allowed(relative: Path | PurePosixPath) -> bool:
    parts = relative.parts
    if len(parts) == 1:
        return parts[0] in ROOT_FILES
    if len(parts) == 2:
        parent, name = parts
        return (
            (parent == "agents" and name == "openai.yaml")
            or (parent == "assets" and name in {"algorithm-problems.json", "demo-case.json"})
            or (parent == "references" and name.endswith(".md"))
            or (parent == "scripts" and (name.endswith(".py") or name in {"ocr_image.swift", "CoachHarness.java"}))
            or (parent == "tests" and name.startswith("test_") and name.endswith(".py"))
        )
    if len(parts) == 3:
        return (
            (parts[0] == "tests" and parts[1] == "fixtures" and parts[2] in {"interview.md", "jd.txt", "resume.txt"})
            or (parts[0] == ".github" and parts[1] == "workflows" and parts[2].endswith((".yml", ".yaml")))
        )
    return False


def run_git(source: Path, *args: str, text: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(source), *args],
        check=False,
        capture_output=True,
        text=text,
    )


def git_root(source: Path) -> Path | None:
    result = run_git(source, "rev-parse", "--show-toplevel", text=True)
    if result.returncode != 0 or not result.stdout.strip():
        return None
    root = Path(result.stdout.strip()).resolve()
    return root if root == source else None


def entropy(value: str) -> float:
    counts = Counter(value)
    size = len(value)
    return -sum((count / size) * math.log2(count / size) for count in counts.values())


def line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def scan_text(scope: str, locator: str, content: bytes) -> tuple[list[str], list[str]]:
    blockers: list[str] = []
    warnings: list[str] = []
    if b"\x00" in content:
        blockers.append(f"[{scope}] 非文本公开文件：{locator}")
        return blockers, warnings
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        blockers.append(f"[{scope}] 公开文件不是 UTF-8 文本：{locator}")
        return blockers, warnings

    for category, pattern in SECRET_PATTERNS:
        match = pattern.search(text)
        if match:
            blockers.append(
                f"[{scope}] 疑似密钥或凭据（{category}）：{locator}:第{line_number(text, match.start())}行"
            )
    for match in GENERIC_SECRET.finditer(text):
        candidate = match.group(1)
        if entropy(candidate) >= 3.5 and not re.search(r"(?i)(example|placeholder|replace|your[_-])", candidate):
            blockers.append(
                f"[{scope}] 疑似高熵认证值：{locator}:第{line_number(text, match.start())}行"
            )
            break
    path_match = PERSONAL_PATH.search(text)
    if path_match:
        blockers.append(
            f"[{scope}] 疑似个人绝对路径：{locator}:第{line_number(text, path_match.start())}行"
        )
    for label, pattern in (("邮箱", EMAIL), ("手机号", PHONE_CN), ("身份证号", ID_CN)):
        match = pattern.search(text)
        if label == "邮箱" and match and match.group(0).lower().endswith(
            (".example", ".invalid", ".localhost", ".test")
        ):
            continue
        if match:
            warnings.append(
                f"[{scope}] 疑似个人{label}，需人工复核：{locator}:第{line_number(text, match.start())}行"
            )
    return blockers, warnings


def suspicious_file(scope: str, path: PurePosixPath, locator: str) -> str | None:
    if any(part in PRIVATE_DIRECTORIES for part in path.parts):
        return f"[{scope}] 包含私人数据路径：{locator}"
    if path.suffix.lower() in SUSPICIOUS_BINARY_SUFFIXES:
        return f"[{scope}] 可疑敏感文件需人工审核：{locator}"
    return None


def audit_workspace(source: Path) -> tuple[list[str], list[str], list[str], list[str]]:
    blockers: list[str] = []
    warnings: list[str] = []
    public_files: list[str] = []
    skipped_local_environments: list[str] = []
    for directory, subdirs, files in os.walk(source, followlinks=False):
        base = Path(directory)
        kept = []
        for name in subdirs:
            path = base / name
            relative = path.relative_to(source)
            if path.is_symlink():
                blockers.append(f"[workspace] 符号链接目录不可发布：{relative}")
            elif name in PRIVATE_DIRECTORIES:
                blockers.append(f"[workspace] 包含本地数据目录：{relative}")
            elif name in SKIP_DIRECTORIES:
                if name in {".venv", ".venv-whisperx"}:
                    skipped_local_environments.append(relative.as_posix())
            else:
                kept.append(name)
        subdirs[:] = sorted(kept)
        for name in sorted(files):
            path = base / name
            relative = path.relative_to(source)
            relative_text = relative.as_posix()
            if name == ".DS_Store" or name.endswith(".pyc"):
                continue
            if path.is_symlink():
                blockers.append(f"[workspace] 符号链接文件不可发布：{relative_text}")
                continue
            if not allowed(relative):
                blockers.append(f"[workspace] 未列入公开文件清单：{relative_text}")
                continue
            if path.stat().st_size > MAX_TEXT_BYTES:
                blockers.append(f"[workspace] 公开文件超过 2 MB，需人工核对：{relative_text}")
                continue
            file_blockers, file_warnings = scan_text("workspace", relative_text, path.read_bytes())
            blockers.extend(file_blockers)
            warnings.extend(file_warnings)
            public_files.append(relative_text)
    return blockers, warnings, public_files, skipped_local_environments


def decode_z_paths(raw: bytes) -> list[str]:
    return [item.decode("utf-8", "surrogateescape") for item in raw.split(b"\0") if item]


def audit_staged(source: Path) -> tuple[list[str], list[str], int]:
    blockers: list[str] = []
    warnings: list[str] = []
    listed = run_git(source, "diff", "--cached", "--name-only", "-z", "--diff-filter=ACMR")
    if listed.returncode != 0:
        return blockers, ["[staged] 无法读取 Git 暂存区"], 0
    paths = decode_z_paths(listed.stdout)
    for path_text in paths:
        relative = PurePosixPath(path_text)
        locator = relative.as_posix()
        risk = suspicious_file("staged", relative, locator)
        if risk:
            blockers.append(risk)
        if not allowed(relative):
            blockers.append(f"[staged] 未列入公开文件清单：{locator}")
            continue
        shown = run_git(source, "show", f":{path_text}")
        if shown.returncode != 0:
            blockers.append(f"[staged] 无法读取暂存内容：{locator}")
            continue
        if len(shown.stdout) > MAX_TEXT_BYTES:
            blockers.append(f"[staged] 文件超过 2 MB，需人工核对：{locator}")
            continue
        file_blockers, file_warnings = scan_text("staged", locator, shown.stdout)
        blockers.extend(file_blockers)
        warnings.extend(file_warnings)
    return blockers, warnings, len(paths)


def iter_history_blobs(source: Path) -> Iterable[tuple[str, str, str]]:
    revisions = run_git(source, "rev-list", "--all", text=True)
    if revisions.returncode != 0:
        return
    seen: set[tuple[str, str]] = set()
    count = 0
    for revision in revisions.stdout.splitlines():
        tree = run_git(source, "ls-tree", "-r", "-z", "--full-tree", revision)
        if tree.returncode != 0:
            continue
        for entry in tree.stdout.split(b"\0"):
            if not entry or b"\t" not in entry:
                continue
            metadata, raw_path = entry.split(b"\t", 1)
            fields = metadata.split()
            if len(fields) != 3 or fields[1] != b"blob":
                continue
            object_id = fields[2].decode("ascii")
            path = raw_path.decode("utf-8", "surrogateescape")
            key = (object_id, path)
            if key in seen:
                continue
            seen.add(key)
            count += 1
            if count > MAX_HISTORY_BLOBS:
                raise RuntimeError(f"Git 历史对象超过 {MAX_HISTORY_BLOBS}，请使用专用密钥扫描工具复核")
            yield revision, object_id, path


def audit_history(source: Path) -> tuple[list[str], list[str], int]:
    blockers: list[str] = []
    warnings: list[str] = []
    scanned = 0
    try:
        for revision, object_id, path_text in iter_history_blobs(source):
            scanned += 1
            relative = PurePosixPath(path_text)
            locator = f"{relative.as_posix()}@{revision[:12]}"
            risk = suspicious_file("history", relative, locator)
            if risk:
                blockers.append(risk)
            size_result = run_git(source, "cat-file", "-s", object_id, text=True)
            if size_result.returncode != 0:
                blockers.append(f"[history] 无法读取 Git 对象：{locator}")
                continue
            try:
                size = int(size_result.stdout.strip())
            except ValueError:
                blockers.append(f"[history] Git 对象大小无效：{locator}")
                continue
            if size > MAX_TEXT_BYTES:
                blockers.append(f"[history] 文件超过 2 MB，需人工核对：{locator}")
                continue
            body = run_git(source, "cat-file", "blob", object_id)
            if body.returncode != 0:
                blockers.append(f"[history] 无法读取 Git 对象：{locator}")
                continue
            file_blockers, file_warnings = scan_text("history", locator, body.stdout)
            blockers.extend(file_blockers)
            warnings.extend(file_warnings)
    except RuntimeError as exc:
        blockers.append(f"[history] {exc}")
    return blockers, warnings, scanned


def audit(
    source: Path,
    *,
    privacy_only: bool = False,
    scopes: Iterable[str] = ("workspace", "staged", "history"),
) -> dict[str, object]:
    source = source.expanduser().resolve()
    selected = tuple(dict.fromkeys(scopes))
    invalid = set(selected) - {"workspace", "staged", "history"}
    if invalid:
        raise ValueError("未知审计范围：" + ", ".join(sorted(invalid)))
    blockers: list[str] = []
    warnings: list[str] = []
    public_files: list[str] = []
    coverage: dict[str, object] = {}

    if not privacy_only:
        if not (source / "SKILL.md").is_file():
            blockers.append("[workspace] 缺少 SKILL.md")
        if not (source / "README.md").is_file():
            blockers.append("[workspace] 缺少 GitHub 使用说明 README.md")
        if not (source / "LICENSE").is_file() and not (source / "LICENSE.md").is_file():
            blockers.append("[workspace] 缺少开源许可证 LICENSE；需由维护者选择")
        if not (source / ".github/workflows/tests.yml").is_file():
            warnings.append("[workspace] 缺少自动测试工作流")

    if "workspace" in selected:
        found, cautions, public_files, skipped = audit_workspace(source)
        blockers.extend(found)
        warnings.extend(cautions)
        coverage["workspace"] = {"status": "scanned", "file_count": len(public_files)}
        if skipped:
            warnings.append("[workspace] 本地虚拟环境已排除，确认未被 Git 强制添加：" + ", ".join(skipped))

    repository = git_root(source)
    for scope in ("staged", "history"):
        if scope not in selected:
            continue
        if repository is None:
            coverage[scope] = {"status": "unavailable", "reason": "source 不是 Git 根目录"}
            warnings.append(f"[{scope}] 未扫描：源目录不是 Git 根目录")
            continue
        if scope == "staged":
            found, cautions, count = audit_staged(source)
            coverage[scope] = {"status": "scanned", "file_count": count}
        else:
            found, cautions, count = audit_history(source)
            coverage[scope] = {"status": "scanned", "blob_path_count": count}
        blockers.extend(found)
        warnings.extend(cautions)

    return {
        "status": "ready" if not blockers else "blocked",
        "mode": "privacy_only" if privacy_only else "release",
        "source": str(source),
        "scopes": list(selected),
        "coverage": coverage,
        "public_file_count": len(public_files),
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="发布前的工作区、暂存区与 Git 历史隐私审计")
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--privacy-only", action="store_true", help="跳过许可证等发布元数据检查")
    parser.add_argument(
        "--scope", action="append", choices=("workspace", "staged", "history"),
        help="可重复指定；默认扫描工作区、暂存区和历史",
    )
    parser.add_argument("--strict", action="store_true", help="存在阻断项时返回非零状态")
    args = parser.parse_args()
    result = audit(
        args.source,
        privacy_only=args.privacy_only,
        scopes=args.scope or ("workspace", "staged", "history"),
    )
    print(json.dumps(result, ensure_ascii=False))
    return 1 if args.strict and result["blockers"] else 0


if __name__ == "__main__":
    sys.exit(main())
