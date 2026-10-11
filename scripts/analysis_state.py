"""Track which evidence and analysis files were validated together."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from common import sha256_file


ANALYSIS_FILES = (
    "qa-tree.json",
    "claims.json",
    "scores.json",
    "review.md",
    "profile-patch.json",
)


def output_files(case_path: Path) -> tuple[str, ...]:
    return ANALYSIS_FILES + (("jd-matrix.json",) if (case_path / "jd.md").is_file() else ())


def file_hashes(case_path: Path, names: tuple[str, ...]) -> dict[str, str]:
    return {
        name: sha256_file(case_path / name)
        for name in names
        if (case_path / name).is_file()
    }


def fingerprint(case_path: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    transcript = "transcript.revised.json" if (case_path / "transcript.revised.json").is_file() else "transcript.raw.json"
    inputs = file_hashes(
        case_path,
        ("transcript.raw.json", transcript, "speaker-map.json", "jd.md", "resume.md"),
    )
    result = {
        "inputs": inputs,
        "context": {key: manifest.get(key) for key in ("company", "role", "round")},
        "outputs": file_hashes(case_path, output_files(case_path)),
    }
    if manifest.get("legacy_audio_migration"):
        result["supplemental_audio"] = {
            "audio": manifest.get("inputs", {}).get("audio"),
            "transcription": manifest.get("transcription"),
            "migration": manifest.get("legacy_audio_migration"),
        }
    return result


def mark_stale(case_path: Path, manifest: dict[str, Any]) -> None:
    existing = file_hashes(case_path, ANALYSIS_FILES + ("jd-matrix.json",))
    if not existing:
        manifest["analysis_status"] = "not_started"
        return
    manifest["analysis_status"] = "stale"
    manifest["analysis_invalidated_at_ns"] = time.time_ns()
    manifest["analysis_stale_outputs"] = existing


def refresh_errors(case_path: Path, manifest: dict[str, Any]) -> list[str]:
    if manifest.get("analysis_status") != "stale":
        return []
    invalidated_at = manifest.get("analysis_invalidated_at_ns")
    if not isinstance(invalidated_at, int):
        return ["旧场次缺少失效时间；请重新标记分析过期后重算"]
    names = output_files(case_path)
    unchanged_time = [
        name for name in names
        if not (case_path / name).is_file()
        or (case_path / name).stat().st_mtime_ns <= invalidated_at
    ]
    errors = []
    if unchanged_time:
        errors.append("以下分析文件在失效后未重写：" + "、".join(unchanged_time))
    old = manifest.get("analysis_stale_outputs") or {}
    new = file_hashes(case_path, names)
    if old and all(new.get(name) == old.get(name) for name in names):
        errors.append("分析文件内容均未变化，不能直接恢复 current")
    return errors


def current_errors(case_path: Path, manifest: dict[str, Any]) -> list[str]:
    if manifest.get("analysis_status") != "current":
        return ["分析产物未处于 current 状态"]
    saved = manifest.get("analysis_fingerprint")
    if saved is not None and saved != fingerprint(case_path, manifest):
        return ["已校验的证据或分析文件发生变化；请重算并重新校验"]
    return []
