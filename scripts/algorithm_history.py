"""Find algorithm-topic candidates in existing interview question trees."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from common import read_json


RULES = (
    ("leetcode-1", "hash-map", ("两数之和", "哈希表", "hashmap", "hash map")),
    ("leetcode-217", "hash-set", ("重复元素", "去重", "hashset", "hash set")),
    ("leetcode-283", "two-pointers", ("移动零", "快慢指针", "双指针")),
    ("leetcode-125", "two-pointers", ("回文", "左右指针")),
    ("first-position", "binary-search", ("二分", "binary search")),
    ("balanced-brackets", "stack", ("括号", "栈", "stack")),
    ("unweighted-route", "bfs", ("广度优先", "bfs", "最短路径")),
    ("max-subarray", "dynamic-programming", ("动态规划", "dp", "最大子数组")),
    ("count-target-subarrays", "prefix-sum", ("前缀和", "prefix sum")),
    ("course-order", "topological-sort", ("拓扑排序", "topological")),
    ("component-queries", "union-find", ("并查集", "union find")),
    ("leetcode-206", "linked-list", ("链表", "反转链表", "linked list")),
    ("leetcode-104", "binary-tree", ("二叉树", "树的深度", "binary tree")),
    ("leetcode-200", "grid-search", ("岛屿", "网格搜索", "dfs", "图搜索")),
)
BROAD_TERMS = {"哈希表", "hashmap", "hash map", "去重", "双指针", "dfs", "图搜索"}


def scan_history(root: Path) -> dict[str, Any]:
    candidates = []
    skipped = []
    for case in sorted((root / "interviews").glob("*")):
        if not case.is_dir() or case.is_symlink():
            continue
        qa_path = case / "qa-tree.json"
        if not qa_path.is_file():
            continue
        if qa_path.is_symlink():
            skipped.append({"case_id": case.name, "reason": "qa-tree.json 是符号链接"})
            continue
        manifest_path = case / "manifest.json"
        if manifest_path.is_file() and read_json(manifest_path).get("analysis_status") == "stale":
            skipped.append({"case_id": case.name, "reason": "分析已失效"})
            continue
        payload = read_json(qa_path)
        case_id = payload.get("case_id", case.name)
        for question in payload.get("questions", []):
            question_id = question.get("question_id")
            text = str(question.get("question", "")).lower()
            if not question_id or not text:
                continue
            matches = []
            for problem_id, knowledge_key, terms in RULES:
                found = [term for term in terms if term.lower() in text]
                if found:
                    score = sum(1 if term.lower() in BROAD_TERMS else 2 for term in found)
                    matches.append({
                        "problem_id": problem_id, "knowledge_key": knowledge_key,
                        "matched_terms": found, "match_score": score,
                        "confidence": "high" if score >= 2 else "candidate",
                    })
            best_by_key = {}
            for match in matches:
                key = match["knowledge_key"]
                if key not in best_by_key or match["match_score"] > best_by_key[key]["match_score"]:
                    best_by_key[key] = match
            for match in best_by_key.values():
                candidates.append({
                    "case_id": case_id, "question_id": question_id,
                    "source_path": qa_path.relative_to(root).as_posix(), **match,
                })
    return {"status": "scanned", "write_performed": False,
            "candidate_count": len(candidates), "candidates": candidates, "skipped": skipped}
