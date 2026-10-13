from __future__ import annotations

import argparse
import json
from pathlib import Path

from analysis_state import mark_stale
from check_data_compatibility import require_store_compatible
from common import (
    now_iso,
    parse_edited_transcript,
    read_json,
    render_transcript,
    write_json,
    write_private_text,
)


ALLOWED_ROLES = {"candidate", "interviewer", "other", "unknown"}


def parse_mapping(values: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise RuntimeError(f"映射格式错误：{value}；应为 speaker_id=role")
        speaker_id, role = (part.strip() for part in value.split("=", 1))
        if role not in ALLOWED_ROLES:
            raise RuntimeError(f"不支持的角色：{role}")
        result[speaker_id] = role
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="确认面试文稿的说话人角色")
    parser.add_argument("--case", required=True, type=Path)
    parser.add_argument("--map", action="append", default=[])
    args = parser.parse_args()

    case_path = args.case.expanduser().resolve()
    require_store_compatible(case_path.parent.parent)
    mapping = parse_mapping(args.map)
    speaker_path = case_path / "speaker-map.json"
    raw_path = case_path / "transcript.raw.json"
    transcript_md = case_path / "transcript.md"
    if not (speaker_path.is_file() and raw_path.is_file() and transcript_md.is_file()):
        raise RuntimeError("场次缺少 speaker-map.json、transcript.raw.json 或 transcript.md")

    speaker_map = read_json(speaker_path)
    known_ids = {item["speaker_id"] for item in speaker_map["speakers"]}
    unknown_ids = set(mapping) - known_ids
    if unknown_ids:
        raise RuntimeError(f"不存在的说话人 ID：{', '.join(sorted(unknown_ids))}")

    raw = read_json(raw_path)
    revised_path = case_path / "transcript.revised.json"
    current_transcript = read_json(revised_path) if revised_path.is_file() else raw
    edited = parse_edited_transcript(transcript_md.read_text(encoding="utf-8"))
    for segment in current_transcript["segments"]:
        current = edited.get(segment["segment_id"])
        expected = segment.get("text_corrected") or segment["text_raw"]
        if current is not None and current != expected:
            raise RuntimeError("逐字稿正文已修改；请先运行 sync_transcript_edits.py，再确认角色")

    for speaker in speaker_map["speakers"]:
        if speaker["speaker_id"] in mapping:
            role = mapping[speaker["speaker_id"]]
            speaker["confirmed_role"] = None if role == "unknown" else role
            speaker["confidence"] = 1.0 if role != "unknown" else 0.0

    roles = {
        item.get("confirmed_role")
        for item in speaker_map["speakers"]
        if item.get("confirmed_role")
    }
    fully_labeled = all(item.get("confirmed_role") for item in speaker_map["speakers"])
    status = (
        "confirmed"
        if fully_labeled and {"candidate", "interviewer"}.issubset(roles)
        else "needs_confirmation"
    )
    speaker_map["status"] = status
    speaker_map["updated_at"] = now_iso()
    write_json(speaker_path, speaker_map)

    role_by_id = {
        item["speaker_id"]: item.get("confirmed_role") or item.get("proposed_role", "unknown")
        for item in speaker_map["speakers"]
    }
    revised = {
        **current_transcript,
        "derived_from": "transcript.raw.json",
        "updated_at": now_iso(),
        "segments": [dict(segment) for segment in current_transcript["segments"]],
    }
    for segment in revised["segments"]:
        role = role_by_id.get(segment["speaker_id"], "unknown")
        segment["role"] = role
        segment["role_confidence"] = 1.0 if role != "unknown" else 0.0
    write_json(case_path / "transcript.revised.json", revised)
    write_private_text(transcript_md, render_transcript(raw["case_id"], revised["segments"]))

    manifest_path = case_path / "manifest.json"
    manifest = read_json(manifest_path)
    manifest["speaker_status"] = status
    mark_stale(case_path, manifest)
    manifest["updated_at"] = now_iso()
    write_json(manifest_path, manifest)

    print(
        json.dumps(
            {
                "status": status,
                "case_path": str(case_path),
                "analysis_status": manifest["analysis_status"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
