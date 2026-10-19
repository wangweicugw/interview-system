from __future__ import annotations

import argparse
import json
from pathlib import Path

from analysis_state import mark_stale
from check_data_compatibility import require_store_compatible
from common import append_private_text, now_iso, read_json, sha256_text, write_json
from labels_zh import round_label
from profile_store import (
    append_event,
    content_hash,
    latest_events,
    load_revisions,
    rebuild_profile,
)


RESULTS = {"通过", "未通过", "进入下一轮", "无结果"}


def record_outcome(
    case_path: Path,
    manifest: dict[str, object],
    reason: str,
    changed: dict[str, dict[str, object]],
) -> dict[str, object]:
    if not {"result", "hr_feedback"}.intersection(changed):
        return {"status": "skipped"}

    root = case_path.parent.parent
    case_id = str(manifest["case_id"])
    latest = latest_events(load_revisions(root))
    event_id = f"{case_id}:outcome"
    previous = latest.get(event_id)
    result = str(manifest.get("result") or "无结果")
    feedback = manifest.get("hr_feedback")
    status = "confirmed" if result != "无结果" or feedback else "pending"
    event = {
        "event_id": event_id,
        "revision": (previous.get("revision", 0) + 1) if previous else 1,
        "recorded_at": now_iso(),
        "case_id": case_id,
        "change_id": "outcome",
        "type": "outcome",
        "key": f"interview/{case_id}/outcome",
        "status": status,
        "evidence": [],
        "reason": reason,
        "value": {"result": result, "hr_feedback": feedback},
    }
    event["content_hash"] = content_hash(event)
    if previous and previous.get("content_hash") == event["content_hash"]:
        return {
            "status": "unchanged",
            "revision": previous.get("revision", 1),
        }
    append_event(root, event)
    state = rebuild_profile(root)
    return {
        "status": "updated",
        "revision": event["revision"],
        "profile_events": state["event_count"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="更新面试场次信息并保留修订历史")
    parser.add_argument("--case", required=True, type=Path)
    parser.add_argument("--result", choices=sorted(RESULTS))
    parser.add_argument("--hr-feedback")
    parser.add_argument("--company")
    parser.add_argument("--role")
    parser.add_argument("--round", dest="round_name")
    parser.add_argument("--reason", required=True)
    args = parser.parse_args()

    case_path = args.case.expanduser().resolve()
    require_store_compatible(case_path.parent.parent)
    manifest_path = case_path / "manifest.json"
    if not manifest_path.is_file():
        raise RuntimeError("缺少 manifest.json")
    manifest = read_json(manifest_path)
    before = dict(manifest)
    updates = {
        "result": args.result,
        "hr_feedback": args.hr_feedback,
        "company": args.company,
        "role": args.role,
        "round": round_label(args.round_name) if args.round_name is not None else None,
    }
    changed: dict[str, dict[str, object]] = {}
    for field, value in updates.items():
        if value is None or value == manifest.get(field):
            continue
        changed[field] = {"before": manifest.get(field), "after": value}
        manifest[field] = value

    if not changed:
        print(json.dumps({"status": "unchanged", "case_path": str(case_path)}, ensure_ascii=False))
        return 0

    updated_at = now_iso()
    manifest["updated_at"] = updated_at
    if {"company", "role", "round"}.intersection(changed):
        mark_stale(case_path, manifest)
    write_json(manifest_path, manifest)
    revision = {
        "revision_id": f"meta-{updated_at}-{sha256_text(json.dumps(changed, ensure_ascii=False))[:8]}",
        "case_id": manifest["case_id"],
        "created_at": updated_at,
        "reason": args.reason,
        "changes": changed,
        "previous_manifest_sha256": sha256_text(json.dumps(before, ensure_ascii=False, sort_keys=True)),
    }
    append_private_text(
        case_path / "manifest-revisions.jsonl",
        json.dumps(revision, ensure_ascii=False) + "\n",
    )
    outcome = record_outcome(case_path, manifest, args.reason, changed)
    print(
        json.dumps(
            {
                "status": "updated",
                "case_path": str(case_path),
                "fields": sorted(changed),
                "analysis_status": manifest.get("analysis_status"),
                "outcome": outcome,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
