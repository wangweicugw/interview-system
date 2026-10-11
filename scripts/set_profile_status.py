from __future__ import annotations

import argparse
import json
from pathlib import Path

from check_data_compatibility import require_store_compatible
from common import now_iso
from profile_store import append_event, content_hash, latest_events, load_revisions, rebuild_profile


def main() -> int:
    parser = argparse.ArgumentParser(description="追加长期档案状态修订")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--event-id", required=True)
    parser.add_argument("--status", required=True, choices=("pending", "confirmed", "rejected"))
    parser.add_argument("--reason", required=True)
    args = parser.parse_args()

    root = args.root.expanduser().resolve()
    require_store_compatible(root)
    latest = latest_events(load_revisions(root))
    if args.event_id not in latest:
        raise RuntimeError(f"找不到事件：{args.event_id}")
    previous = latest[args.event_id]
    event = {
        **previous,
        "revision": previous.get("revision", 0) + 1,
        "recorded_at": now_iso(),
        "status": args.status,
        "revision_reason": args.reason,
    }
    event["content_hash"] = content_hash(event)
    append_event(root, event)
    state = rebuild_profile(root)
    print(
        json.dumps(
            {
                "status": "ok",
                "event_id": args.event_id,
                "new_status": args.status,
                "profile_events": state["event_count"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
