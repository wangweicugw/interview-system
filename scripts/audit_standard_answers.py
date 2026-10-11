#!/usr/bin/env python3
"""Audit mature answers against current clusters and expose new evidence or numeric conflicts."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

from check_data_compatibility import require_store_compatible
from check_standard_answers import validate_standard_answers
from common import now_iso, write_json


NUMBER_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:%|年|个月|月|人|万|亿|ms|毫秒|秒|分钟|qps|QPS|台|个)?")


def _read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _question_answers(root: Path) -> dict[tuple[str, str], str]:
    result: dict[tuple[str, str], str] = {}
    for qa_path in (root / "interviews").glob("*/qa-tree.json"):
        qa = _read(qa_path, {})
        case_id = str(qa.get("case_id") or "")
        transcript = _read(qa_path.parent / "transcript.revised.json", None)
        if not isinstance(transcript, dict):
            transcript = _read(qa_path.parent / "transcript.raw.json", {})
        segments = {
            str(item.get("segment_id") or ""): str(
                item.get("text_corrected") or item.get("text_raw") or item.get("text") or ""
            ).strip()
            for item in transcript.get("segments", [])
            if isinstance(item, dict)
        }
        for question in qa.get("questions", []):
            if not isinstance(question, dict):
                continue
            qid = str(question.get("question_id") or "")
            texts = [segments.get(str(segment_id), "") for segment_id in question.get("answer_evidence", [])]
            answer = " ".join(value for value in texts if value).strip()
            if qid and answer:
                result[(case_id, qid)] = answer
    return result


def audit(root: Path, *, reset_confirmed_stale: bool) -> dict[str, Any]:
    require_store_compatible(root)
    answers_path = root / "profile" / "standard-answers.json"
    clusters_path = root / "profile" / "question-clusters.json"
    answers_payload = _read(answers_path, {"schema_version": 1, "answers": []})
    clusters_payload = _read(clusters_path, {"schema_version": 1, "clusters": []})
    answers = [item for item in answers_payload.get("answers", []) if isinstance(item, dict)]
    clusters = [item for item in clusters_payload.get("clusters", []) if isinstance(item, dict) and item.get("status") != "rejected"]
    answer_texts = _question_answers(root)
    answer_refs = {
        str(answer.get("answer_id") or ""): {
            (str(ref.get("case_id") or ""), str(ref.get("question_id") or ""))
            for ref in answer.get("source_questions", []) if isinstance(ref, dict)
        }
        for answer in answers
    }
    reports = []
    stale_answer_ids: list[str] = []
    for cluster in clusters:
        cluster_id = str(cluster.get("cluster_id") or "")
        refs = {
            (str(ref.get("case_id") or ""), str(ref.get("question_id") or ""))
            for ref in cluster.get("questions", []) if isinstance(ref, dict)
        }
        ranked = []
        for answer in answers:
            answer_id = str(answer.get("answer_id") or "")
            overlap = len(refs & answer_refs.get(answer_id, set()))
            exact = 1 if answer_id == cluster_id else 0
            ranked.append((exact, overlap, answer_id, answer))
        ranked.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
        match = ranked[0][3] if ranked and (ranked[0][0] or ranked[0][1]) else None
        match_id = str(match.get("answer_id") or "") if match else None
        matched_refs = answer_refs.get(match_id or "", set())
        new_refs = sorted(refs - matched_refs)
        snippets = []
        numbers: dict[str, list[str]] = {}
        for ref in sorted(refs):
            text = answer_texts.get(ref, "")
            if not text:
                continue
            tokens = sorted({value.strip() for value in NUMBER_RE.findall(text) if value.strip()})
            snippets.append({
                "case_id": ref[0],
                "question_id": ref[1],
                "answer_excerpt": text[:300],
                "number_tokens": tokens,
            })
            for token in tokens:
                numbers.setdefault(token, []).append(f"{ref[0]}/{ref[1]}")
        if match and new_refs and match.get("status") == "confirmed":
            stale_answer_ids.append(match_id or "")
        reports.append({
            "cluster_id": cluster_id,
            "cluster_label": str(cluster.get("label") or ""),
            "question_count": len(refs),
            "standard_answer_id": match_id,
            "standard_answer_status": match.get("status") if match else None,
            "new_question_refs": [
                {"case_id": case_id, "question_id": question_id}
                for case_id, question_id in new_refs
            ],
            "answer_excerpts": snippets,
            "observed_number_tokens": [
                {"token": token, "references": references}
                for token, references in sorted(numbers.items())
            ],
            "needs_revision": match is None or bool(new_refs),
            "note": "数字差异只是复核线索，不自动判定矛盾；单位、时间范围和口径仍需人工核对。",
        })

    reset_count = 0
    if reset_confirmed_stale and stale_answer_ids:
        changed_at = now_iso()
        log = root / "profile" / "standard-answer-revisions.jsonl"
        os.umask(0o077)
        with log.open("a", encoding="utf-8") as handle:
            for answer in answers:
                answer_id = str(answer.get("answer_id") or "")
                if answer_id not in stale_answer_ids:
                    continue
                answer["status"] = "pending"
                answer["reviewed_at"] = changed_at
                handle.write(json.dumps({
                    "schema_version": 1,
                    "answer_id": answer_id,
                    "previous_status": "confirmed",
                    "status": "pending",
                    "reason": "高频问法新增了尚未纳入成熟答案的真实回答，需重新复核。",
                    "changed_at": changed_at,
                }, ensure_ascii=False) + "\n")
                reset_count += 1
        write_json(answers_path, answers_payload)
        issues = validate_standard_answers(root)
        if issues:
            raise RuntimeError("成熟答案状态重置后校验失败：" + "；".join(issues))

    report = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "cluster_count": len(clusters),
        "standard_answer_count": len([item for item in answers if item.get("status") != "rejected"]),
        "missing_standard_answers": sum(item["standard_answer_id"] is None for item in reports),
        "answers_needing_revision": sum(item["needs_revision"] for item in reports),
        "reset_to_pending": reset_count,
        "clusters": reports,
    }
    report_path = root / "profile" / "standard-answer-audit.json"
    write_json(report_path, report)
    return {
        "status": "ok",
        "report": str(report_path),
        "clusters": len(clusters),
        "missing_standard_answers": report["missing_standard_answers"],
        "answers_needing_revision": report["answers_needing_revision"],
        "reset_to_pending": reset_count,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="对比高频问法与成熟答案，输出新增回答和数字冲突线索")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--reset-confirmed-stale", action="store_true")
    args = parser.parse_args()
    result = audit(args.root.expanduser().resolve(), reset_confirmed_stale=args.reset_confirmed_stale)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False))
        raise SystemExit(1)
