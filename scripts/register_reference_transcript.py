#!/usr/bin/env python3
"""Register an immutable user-provided reference transcript snapshot."""

from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path
from typing import Any

from check_data_compatibility import require_store_compatible
from common import append_private_text, now_iso, read_json, sha256_file, write_json


COVERAGE_VALUES = {"none", "partial", "full"}


def load_manifest(case_path: Path) -> tuple[Path, dict[str, Any]]:
    manifest_path = case_path / "manifest.json"
    if not manifest_path.is_file():
        raise RuntimeError(f"case 缺少 manifest.json：{case_path}")
    try:
        manifest = read_json(manifest_path)
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"manifest.json 无法读取：{exc}") from exc
    if not str(manifest.get("case_id", "")).strip():
        raise RuntimeError("manifest.json 缺少 case_id")
    return manifest_path, manifest


def existing_references(inputs: dict[str, Any]) -> list[dict[str, Any]]:
    references = inputs.get("reference_transcripts")
    if isinstance(references, list):
        return [item for item in references if isinstance(item, dict)]
    legacy = inputs.get("reference_transcript")
    if isinstance(legacy, dict):
        return [legacy]
    return []


def append_revision(case_path: Path, entry: dict[str, Any]) -> None:
    revisions = case_path / "reference-transcript-revisions.jsonl"
    append_private_text(revisions, json.dumps(entry, ensure_ascii=False) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="登记参考文字稿快照，不覆盖原始转写或补造时间戳"
    )
    parser.add_argument("--case", required=True, type=Path)
    parser.add_argument("--reference", required=True, type=Path)
    parser.add_argument(
        "--timestamp-coverage", choices=sorted(COVERAGE_VALUES), default="none"
    )
    parser.add_argument(
        "--speaker-labels", choices=sorted(COVERAGE_VALUES), default="none"
    )
    parser.add_argument("--source-label", default="user-provided")
    parser.add_argument(
        "--use-note",
        default="只用于术语与语义交叉核对，不替代原始带时间戳转写",
    )
    args = parser.parse_args()

    os.umask(0o077)
    case_path = args.case.expanduser().resolve()
    require_store_compatible(case_path.parent.parent)
    reference = args.reference.expanduser().resolve()
    if not reference.is_file():
        raise RuntimeError(f"参考文稿不存在：{reference}")
    if reference.stat().st_size == 0:
        raise RuntimeError("参考文稿为空")
    private = case_path / "private"
    if not private.is_dir():
        raise RuntimeError(f"case 缺少 private/ 目录：{case_path}")

    manifest_path, manifest = load_manifest(case_path)
    inputs = manifest.setdefault("inputs", {})
    if not isinstance(inputs, dict):
        raise RuntimeError("manifest.inputs 必须为对象")
    digest = sha256_file(reference)
    references = existing_references(inputs)
    for item in references:
        if item.get("sha256") == digest:
            print(
                json.dumps(
                    {
                        "status": "duplicate",
                        "case_id": manifest["case_id"],
                        "reference": item.get("original"),
                        "sha256": digest,
                    },
                    ensure_ascii=False,
                )
            )
            return 0

    suffix = reference.suffix.lower() or ".txt"
    target = private / f"reference-transcript-{digest[:12]}{suffix}"
    shutil.copy2(reference, target)
    registered_at = now_iso()
    record = {
        "original": str(target.relative_to(case_path)),
        "sha256": digest,
        "registered_at": registered_at,
        "source_label": args.source_label.strip() or "user-provided",
        "timestamp_coverage": args.timestamp_coverage,
        "speaker_labels": args.speaker_labels,
        "use": args.use_note.strip() or "仅作参考，不替代原始转写",
    }
    references.append(record)
    inputs["reference_transcripts"] = references
    inputs["reference_transcript"] = record
    manifest["updated_at"] = registered_at
    write_json(manifest_path, manifest)
    append_revision(
        case_path,
        {
            "recorded_at": registered_at,
            "action": "registered",
            "case_id": manifest["case_id"],
            "reference": record,
        },
    )
    print(
        json.dumps(
            {
                "status": "registered",
                "case_id": manifest["case_id"],
                "reference": str(target),
                "sha256": digest,
                "count": len(references),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
