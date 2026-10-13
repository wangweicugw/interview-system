"""Conservative static checks for common Java interview mistakes."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def inspect_java(path: Path, problem_id: str) -> list[dict[str, Any]]:
    source = path.read_text(encoding="utf-8")
    findings: list[dict[str, Any]] = []

    def add(category: str, kind: str, message: str) -> None:
        findings.append({"category": category, "kind": kind, "message": message})

    if re.search(r"new\s+int\s*\{", source):
        add("array_initializer_syntax", "syntax", "Java 数组返回值应写成 new int[]{...}。")
    if problem_id == "leetcode-1":
        if re.search(r"return\s+new\s+int(?:\s*\[\s*])?\s*\{\s*nums\s*\[", source):
            add("returned_value_instead_of_index", "logic", "题目要求返回下标，不是 nums 中的值。")
        put = source.find(".put(")
        lookup_candidates = [position for token in (".containsKey(", ".get(") if (position := source.find(token)) >= 0]
        lookup = min(lookup_candidates) if lookup_candidates else -1
        if put >= 0 and lookup >= 0 and put < lookup:
            add("insert_before_lookup_self_pair", "boundary", "先插入再查找可能让同一元素与自己配对，例如 [3,3]、target=6。")
    return findings


def classify_java_errors(errors: list[str]) -> list[dict[str, Any]]:
    rules = (
        ("missing_terminal_return", "syntax", "missing return statement", "存在未覆盖的返回路径。"),
        ("missing_symbol_or_import", "syntax", "cannot find symbol", "存在未定义类型、变量或缺失 import。"),
        ("incompatible_types", "syntax", "incompatible types", "Java 类型不匹配。"),
        ("index_out_of_bounds", "boundary", "IndexOutOfBoundsException", "运行时发生下标越界。"),
        ("null_pointer", "boundary", "NullPointerException", "运行时访问了 null。"),
        ("stack_overflow", "boundary", "StackOverflowError", "递归深度或终止条件可能有问题。"),
    )
    findings = []
    combined = "\n".join(errors)
    for category, kind, token, message in rules:
        if token.lower() in combined.lower():
            findings.append({"category": category, "kind": kind, "message": message})
    return findings
