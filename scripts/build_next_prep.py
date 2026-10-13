#!/usr/bin/env python3
"""Build a deterministic next-interview preparation brief from local artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from analysis_state import current_errors
from check_data_compatibility import require_store_compatible
from common import SCHEMA_VERSION, now_iso, read_json, write_json, write_private_text
from labels_zh import label


PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}
ACTIVE_PROFILE_TYPES = {
    "answer_version",
    "knowledge_state",
    "resume_risk",
    "algorithm_weakness",
}


def ensure_inside(root: Path, candidate: Path, label: str) -> Path:
    resolved = candidate.expanduser().resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise RuntimeError(f"{label} 必须位于数据目录内：{resolved}") from exc
    return resolved


def select_case(root: Path, requested: Path | None) -> Path:
    if requested is not None:
        case_path = ensure_inside(root, requested, "case")
        if not (case_path / "manifest.json").is_file():
            raise RuntimeError(f"case 缺少 manifest.json：{case_path}")
        if not (case_path / "jd-matrix.json").is_file():
            raise RuntimeError(f"case 缺少 jd-matrix.json：{case_path}")
        errors = current_errors(case_path, read_json(case_path / "manifest.json"))
        if errors:
            raise RuntimeError("不能使用此场次生成准备简报：" + "；".join(errors))
        return case_path

    candidates: list[tuple[str, str, Path]] = []
    for manifest_path in (root / "interviews").glob("*/manifest.json"):
        case_path = manifest_path.parent
        if not (case_path / "jd-matrix.json").is_file():
            continue
        manifest = read_json(manifest_path)
        if current_errors(case_path, manifest):
            continue
        candidates.append(
            (
                str(manifest.get("date") or ""),
                str(manifest.get("case_id") or case_path.name),
                case_path,
            )
        )
    if not candidates:
        raise RuntimeError("没有分析状态有效且包含 jd-matrix.json 的面试场次")
    candidates.sort(reverse=True)
    return candidates[0][2]


def build_requirements(matrix: dict[str, Any]) -> list[dict[str, Any]]:
    requirements: list[dict[str, Any]] = []
    for item in matrix.get("requirements", []):
        if not isinstance(item, dict):
            continue
        requirement = str(item.get("requirement") or "未命名能力").strip()
        coverage = str(item.get("coverage") or "not_asked")
        performance = str(item.get("performance") or "unverified")
        priority = str(item.get("next_priority") or "medium")
        if priority not in PRIORITY_ORDER:
            priority = "medium"
        if coverage == "not_asked":
            state = "未覆盖"
        elif performance in {"weak", "unverified"}:
            state = "已覆盖但有风险"
        else:
            state = "已有覆盖"
        requirements.append(
            {
                "requirement": requirement,
                "coverage": coverage,
                "performance": performance,
                "next_priority": priority,
                "state": state,
                "evidence_questions": item.get("evidence_questions", []),
                "inference_note": str(item.get("inference_note") or ""),
            }
        )
    return sorted(
        requirements,
        key=lambda item: (PRIORITY_ORDER[item["next_priority"]], item["requirement"]),
    )


def build_profile_items(state: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    confirmed: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    rejected_count = 0
    for event_type, events in (state.get("by_type") or {}).items():
        if event_type not in ACTIVE_PROFILE_TYPES or not isinstance(events, list):
            continue
        for event in events:
            if not isinstance(event, dict):
                continue
            item = {
                "type": event_type,
                "key": str(event.get("key") or ""),
                "status": event.get("status"),
                "reason": str(event.get("reason") or ""),
                "case_id": str(event.get("case_id") or ""),
                "change_id": str(event.get("change_id") or ""),
            }
            if event.get("status") == "rejected":
                rejected_count += 1
            elif event.get("status") == "confirmed":
                confirmed.append(item)
            else:
                pending.append(item)
    key = lambda item: (item["type"], item["key"], item["case_id"])
    return sorted(confirmed, key=key), sorted(pending, key=key), rejected_count


def build_actions(requirements: list[dict[str, Any]]) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    for item in requirements:
        if item["state"] == "未覆盖":
            action = f"补齐未覆盖能力：{item['requirement']}"
        elif item["state"] == "已覆盖但有风险":
            action = f"复盘并练习风险能力：{item['requirement']}"
        else:
            action = f"准备该能力的追问展开：{item['requirement']}"
        actions.append(
            {
                "priority": "P0" if item["next_priority"] == "high" else "P1" if item["next_priority"] == "medium" else "P2",
                "action": action,
                "completion_signal": "写出一版可核验回答，并标注对应的真实证据或待补充项。",
            }
        )
    return actions


def markdown(brief: dict[str, Any]) -> str:
    source = brief["source_case"]
    lines = [
        "# 下一场面试准备简报",
        "",
        f"> 生成时间：{brief['generated_at']}",
        "> 本简报由本地 case、JD 矩阵和长期档案确定性生成；不代表通过率预测或面试官意图。",
        "## 来源场次",
        "",
        f"- `{source['case_id']}` · {source['date']} · {source['company']} · {source['role']} · {source['round']}",
        "",
        "## JD 能力重点",
        "",
    ]
    if brief["requirements"]:
        lines.append("| 优先级 | 能力 | 覆盖 | 表现 | 状态 |")
        lines.append("|---|---|---|---|---|")
        for item in brief["requirements"]:
            priority = label("priority", item["next_priority"])
            coverage = label("coverage", item["coverage"])
            performance = label("performance", item["performance"])
            lines.append(
                f"| {priority} | {item['requirement']} | {coverage} | "
                f"{performance} | {item['state']} |"
            )
    else:
        lines.append("暂无可用的 JD 能力要求。")

    lines.extend(["", "## 可直接复用的已确认资产", ""])
    if brief["profile"]["confirmed"]:
        for item in brief["profile"]["confirmed"]:
            lines.append(f"- `{item['key']}` · {label('profile_type', item['type'])} · 来源 `{item['case_id']}`")
    else:
        lines.append("暂无已确认资产。")

    lines.extend(["", "## 待确认或风险资产", ""])
    if brief["profile"]["pending"]:
        for item in brief["profile"]["pending"]:
            reason = f"：{item['reason']}" if item["reason"] else ""
            lines.append(f"- `{item['key']}` · {label('profile_type', item['type'])}{reason}")
    else:
        lines.append("暂无待确认资产。")

    lines.extend(["", "## 行动清单", ""])
    for item in brief["actions"]:
        lines.append(f"- [{item['priority']}] {item['action']}（完成信号：{item['completion_signal']}）")

    lines.extend(
        [
            "",
            "## 边界",
            "",
            f"- 已拒绝档案事件未作为当前能力依据；本次排除 {brief['profile']['rejected_count']} 条。",
            "- “未覆盖”和“有风险”来自结构化 JD 矩阵，不等于能力不存在。",
            "- 预测性追问仍需由复盘者基于 JD 和事实另行生成，并明确标注为 AI 推测。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="生成下一场面试准备简报")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--case", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    root = args.root.expanduser().resolve()
    if not (root / "config.json").is_file():
        raise RuntimeError("数据目录尚未初始化，请先运行 init_store.py")
    require_store_compatible(root)
    case_path = select_case(root, args.case)
    manifest = read_json(case_path / "manifest.json")
    matrix = read_json(case_path / "jd-matrix.json")
    state_path = root / "profile" / "state.json"
    state = read_json(state_path) if state_path.is_file() else {}
    confirmed, pending, rejected_count = build_profile_items(state)
    requirements = build_requirements(matrix)
    brief = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now_iso(),
        "source_case": {
            "case_id": manifest.get("case_id", case_path.name),
            "date": manifest.get("date") or "未知日期",
            "company": manifest.get("company") or "未知公司",
            "role": manifest.get("role") or "未知岗位",
            "round": manifest.get("round") or "未知轮次",
        },
        "requirements": requirements,
        "profile": {
            "confirmed": confirmed,
            "pending": pending,
            "rejected_count": rejected_count,
        },
        "actions": build_actions(requirements),
    }
    output = (
        args.output.expanduser().resolve()
        if args.output
        else root / "profile" / "next-interview-prep.md"
    )
    output = ensure_inside(root, output, "输出文件")
    json_output = output.with_suffix(".json")
    output.parent.mkdir(parents=True, exist_ok=True)
    write_private_text(output, markdown(brief))
    write_json(json_output, brief)
    print(
        json.dumps(
            {
                "status": "created",
                "case_id": brief["source_case"]["case_id"],
                "markdown": str(output),
                "json": str(json_output),
                "requirements": len(requirements),
                "actions": len(brief["actions"]),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
