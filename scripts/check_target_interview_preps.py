#!/usr/bin/env python3
"""Validate per-company target interview preparation payloads."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from check_data_compatibility import require_store_compatible
from common import read_json


STATUSES = {"pending", "confirmed", "rejected"}
SOURCE_TYPES = {"招聘方参考题", "历史原题", "用户记忆转述", "延伸练习题"}
PRIORITIES = {"P0", "P1", "P2"}
JD_SOURCE_TYPES = {"用户提供文本", "用户截图转写", "本地 OCR", "公开招聘页"}
SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def valid_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def question_index(root: Path) -> set[tuple[str, str]]:
    result: set[tuple[str, str]] = set()
    for path in (root / "interviews").glob("*/qa-tree.json"):
        try:
            payload = read_json(path)
        except (OSError, ValueError):
            continue
        case_id = str(payload.get("case_id", ""))
        for item in payload.get("questions", []):
            if isinstance(item, dict) and item.get("question_id"):
                result.add((case_id, str(item["question_id"])))
    return result


def validate_sources(value: Any, label: str, errors: list[str]) -> None:
    if value is None:
        return
    if not isinstance(value, list):
        errors.append(f"{label} 必须为数组")
        return
    for index, source in enumerate(value, start=1):
        if not isinstance(source, dict):
            errors.append(f"{label} 第 {index} 项必须为对象")
            continue
        if not str(source.get("title", "")).strip():
            errors.append(f"{label} 第 {index} 项缺少 title")
        if not valid_url(source.get("url")):
            errors.append(f"{label} 第 {index} 项 URL 必须为 HTTP(S)")


def validate_payload(
    payload: Any,
    *,
    known_questions: set[tuple[str, str]] | None = None,
    require_historical_evidence: bool = False,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["准备稿必须为 JSON 对象"]
    if payload.get("schema_version") != 1:
        errors.append("schema_version 必须为 1")
    if payload.get("status") not in STATUSES:
        errors.append("status 必须为 pending、confirmed 或 rejected")
    target = payload.get("target")
    if not isinstance(target, dict):
        errors.append("target 必须为对象")
    else:
        for field in ("company", "role", "summary"):
            if not str(target.get(field, "")).strip():
                errors.append(f"target.{field} 不能为空")
    jd = payload.get("jd")
    if jd is not None:
        if not isinstance(jd, dict):
            errors.append("jd 必须为对象")
        else:
            if not isinstance(jd.get("text"), str) or not jd["text"].strip():
                errors.append("jd.text 必须为非空字符串")
            if jd.get("source_type") not in JD_SOURCE_TYPES:
                errors.append("jd.source_type 无效")
            if "source_note" in jd and not isinstance(jd["source_note"], str):
                errors.append("jd.source_note 必须为字符串")
            source_url = jd.get("source_url")
            if source_url is not None and not valid_url(source_url):
                errors.append("jd.source_url 必须为 HTTP(S)")
            if jd.get("source_type") == "公开招聘页" and not valid_url(source_url):
                errors.append("公开招聘页 JD 必须提供 source_url")
    validate_sources(payload.get("company_sources"), "company_sources", errors)
    questions = payload.get("questions")
    if not isinstance(questions, list) or not questions:
        errors.append("questions 必须为非空数组")
        questions = []
    seen: set[str] = set()
    for index, item in enumerate(questions, start=1):
        label = f"questions[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{label} 必须为对象")
            continue
        qid = str(item.get("question_id", "")).strip()
        if not qid:
            errors.append(f"{label}.question_id 不能为空")
        elif qid in seen:
            errors.append(f"question_id 重复：{qid}")
        seen.add(qid)
        if item.get("source_type") not in SOURCE_TYPES:
            errors.append(f"{label}.source_type 无效")
        if item.get("priority") not in PRIORITIES:
            errors.append(f"{label}.priority 必须为 P0、P1 或 P2")
        for field in ("question", "short_30s"):
            if not str(item.get(field, "")).strip():
                errors.append(f"{label}.{field} 不能为空")
        for field in ("standard_2m",):
            if field in item and not isinstance(item[field], str):
                errors.append(f"{label}.{field} 必须为字符串")
        for field in ("risks",):
            value = item.get(field, [])
            if not isinstance(value, list) or any(not isinstance(row, str) for row in value):
                errors.append(f"{label}.{field} 必须为字符串数组")
        followups = item.get("followups", [])
        if not isinstance(followups, list):
            errors.append(f"{label}.followups 必须为数组")
        else:
            for followup_index, row in enumerate(followups, start=1):
                if not isinstance(row, dict) or not str(row.get("question", "")).strip() or not str(row.get("answer", "")).strip():
                    errors.append(f"{label}.followups[{followup_index}] 必须包含 question 和 answer")
        validate_sources(item.get("sources"), f"{label}.sources", errors)
        evidence = item.get("evidence_refs", [])
        if not isinstance(evidence, list):
            errors.append(f"{label}.evidence_refs 必须为数组")
        else:
            for evidence_index, row in enumerate(evidence, start=1):
                if not isinstance(row, dict):
                    errors.append(f"{label}.evidence_refs[{evidence_index}] 必须为对象")
                    continue
                ref = (str(row.get("case_id", "")).strip(), str(row.get("question_id", "")).strip())
                if not all(ref):
                    errors.append(f"{label}.evidence_refs[{evidence_index}] 缺少 case_id 或 question_id")
                elif known_questions is not None and ref not in known_questions:
                    errors.append(f"{label}.evidence_refs[{evidence_index}] 引用了未知问题 {ref[0]}/{ref[1]}")
        if require_historical_evidence and item.get("source_type") == "历史原题" and not evidence:
            errors.append(f"{label} 标为历史原题但缺少 evidence_refs")
    reverse = payload.get("reverse_questions", [])
    if not isinstance(reverse, list) or any(not isinstance(row, str) or not row.strip() for row in reverse):
        errors.append("reverse_questions 必须为非空字符串数组")
    return errors


def load_existing(root: Path) -> list[tuple[Path, dict[str, Any]]]:
    result = []
    prep_dir = root / "profile" / "target-interview-preps"
    if prep_dir.is_dir():
        for path in sorted(prep_dir.glob("*.json")):
            result.append((path, read_json(path)))
    return result


def duplicate_errors(rows: list[tuple[Path, dict[str, Any]]]) -> list[str]:
    seen: dict[tuple[str, str], Path] = {}
    errors: list[str] = []
    for path, payload in rows:
        target = payload.get("target", {}) if isinstance(payload, dict) else {}
        key = (str(target.get("company", "")).strip(), str(target.get("role", "")).strip())
        if not all(key):
            continue
        if key in seen:
            errors.append(f"公司与岗位重复：{key[0]} / {key[1]}（{seen[key].name}、{path.name}）")
        seen[key] = path
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="校验目标公司面试准备稿")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--input", type=Path, help="额外校验尚未写入的准备稿")
    parser.add_argument("--slug", help="更新既有准备稿时，排除同 slug 的当前版本")
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    require_store_compatible(root)
    known = question_index(root)
    rows = load_existing(root)
    if args.slug:
        if not SLUG_RE.fullmatch(args.slug):
            parser.error("--slug 只能使用小写英文、数字和连字符")
        current = (root / "profile" / "target-interview-preps" / f"{args.slug}.json").resolve()
        rows = [(path, payload) for path, payload in rows if path.resolve() != current]
    errors: list[str] = []
    warnings: list[str] = []
    for path, payload in rows:
        errors.extend(f"{path.name}: {item}" for item in validate_payload(payload, known_questions=known))
        for index, item in enumerate(payload.get("questions", []), start=1):
            if isinstance(item, dict) and item.get("source_type") == "历史原题" and not item.get("evidence_refs"):
                warnings.append(f"{path.name}: questions[{index}] 标为历史原题但缺少 evidence_refs")
    if args.input:
        source = args.input.expanduser().resolve()
        payload = json.loads(source.read_text(encoding="utf-8"))
        errors.extend(
            f"input: {item}"
            for item in validate_payload(
                payload,
                known_questions=known,
                require_historical_evidence=True,
            )
        )
        rows.append((source, payload))
    errors.extend(duplicate_errors(rows))
    result = {"status": "valid" if not errors else "invalid", "files": len(rows), "errors": errors, "warnings": warnings}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
