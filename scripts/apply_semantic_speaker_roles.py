#!/usr/bin/env python3
"""Apply an explicit segment-level semantic role assignment without altering raw evidence."""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

from analysis_state import mark_stale
from check_data_compatibility import require_store_compatible
from common import (
    append_private_text,
    now_iso,
    parse_edited_transcript,
    read_json,
    render_transcript,
    write_json,
    write_private_text,
)


ROLES = {"candidate", "interviewer", "other", "unknown"}
LABELS = {
    "candidate": ("spk-candidate", "候选人"),
    "interviewer": ("spk-interviewer", "面试官"),
    "other": ("spk-other", "其他"),
    "unknown": ("unknown", "未可靠归属"),
}


def load_assignment(path: Path, segment_ids: set[str]) -> tuple[dict[str, str], dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise RuntimeError("assignment.schema_version 必须为 1")
    rows = payload.get("segments")
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("assignment.segments 必须为非空数组")
    mapping: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise RuntimeError("assignment.segments 每项必须为对象")
        segment_id = str(row.get("segment_id", "")).strip()
        role = str(row.get("role", "")).strip()
        if segment_id not in segment_ids:
            raise RuntimeError(f"assignment 引用了未知片段：{segment_id}")
        if segment_id in mapping:
            raise RuntimeError(f"assignment 片段重复：{segment_id}")
        if role not in ROLES:
            raise RuntimeError(f"assignment 角色无效：{segment_id}={role}")
        mapping[segment_id] = role
    return mapping, payload


def main() -> int:
    parser = argparse.ArgumentParser(description="应用逐段语义说话人角色映射")
    parser.add_argument("--case", required=True, type=Path)
    parser.add_argument("--assignment", required=True, type=Path)
    args = parser.parse_args()

    os.umask(0o077)
    case = args.case.expanduser().resolve()
    require_store_compatible(case.parent.parent)
    raw = read_json(case / "transcript.raw.json")
    current_path = case / "transcript.revised.json"
    current = read_json(current_path) if current_path.is_file() else raw
    edited = parse_edited_transcript((case / "transcript.md").read_text(encoding="utf-8"))
    for segment in current["segments"]:
        expected = segment.get("text_corrected") or segment["text_raw"]
        if edited.get(segment["segment_id"], expected) != expected:
            raise RuntimeError("逐字稿正文已修改；请先运行 sync_transcript_edits.py")

    segment_ids = {item["segment_id"] for item in current["segments"]}
    mapping, payload = load_assignment(args.assignment.expanduser().resolve(), segment_ids)
    revised_segments = []
    for segment in current["segments"]:
        item = dict(segment)
        role = mapping.get(item["segment_id"], "unknown")
        speaker_id, label = LABELS[role]
        item.update(
            speaker_id=speaker_id,
            speaker_label=label,
            role=role,
            role_confidence=None,
        )
        revised_segments.append(item)

    counts = Counter(item["role"] for item in revised_segments)
    if not counts["candidate"] or not counts["interviewer"]:
        raise RuntimeError("语义映射必须同时包含 candidate 和 interviewer")
    now = now_iso()
    revised = {
        **current,
        "derived_from": "transcript.raw.json",
        "updated_at": now,
        "speaker_assignment_method": "semantic_role_assignment",
        "segments": revised_segments,
    }
    write_json(current_path, revised)
    write_private_text(case / "transcript.md", render_transcript(raw["case_id"], revised_segments))

    raw_speaker_ids = {
        item.get("speaker_id") for item in raw["segments"] if item.get("speaker_id")
    }
    speakers = [
        {
            "speaker_id": speaker_id,
            "label": "原始转写标签",
            "proposed_role": "unknown",
            "confirmed_role": None,
            "confidence": None,
            "examples": [],
        }
        for speaker_id in sorted(raw_speaker_ids)
    ]
    for role, (speaker_id, label) in LABELS.items():
        examples = [
            {"segment_id": item["segment_id"], "text": item["text_raw"][:160]}
            for item in revised_segments
            if item["role"] == role
        ][:2]
        if examples and speaker_id not in raw_speaker_ids:
            speakers.append(
                {
                    "speaker_id": speaker_id,
                    "label": label,
                    "proposed_role": role,
                    "confirmed_role": None if role == "unknown" else role,
                    "confidence": None,
                    "examples": examples,
                }
            )

    status = "confirmed" if not counts["unknown"] else "semantic_assignment_confirmed"
    speaker_map = {
        "schema_version": 1,
        "case_id": raw["case_id"],
        "status": status,
        "method": "semantic_role_assignment",
        "confidence": None,
        "updated_at": now,
        "confirmation_note": str(payload.get("confirmation_note", "")).strip(),
        "confirmed_anchors": payload.get("anchors", []),
        "speakers": speakers,
        "segment_role_counts": dict(counts),
        "limitations": payload.get("limitations", []),
    }
    write_json(case / "speaker-map.json", speaker_map)

    manifest_path = case / "manifest.json"
    manifest = read_json(manifest_path)
    manifest["speaker_status"] = status
    if isinstance(manifest.get("transcription"), dict):
        manifest["transcription"]["speaker_method"] = "semantic_role_assignment"
        manifest["transcription"]["speaker_confidence"] = None
    mark_stale(case, manifest)
    manifest["updated_at"] = now
    write_json(manifest_path, manifest)
    append_private_text(
        case / "speaker-role-revisions.jsonl",
        json.dumps(
            {
                "recorded_at": now,
                "action": "semantic_role_assignment",
                "assignment_source": str(args.assignment.expanduser().resolve()),
                "anchors": payload.get("anchors", []),
                "counts": dict(counts),
            },
            ensure_ascii=False,
        )
        + "\n",
    )
    print(
        json.dumps(
            {"status": status, "case_path": str(case), "counts": dict(counts)},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
