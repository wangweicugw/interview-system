#!/usr/bin/env python3
"""Audit stored public evidence links without sending interview text."""

from __future__ import annotations

import argparse
import json
import socket
import urllib.error
import urllib.request
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from check_data_compatibility import require_store_compatible
from common import now_iso, write_json


def _read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _sources(root: Path) -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for path in (root / "interviews").glob("*/claims.json"):
        payload = _read(path, {})
        for claim in payload.get("claims", []):
            if not isinstance(claim, dict):
                continue
            for source in claim.get("sources", []):
                if not isinstance(source, dict):
                    continue
                url = str(source.get("url") or "")
                if urlparse(url).scheme not in {"http", "https"}:
                    continue
                item = found.setdefault(url, {"url": url, "titles": set(), "accessed_dates": set(), "references": []})
                item["titles"].add(str(source.get("title") or "技术资料"))
                if source.get("accessed_at"):
                    item["accessed_dates"].add(str(source["accessed_at"]))
                item["references"].append({"case_id": payload.get("case_id"), "claim_id": claim.get("claim_id")})
    standards = _read(root / "profile" / "standard-answers.json", {})
    for answer in standards.get("answers", []):
        if not isinstance(answer, dict) or answer.get("status") == "rejected":
            continue
        for source in answer.get("sources", []):
            if not isinstance(source, dict):
                continue
            url = str(source.get("url") or "")
            if urlparse(url).scheme not in {"http", "https"}:
                continue
            item = found.setdefault(url, {"url": url, "titles": set(), "accessed_dates": set(), "references": []})
            item["titles"].add(str(source.get("title") or "技术资料"))
            if source.get("accessed_at"):
                item["accessed_dates"].add(str(source["accessed_at"]))
            item["references"].append({"answer_id": answer.get("answer_id")})
    return [
        {**item, "titles": sorted(item["titles"]), "accessed_dates": sorted(item["accessed_dates"])}
        for item in found.values()
    ]


def _age_days(values: list[str]) -> int | None:
    parsed = []
    for value in values:
        try:
            parsed.append(date.fromisoformat(value[:10]))
        except ValueError:
            continue
    return (date.today() - max(parsed)).days if parsed else None


def _request(url: str, timeout: int) -> tuple[str, int | None, str | None]:
    headers = {"User-Agent": "InterviewSystemLinkAudit/1.0"}
    request = urllib.request.Request(url, method="HEAD", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return "reachable", int(response.status), response.geturl()
    except urllib.error.HTTPError as error:
        if error.code not in {403, 405}:
            return "http_error", error.code, None
    except (urllib.error.URLError, socket.timeout, TimeoutError) as error:
        return "network_error", None, str(error.reason if isinstance(error, urllib.error.URLError) else error)
    request = urllib.request.Request(url, method="GET", headers={**headers, "Range": "bytes=0-0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return "reachable", int(response.status), response.geturl()
    except urllib.error.HTTPError as error:
        return "http_error", error.code, None
    except (urllib.error.URLError, socket.timeout, TimeoutError) as error:
        return "network_error", None, str(error.reason if isinstance(error, urllib.error.URLError) else error)


def check(root: Path, *, online: bool, timeout: int) -> dict[str, Any]:
    require_store_compatible(root)
    sources = _sources(root)
    results = []
    for source in sources:
        age = _age_days(source["accessed_dates"])
        if online:
            status, code, detail = _request(source["url"], timeout)
        else:
            status, code, detail = "not_checked", None, "未启用 --online；没有发出网络请求"
        results.append({
            **source,
            "age_days": age,
            "version_sensitive_review_due": age is None or age > 180,
            "link_status": status,
            "http_status": code,
            "resolved_url_or_error": detail,
        })
    payload = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "online_checked": online,
        "privacy_note": "联网检查只发送已保存的公开 URL，不发送问题、回答、简历或公司私密内容。",
        "source_count": len(results),
        "reachable": sum(item["link_status"] == "reachable" for item in results),
        "review_due": sum(item["version_sensitive_review_due"] for item in results),
        "sources": results,
    }
    output = root / "profile" / "reference-health.json"
    write_json(output, payload)
    return {"status": "ok", "output": str(output), "sources": len(results), "online_checked": online,
            "reachable": payload["reachable"], "review_due": payload["review_due"]}


def main() -> int:
    parser = argparse.ArgumentParser(description="检查已保存官方资料的时效与可访问性")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--online", action="store_true", help="实际访问已保存 URL；默认只做离线时效审计")
    parser.add_argument("--timeout", type=int, default=8)
    args = parser.parse_args()
    if not 1 <= args.timeout <= 30:
        raise RuntimeError("--timeout 须在 1–30 秒之间")
    print(json.dumps(check(args.root.expanduser().resolve(), online=args.online, timeout=args.timeout), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False))
        raise SystemExit(1)
