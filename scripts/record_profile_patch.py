from __future__ import annotations

import argparse
import json
from pathlib import Path

from analysis_state import current_errors
from check_data_compatibility import require_store_compatible
from common import now_iso, read_json
from profile_store import append_event, content_hash, latest_events, load_revisions, rebuild_profile


def main() -> int:
    parser = argparse.ArgumentParser(description="将单场变更追加到长期档案")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--case", required=True, type=Path)
    args = parser.parse_args()

    root = args.root.expanduser().resolve()
    case_path = args.case.expanduser().resolve()
    require_store_compatible(root, include_optional=True)
    manifest_path = case_path / "manifest.json"
    if not manifest_path.is_file():
        raise RuntimeError("缺少 manifest.json")
    manifest = read_json(manifest_path)
    integrity_errors = current_errors(case_path, manifest)
    if integrity_errors:
        raise RuntimeError(
            "不能写入长期档案：" + "；".join(integrity_errors)
        )
    patch_path = case_path / "profile-patch.json"
    if not patch_path.is_file():
        raise RuntimeError("缺少 profile-patch.json")
    patch = read_json(patch_path)
    case_id = patch.get("case_id")
    if not case_id:
        raise RuntimeError("profile-patch.json 缺少 case_id")
    if case_id != manifest.get("case_id"):
        raise RuntimeError("profile-patch.json 的 case_id 与 manifest.json 不一致")

    latest = latest_events(load_revisions(root))
    added = 0
    skipped = 0
    for change in patch.get("changes", []):
        event_id = f"{case_id}:{change['change_id']}"
        previous = latest.get(event_id)
        event = {
            "event_id": event_id,
            "revision": (previous.get("revision", 0) + 1) if previous else 1,
            "recorded_at": now_iso(),
            "case_id": case_id,
            "change_id": change["change_id"],
            "type": change["type"],
            "key": change["key"],
            "status": change.get("status", "pending"),
            "evidence": change.get("evidence", []),
            "reason": change.get("reason", ""),
            "value": change.get("value"),
        }
        event["content_hash"] = content_hash(event)
        if previous and previous.get("content_hash") == event["content_hash"]:
            skipped += 1
            continue
        append_event(root, event)
        latest[event_id] = event
        added += 1

    state = rebuild_profile(root)
    print(
        json.dumps(
            {
                "status": "ok",
                "added": added,
                "skipped": skipped,
                "profile_events": state["event_count"],
                "profile_path": str(root / "profile" / "state.json"),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
