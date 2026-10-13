"""Heuristic structure feedback for an already-local algorithm explanation transcript."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from algorithm_progress import add_mistake, ensure_problem, load_progress, save_progress
from common import now_iso, read_supported_text, sha256_file


GENERIC_CHECKS = (
    {"id": "approach", "label": "核心思路", "terms": ("遍历", "循环", "指针", "哈希", "递归", "队列", "栈")},
    {"id": "complexity", "label": "复杂度", "terms": ("o(n", "o(1", "复杂度", "时间", "空间")},
    {"id": "boundary", "label": "边界条件", "terms": ("边界", "空", "重复", "负数", "单个", "零", "0")},
)


def review_oral(root: Path, session: dict[str, Any], problem: dict[str, Any], source: Path) -> dict[str, Any]:
    source = source.expanduser().resolve()
    if not source.is_file() or source.is_symlink():
        raise RuntimeError("口述转写必须是存在的普通本地文件")
    if source.stat().st_size > 524288:
        raise RuntimeError("口述转写超过 512 KiB 限制")
    text = read_supported_text(source, allow_pdf=False).strip()
    if not text:
        raise RuntimeError("口述转写为空")
    normalized = re.sub(r"\s+", "", text.lower())
    checks = list(GENERIC_CHECKS) + problem.get("oral_checks", [])
    coverage = []
    seen = set()
    for check in checks:
        if check["id"] in seen:
            continue
        seen.add(check["id"])
        matched = [term for term in check["terms"] if term.lower().replace(" ", "") in normalized]
        coverage.append({"id": check["id"], "label": check["label"],
                         "covered": bool(matched), "matched_terms": matched})
    missing = [item for item in coverage if not item["covered"]]
    number = 1
    private = root / "algorithms" / "private" / session["session_id"]
    while (private / f"oral-{number:03d}{source.suffix.lower()}").exists():
        number += 1
    snapshot = private / f"oral-{number:03d}{source.suffix.lower()}"
    shutil.copy2(source, snapshot)
    record = {
        "created_at": now_iso(), "session_id": session["session_id"], "problem_id": problem["id"],
        "source": str(snapshot.relative_to(root)), "source_sha256": sha256_file(snapshot),
        "method": "keyword_heuristic", "actual_audio_transcribed": False,
        "semantic_correctness_verified": False, "coverage": coverage,
        "missing": [item["id"] for item in missing],
    }
    with (root / "algorithms" / "sessions" / session["session_id"] / "oral-feedback.jsonl").open(
        "a", encoding="utf-8"
    ) as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    progress = load_progress(root)
    item = ensure_problem(progress, problem)
    for value in missing:
        add_mistake(item, f"oral_missing_{value['id']}", "oral_heuristic", None)
    save_progress(root, progress)
    next_prompt = (f"请补充{missing[0]['label']}，只回答这一点。" if missing
                   else "结构覆盖完整；仍需结合代码和真实测试核验正确性。")
    return {"status": "reviewed", **record, "coverage_score": round((len(coverage) - len(missing)) / len(coverage), 3),
            "next_prompt": next_prompt, "mastery_changed": False}
