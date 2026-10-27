"""面试题分类的受控词表，以及历史写法到受控值的映射。

`category` 只回答「这道题在问什么类型的事」，不回答「问的是哪个技术领域」。
技术领域（MySQL、消息队列、分片、迁移等）由
`render_interview_html.question_topic` 根据题面关键词推断，不再塞进 `category`，
避免 `technical` 与 `database` / `messaging` / `performance` 这类领域值混用。
"""

from __future__ import annotations

from typing import Any

# 受控取值。新增取值必须先改这里，再由 validate_case 拦截，避免再次出现同义写法。
CATEGORIES: dict[str, str] = {
    "technical": "技术原理与机制",
    "system_design": "系统设计与架构",
    "project": "项目与经历",
    "behavioral": "行为、协作与自我认知",
    "career": "求职动机与条件",
    "algorithm": "算法与现场编码",
    "ai": "AI 与智能化",
    "product": "产品与业务推动",
}

# 历史写法 -> 受控值。只做同义归并，不改语义。
ALIASES: dict[str, str] = {
    # 设计与架构是同一类
    "design": "system_design",
    "architecture": "system_design",
    "system-design": "system_design",
    # 经历陈述
    "experience": "project",
    "background": "project",
    "resume": "project",
    "problem_solving": "project",
    "problem-solving": "project",
    # 协作与领导力
    "collaboration": "behavioral",
    "leadership": "behavioral",
    "organization": "behavioral",
    # 求职动机与条件
    "motivation": "career",
    "compensation": "career",
    "logistics": "career",
    "role_fit": "career",
    "role-fit": "career",
    "availability": "technical",
    # 算法与现场编码
    "coding": "algorithm",
    # 技术领域值并入技术类
    "database": "technical",
    "messaging": "technical",
    "performance": "technical",
    "data-migration": "technical",
    "data_migration": "technical",
    "troubleshooting": "technical",
    "engineering": "technical",
}


def normalize(value: Any) -> str:
    """把任意历史写法收成受控值；无法识别时返回去空白的小写原文。"""
    text = str(value or "").strip().lower()
    if text in CATEGORIES:
        return text
    return ALIASES.get(text, text)


def is_canonical(value: Any) -> bool:
    return str(value or "").strip().lower() in CATEGORIES


def label(value: Any) -> str:
    key = normalize(value)
    return CATEGORIES.get(key, str(value or "").strip())


def unknown(values: list[str]) -> list[str]:
    """返回不在受控词表内、也没有别名映射的取值。"""
    return sorted({value for value in values if not is_canonical(normalize(value))})
