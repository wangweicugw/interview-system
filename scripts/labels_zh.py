"""将结构化枚举转换为面向用户的中文标签。"""

from __future__ import annotations

import re


LABELS = {
    "coverage": {
        "not_asked": "未考察",
        "shallow": "浅度涉及",
        "deep": "深入考察",
    },
    "performance": {
        "unverified": "未核验",
        "strong": "表现较强",
        "medium": "表现一般",
        "weak": "表现较弱",
    },
    "priority": {"high": "高", "medium": "中", "low": "低"},
    "analysis_status": {
        "not_started": "未开始",
        "current": "已更新",
        "stale": "待重算",
    },
    "speaker_status": {
        "confirmed": "已确认",
        "needs_confirmation": "待确认",
    },
    "question_kind": {"main": "主问题", "follow_up": "追问"},
    "role": {
        "candidate": "候选人",
        "interviewer": "面试官",
        "other": "其他",
        "unknown": "未确认",
    },
    "profile_type": {
        "answer_version": "回答需要改进",
        "knowledge_state": "知识点掌握情况",
        "interview_pattern": "面试追问规律",
        "algorithm_weakness": "算法练习提醒",
        "resume_risk": "简历表述提醒",
    },
}


def label(kind: str, value: object) -> str:
    text = str(value) if value is not None else ""
    return LABELS[kind].get(text, text)


def round_label(value: object) -> str:
    """统一明确的面试轮次别名；不猜测终面、加面等轮次。"""
    text = re.sub(r"\s+", "", str(value or ""))
    if text in {"", "未知轮次", "轮次待确认", "待确认轮次", "轮次未知"}:
        return "轮次待确认"
    if text in {"初面", "首面", "首轮"}:
        return "一面"
    for number, chinese in enumerate("一二三四五", start=1):
        aliases = {
            f"{chinese}面", f"{chinese}轮", f"第{chinese}轮",
            f"{chinese}轮面试", f"第{chinese}轮面试",
            f"{number}面", f"{number}轮", f"第{number}轮",
            f"{number}轮面试", f"第{number}轮面试",
        }
        if text in aliases:
            return f"{chinese}面"
    return str(value or "").strip()
