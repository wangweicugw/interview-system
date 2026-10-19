#!/usr/bin/env python3
"""Build explainable, non-authoritative tags for every recorded interview question."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from check_data_compatibility import require_store_compatible
from common import now_iso, write_json
from render_interview_html import TOPIC_LABELS, question_topic


def read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def text_for(question: dict[str, Any]) -> str:
    return str(question.get("question") or "").strip()


def append_if(values: list[str], text: str, label: str, words: tuple[str, ...]) -> None:
    if any(word in text for word in words) and label not in values:
        values.append(label)


def build_tags(question: dict[str, Any], cluster_case_count: int) -> dict[str, Any]:
    text = text_for(question)
    category = str(question.get("category") or "").lower()
    topic = question_topic(question)
    domain = [TOPIC_LABELS.get(topic, topic)]
    intent: list[str] = []
    object_scene: list[str] = []
    constraints: list[str] = []

    if category in {"algorithm", "coding"}:
        intent.append("现场编码")
    if category in {"system_design", "design", "architecture"} or any(word in text for word in ("架构", "设计", "方案")):
        intent.append("系统设计")
    if any(word in text for word in ("为什么", "决策", "取舍", "依据")):
        intent.append("决策与取舍")
    if any(word in text for word in ("如何", "怎么", "怎样", "流程", "步骤")):
        intent.append("方案与流程")
    if any(word in text for word in ("排查", "定位", "故障", "异常", "告警")):
        intent.append("故障排查")
    if any(word in text for word in ("负责", "贡献", "角色", "做了什么", "工作")):
        intent.append("个人贡献")
    if any(word in text for word in ("验证", "指标", "效果", "证明", "数据")):
        intent.append("效果与证据")
    if not intent:
        intent.append("经历与理解")

    append_if(object_scene, text, "VtDriver", ("VtDriver",))
    append_if(object_scene, text, "Vitess", ("Vitess", "VTTablet", "Gen4 Planner"))
    append_if(object_scene, text, "DongDAL", ("DongDAL",))
    append_if(object_scene, text, "MySQL", ("MySQL", "InnoDB", "主从"))
    append_if(object_scene, text, "分库分表", ("分库分表", "分片", "跨分片"))
    append_if(object_scene, text, "数据迁移", ("迁移", "新旧库", "双写", "切流"))
    append_if(object_scene, text, "监控与故障", ("监控", "告警", "排障", "故障", "磁盘"))
    append_if(object_scene, text, "团队协作", ("团队", "职责", "说服", "不配合", "协作"))
    append_if(object_scene, text, "求职与岗位", ("岗位", "公司", "离职", "职业规划", "求职"))
    if not object_scene:
        object_scene.append("通用面试场景")

    append_if(constraints, text, "性能与规模", ("性能", "并发", "大表", "大事务", "规模", "延迟", "吞吐"))
    append_if(constraints, text, "一致性与可靠性", ("一致性", "数据丢失", "可靠", "双写", "主从延迟"))
    append_if(constraints, text, "回滚与边界", ("回滚", "边界", "失败", "降级", "恢复"))
    append_if(constraints, text, "指标与证据", ("指标", "监控", "告警", "验证", "证明", "数据"))
    append_if(constraints, text, "组织与职责", ("职责", "团队", "业务", "说服", "推进"))
    if not constraints:
        constraints.append("未识别特殊约束")

    if cluster_case_count >= 3 or topic in {"algorithm", "system-design", "sharding", "middleware", "observability"}:
        reusability = "high"
    elif cluster_case_count >= 2:
        reusability = "medium"
    else:
        reusability = "low"
    return {
        "domain": domain,
        "intent": intent,
        "object_scene": object_scene,
        "constraints": constraints,
        "reusability": reusability,
    }


def build(root: Path) -> dict[str, Any]:
    require_store_compatible(root)
    clusters = read(root / "profile" / "question-clusters.json", {}).get("clusters", [])
    cluster_cases: dict[tuple[str, str], set[str]] = defaultdict(set)
    for cluster in clusters if isinstance(clusters, list) else []:
        if not isinstance(cluster, dict) or cluster.get("status") == "rejected":
            continue
        for ref in cluster.get("questions", []):
            if isinstance(ref, dict) and ref.get("case_id") and ref.get("question_id"):
                cluster_cases[(str(ref["case_id"]), str(ref["question_id"]))].add(str(cluster.get("cluster_id")))

    rows: list[dict[str, Any]] = []
    for qa_path in sorted((root / "interviews").glob("*/qa-tree.json")):
        qa = read(qa_path, {})
        case_id = str(qa.get("case_id") or qa_path.parent.name)
        for question in qa.get("questions", []):
            if not isinstance(question, dict) or not question.get("question_id"):
                continue
            key = (case_id, str(question["question_id"]))
            rows.append({
                "case_id": case_id,
                "question_id": str(question["question_id"]),
                "tags": build_tags(question, len(cluster_cases.get(key, set()))),
                "source": "deterministic-v1",
            })
    payload = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "method": "基于题目类别、短题干和有效题组覆盖的确定性标签；仅用于检索与候选，不代表事实确认。",
        "questions": rows,
    }
    write_json(root / "profile" / "question-tags.json", payload)
    return {"status": "ok", "questions": len(rows), "output": str(root / "profile" / "question-tags.json")}


def main() -> int:
    parser = argparse.ArgumentParser(description="生成面试题多维检索标签")
    parser.add_argument("--root", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.root.expanduser().resolve()), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
