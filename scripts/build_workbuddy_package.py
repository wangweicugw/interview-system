#!/usr/bin/env python3
"""Build a WorkBuddy-compatible package without including interview data."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path


EXCLUDED_NAMES = {"__pycache__", ".DS_Store"}
RESOURCE_DIRECTORIES = ("references", "scripts", "assets")
ROOT_FILES = ("LICENSE", "requirements.lock", "requirements-optional.txt")


def ignore_unwanted(_: str, names: list[str]) -> set[str]:
    return {name for name in names if name in EXCLUDED_NAMES or name.endswith(".pyc")}


def require(path: Path, description: str) -> None:
    if not path.exists():
        raise ValueError(f"missing {description}: {path}")


def reject_symlinks(path: Path) -> None:
    if path.is_symlink():
        raise ValueError(f"symbolic links cannot be packaged: {path}")
    if path.is_dir():
        for child in path.rglob("*"):
            if child.is_symlink():
                raise ValueError(f"symbolic links cannot be packaged: {child}")


def add_tree(archive: zipfile.ZipFile, root: Path, prefix: Path) -> int:
    count = 0
    for file_path in sorted(root.rglob("*")):
        if not file_path.is_file() or any(part in EXCLUDED_NAMES for part in file_path.parts):
            continue
        if file_path.suffix == ".pyc":
            continue
        archive.write(file_path, prefix / file_path.relative_to(root))
        count += 1
    return count


def build(source: Path, output: Path) -> dict[str, object]:
    source = source.resolve()
    adapter = source / "templates" / "workbuddy-SKILL.md"
    require(adapter, "WorkBuddy adapter template")
    reject_symlinks(adapter)
    for directory in RESOURCE_DIRECTORIES:
        require(source / directory, f"{directory} directory")
        reject_symlinks(source / directory)

    for file_name in ROOT_FILES:
        candidate = source / file_name
        if candidate.exists() or candidate.is_symlink():
            reject_symlinks(candidate)

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="interview-system-workbuddy-") as temp_dir:
        staged_root = Path(temp_dir) / "interview-system"
        staged_root.mkdir()
        shutil.copy2(adapter, staged_root / "SKILL.md")
        for directory in RESOURCE_DIRECTORIES:
            shutil.copytree(
                source / directory,
                staged_root / directory,
                ignore=ignore_unwanted,
            )
        for file_name in ROOT_FILES:
            candidate = source / file_name
            if candidate.exists():
                shutil.copy2(candidate, staged_root / file_name)

        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            file_count = add_tree(archive, staged_root, Path("interview-system"))

    return {
        "status": "created",
        "package": str(output.resolve()),
        "root": "interview-system",
        "file_count": file_count,
        "contains_interview_cases": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a WorkBuddy package from the portable interview-system source."
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Portable skill source directory.",
    )
    parser.add_argument("--output", type=Path, required=True, help="ZIP file to create.")
    args = parser.parse_args()
    try:
        print(json.dumps(build(args.source, args.output), ensure_ascii=False))
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
