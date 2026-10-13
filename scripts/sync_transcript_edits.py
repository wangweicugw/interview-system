from __future__ import annotations

import argparse
import json
from difflib import SequenceMatcher
from pathlib import Path

from analysis_state import mark_stale
from check_data_compatibility import require_store_compatible
from common import (
    now_iso,
    parse_edited_transcript,
    read_json,
    render_transcript,
    append_private_text,
    sha256_text,
    write_json,
    write_private_text,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="同步 transcript.md 的人工修订")
    parser.add_argument("--case", required=True, type=Path)
    parser.add_argument("--reason", default="")
    args = parser.parse_args()

    case_path = args.case.expanduser().resolve()
    require_store_compatible(case_path.parent.parent)
    raw_path = case_path / "transcript.raw.json"
    revised_path = case_path / "transcript.revised.json"
    markdown_path = case_path / "transcript.md"
    if not raw_path.is_file() or not markdown_path.is_file():
        raise RuntimeError("场次缺少 transcript.raw.json 或 transcript.md")

    raw = read_json(raw_path)
    current = read_json(revised_path) if revised_path.is_file() else raw
    edited = parse_edited_transcript(markdown_path.read_text(encoding="utf-8"))
    expected_ids = {item["segment_id"] for item in current["segments"]}
    actual_ids = set(edited)
    if actual_ids != expected_ids:
        missing = sorted(expected_ids - actual_ids)
        extra = sorted(actual_ids - expected_ids)
        raise RuntimeError(f"分段 ID 不一致；缺少={missing}，新增={extra}")

    changes: list[dict[str, str | float | bool]] = []
    major_change = False
    updated_segments = [dict(segment) for segment in current["segments"]]
    for segment in updated_segments:
        before = segment.get("text_corrected") or segment.get("text_raw") or ""
        after = edited[segment["segment_id"]].strip()
        if before == after:
            continue
        ratio = SequenceMatcher(None, before, after).ratio()
        major = not after or ratio < 0.7
        major_change = major_change or major
        change = {
            "segment_id": segment["segment_id"],
            "before": before,
            "after": after,
            "similarity": round(ratio, 4),
            "major": major,
        }
        changes.append(change)
        segment["text_corrected"] = after
        corrections = list(segment.get("corrections") or [])
        corrections.append(
            {
                "at": now_iso(),
                "from": before,
                "to": after,
                "reason": args.reason or "文字修订",
            }
        )
        segment["corrections"] = corrections

    if major_change and not args.reason.strip():
        raise RuntimeError("检测到删除或大幅改写；必须通过 --reason 记录原因")

    if not changes:
        print(json.dumps({"status": "unchanged", "case_path": str(case_path)}, ensure_ascii=False))
        return 0

    updated_at = now_iso()
    revised = {
        **current,
        "derived_from": "transcript.raw.json",
        "updated_at": updated_at,
        "segments": updated_segments,
    }
    write_json(revised_path, revised)
    write_private_text(markdown_path, render_transcript(raw["case_id"], updated_segments))

    revision = {
        "revision_id": f"rev-{updated_at}-{sha256_text(json.dumps(changes, ensure_ascii=False))[:8]}",
        "case_id": raw["case_id"],
        "created_at": updated_at,
        "reason": args.reason or "文字修订",
        "changes": changes,
    }
    revision_log = case_path / "transcript-revisions.jsonl"
    append_private_text(revision_log, json.dumps(revision, ensure_ascii=False) + "\n")

    manifest_path = case_path / "manifest.json"
    manifest = read_json(manifest_path)
    manifest["updated_at"] = updated_at
    mark_stale(case_path, manifest)
    write_json(manifest_path, manifest)

    print(
        json.dumps(
            {
                "status": "updated",
                "case_path": str(case_path),
                "changes": len(changes),
                "analysis_status": manifest["analysis_status"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
