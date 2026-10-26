#!/usr/bin/env python3
"""按显式清单构建宿主中立的 Skill 发行包。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from check_public_release import audit, load_private_markers, scan_text


PACKAGE_ROOT = PurePosixPath("interview-system")
EXCLUDED_NAMES = {"__pycache__", ".DS_Store"}
PACKAGE_FILES = (
    "SKILL.md",
    "AGENTS.md",
    ".githooks/pre-push",
    "start-interview-system.command",
    "README.md",
    "LICENSE",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "requirements.lock",
    "requirements-optional.txt",
    "agents/openai.yaml",
    "assets/algorithm-problems.json",
    "assets/demo-case.json",
    "references/algorithm-advanced.md",
    "references/algorithm-coach.md",
    "references/audio-and-html.md",
    "references/company-prep.md",
    "references/fact-checking.md",
    "references/html-presentation.md",
    "references/local-audio-setup.md",
    "references/pdf-ocr.md",
    "references/privacy-and-security.md",
    "references/profile-and-prep.md",
    "references/schemas.md",
    "references/scoring.md",
    "references/workflow.md",
    "scripts/CoachHarness.java",
    "scripts/algorithm_coach.py",
    "scripts/algorithm_history.py",
    "scripts/algorithm_oral.py",
    "scripts/algorithm_progress.py",
    "scripts/algorithm_reports.py",
    "scripts/analysis_state.py",
    "scripts/apply_semantic_speaker_roles.py",
    "scripts/apply_review_actions.py",
    "scripts/audio_setup.py",
    "scripts/audit_question_coverage.py",
    "scripts/audit_standard_answers.py",
    "scripts/backup_store.py",
    "scripts/build_mistake_book.py",
    "scripts/build_next_prep.py",
    "scripts/build_pages_demo.py",
    "scripts/build_release_package.py",
    "scripts/build_review_plan.py",
    "scripts/build_question_tags.py",
    "scripts/check_data_compatibility.py",
    "scripts/check_target_interview_preps.py",
    "scripts/check_practice_answers.py",
    "scripts/check_practice_answer_depth.py",
    "scripts/check_public_release.py",
    "scripts/check_question_clusters.py",
    "scripts/check_question_preferences.py",
    "scripts/check_question_tags.py",
    "scripts/check_reference_freshness.py",
    "scripts/check_standard_answers.py",
    "scripts/coach_runner.py",
    "scripts/common.py",
    "scripts/demo_smoke.py",
    "scripts/doctor.py",
    "scripts/ingest_audio_transcript.py",
    "scripts/ingest_transcript.py",
    "scripts/init_store.py",
    "scripts/java_review.py",
    "scripts/labels_zh.py",
    "scripts/local_answer_server.py",
    "scripts/migrate_legacy_audio.py",
    "scripts/migrate_store.py",
    "scripts/normalize_question_categories.py",
    "scripts/ocr_image.swift",
    "scripts/pdf_ocr.py",
    "scripts/profile_store.py",
    "scripts/privacy_check.py",
    "scripts/question_taxonomy.py",
    "scripts/record_practice_attempt.py",
    "scripts/record_profile_patch.py",
    "scripts/refresh_question_clusters.py",
    "scripts/revise_question_clusters.py",
    "scripts/register_reference_transcript.py",
    "scripts/render_interview_html.py",
    "scripts/set_profile_status.py",
    "scripts/set_speaker_roles.py",
    "scripts/set_standard_answer_status.py",
    "scripts/split_curated_entries.py",
    "scripts/sync_transcript_edits.py",
    "scripts/transcribe_local_audio.py",
    "scripts/update_case.py",
    "scripts/upsert_target_interview_prep.py",
    "scripts/validate_case.py",
)
RESOURCE_ROOTS = {".githooks", "agents", "assets", "references", "scripts"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_inventory(source: Path) -> set[str]:
    files: set[str] = set()
    for root_name in RESOURCE_ROOTS:
        root = source / root_name
        if not root.is_dir() or root.is_symlink():
            raise ValueError(f"发行资源目录不可用：{root_name}")
        for path in root.rglob("*"):
            relative = path.relative_to(source)
            if path.is_symlink():
                raise ValueError(f"发行包不接受符号链接：{relative}")
            if path.is_dir():
                continue
            if path.name in EXCLUDED_NAMES or path.suffix == ".pyc" or any(
                part in EXCLUDED_NAMES for part in relative.parts
            ):
                continue
            files.add(relative.as_posix())
    return files


def _validate_source(source: Path) -> None:
    audit_result = audit(source, privacy_only=False)
    if audit_result["blockers"]:
        summary = "；".join(str(item) for item in audit_result["blockers"][:8])
        raise ValueError("发布前隐私检查未通过：" + summary)
    expected = set(PACKAGE_FILES)
    for relative in PACKAGE_FILES:
        path = source / relative
        if path.is_symlink():
            raise ValueError(f"发行包不接受符号链接：{relative}")
        if not path.is_file():
            raise ValueError(f"缺少发行文件：{relative}")
    unknown = sorted(_source_inventory(source) - expected)
    if unknown:
        raise ValueError("发行资源目录包含未列入清单的文件：" + "、".join(unknown[:8]))


def _manifest(staged_root: Path) -> dict[str, object]:
    files = []
    for relative in PACKAGE_FILES:
        path = staged_root / relative
        files.append({
            "path": relative,
            "sha256": sha256(path),
            "size": path.stat().st_size,
        })
    return {
        "schema_version": 1,
        "package": "interview-system",
        "entrypoint": "SKILL.md",
        "host_specific_rewrite": False,
        "files": files,
    }


def _write_archive(staged_root: Path, archive_path: Path) -> None:
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative in (*PACKAGE_FILES, "MANIFEST.json"):
            archive.write(staged_root / relative, (PACKAGE_ROOT / relative).as_posix())


def _validate_archive(archive_path: Path, package_manifest: dict[str, object]) -> None:
    private_markers, marker_errors = load_private_markers()
    if marker_errors:
        raise ValueError("发行 ZIP 隐私检查未通过：" + "；".join(marker_errors))
    expected_names = {
        (PACKAGE_ROOT / relative).as_posix() for relative in (*PACKAGE_FILES, "MANIFEST.json")
    }
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or set(names) != expected_names:
            raise ValueError("发行 ZIP 文件列表与显式清单不一致")
        for info in archive.infolist():
            mode = info.external_attr >> 16
            if info.is_dir() or mode & 0o170000 == 0o120000:
                raise ValueError(f"发行 ZIP 包含非普通文件：{info.filename}")
        embedded = json.loads(archive.read((PACKAGE_ROOT / "MANIFEST.json").as_posix()))
        if embedded != package_manifest:
            raise ValueError("发行 ZIP 内的 MANIFEST.json 与打包前清单不一致")
        for entry in package_manifest["files"]:
            body = archive.read((PACKAGE_ROOT / str(entry["path"])).as_posix())
            if len(body) != entry["size"] or hashlib.sha256(body).hexdigest() != entry["sha256"]:
                raise ValueError(f"发行 ZIP 文件校验失败：{entry['path']}")
            blockers, _ = scan_text("archive", str(entry["path"]), body, private_markers)
            if blockers:
                raise ValueError("发行 ZIP 隐私检查未通过：" + "；".join(blockers[:8]))


def build(source: Path, output: Path, *, force: bool = False) -> dict[str, object]:
    source = source.expanduser().resolve()
    output = output.expanduser().absolute()
    if output.is_symlink():
        raise ValueError("输出文件不能是符号链接")
    if output == source or source in output.parents:
        raise ValueError("发行包必须输出到源码目录之外")
    if output.exists() and not force:
        raise ValueError("输出文件已存在；确认覆盖后添加 --force")
    _validate_source(source)

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="interview-system-release-",
        dir=output.parent,
    ) as temporary:
        temporary_root = Path(temporary)
        staged_root = temporary_root / PACKAGE_ROOT.name
        staged_root.mkdir(mode=0o700)
        for relative in PACKAGE_FILES:
            destination = staged_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / relative, destination)
        package_manifest = _manifest(staged_root)
        manifest_path = staged_root / "MANIFEST.json"
        manifest_path.write_text(
            json.dumps(package_manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        staged_archive = temporary_root / "interview-system.zip"
        _write_archive(staged_root, staged_archive)
        _validate_archive(staged_archive, package_manifest)
        staged_archive.chmod(0o600)
        os.replace(staged_archive, output)

    return {
        "status": "created",
        "package": str(output),
        "package_sha256": sha256(output),
        "root": PACKAGE_ROOT.as_posix(),
        "entrypoint": "SKILL.md",
        "file_count": len(PACKAGE_FILES) + 1,
        "host_specific_rewrite": False,
        "contains_interview_cases": False,
        "source_privacy_check": "passed",
        "archive_validation": "passed",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="构建不针对特定宿主改写的统一 Skill 发行包")
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--force", action="store_true", help="明确覆盖已有发行包")
    args = parser.parse_args()
    try:
        print(json.dumps(build(args.source, args.output, force=args.force), ensure_ascii=False))
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
