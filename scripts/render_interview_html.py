#!/usr/bin/env python3
"""Render offline interview review pages and a searchable history index."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
import tempfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

from analysis_state import current_errors
from check_data_compatibility import scan_store
from check_question_clusters import validate_question_clusters
from check_question_preferences import validate_question_preferences
from check_question_tags import validate_question_tags
from check_standard_answers import validate_standard_answers
from labels_zh import label, round_label

import question_taxonomy


CASE_HTML = "interview-review.html"
HISTORY_HTML = "interview-history.html"
QUESTION_BANK_HTML = "interview-question-bank.html"
ANSWERS_HTML = "interview-answers.html"
PENDING_REVIEW_HTML = "interview-pending-review.html"
MISTAKE_BOOK_HTML = "interview-mistakes.html"
PREPARATIONS_HTML = "interview-preparations.html"
PREPARATION_HTML_PREFIX = "interview-preparation-"


def answer_fingerprint(answer: dict[str, Any]) -> str:
    """Bind a browser edit to the exact saved answer version it displayed."""
    canonical = json.dumps(answer, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


SECURITY_META = (
    '<meta http-equiv="Content-Security-Policy" '
    'content="default-src \'none\'; style-src \'unsafe-inline\'; script-src \'unsafe-inline\'; '
    'img-src data:; font-src \'none\'; connect-src \'none\'; media-src \'none\'; '
    'object-src \'none\'; frame-src \'none\'; base-uri \'none\'; form-action \'none\'">'
    '<meta name="referrer" content="no-referrer">'
)

OVERALL_LABELS = {
    "professional": "专业能力",
    "credibility": "经历可信度",
    "structure": "结构化表达",
    "stability": "现场稳定性",
}

VERDICT_LABELS = {
    "correct": "正确",
    "partial": "部分正确",
    "incorrect": "错误",
    "unverified": "未核验",
    "personal": "个人经历，无法外部核验",
}

TOPIC_LABELS = {
    "self-intro": "自我介绍与个人背景",
    "career": "求职动机与职业规划",
    "project-role": "项目经历与个人贡献",
    "middleware": "数据库中间件架构",
    "sharding": "分库分表与跨分片查询",
    "mysql": "MySQL 原理与实践",
    "availability": "高可用、容灾与切流",
    "migration": "数据迁移与一致性",
    "observability": "监控、运维与故障排查",
    "performance": "性能诊断与优化",
    "java": "Java、JVM 与并发",
    "algorithm": "算法与现场编码",
    "system-design": "系统与平台设计",
    "ai": "AI 与 Agent",
    "messaging": "消息队列",
    "collaboration": "协作、领导力与组织",
    "product": "产品推动与业务接入",
    "other": "其他问题",
}


def question_topic(question: dict[str, Any]) -> str:
    """Assign one explainable primary topic; this is a display heuristic, not a fact."""
    # 公开源码只保留通用领域词，不放真实面试问法、城市或个人项目短语。
    # 先用受控词表收口，避免 design/architecture、coding 这类历史写法各自分支。
    category = question_taxonomy.normalize(question.get("category"))
    text = str(question.get("question") or "")

    if category == "algorithm":
        return "algorithm"
    if category == "ai" or any(value in text for value in (" AI", "AI ", "Agent", "ReAct")):
        return "ai"
    if any(value in text for value in ("消息队列", "生产者", "消费者", "Kafka", "RocketMQ")):
        return "messaging"
    if any(value in text for value in ("自我介绍", "个人背景", "个人经历")):
        return "self-intro"
    if category == "career" or any(
        value in text for value in ("离职", "求职", "职业规划", "薪资", "薪酬", "入职", "工作地点")
    ):
        return "career"
    if category == "behavioral" or any(
        value in text for value in ("团队", "协作", "沟通", "冲突", "领导力")
    ):
        return "collaboration"
    if category == "product" or any(value in text for value in ("产品", "用户", "推广", "业务接入")):
        return "product"
    if any(value in text for value in ("JVM", "GC", "CMS", "G1", "Java", "HikariCP", "Druid", "DBCP", "垃圾回收", "JDK", "HashMap", "ConcurrentHashMap", "线程池", "volatile", "synchronized", "类加载")):
        return "java"
    if any(value in text for value in ("监控", "告警", "排障", "故障", "可观测性")):
        return "observability"
    if any(value in text for value in ("数据迁移", "双写", "最终一致", "配置中心")):
        return "migration"
    if any(value in text for value in ("性能", "慢 SQL", "执行计划", "IO", "大事务")):
        return "performance"
    if any(value in text for value in ("高可用", "容灾", "切流", "选主", "限流", "降级", "熔断", "数据丢失", "半同步", "单元化")):
        return "availability"
    if any(value in text for value in ("分库分表", "分库/分表", "分片", "Group By", "Limit", "Distinct", "路由算法", "哈希扩容")):
        return "sharding"
    if any(value in text for value in ("Vitess", "ShardingSphere", "数据库中间件", "数据库代理", "分布式数据库", "数据库网关", "VTTablet", "SQL 解析", "语法树")):
        return "middleware"
    if any(value in text for value in ("MySQL", "InnoDB", "锁等待", "意向锁", "Gap Lock", "唯一索引", "主从延迟")):
        return "mysql"
    if category == "system_design":
        return "system-design"
    if category == "project":
        return "project-role"
    return "other"


def read_json(path: Path, default: Any) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def reference_source_notice(manifest: dict[str, Any]) -> str:
    """Show newly registered references without presenting them as verified evidence."""
    inputs = manifest.get("inputs", {})
    if not isinstance(inputs, dict):
        return ""
    references = inputs.get("reference_transcripts")
    if not isinstance(references, list) or not references:
        return ""
    latest = references[-1]
    if not isinstance(latest, dict):
        return ""
    label_text = html.escape(str(latest.get("source_label") or "参考材料"))
    use_text = html.escape(str(latest.get("use") or "仅作交叉核对"))
    return (
        '<div class="notice"><strong>补充材料已登记</strong>'
        f'<p>共 {len(references)} 份；最新：{label_text}。{use_text}。'
        '登记不会自动重算本场分析；下方完整复盘保留生成时的材料状态。</p></div>'
    )


def question_tag_map(root: Path) -> dict[tuple[str, str], dict[str, Any]]:
    payload = read_json(root / "profile" / "question-tags.json", {})
    rows = payload.get("questions", []) if isinstance(payload, dict) else []
    return {
        (str(item.get("case_id") or ""), str(item.get("question_id") or "")): item.get("tags", {})
        for item in rows
        if isinstance(item, dict) and item.get("case_id") and item.get("question_id")
        and isinstance(item.get("tags"), dict)
    }


def question_preference_map(root: Path) -> dict[tuple[str, str, str], dict[str, Any]]:
    payload = read_json(root / "profile" / "question-preferences.json", {})
    rows = payload.get("preferences", []) if isinstance(payload, dict) else []
    result = {}
    for item in rows:
        if not isinstance(item, dict):
            continue
        target_type = str(item.get("target_type") or "")
        if target_type == "question":
            key = (target_type, str(item.get("case_id") or ""), str(item.get("question_id") or ""))
        elif target_type == "cluster":
            key = (target_type, "", str(item.get("target_id") or ""))
        else:
            continue
        result[key] = item
    return result


def preference_state(preferences: dict[tuple[str, str, str], dict[str, Any]], target_type: str,
                     *, case_id: str = "", question_id: str = "", target_id: str = "") -> str:
    key = (target_type, case_id if target_type == "question" else "", question_id if target_type == "question" else target_id)
    return str(preferences.get(key, {}).get("preference") or "normal")


def effective_question_preference(root: Path, preferences: dict[tuple[str, str, str], dict[str, Any]],
                                  case_id: str, question_id: str) -> str:
    direct_key = ("question", case_id, question_id)
    if direct_key in preferences:
        return str(preferences[direct_key].get("preference") or "normal")
    cluster_payload = read_json(root / "profile" / "question-clusters.json", {})
    cluster_states = []
    for cluster in cluster_payload.get("clusters", []) if isinstance(cluster_payload, dict) else []:
        if not isinstance(cluster, dict) or cluster.get("status") == "rejected":
            continue
        if any(isinstance(ref, dict) and str(ref.get("case_id") or "") == case_id
               and str(ref.get("question_id") or "") == question_id
               for ref in cluster.get("questions", [])):
            cluster_states.append(preference_state(preferences, "cluster", target_id=str(cluster.get("cluster_id") or "")))
    if "ignored" in cluster_states:
        return "ignored"
    if "low_priority" in cluster_states:
        return "low_priority"
    return "normal"


def preference_controls(target_type: str, *, case_id: str = "", question_id: str = "",
                        target_id: str = "", current: str = "normal", show_label: bool = True) -> str:
    if target_type == "question":
        identity = f"question|{case_id}|{question_id}"
    else:
        identity = f"cluster|{target_id}"
    labels = {
        "normal": "正常",
        "low_priority": "降低优先级",
        "ignored": "忽略整组练习" if target_type == "cluster" else "忽略本题练习",
    }
    scope_label = "整组练习" if target_type == "cluster" else "本题练习"
    current_label = labels.get(current, current)
    buttons = "".join(
        f'<button type="button" data-preference-choice="{state}" aria-pressed="{str(state == current).lower()}">{label_text}</button>'
        for state, label_text in labels.items()
    )
    fields = (
        f'data-preference-case-id="{html.escape(case_id, quote=True)}" data-preference-question-id="{html.escape(question_id, quote=True)}"'
        if target_type == "question" else
        f'data-preference-target-id="{html.escape(target_id, quote=True)}"'
    )
    return (
        f'<details class="preference-menu" data-preference-item data-preference-kind="{target_type}" '
        f'data-preference-id="{html.escape(identity, quote=True)}" data-preference-current="{html.escape(current, quote=True)}" '
        f'aria-label="{html.escape("整组练习偏好" if target_type == "cluster" else "练习偏好", quote=True)}" {fields}>'
        f'<summary data-preference-summary aria-expanded="false">{html.escape(scope_label)}：{html.escape(current_label)}⌄</summary>'
        f'<div class="preference-menu-options" role="group" aria-label="{html.escape(scope_label)}选项">{buttons}</div>'
        f'<span class="sr-only" data-preference-result>当前：{html.escape(current_label)}</span></details>'
    )


def _merge_ref_key(ref: dict[str, Any]) -> tuple[str, str]:
    return str(ref.get("case_id") or ""), str(ref.get("question_id") or "")


def render_merge_builder(root: Path, cases: list[dict[str, Any]], *, builder_id: str = "manual-merge") -> str:
    """Render a collapsed, evidence-first manual cluster builder."""
    cluster_payload = read_json(root / "profile" / "question-clusters.json", {})
    active_cluster_records = {
        str(cluster.get("cluster_id") or ""): cluster
        for cluster in cluster_payload.get("clusters", [])
        if isinstance(cluster, dict)
        and cluster.get("status") in {"pending", "confirmed"}
        and cluster.get("cluster_id")
    } if isinstance(cluster_payload, dict) else {}
    active_clusters = {
        cluster_id: [
            _merge_ref_key(ref) for ref in cluster.get("questions", [])
            if isinstance(ref, dict)
        ]
        for cluster_id, cluster in active_cluster_records.items()
    }
    active_cluster_statuses = {
        cluster_id: str(cluster.get("status") or "pending")
        for cluster_id, cluster in active_cluster_records.items()
    }
    ref_clusters: dict[tuple[str, str], list[str]] = {}
    for cluster_id, refs in active_clusters.items():
        for ref in refs:
            ref_clusters.setdefault(ref, []).append(cluster_id)
    rows: list[str] = []
    for case in cases:
        for entry in practice_items(case["path"]):
            question = entry["question"]
            case_id = str(case["case_id"])
            question_id = str(question.get("question_id") or "")
            text = str(question.get("question") or "").strip()
            if not case_id or not question_id or not text:
                continue
            topic = question_topic(question)
            memberships = ref_clusters.get((case_id, question_id), [])
            membership_links = []
            for cluster_id in memberships:
                cluster = active_cluster_records[cluster_id]
                cluster_label = str(cluster.get("label") or cluster_id)
                href = f'#cluster-{quote(cluster_id)}'
                if builder_id != "bank-manual-merge":
                    href = QUESTION_BANK_HTML + href
                status_suffix = "（未核对）" if cluster.get("status") == "pending" else ""
                membership_links.append(
                    f'<a href="{html.escape(href, quote=True)}">{html.escape(cluster_label)}</a>'
                    f'{status_suffix}'
                )
            membership_note = (
                '<div class="merge-membership">已归入题组：' + "、".join(membership_links) + '</div>'
                if membership_links else ""
            )
            search = " ".join((text, str(case.get("company") or ""), TOPIC_LABELS.get(topic, topic),
                               *(str(active_cluster_records[cluster_id].get("label") or "") for cluster_id in memberships))).lower()
            rows.append(
                f'<div class="merge-option" data-merge-option data-merge-search="{html.escape(search, quote=True)}" '
                f'data-merge-case-id="{html.escape(case_id, quote=True)}" data-merge-question-id="{html.escape(question_id, quote=True)}" '
                f'data-merge-topic="{html.escape(topic, quote=True)}" '
                f'data-merge-clusters="{html.escape(json.dumps(memberships, ensure_ascii=False), quote=True)}">'
                f'<label><input type="checkbox" data-merge-checkbox> '
                f'<span class="merge-question-text">{html.escape(text)}</span>'
                f'<small>{html.escape(str(case.get("company") or ""))} · {html.escape(str(case.get("date") or ""))} · '
                f'{html.escape(question_id)} · 候选主题提示：{html.escape(TOPIC_LABELS.get(topic, topic))}</small></label>'
                f'{membership_note}</div>'
            )
    if not rows:
        return ""
    topic_options = "".join(
        f'<option value="{html.escape(topic, quote=True)}">{html.escape(label_text)}</option>'
        for topic, label_text in TOPIC_LABELS.items()
    )
    return (
        f'<details class="merge-builder" id="{html.escape(builder_id, quote=True)}" data-merge-builder '
        f'data-merge-active-clusters="{html.escape(json.dumps(active_clusters, ensure_ascii=False), quote=True)}" '
        f'data-merge-cluster-statuses="{html.escape(json.dumps(active_cluster_statuses, ensure_ascii=False), quote=True)}">'
        '<summary>手动合并同类问题（按需选择）</summary>'
        '<p class="muted">选至少 2 道原题，填写组名和理由。主题标签仅供搜索；合并方案会先作为未核对草稿保存。</p>'
        '<label for="merge-search">搜索原始题面或来源</label>'
        '<input id="merge-search" class="search" data-merge-search-input type="search" placeholder="输入题面、公司或主题">'
        '<div class="merge-selection-summary"><span data-merge-count>已选择 0 题</span><span>至少选择 2 题</span></div>'
        f'<div class="merge-options" data-merge-options>{"".join(rows)}</div>'
        '<div class="merge-form-grid">'
        '<label>新题组名称<input data-merge-label type="text" placeholder="例如：跨团队争议决策"></label>'
        '<label>题组主题<select data-merge-topic>' + topic_options + '</select></label>'
        '</div>'
        '<label>合并理由<textarea data-merge-reason rows="3" placeholder="说明这些原题为何共享同一核心意图，以及排除了什么边界"></textarea></label>'
        '<button type="button" data-merge-queue>加入本次修改</button>'
        '<button type="button" class="secondary" data-merge-undo hidden>撤销本次合并</button>'
        '<span data-merge-result></span></details>'
    )


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, delete=False
        ) as handle:
            handle.write(value)
            temporary = handle.name
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def safe_url(value: str) -> str | None:
    if any(character in value for character in ("\r", "\n", "\x00")):
        return None
    parsed = urlparse(value)
    if (
        parsed.scheme in {"http", "https"}
        and parsed.hostname
        and parsed.username is None
        and parsed.password is None
    ):
        return value
    return None


def archive_id(root: Path) -> str:
    config = read_json(root / "config.json", {})
    seed = json.dumps(
        {
            "schema_version": config.get("schema_version"),
            "created_at": config.get("created_at"),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def privacy_notice(*, compact: bool = False) -> str:
    if compact:
        return (
            '<details class="privacy-compact"><summary>隐私提醒：本页含个人面试资料</summary>'
            '<p>请勿上传到公开网盘或仓库。页面本身不加载远程资源；点击标记为外部资料的链接会联网。</p>'
            '</details>'
        )
    return (
        '<div class="notice"><strong>隐私提醒</strong><p>本页可能包含简历、逐字稿、JD、公司名称和个人分析，'
        '请勿上传到公开网盘或仓库。页面本身不加载远程资源；点击标记为外部资料的链接会联网。</p></div>'
    )


def external_link(url: str, title: str) -> str:
    return (
        f'<a href="{html.escape(url, quote=True)}" target="_blank" rel="noopener">'
        f'{html.escape(title)} <span class="external-note">↗ 外部资料</span></a>'
    )


def inline_markdown(value: str) -> str:
    rendered = html.escape(value)
    rendered = re.sub(r"`([^`]+)`", r"<code>\1</code>", rendered)
    rendered = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", rendered)
    return rendered


def markdown_to_html(markdown: str) -> str:
    lines = markdown.splitlines()
    output: list[str] = []
    paragraph: list[str] = []
    list_kind: str | None = None

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            text = " ".join(item.strip() for item in paragraph).strip()
            if text:
                output.append(f"<p>{inline_markdown(text)}</p>")
            paragraph = []

    def close_list() -> None:
        nonlocal list_kind
        if list_kind:
            output.append(f"</{list_kind}>")
            list_kind = None

    for line in lines:
        stripped = line.strip()
        heading = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        bullet = re.match(r"^[-*]\s+(.+)$", stripped)
        numbered = re.match(r"^\d+[.)]\s+(.+)$", stripped)
        if not stripped:
            flush_paragraph()
            close_list()
            continue
        if heading:
            flush_paragraph()
            close_list()
            level = min(len(heading.group(1)) + 1, 6)
            output.append(f"<h{level}>{inline_markdown(heading.group(2))}</h{level}>")
            continue
        if stripped.startswith(">"):
            flush_paragraph()
            close_list()
            output.append(
                f"<blockquote>{inline_markdown(stripped.lstrip('>').strip())}</blockquote>"
            )
            continue
        match = bullet or numbered
        if match:
            flush_paragraph()
            desired = "ul" if bullet else "ol"
            if list_kind != desired:
                close_list()
                list_kind = desired
                output.append(f"<{desired}>")
            output.append(f"<li>{inline_markdown(match.group(1))}</li>")
            continue
        close_list()
        paragraph.append(stripped)

    flush_paragraph()
    close_list()
    return "\n".join(output)


def plain_excerpt(markdown: str, limit: int = 180) -> str:
    text = re.sub(r"[#>*`_\[\]()]", " ", markdown)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] + ("…" if len(text) > limit else "")


def review_bullets(markdown: str, heading: str, limit: int = 3) -> list[str]:
    """Take only explicit bullets from the newest review section with this title."""
    active_level: int | None = None
    items: list[str] = []
    for line in markdown.splitlines():
        match = re.match(r"^(#{2,6})\s+(.+?)\s*$", line)
        if match:
            level = len(match.group(1))
            title = match.group(2).strip()
            if active_level is not None and level <= active_level:
                break
            if active_level is None and title == heading:
                active_level = level
            continue
        if active_level is not None:
            bullet = re.match(r"^\s*[-*]\s+(.+)$", line)
            if bullet:
                items.append(bullet.group(1).strip())
                if len(items) >= limit:
                    break
    return items


def linked_segments(value: str) -> str:
    rendered = inline_markdown(value)
    return re.sub(r"s-\d{3,}", lambda match: f'<a href="#{match.group(0)}">{match.group(0)}</a>', rendered)


def review_focus(markdown: str) -> str:
    actions = review_bullets(markdown, "下一步与验收", 2)
    facts = review_bullets(markdown, "已确认事实与片段", 1)
    judgments = review_bullets(markdown, "本轮 AI 判断", 1)
    action_html = "".join(f"<li>{inline_markdown(item)}</li>" for item in actions)
    if not action_html:
        action_html = '<li>本场未单列下一步；请从完整复盘中确认可交付的练习或证据。</li>'
    fact = linked_segments(facts[0]) if facts else "本场未单列已确认事实，请查看完整复盘。"
    judgment = inline_markdown(judgments[0]) if judgments else "本场未单列 AI 判断，请查看完整复盘。"
    return f"""
<section id="focus" class="focus-section"><h2>先看结论，再行动</h2>
  <p class="muted">以下摘自本场已保存的复盘；页面重排不等于重新核验。</p>
  <div class="focus-main"><div class="focus-label">下一步 · 来自本场复盘</div>
    <ol>{action_html}</ol><a href="#review">查看完整建议与验收依据 →</a></div>
  <div class="focus-grid">
    <div class="focus-small"><div class="focus-label">已确认事实</div><p>{fact}</p><a href="#evidence">查看逐字稿证据 →</a></div>
    <div class="focus-small"><div class="focus-label">AI 判断，待核对</div><p>{judgment}</p><a href="#review">查看完整复盘 →</a></div>
  </div>
</section>"""


def find_action_plan(root: Path) -> Path | None:
    profile = root / "profile"
    canonical = profile / "action-plan.md"
    if canonical.is_file():
        return canonical
    candidates = sorted(profile.glob("after-*-interviews-todo-*.md"), reverse=True)
    return candidates[0] if candidates else None


def plan_tasks(path: Path) -> list[dict[str, str]]:
    tasks: list[dict[str, str]] = []
    priority = ""
    for line in read_text(path).splitlines():
        heading = re.match(r"^##\s+(P[0-9])\b", line)
        if heading:
            priority = heading.group(1)
            continue
        item = re.match(r"^- \[([ xX])\] \*\*(.+?)\*\*", line)
        if item and priority:
            tasks.append({"priority": priority, "title": item.group(2),
                          "state": "done" if item.group(1).lower() == "x" else "pending",
                          "deliverable": "", "acceptance": ""})
            continue
        detail = re.match(r"^\s+-\s+(交付|验收)：\s*(.+)$", line)
        if detail and tasks:
            key = "deliverable" if detail.group(1) == "交付" else "acceptance"
            tasks[-1][key] = detail.group(2).strip()
    return tasks


def plan_href(plan: Path, from_case: bool) -> str:
    target = plan.with_suffix(".html") if plan.with_suffix(".html").is_file() else plan
    prefix = "../../profile/" if from_case else "profile/"
    return prefix + quote(target.name)


def plan_provenance(path: Path) -> str:
    """Expose the saved plan's scope without silently expanding it to new cases."""
    heading = ""
    generated = ""
    for line in read_text(path).splitlines():
        if not heading and line.startswith("# "):
            heading = line[2:].strip()
        if not generated:
            match = re.search(r"生成日期：([^。]+)", line)
            if match:
                generated = match.group(1).strip()
        if heading and generated:
            break
    parts = [value for value in (heading, f"生成日期 {generated}" if generated else "") if value]
    return "；".join(parts) or path.name


def render_plan_summary(root: Path, case_count: int) -> str:
    plan = find_action_plan(root)
    if not plan:
        return """<section id="next-actions" class="next-actions"><h2>现在该做什么</h2>
<p class="muted">尚未保存跨场待办；打开最近一场复盘，从“下一步”开始。</p></section>"""
    pending = [item for item in plan_tasks(plan) if item["state"] == "pending"]
    pending.sort(key=lambda item: item["priority"])
    cards = []
    for item in pending[:3]:
        cards.append(
            '<article class="action-card">'
            f'<span class="priority">{html.escape(item["priority"])}</span>'
            f'<h3>{html.escape(item["title"])}</h3>'
            f'<p><strong>交付：</strong>{html.escape(item["deliverable"] or "见完整待办")}</p>'
            f'<p class="acceptance"><strong>验收：</strong>{html.escape(item["acceptance"] or "见完整待办")}</p>'
            '</article>'
        )
    body = '<div class="action-grid">' + "".join(cards) + '</div>' if cards else '<p>已保存的任务均标记为完成；请核对实际产物与验收记录。</p>'
    remaining = f'；另有 {len(pending) - 3} 项待办' if len(pending) > 3 else ""
    return f"""<section id="next-actions" class="next-actions"><h2>现在该做什么</h2>
<p class="muted">按已保存的跨场待办排序；这里只展示前三项{remaining}。勾选状态来自待办原文，不代表系统已验收。</p>
<p class="plan-provenance">待办来源：{html.escape(plan_provenance(plan))}；当前档案 {case_count} 场。本页不自动把新场次并入这份待办。</p>
{body}<p><a class="primary-link" href="{plan_href(plan, False)}">查看全部待办与证据 →</a></p></section>"""


def review_plan_goals(root: Path, limit: int = 3) -> list[dict[str, Any]]:
    plan = read_json(root / "profile" / "review-plan.json", {})
    goals = plan.get("training_goals", []) if isinstance(plan, dict) else []
    return [item for item in goals if isinstance(item, dict)][:limit]


def render_next_prep(
    root: Path,
    cases: list[dict[str, Any]],
    *,
    section_id: str = "next-prep",
    question_page: str = QUESTION_BANK_HTML,
    compact: bool = False,
) -> str:
    goals = review_plan_goals(root, 3)
    if not goals:
        fallback_goals = []
        for case in cases:
            items = practice_items(case["path"])
            if not items:
                continue
            question = items[0]["question"]
            qid = str(question.get("question_id") or "")
            topic = question_topic(question)
            fallback_goals.append({
                "title": f"建议主题：{TOPIC_LABELS.get(topic, topic)}",
                "evidence_status": "建议主题（未生成当前计划）",
                "why_priority": "来自最近场次的结构化题目；请先核对原题和现场回答。",
                "deliverable": "完成一次围绕代表题的脱稿回答，并记录 1 条缺口。",
                "acceptance": "回答后能指出个人职责、关键边界和需要补证的地方。",
                "representative": {"case_id": str(case["case_id"]), "question_id": qid},
                "sources": [{"interview_date": str(case.get("date") or ""), "company": str(case.get("company") or ""), "question": str(question.get("question") or "")}],
            })
            if len(fallback_goals) >= 3:
                break
        goals = fallback_goals
    if not goals:
        return (
            f'<section id="{html.escape(section_id, quote=True)}" class="next-prep">'
            '<h2>下一场怎么准备</h2><p class="muted">尚未生成当前复习计划；历史题库仍可搜索，运行复习计划构建器后显示训练目标。</p></section>'
        )
    cards = []
    for index, goal in enumerate(goals):
        representative = goal.get("representative", {})
        case_id = str(representative.get("case_id") or "")
        question_id = str(representative.get("question_id") or "")
        href = f'{question_page}#practice-{case_id}-{question_id}' if case_id and question_id else question_page
        if index == 0 and not compact:
            href = "#practice"
        sources = goal.get("sources", []) if isinstance(goal.get("sources"), list) else []
        source_items = []
        for source in sources:
            if not isinstance(source, dict):
                continue
            source_items.append(
                f'<li>{html.escape(str(source.get("interview_date") or ""))} · '
                f'{html.escape(str(source.get("company") or ""))} · '
                f'{html.escape(str(source.get("question") or ""))}</li>'
            )
        source_html = (
            '<details class="goal-sources"><summary>查看来源（'
            f'{len(sources)} 条原题引用）</summary><ul>{"".join(source_items)}</ul></details>'
            if source_items else '<p class="muted">来源引用暂缺，请回到题库搜索原题。</p>'
        )
        goal_details = (
            '<details class="goal-details"><summary>为什么练这题、怎样算完成</summary>'
            f'<p><strong>原因：</strong>{html.escape(str(goal.get("why_priority") or ""))}</p>'
            f'<p><strong>练习结果：</strong>{html.escape(str(goal.get("deliverable") or ""))}</p>'
            f'<p class="acceptance"><strong>完成标准：</strong>{html.escape(str(goal.get("acceptance") or ""))}</p>'
            f'{source_html}</details>'
        )
        cards.append(
            f'<article class="goal-card" data-goal-card><div class="goal-heading">'
            f'<h3>{html.escape(str(goal.get("title") or "训练目标"))}</h3>'
            f'<span class="status unverified">{html.escape(str(goal.get("evidence_status") or "建议"))}</span></div>'
            f'<p><a class="primary-link" href="{html.escape(href, quote=True)}">练这道题 →</a></p>'
            f'{goal_details}</article>'
        )
    intro = (
        f"基于当前 {len(cases)} 场档案的通用准备建议，尚未针对下一场岗位 JD 匹配；按训练意图去重。已确认跨场归并才作为事实；“建议主题”只是展示层近似，不代表原题相同。"
    )
    if compact:
        intro = "按训练目标找题；完整原题可在下方搜索。"
    goal_content = f'<div class="goal-grid">{"".join(cards)}</div>'
    if not compact:
        goal_content = (
            f'<div class="goal-grid home-goal-grid">{cards[0]}</div>'
            + (f'<details class="more-goals"><summary>查看另外 {len(cards) - 1} 个训练方向</summary>'
               f'<div class="goal-grid">{"".join(cards[1:])}</div></details>' if len(cards) > 1 else '')
        )
        intro = "先练一个方向；完成后再看其他建议。"
    return (
        f'<section id="{html.escape(section_id, quote=True)}" class="next-prep">'
        '<div class="topic-heading"><h2>下一场怎么准备</h2></div>'
        f'<p class="muted">{intro}</p>{goal_content}</section>'
    )


def discover_cases(root: Path) -> list[dict[str, Any]]:
    interviews = root / "interviews"
    cases: list[dict[str, Any]] = []
    if not interviews.is_dir():
        return cases
    for case_path in sorted(interviews.iterdir()):
        if not case_path.is_dir():
            continue
        manifest = read_json(case_path / "manifest.json", {})
        if not manifest:
            continue
        scores = read_json(case_path / "scores.json", {})
        review = read_text(case_path / "review.md")
        cases.append(
            {
                "path": case_path,
                "name": case_path.name,
                "case_id": str(manifest.get("case_id", case_path.name)),
                "date": str(manifest.get("date") or "未知日期"),
                "datetime": str(
                    manifest.get("interview_datetime") or manifest.get("date") or "未知日期"
                ),
                "company": str(manifest.get("company") or "未知公司"),
                "role": str(manifest.get("role") or "未知岗位"),
                "round": round_label(manifest.get("round")),
                "result": str(manifest.get("result") or "无结果"),
                "analysis_status": str(manifest.get("analysis_status") or "unknown"),
                "speaker_status": str(manifest.get("speaker_status") or "unknown"),
                "overall": scores.get("overall", {}),
                "excerpt": plain_excerpt(review),
                "next_action": review_bullets(review, "下一步与验收", 1),
            }
        )
    return sorted(cases, key=lambda item: (item["date"], item["case_id"]), reverse=True)


def badge(value: str) -> str:
    return f'<span class="badge">{html.escape(value)}</span>'


def display_result(value: str) -> str:
    """Translate the stored no-result enum for readers without changing the fact source."""
    value = str(value or "").strip()
    return "尚未记录结果" if not value or value == "无结果" else value


def result_is_missing(value: str) -> bool:
    """Keep missing outcomes visibly distinct from real stored results."""
    return display_result(value) == "尚未记录结果"


def score_cards(overall: dict[str, Any]) -> str:
    cards: list[str] = []
    for key, label in OVERALL_LABELS.items():
        value = overall.get(key)
        if not isinstance(value, (int, float)):
            continue
        percent = max(0, min(100, float(value) * 20))
        cards.append(
            "<div class=\"card score-card\">"
            f"<div class=\"label\">{label}</div>"
            f"<div class=\"metric\">{value:g}<span>/5</span></div>"
            f"<div class=\"track\"><i style=\"width:{percent:g}%\"></i></div>"
            "</div>"
        )
    return "".join(cards) or '<div class="card muted">暂无整场评分。</div>'


def jd_table(matrix: dict[str, Any]) -> str:
    rows: list[str] = []
    for item in matrix.get("requirements", []):
        requirement = html.escape(str(item.get("requirement", "")))
        coverage = html.escape(label("coverage", item.get("coverage")))
        performance_code = str(item.get("performance", "unverified"))
        performance = html.escape(label("performance", performance_code))
        priority = html.escape(label("priority", item.get("next_priority")))
        note = html.escape(str(item.get("inference_note", "")))
        rows.append(
            f'<tr><td>{requirement}</td><td>{coverage}</td>'
            f'<td><span class="status {html.escape(performance_code, quote=True)}">{performance}</span></td>'
            f'<td>{priority}</td><td>{note}</td></tr>'
        )
    if not rows:
        return '<p class="muted">本场未提供 JD 或尚未生成能力矩阵。</p>'
    return (
        '<div class="table-wrap"><table><thead><tr>'
        '<th>能力要求</th><th>覆盖</th><th>表现</th><th>优先级</th><th>证据说明</th>'
        f'</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
    )


def jd_source(case_path: Path, manifest: dict[str, Any]) -> str:
    inputs = manifest.get("inputs") or {}
    jd = inputs.get("jd") if isinstance(inputs, dict) else None
    if not isinstance(jd, dict):
        return '<p class="muted">本场未提供 JD。</p>'
    status = jd.get("extraction_status")
    if status == "failed":
        return '<p class="muted">已保存 JD 文件，但文本提取失败，暂无法展示内容。</p>'
    filename = jd.get("text")
    if not isinstance(filename, str) or not filename or Path(filename).name != filename:
        return '<p class="muted">JD 文本路径无效，暂无法展示内容。</p>'
    source = read_text(case_path / filename)
    if source.startswith("# JD\n"):
        source = source.removeprefix("# JD\n").strip()
    if not source.strip():
        return '<p class="muted">JD 文本为空，暂无法展示内容。</p>'
    methods = jd.get("pdf_pages") or []
    if status == "manual_transcription_from_image":
        note = "以下为根据 JD 截图人工转写的文本，请与原图核对。"
    elif isinstance(methods, list) and any(
        isinstance(item, dict) and item.get("method") != "text_layer" for item in methods
    ):
        note = "扫描版 JD 的文字由 OCR 提取，请与原件核对。"
    elif status == "ok":
        note = "以下是导入时保存的 JD 文本。"
    else:
        note = "以下是导入时保存的 JD 文本；提取方式未标注，请核对原件。"
    return (
        '<div class="card"><p class="muted">'
        f'{html.escape(note)}</p><pre class="jd-text">{html.escape(source)}</pre></div>'
    )


def claims_table(claims: dict[str, Any]) -> str:
    rows: list[str] = []
    for item in claims.get("claims", []):
        verdict = str(item.get("verdict", "unverified"))
        sources: list[str] = []
        for source in item.get("sources", []):
            url = safe_url(str(source.get("url", "")))
            title = str(source.get("title") or "来源")
            if url:
                sources.append(external_link(url, title))
        evidence = "、".join(str(value) for value in item.get("evidence_segments", []))
        rows.append(
            f'<tr><td><code>{html.escape(str(item.get("claim_id", "")))}</code></td>'
            f'<td>{html.escape(str(item.get("claim", "")))}</td>'
            f'<td><span class="status {html.escape(verdict)}">{VERDICT_LABELS.get(verdict, verdict)}</span></td>'
            f'<td>{html.escape(str(item.get("explanation", "")))}</td>'
            f'<td>{"<br>".join(sources) or "—"}</td>'
            f'<td>{html.escape(evidence) or "—"}</td></tr>'
        )
    if not rows:
        return '<p class="muted">本场没有技术 claim。</p>'
    return (
        '<div class="table-wrap"><table><thead><tr>'
        '<th>ID</th><th>原子结论</th><th>判定</th><th>说明</th><th>来源</th><th>片段</th>'
        f'</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
    )


def transcript_segments(case_path: Path) -> dict[str, dict[str, Any]]:
    source = case_path / "transcript.revised.json"
    if not source.is_file():
        source = case_path / "transcript.raw.json"
    transcript = read_json(source, {})
    return {
        str(item.get("segment_id")): item
        for item in transcript.get("segments", [])
        if isinstance(item, dict) and item.get("segment_id")
    }


def practice_items(case_path: Path) -> list[dict[str, Any]]:
    qa = read_json(case_path / "qa-tree.json", {})
    scores = read_json(case_path / "scores.json", {})
    score_map = {
        str(item.get("question_id")): item
        for item in scores.get("questions", [])
        if isinstance(item, dict)
    }
    questions = [item for item in qa.get("questions", []) if isinstance(item, dict)]
    by_id = {str(item.get("question_id")): item for item in questions}
    children: dict[str, list[dict[str, Any]]] = {}
    for item in questions:
        parent_id = item.get("parent_id")
        if isinstance(parent_id, str) and parent_id:
            children.setdefault(parent_id, []).append(item)
    items: list[dict[str, Any]] = []
    for index, item in enumerate(questions):
        qid = str(item.get("question_id", ""))
        if not qid or not str(item.get("question", "")).strip():
            continue
        category = str(item.get("category", ""))
        score = score_map.get(qid, {})
        total = score.get("total")
        technical = category in {
            "technical", "algorithm", "coding", "system_design", "design",
            "architecture", "database", "messaging",
        }
        parent_id = str(item.get("parent_id") or "")
        items.append({
            "question": item,
            "score": score,
            "parent_question": str(by_id.get(parent_id, {}).get("question", "")),
            "recorded_followups": children.get(qid, []),
            "rank": (0 if technical else 1, float(total) if isinstance(total, (int, float)) else 99, index),
        })
    return sorted(items, key=lambda item: item["rank"])


def practice_attempt_history(case_path: Path, question_id: str, limit: int = 3) -> str:
    """只读展示本题最近练习；正文来自私有路径并经过 HTML 转义。"""
    root = case_path.parents[1]
    qa = read_json(case_path / "qa-tree.json", {})
    case_id = str(qa.get("case_id") or "")
    log = root / "practice" / "attempts.jsonl"
    if not case_id or not log.is_file():
        return '<div class="practice-history" data-practice-history><p class="muted">暂无已保存练习记录。</p></div>'
    rows: list[dict[str, Any]] = []
    malformed = False
    for line in log.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            malformed = True
            continue
        if isinstance(item, dict) and item.get("case_id") == case_id and item.get("question_id") == question_id:
            rows.append(item)
    cards: list[str] = []
    for item in rows[-limit:][::-1]:
        answer_path = root / str(item.get("answer_path") or "")
        try:
            private_root = (root / "practice" / "private").resolve()
            if private_root not in answer_path.resolve().parents:
                raise ValueError("练习正文路径越界")
            answer = answer_path.read_text(encoding="utf-8").strip()
        except (OSError, ValueError):
            answer = "练习正文暂时不可读，请检查本机档案。"
        cards.append(
            '<article class="practice-history-card">'
            f'<p class="practice-history-meta"><strong>{html.escape(str(item.get("recorded_at") or "未知时间"))}</strong>'
            f' · 自评 {html.escape(str(item.get("self_rating") or "未记录"))} · 待审核</p>'
            f'<p>{html.escape(answer)}</p></article>'
        )
    warning = '<p class="notice">练习记录索引需检查，部分记录未能读取。</p>' if malformed else ""
    return '<div class="practice-history" data-practice-history><h5>最近练习记录</h5>' + warning + (
        "".join(cards) if cards else '<p class="muted">暂无已保存练习记录。</p>'
    ) + '</div>'


def case_answer_map(case_path: Path) -> dict[str, dict[str, Any]]:
    payload = read_json(case_path / "practice-answers.json", {})
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        return {}
    questions = payload.get("questions", [])
    if not isinstance(questions, list):
        return {}
    return {
        str(item["question_id"]): item
        for item in questions
        if isinstance(item, dict) and isinstance(item.get("question_id"), str)
    }


def question_source_map(case_path: Path) -> dict[str, list[dict[str, str]]]:
    """Collect verified claim sources by question without changing analysis data."""
    payload = read_json(case_path / "claims.json", {})
    result: dict[str, list[dict[str, str]]] = {}
    seen: dict[str, set[str]] = {}
    for claim in payload.get("claims", []):
        if not isinstance(claim, dict):
            continue
        qid = str(claim.get("question_id") or "")
        if not qid:
            continue
        for source in claim.get("sources", []):
            if not isinstance(source, dict):
                continue
            url = safe_url(str(source.get("url") or ""))
            if not url or url in seen.setdefault(qid, set()):
                continue
            seen[qid].add(url)
            result.setdefault(qid, []).append({
                "title": str(source.get("title") or "技术核验资料"),
                "section": str(source.get("section") or ""),
                "url": url,
            })
    return result


def standard_answer_map(root: Path) -> dict[str, list[dict[str, Any]]]:
    """Load user-reviewed cross-case answers without upgrading their status."""
    payload = read_json(root / "profile" / "standard-answers.json", {})
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        return {}
    answers = payload.get("answers", [])
    if not isinstance(answers, list):
        return {}
    result: dict[str, list[dict[str, Any]]] = {}
    for item in answers:
        if not isinstance(item, dict):
            continue
        topic = str(item.get("topic") or "")
        versions = item.get("versions")
        status = str(item.get("status") or "")
        if topic not in TOPIC_LABELS or status not in {"pending", "confirmed"}:
            continue
        if not isinstance(versions, dict):
            continue
        if not str(versions.get("short_30s") or "").strip() or not str(versions.get("standard_2m") or "").strip():
            continue
        result.setdefault(topic, []).append(item)
    return result


def standard_answer_question_map(root: Path) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Map one real question to every applicable mature answer, preserving file order."""
    result: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for answers in standard_answer_map(root).values():
        for answer in answers:
            for ref in answer.get("source_questions", []):
                if not isinstance(ref, dict):
                    continue
                key = (str(ref.get("case_id") or ""), str(ref.get("question_id") or ""))
                if all(key):
                    result.setdefault(key, []).append(answer)
    return result


def review_buttons(kind: str, item_id: str, status: str, *, include_result: bool = True) -> str:
    """Render offline review controls; JavaScript only queues an exportable action."""
    result = (
        '<span data-review-result>尚未选择；导出并应用后才会生效</span>'
        if include_result else ''
    )
    return (
        f'<div class="review-actions" data-review-item data-review-kind="{html.escape(kind, quote=True)}" '
        f'data-review-id="{html.escape(item_id, quote=True)}" data-review-current="{html.escape(status, quote=True)}">'
        '<button type="button" aria-pressed="false" data-review-status="confirmed">确认正确</button>'
        '<button type="button" aria-pressed="false" data-review-status="pending">暂不确定</button>'
        '<button type="button" aria-pressed="false" class="danger" data-review-status="rejected">不应采用</button>'
        '<button type="button" class="undo" data-review-undo hidden>撤销选择</button>'
        f'{result}</div>'
    )


def render_standard_answer(
    answer: dict[str, Any],
    cases: dict[str, dict[str, Any]],
    *,
    editable: bool = False,
) -> str:
    """Render one cross-case answer while preserving evidence and review state."""
    title = str(answer.get("title") or "").strip() or "成熟答案（标题未记录）"
    status = str(answer.get("status") or "pending")
    status_text = "已确认" if status == "confirmed" else "未核对草稿"
    answer_kind = "已确认的跨场答案" if status == "confirmed" else "跨场答案草稿"
    basis = str(answer.get("basis") or "").strip()
    versions = answer.get("versions") if isinstance(answer.get("versions"), dict) else {}
    short_answer = str(versions.get("short_30s") or "").strip()
    standard_answer = str(versions.get("standard_2m") or "").strip()
    followups = versions.get("followups") if isinstance(versions.get("followups"), list) else []
    followup_html = []
    for item in followups:
        if not isinstance(item, dict):
            continue
        question = str(item.get("question") or "").strip()
        response = str(item.get("answer") or "").strip()
        if question and response:
            followup_html.append(
                f'<details><summary>{html.escape(question)}</summary><p>{html.escape(response)}</p></details>'
            )
    limitations = [
        str(value).strip() for value in answer.get("limitations", [])
        if isinstance(value, str) and value.strip()
    ] if isinstance(answer.get("limitations"), list) else []
    limitation_html = (
        '<div class="standard-limitations"><strong>待补证据</strong><ul>'
        + "".join(f'<li>{html.escape(value)}</li>' for value in limitations)
        + '</ul></div>'
    ) if limitations else ""
    evidence_links = []
    for ref in answer.get("source_questions", []):
        if not isinstance(ref, dict):
            continue
        case_id = str(ref.get("case_id") or "")
        question_id = str(ref.get("question_id") or "")
        case = cases.get(case_id)
        if not case or not question_id:
            continue
        href = f'interviews/{quote(str(case["name"]))}/{CASE_HTML}#practice-{quote(question_id)}'
        evidence_links.append(
            f'<a href="{html.escape(href, quote=True)}">{html.escape(str(case["company"]))} · {html.escape(question_id)}</a>'
        )
    evidence_html = (
        '<p class="standard-evidence"><strong>回答依据：</strong>' + "、".join(evidence_links) + '</p>'
        if evidence_links else '<p class="muted"><strong>回答依据：</strong>尚未绑定可追溯问题。</p>'
    )
    source_links = []
    for source in answer.get("sources", []):
        if not isinstance(source, dict):
            continue
        url = safe_url(str(source.get("url") or ""))
        if not url:
            continue
        source_links.append(external_link(url, str(source.get("title") or "官方资料")))
    sources_html = (
        '<p class="standard-evidence"><strong>技术核验：</strong>' + "、".join(source_links) + '</p>'
        if source_links else ""
    )
    if status == "pending":
        review_html = (
            '<p class="standard-review">需要修改这份草稿时，可在下方修订；不会影响直接练习。</p>'
            if editable else
            f'<p class="standard-review">这是未经核对的参考草稿。<a href="{PENDING_REVIEW_HTML}">按需修订 →</a></p>'
        )
    else:
        review_html = '<p class="standard-review confirmed"><strong>状态：</strong>该版本已由用户确认；后续修改仍须追加修订。</p>'
    answer_id = str(answer.get("answer_id") or "")
    editor_html = (
        '<details class="answer-editor"><summary>修订或确认这份答案</summary>'
        '<label>标题<input data-edit-title type="text" value="'
        f'{html.escape(title, quote=True)}"></label>'
        '<label>归并依据<textarea data-edit-basis rows="3">'
        f'{html.escape(basis)}</textarea></label>'
        '<label>30 秒版<textarea data-edit-short rows="5">'
        f'{html.escape(short_answer)}</textarea></label>'
        '<label>2 分钟版<textarea data-edit-standard rows="10">'
        f'{html.escape(standard_answer)}</textarea></label>'
        '<label>待补证据（每行一项）<textarea data-edit-limitations rows="4">'
        f'{html.escape(chr(10).join(limitations))}</textarea></label>'
        '<details class="technical-details"><summary>高级设置（JSON，一般不用修改）</summary>'
        '<label>常见追问<textarea data-edit-followups rows="8">'
        f'{html.escape(json.dumps(followups, ensure_ascii=False, indent=2))}</textarea></label>'
        '<label>技术资料（JSON 数组）<textarea data-edit-sources rows="6">'
        f'{html.escape(json.dumps(answer.get("sources", []), ensure_ascii=False, indent=2))}</textarea></label>'
        '<label>回答依据（JSON 数组）<textarea data-edit-source-questions rows="6">'
        f'{html.escape(json.dumps(answer.get("source_questions", []), ensure_ascii=False, indent=2))}</textarea></label></details>'
        f'{review_buttons("standard_answer", answer_id, status)}</details>'
    ) if editable else ""
    search = " ".join([
        title, basis, short_answer, standard_answer,
        " ".join(str(item.get("question") or "") + " " + str(item.get("answer") or "")
                 for item in followups if isinstance(item, dict)),
        " ".join(limitations),
    ]).lower()
    return (
        f'<details class="standard-answer" id="standard-answer-{html.escape(answer_id, quote=True)}" data-standard-answer data-review-search-row data-search="{html.escape(search, quote=True)}">'
        '<summary class="standard-heading">'
        f'<div><div class="standard-kicker">{answer_kind}</div><h3>{html.escape(title)}</h3></div>'
        f'<span class="status {"strong" if status == "confirmed" else "unverified"}">{status_text}</span></summary>'
        '<div class="standard-body">'
        f'<p class="muted">{html.escape(basis)}</p>'
        '<div class="standard-versions">'
        f'<div><h4>30 秒版</h4><p>{html.escape(short_answer)}</p></div>'
        f'<details><summary>展开 2 分钟版</summary><p>{html.escape(standard_answer)}</p></details></div>'
        + (f'<div class="standard-followups"><h4>常见追问</h4>{"".join(followup_html)}</div>' if followup_html else "")
        + limitation_html + review_html + evidence_html + sources_html + editor_html + '</div></details>'
    )


def ungrouped_question_options(
    cases: list[dict[str, Any]], clusters: list[Any], *, include_grouped: bool = False,
) -> list[dict[str, str]]:
    """List question-bank entries, optionally including their active group."""
    active_assignments: dict[tuple[str, str], dict[str, str]] = {}
    for candidate in clusters:
        if not isinstance(candidate, dict) or candidate.get("status") not in {"pending", "confirmed"}:
            continue
        for ref in candidate.get("questions", []):
            if isinstance(ref, dict):
                active_assignments[(str(ref.get("case_id") or ""), str(ref.get("question_id") or ""))] = {
                    "cluster_id": str(candidate.get("cluster_id") or ""),
                    "cluster_label": str(candidate.get("label") or "未命名题组"),
                }
    options: list[dict[str, str]] = []
    for case in cases:
        segments = transcript_segments(case["path"])
        for entry in practice_items(case["path"]):
            question = entry["question"]
            key = (str(case["case_id"]), str(question.get("question_id") or ""))
            if key in active_assignments and not include_grouped:
                continue
            question_text = str(question.get("question") or "").strip()
            if question_text:
                originals = [
                    str(segments[segment_id].get("text_corrected") or segments[segment_id].get("text_raw") or "").strip()
                    for segment_id in question.get("question_evidence", [])
                    if segment_id in segments and segments[segment_id].get("role") == "interviewer"
                ]
                options.append({
                    "case_id": key[0], "question_id": key[1], "question": question_text,
                    "company": str(case.get("company") or ""),
                    "date": str(case.get("date") or ""),
                    "original": max(originals, key=len, default=""),
                    **active_assignments.get(key, {}),
                })
    return options


def render_question_clusters(
    root: Path,
    cases: list[dict[str, Any]],
    *,
    editable: bool = False,
    status_filter: set[str] | None = None,
    cluster_ids: set[str] | None = None,
    section_id: str = "frequent-questions",
    section_title: str = "高频重复问法",
    section_intro: str | None = None,
    question_page: str = "",
) -> tuple[str, str, int]:
    payload = read_json(root / "profile" / "question-clusters.json", {})
    clusters = payload.get("clusters", []) if isinstance(payload, dict) else []
    if not isinstance(clusters, list):
        return "", "", 0
    case_by_id = {str(case["case_id"]): case for case in cases}
    question_index: dict[tuple[str, str], str] = {}
    for case in cases:
        for entry in practice_items(case["path"]):
            question = entry["question"]
            question_index[(str(case["case_id"]), str(question.get("question_id") or ""))] = str(question.get("question") or "")
    ungrouped_questions = ungrouped_question_options(cases, clusters)
    rendered = []
    for cluster in clusters:
        if not isinstance(cluster, dict) or cluster.get("status") == "rejected":
            continue
        cluster_id = str(cluster.get("cluster_id") or "")
        if cluster_ids is not None and cluster_id not in cluster_ids:
            continue
        if status_filter is not None and str(cluster.get("status") or "pending") not in status_filter:
            continue
        refs = cluster.get("questions", [])
        refs = refs if isinstance(refs, list) else []
        valid_refs = []
        for ref in refs:
            if not isinstance(ref, dict):
                continue
            key = (str(ref.get("case_id") or ""), str(ref.get("question_id") or ""))
            case = case_by_id.get(key[0])
            question = question_index.get(key)
            if case and question:
                valid_refs.append((case, key[1], question))
        if len(valid_refs) < 2:
            continue
        case_count = len({str(case["case_id"]) for case, _, _ in valid_refs})
        status = str(cluster.get("status") or "pending")
        rendered.append((
            -case_count,
            -len(valid_refs),
            str(cluster.get("label") or ""),
            cluster,
            valid_refs,
        ))
    rendered.sort(key=lambda item: item[:3])
    cards = []
    bookmarks = []
    for _, _, _, cluster, refs in rendered:
        cluster_id = str(cluster.get("cluster_id") or "")
        label_text = str(cluster.get("label") or "")
        status = str(cluster.get("status") or "pending")
        case_count = len({str(case["case_id"]) for case, _, _ in refs})
        links = []
        for case, qid, question in refs:
            anchor = f'practice-{case["case_id"]}-{qid}'
            href = f'{question_page}#{anchor}' if question_page else f'#{anchor}'
            links.append(
                f'<li><a href="{html.escape(href, quote=True)}">{html.escape(question)}</a>'
                f'<span>{html.escape(str(case["company"]))}</span></li>'
            )
        note = str(cluster.get("note") or "").strip()
        note_html = f'<p class="muted">{html.escape(note)}</p>' if note else ""
        search = " ".join([label_text, note] + [question for _, _, question in refs]).lower()
        card_id = f'cluster-{cluster_id}'
        editor_html = (
            '<details class="answer-editor"><summary>确认这些问题是否属于同一类</summary>'
            '<p class="muted">判断标准：这些原题是否在问同一个核心问题。是就选“确认正确”；分组明显不合理就选“不应采用”；拿不准就选“暂不确定”。</p>'
            '<label>这组问题的名称<input data-edit-cluster-label type="text" value="'
            f'{html.escape(label_text, quote=True)}"></label>'
            f'<label>为什么归为一组（可选）<textarea data-edit-cluster-note rows="3">{html.escape(note)}</textarea></label>'
            f'<details class="technical-details"><summary>高级设置：{len(refs)} 道问题的原始引用（一般不用修改）</summary>'
            '<label>真实问题引用（JSON）<textarea data-edit-cluster-questions rows="8">'
            f'{html.escape(json.dumps(cluster.get("questions", []), ensure_ascii=False, indent=2))}</textarea></label></details>'
            '</details>'
        ) if editable else ""
        review_html = review_buttons("question_cluster", cluster_id, status, include_result=False) if editable else ""
        feedback_html = (
            '<p class="cluster-action-feedback" data-review-result hidden>尚未选择</p>'
            if editable else ""
        )
        if editable:
            action_bar = (
                '<div class="cluster-action-bar" role="group" aria-label="题组确认">'
                f'<span class="sr-only">题组确认</span>{review_html}'
                '</div>'
            )
        else:
            action_bar = ""
        membership_html = render_cluster_membership_manager(cluster, ungrouped_questions)
        cards.append(
            f'<article class="cluster-card" id="{html.escape(card_id, quote=True)}" data-question-cluster data-review-search-row '
            f'data-search="{html.escape(search, quote=True)}"><div class="cluster-heading">'
            f'<h3>{html.escape(label_text)}</h3><span class="status {"strong" if status == "confirmed" else "unverified"}">'
            f'{"已确认" if status == "confirmed" else "AI 归并 · 未核对"}</span></div>'
            f'<p class="cluster-frequency">覆盖 {case_count} 场 · {len(refs)} 道真实问题</p>{note_html}'
            f'<details><summary>查看归入本组的问题</summary><ul>{"".join(links)}</ul></details>'
            f'{action_bar}{feedback_html}{editor_html}{membership_html}</article>'
        )
        bookmarks.append(
            f'<a class="bookmark" data-cluster-bookmark="{html.escape(card_id, quote=True)}" href="#{html.escape(card_id, quote=True)}">'
            f'<span>{html.escape(label_text)}</span><span>{case_count} 场</span></a>'
        )
    if not cards:
        return "", "", 0
    intro = section_intro or "这里统计语义相同或面试意图相近的真实问题，不再把整个主题都视为同一道题。未核对分组属于 AI 判断，可继续拆分或合并。"
    section = (
        f'<section class="cluster-section" id="{html.escape(section_id, quote=True)}" data-cluster-section>'
        f'<div class="topic-heading"><h2>{html.escape(section_title)}</h2><span class="topic-frequency">按跨场语义归并排序</span></div>'
        f'<p class="muted">{html.escape(intro)}</p>'
        f'<div class="cluster-grid">{"".join(cards)}</div></section>'
    )
    return section, "".join(bookmarks), len(cards)


def segment_links(ids: Any, base: str) -> str:
    links = []
    if not isinstance(ids, list):
        return "—"
    for segment_id in ids:
        if not isinstance(segment_id, str) or not re.fullmatch(r"s-\d{3,}", segment_id):
            continue
        links.append(f'<a href="{html.escape(base + "#" + segment_id, quote=True)}">{html.escape(segment_id)}</a>')
    return "、".join(links) or "—"


def answer_view(answer: Any, summary: str) -> str:
    if not isinstance(answer, dict) or not isinstance(answer.get("steps"), list):
        return ""
    steps = []
    for step in answer["steps"]:
        if not isinstance(step, dict):
            continue
        heading = str(step.get("heading") or "").strip()
        body = str(step.get("body") or "").strip()
        if heading and body:
            steps.append(f'<li><strong>{html.escape(heading)}：</strong>{html.escape(body)}</li>')
    if not steps:
        return ""
    scope = str(answer.get("scope") or "").strip()
    scope_html = f'<p class="muted">{html.escape(scope)}</p>' if scope else ""
    validation = str(answer.get("validation") or "").strip()
    validation_html = f'<p><strong>如何验收：</strong>{html.escape(validation)}</p>' if validation else ""
    code = str(answer.get("code") or "").strip()
    code_html = f'<pre class="answer-code"><code>{html.escape(code)}</code></pre>' if code else ""
    sources = []
    for source in answer.get("sources", []):
        if not isinstance(source, dict):
            continue
        url = safe_url(str(source.get("url") or ""))
        if url:
            title = str(source.get("title") or "官方来源")
            sources.append(external_link(url, title))
    sources_html = f'<p class="answer-sources"><strong>核验资料：</strong>{"、".join(sources)}</p>' if sources else ""
    return (
        f'<details class="reference-answer"><summary>{html.escape(summary)}</summary>'
        '<p><strong>AI 整理的参考答法，非当场回答。</strong></p>'
        f'{scope_html}<ol>{"".join(steps)}</ol>{code_html}{validation_html}{sources_html}</details>'
    )


def case_reference_view(practice_ref: dict[str, Any], standards: list[dict[str, Any]]) -> str:
    """Put an existing answer beside its source interview, without changing its provenance."""
    curated = practice_ref.get("curated_answer")
    if isinstance(curated, dict):
        versions = curated.get("versions", {})
        short = str(versions.get("short_30s") or "").strip()
        full = str(versions.get("standard_2m") or "").strip()
        if short and full:
            return ('<section class="case-reference" data-saved-answer-view data-saved-answer-kind="case"><div class="case-reference-heading">'
                    '<strong>我的练习答案</strong><span>已核对 · 本题专用</span></div>'
                    f'<div class="case-reference-short"><strong>30 秒版</strong><p data-saved-answer-short>{html.escape(short)}</p></div>'
                    f'<details><summary>查看 2 分钟版</summary><p>{html.escape(full)}</p></details></section>')
    direct = answer_view(practice_ref.get("reference_answer"), "本题参考答法")
    if direct:
        return (
            '<section class="case-reference"><div class="case-reference-heading">'
            '<strong>AI 参考答案</strong><span>本题逐题稿</span></div>'
            + direct.replace('<details class="reference-answer">',
                             '<details class="reference-answer" open>', 1)
            + '</section>'
        )
    if standards:
        answer = standards[0]
        versions = answer.get("versions", {})
        title = str(answer.get("title") or "").strip() or "跨场参考答案（标题未记录）"
        short = str(versions.get("short_30s") or "").strip()
        full = str(versions.get("standard_2m") or "").strip()
        if short and full:
            status = "已确认" if answer.get("status") == "confirmed" else "未核对草稿"
            answer_id = quote(str(answer.get("answer_id") or ""))
            others = f'；另有 {len(standards) - 1} 份关联答案' if len(standards) > 1 else ""
            return (
                '<section class="case-reference"><div class="case-reference-heading">'
                f'<strong>AI 参考答案</strong><span>{status} · 跨场整理</span></div>'
                f'<p class="case-reference-title">{html.escape(title)}</p>'
                '<p class="case-reference-caution">这份答案关联到本题，可借鉴；请先核对问法和个人事实。</p>'
                f'<div class="case-reference-short"><strong>30 秒版</strong><p>{html.escape(short)}</p></div>'
                f'<details><summary>查看 2 分钟版</summary><p>{html.escape(full)}</p></details>'
                f'<p><a href="../../{ANSWERS_HTML}#standard-answer-{html.escape(answer_id, quote=True)}">'
                f'查看来源、追问与限制{others} →</a></p></section>'
            )
    return '<p class="case-reference-empty"><strong>AI 参考答案：</strong>本题尚无已保存的参考版本。</p>'


def case_answer_save_form(case_id: str, question_id: str, practice_ref: dict[str, Any],
                          standards: list[dict[str, Any]]) -> str:
    curated = practice_ref.get("curated_answer")
    versions = curated.get("versions", {}) if isinstance(curated, dict) else {}
    if not isinstance(versions, dict):
        versions = {}
    short = str(versions.get("short_30s") or "")
    full = str(versions.get("standard_2m") or "")
    direct = practice_ref.get("reference_answer")
    steps = direct.get("steps", []) if isinstance(direct, dict) else []
    for step in steps if isinstance(steps, list) else []:
        if not isinstance(step, dict):
            continue
        heading = str(step.get("heading") or "").strip()
        body = str(step.get("body") or "").strip()
        if not short and re.search(r"30\s*秒", heading):
            short = body
        elif not full and re.search(r"2\s*分钟", heading):
            full = body
    if (not short or not full) and standards:
        standard_versions = standards[0].get("versions", {})
        if isinstance(standard_versions, dict):
            short = short or str(standard_versions.get("short_30s") or "")
            full = full or str(standard_versions.get("standard_2m") or "")
    fingerprint = answer_fingerprint(curated if isinstance(curated, dict) else {})
    return (
        f'<details class="answer-confirm" data-answer-confirm data-draft-key="answer:case:{html.escape(case_id, quote=True)}:{html.escape(question_id, quote=True)}" data-answer-kind="case" '
        f'data-case-id="{html.escape(case_id, quote=True)}" '
        f'data-question-id="{html.escape(question_id, quote=True)}" '
        f'data-answer-fingerprint="{fingerprint}">'
        '<summary>修改并保存本题答案</summary>'
        '<p>可从已有参考稿修改；核对事实后点击保存，直接写入本题档案。现场回答和 AI 点评不会改变。</p>'
        f'<label>30 秒版<textarea data-answer-short rows="4">{html.escape(short)}</textarea></label>'
        f'<label>2 分钟版<textarea data-answer-full rows="8">{html.escape(full)}</textarea></label>'
        '<label class="answer-facts-check"><input type="checkbox" data-confirm-facts> '
        '我已核对个人职责、项目、结果和数字</label>'
        '<button type="button" data-confirm-save>保存并确认到档案</button>'
        '<span data-confirm-result role="status"></span></details>'
    )


def home_reference_view(practice_ref: dict[str, Any], standards: list[dict[str, Any]]) -> tuple[str, str]:
    """Show the saved answer beside the home exercise without promoting drafts to facts."""
    curated = practice_ref.get("curated_answer")
    if isinstance(curated, dict) and isinstance(curated.get("versions"), dict):
        versions = curated["versions"]
        short = str(versions.get("short_30s") or "").strip()
        full = str(versions.get("standard_2m") or "").strip()
        if short and full:
            return ('<section class="home-reference" data-saved-answer-view data-saved-answer-kind="case"><h4>本题已保存答案</h4>'
                    f'<div class="home-reference-short"><strong>30 秒版</strong><p data-saved-answer-short>{html.escape(short)}</p></div>'
                    f'<details><summary>查看 2 分钟版</summary><p>{html.escape(full)}</p></details>'
                    '<p class="muted">这是本题专用练习答案；现场回答仍以原始记录为准。</p></section>',
                    f'30 秒版：{short}\n2 分钟版：{full}')
    if standards:
        answer = standards[0]
        versions = answer.get("versions", {})
        title = str(answer.get("title") or "").strip() or "跨场参考答案（标题未记录）"
        short_answer = str(versions.get("short_30s") or "").strip()
        full_answer = str(versions.get("standard_2m") or "").strip()
        status = "已确认" if answer.get("status") == "confirmed" else "未核对草稿"
        answer_id = quote(str(answer.get("answer_id") or ""))
        more = f'；另有 {len(standards) - 1} 份相关答案' if len(standards) > 1 else ""
        return (
            f'<section class="home-reference" data-saved-answer-view data-saved-answer-kind="standard" data-saved-answer-id="{html.escape(answer.get("answer_id") or "", quote=True)}"><div class="home-reference-heading">'
            f'<h4>已生成的参考答案</h4><span class="status {"strong" if status == "已确认" else "unverified"}">{status}</span></div>'
            f'<p class="home-reference-title">{html.escape(title)}</p>'
            f'<div class="home-reference-short"><strong>30 秒版</strong><p data-saved-answer-short>{html.escape(short_answer)}</p></div>'
            f'<details><summary>查看 2 分钟版</summary><p>{html.escape(full_answer)}</p></details>'
            '<p class="muted">根据多场回答整理，仅供练习；个人职责和数据以真实经历核对。</p>'
            f'<p><a href="{ANSWERS_HTML}#standard-answer-{html.escape(answer_id, quote=True)}">查看来源与追问{more} →</a></p></section>',
            f'{title}（{status}）\n30 秒版：{short_answer}\n2 分钟版：{full_answer}',
        )
    direct = practice_ref.get("reference_answer")
    direct_html = answer_view(direct, "逐题参考答法")
    if direct_html:
        steps = direct.get("steps", []) if isinstance(direct, dict) else []
        prompt = "\n".join(
            f'{str(step.get("heading") or "").strip()}：{str(step.get("body") or "").strip()}'
            for step in steps if isinstance(step, dict)
        )
        return (
            '<section class="home-reference"><h4>已生成的参考答案</h4>'
            + direct_html.replace('<details class="reference-answer">', '<details class="reference-answer" open>', 1)
            + f'<p><a href="{ANSWERS_HTML}#question-answers">查看全部已保存答案 →</a></p></section>',
            prompt,
        )
    return (
        '<p class="home-reference-empty">这题尚无已保存的参考答案；可先用现场回答和新想法请 AI 打磨。</p>',
        "",
    )


def home_answer_save_form(case_id: str, question_id: str, practice_ref: dict[str, Any],
                          standards: list[dict[str, Any]]) -> str:
    """Save the answer displayed on the home card to its matching archive scope."""
    if practice_ref.get("curated_answer") or not standards:
        return case_answer_save_form(case_id, question_id, practice_ref, standards)
    answer = standards[0]
    versions = answer.get("versions", {})
    if not isinstance(versions, dict):
        return case_answer_save_form(case_id, question_id, practice_ref, standards)
    answer_id = str(answer.get("answer_id") or "")
    short = str(versions.get("short_30s") or "")
    full = str(versions.get("standard_2m") or "")
    if not answer_id or not short.strip() or not full.strip():
        return case_answer_save_form(case_id, question_id, practice_ref, standards)
    return (
        f'<details class="answer-confirm" data-answer-confirm data-draft-key="answer:standard:{html.escape(answer_id, quote=True)}" data-answer-kind="standard" data-answer-id="{html.escape(answer_id, quote=True)}" '
        f'data-answer-fingerprint="{answer_fingerprint(answer)}">'
        '<summary>选好了？在这里保存并确认</summary>'
        '<p>把满意的 30 秒版和 2 分钟版贴在下面；核对事实后点击保存，直接写入本机档案。</p>'
        f'<label>30 秒版<textarea data-answer-short rows="4">{html.escape(short)}</textarea></label>'
        f'<label>2 分钟版<textarea data-answer-full rows="8">{html.escape(full)}</textarea></label>'
        '<label class="answer-facts-check"><input type="checkbox" data-confirm-facts> '
        '我已核对个人职责、项目、结果和数字</label>'
        '<button type="button" data-confirm-save>保存并确认到档案</button>'
        '<span data-confirm-result role="status"></span>'
        '</details>'
    )


def render_cluster_membership_manager(
    cluster: dict[str, Any],
    ungrouped_questions: list[dict[str, str]],
    *,
    summary: str = "把未归组原题加入本题组",
    show_grouped: bool = False,
) -> str:
    """Render a local-only group search with eligible questions selectable."""
    cluster_id = str(cluster.get("cluster_id") or "")
    if not cluster_id or not ungrouped_questions:
        return (
            '<p class="cluster-membership-empty">暂无未归组原题；题组关系仍需通过本机入口维护。</p>'
            if cluster_id else ""
        )
    options = []
    for item in ungrouped_questions:
        assigned_group = item.get("cluster_id") or ""
        search = " ".join((item.get("question") or "", item.get("original") or "", item.get("company") or "", item.get("date") or "", item.get("cluster_label") or "")).lower()
        original = item.get("original") or ""
        displayed_original = original[:180] + ("…" if len(original) > 180 else "")
        original_html = f'<small>面试官原话：{html.escape(displayed_original)}</small>' if original and original != item["question"] else ""
        if assigned_group:
            state = "已在本组" if assigned_group == cluster_id else f'已归入「{item.get("cluster_label") or "未命名题组"}」'
            link = (
                f'<a href="{QUESTION_BANK_HTML}#cluster-{quote(assigned_group)}">查看原题组</a>'
                if assigned_group != cluster_id else ""
            )
            options.append(
                f'<div class="cluster-add-option cluster-add-assigned" data-cluster-add-option '
                f'data-cluster-add-search="{html.escape(search, quote=True)}">'
                f'<span>{html.escape(item["question"])}</span>'
                f'<small>{html.escape(item.get("company") or "")} · {html.escape(item.get("date") or "")} · '
                f'{html.escape(item["question_id"])} · {html.escape(state)} {link}</small>'
                f'{original_html}</div>'
            )
            continue
        options.append(
            f'<label class="cluster-add-option" data-cluster-add-option '
            f'data-cluster-add-search="{html.escape(search, quote=True)}">'
            f'<input type="checkbox" data-cluster-add-checkbox '
            f'data-case-id="{html.escape(item["case_id"], quote=True)}" '
            f'data-question-id="{html.escape(item["question_id"], quote=True)}">'
            f'<span>{html.escape(item["question"])}</span>'
            f'<small>{html.escape(item.get("company") or "")} · {html.escape(item.get("date") or "")} · '
            f'{html.escape(item["question_id"])}</small>{original_html}</label>'
        )
    intro = (
        '<p class="muted">可按整理后的题面或面试官原话搜索。未归组原题可勾选加入；已归组原题会标出所在题组，不能重复加入。两题题组移出任一道时，会解散整个题组。</p>'
        if show_grouped else '<p class="muted">只显示题库中尚未归组的原题。选择后点击保存，直接加入当前题组。</p>'
    )
    search_label = "搜索题库原题" if show_grouped else "搜索未归组原题"
    return (
        f'<details class="cluster-membership-manager" data-cluster-membership '
        f'data-cluster-id="{html.escape(cluster_id, quote=True)}" '
        f'data-cluster-status="{html.escape(str(cluster.get("status") or "pending"), quote=True)}" '
        f'data-cluster-fingerprint="{answer_fingerprint(cluster)}">'
        f'<summary>{html.escape(summary)}</summary>'
        + intro
        + f'<label>{search_label}<input type="search" data-cluster-add-search-input placeholder="输入原话、题面、公司或日期"></label>'
        +
        '<div class="cluster-add-options" data-cluster-add-options>' + "".join(options) + '</div>'
        '<span data-cluster-add-count>已选择 0 题</span>'
        '<button type="button" data-cluster-add>保存到本机档案</button>'
        '<span class="cluster-add-result" data-cluster-add-result role="status"></span></details>'
    )


def home_related_questions(root: Path, cases: list[dict[str, Any]],
                           case_id: str, question_id: str) -> tuple[str, str, int]:
    """Show each original question once, with its independent relations."""
    selected = (case_id, question_id)
    references: dict[tuple[str, str], list[dict[str, str]]] = {}

    def add_refs(refs: Any, relation: dict[str, str], *, include_selected: bool = False) -> None:
        if not isinstance(refs, list):
            return
        for ref in refs:
            if not isinstance(ref, dict):
                continue
            key = (str(ref.get("case_id") or ""), str(ref.get("question_id") or ""))
            if (key == selected and not include_selected) or (key[0] == case_id and key != selected) or not all(key):
                continue
            current = references.setdefault(key, [])
            relation_key = (relation.get("kind", ""), relation.get("id", ""))
            if not any((item.get("kind", ""), item.get("id", "")) == relation_key for item in current):
                current.append(relation)

    cluster_payload = read_json(root / "profile" / "question-clusters.json", {})
    clusters = cluster_payload.get("clusters", []) if isinstance(cluster_payload, dict) else []
    selected_cluster: dict[str, Any] | None = None
    for cluster in clusters:
        if not isinstance(cluster, dict) or cluster.get("status") not in {"confirmed", "pending"}:
            continue
        refs = cluster.get("questions", [])
        if not isinstance(refs, list) or not any(
            isinstance(ref, dict) and
            (str(ref.get("case_id") or ""), str(ref.get("question_id") or "")) == selected
            for ref in refs
        ):
            continue
        selected_cluster = cluster
        add_refs(refs, {
            "kind": "question_cluster",
            "id": str(cluster.get("cluster_id") or ""),
            "label": "同类问题分组",
            "detail": ("这道原题归在「" + str(cluster.get("label") or "未命名题组")
                       + "」中，可与组内其他场次并列对照。"),
            "action": "从这个题组移出",
            "fingerprint": answer_fingerprint(cluster),
            "status": str(cluster.get("status") or "pending"),
        }, include_selected=True)

    rows: list[str] = []
    prompt_parts: list[str] = []
    related_cases: set[str] = set()
    for case in cases:
        other_id = str(case["case_id"])
        if not any(key[0] == other_id for key in references):
            continue
        segments = transcript_segments(case["path"])
        for entry in practice_items(case["path"]):
            question = entry["question"]
            other_qid = str(question.get("question_id") or "")
            relations = references.get((other_id, other_qid))
            if not relations:
                continue
            raw_parts = [
                str(segments[segment_id].get("text_corrected") or segments[segment_id].get("text_raw") or "").strip()
                for segment_id in question.get("question_evidence", [])
                if segment_id in segments and segments[segment_id].get("role") == "interviewer"
            ]
            raw_question = max(raw_parts, key=len, default="")
            question_text = raw_question or str(question.get("question") or "")
            answer_parts = [
                str(segments[segment_id].get("text_corrected") or segments[segment_id].get("text_raw") or "").strip()
                for segment_id in question.get("answer_evidence", [])
                if segment_id in segments and segments[segment_id].get("role") == "candidate"
            ]
            old_answer = " ".join(part for part in answer_parts if part)
            href = f'interviews/{quote(str(case["name"]))}/{CASE_HTML}#practice-{quote(other_qid)}'
            related_cases.add(other_id)
            relation_rows: list[str] = []
            for relation in relations:
                relation_kind = relation.get("kind") or ""
                relation_id = relation.get("id") or ""
                remaining = [
                    ref for ref in selected_cluster.get("questions", [])
                    if isinstance(ref, dict) and
                    (str(ref.get("case_id") or ""), str(ref.get("question_id") or "")) != (other_id, other_qid)
                ] if selected_cluster and relation_kind == "question_cluster" else []
                keeps_valid_cluster = relation_kind != "question_cluster" or (
                    len(remaining) >= 2 and len({str(ref.get("case_id") or "") for ref in remaining}) >= 2
                )
                dissolves_cluster = relation_kind == "question_cluster" and not keeps_valid_cluster
                action_label = "移出并解散题组" if dissolves_cluster else (relation.get("action") or "移出这条关联")
                button = (
                    f'<button type="button" class="relation-remove" data-relation-remove '
                    f'data-relation-kind="{html.escape(relation_kind, quote=True)}" '
                    f'data-relation-id="{html.escape(relation_id, quote=True)}" '
                    f'data-relation-status="{html.escape(relation.get("status") or "pending", quote=True)}" '
                    f'data-relation-fingerprint="{html.escape(relation.get("fingerprint") or "", quote=True)}" '
                    f'data-relation-case-id="{html.escape(other_id, quote=True)}" '
                    f'data-relation-question-id="{html.escape(other_qid, quote=True)}" '
                    f'data-relation-dissolves-cluster="{str(dissolves_cluster).lower()}">'
                    f'{html.escape(str(action_label))}</button>'
                )
                relation_rows.append(
                    '<div class="related-relation"><span class="related-relation-copy">'
                    f'<strong>{html.escape(relation.get("label") or "关联")}</strong>'
                    f'<span>{html.escape(relation.get("detail") or "")}</span></span>{button}</div>'
                )
            relation_labels = " · ".join(relation.get("label") or "关联" for relation in relations)
            relation_details = "；".join(relation.get("detail") or "" for relation in relations)
            rows.append(
                '<li><details class="related-question">'
                '<summary>'
                f'<span class="related-meta">{html.escape(str(case["date"]))} · '
                f'{html.escape(str(case["company"]))} · {html.escape(str(case["round"]))}</span>'
                f'<span class="related-title">{html.escape(question_text)}</span>'
                f'<span class="related-link-state">{html.escape(relation_labels)}</span>'
                '</summary><div class="related-answer-body">'
                f'{"".join(relation_rows)}'
                '<strong>当时回答</strong>'
                f'<div class="related-answer-text">{html.escape(old_answer) if old_answer else "未能可靠定位"}</div>'
                f'<a href="{html.escape(href, quote=True)}">查看本场证据 →</a>'
                '</div></details></li>'
            )
            if len("\n".join(prompt_parts)) < 6000:
                prompt_parts.append(
                    f'{case["case_id"]} / {other_qid} · {case["date"]} · {case["company"]} · {case["round"]}（{relation_details}）\n'
                    f'原题：{question_text[:300]}\n当时回答：{old_answer[:650] or "未能可靠定位"}'
                )

    if not rows:
        return "", "", 0
    membership_html = ""
    if selected_cluster is not None:
        membership_html = render_cluster_membership_manager(
            selected_cluster, ungrouped_question_options(cases, clusters, include_grouped=True),
            summary="从题库搜索，加入这个题组",
            show_grouped=True,
        )
    heading = (
        f'同题组的原题与旧回答 · {len(related_cases)} 场 {len(rows)} 题'
        if selected_cluster else
        f'其他场次的相关问法与旧回答 · {len(related_cases)} 场 {len(rows)} 题'
    )
    explanation = (
        '组内每道原题并列展示；移出题组不会删除原题或现场回答。'
        if selected_cluster else
        '这里只显示与当前题有关联的其他场次原题及当时回答。'
    )
    markup = (
        f'<section class="related-questions"><h4>{heading}</h4>'
        f'<p class="muted">{explanation}</p>'
        f'{membership_html}'
        f'<ul>{"".join(rows)}</ul></section>'
    )
    return markup, "\n\n".join(prompt_parts)[:7000], len(related_cases)


def question_card(case: dict[str, Any], entry: dict[str, Any], segments: dict[str, dict[str, Any]],
                  from_case: bool, practice_ref: dict[str, Any] | None = None,
                  related_sources: list[dict[str, str]] | None = None,
                  search_tags: str = "", fallback_standards: list[dict[str, Any]] | None = None,
                  tags: dict[str, Any] | None = None, preference: str = "normal",
                  home_focus: bool = False, related_html: str = "", related_prompt: str = "",
                  related_case_count: int = 0, home_grouped: bool = False,
                  home_group_id: str = "", home_group_label: str = "") -> str:
    question = entry["question"]
    score = entry["score"]
    qid = str(question.get("question_id", ""))
    base = "" if from_case else f'interviews/{quote(str(case["name"]))}/{CASE_HTML}'
    card_id = f"practice-{qid}" if from_case else f'practice-{case["case_id"]}-{qid}'
    question_title = str(question.get("question") or "").strip() or "题面未记录（请核对原题）"
    title = html.escape(question_title)
    if not from_case:
        title = f'<a href="{html.escape(base + "#practice-" + qid, quote=True)}">{title}</a>'
    question_ids = question.get("question_evidence", [])
    answer_ids = question.get("answer_evidence", [])
    interviewer_parts = [
        str(segments[segment_id].get("text_corrected") or segments[segment_id].get("text_raw") or "").strip()
        for segment_id in question_ids if segment_id in segments
        and segments[segment_id].get("role") == "interviewer"
    ] if isinstance(question_ids, list) else []
    original = max(interviewer_parts, key=len, default="")
    original = original[:240] + ("…" if len(original) > 240 else "")
    original_html = (
        f'<p class="practice-original"><strong>原始问题：</strong>“{html.escape(original)}”</p>'
        if original else '<p class="muted">面试官原话未能可靠定位；请核对提问证据。</p>'
    )
    candidate_parts = [
        str(segments[segment_id].get("text_corrected") or segments[segment_id].get("text_raw") or "").strip()
        for segment_id in answer_ids if segment_id in segments
        and segments[segment_id].get("role") == "candidate"
    ] if isinstance(answer_ids, list) else []
    candidate_answer_full = " ".join(part for part in candidate_parts if part)
    candidate_answer = candidate_answer_full[:1200] + ("…" if len(candidate_answer_full) > 1200 else "")
    candidate_answer_for_ai = candidate_answer_full[:4000] + ("…（现场回答较长，已截取前 4000 字）" if len(candidate_answer_full) > 4000 else "")
    answer_html = (
        f'<div class="practice-answer"><strong>我的现场回答：</strong><p>{html.escape(candidate_answer)}</p></div>'
        if candidate_answer else
        '<p class="muted"><strong>我的现场回答：</strong>未能可靠定位候选人回答，请核对回答证据。</p>'
    )
    total = score.get("total")
    score_text = f"本场复盘 {total:g}/20" if isinstance(total, (int, float)) else "本场未评分"
    parent = entry["parent_question"]
    parent_html = f'<p class="practice-parent">追问上题：{html.escape(parent)}</p>' if parent else ""
    notes = str(score.get("notes") or "")
    notes_html = (
        f'<p class="practice-diagnosis"><strong>回答问题（AI 判断）：</strong>{html.escape(notes)}</p>'
        if notes else
        '<p class="muted"><strong>回答问题（AI 判断）：</strong>尚未形成明确的逐题诊断。</p>'
    )
    recorded = []
    for child in entry.get("recorded_followups", []):
        child_id = str(child.get("question_id", ""))
        child_text = str(child.get("question", "")).strip()
        if child_id and child_text:
            href = html.escape(base + "#practice-" + child_id, quote=True)
            recorded.append(f'<li><a href="{href}">{html.escape(child_text)}</a></li>')
    recorded_html = (
        '<div class="practice-followups"><strong>问答树记录的追问</strong><ul>'
        + "".join(recorded) + '</ul></div>' if recorded else ""
    )
    practice_ref = practice_ref or {}
    point = str(practice_ref.get("knowledge_point") or "").strip()
    point_html = f'<p class="practice-point"><strong>练习知识点：</strong>{html.escape(point)}</p>' if point else ""
    followups = practice_ref.get("followups")
    followups = [str(value).strip() for value in followups if isinstance(value, str) and value.strip()] if isinstance(followups, list) else []
    if followups:
        answers = practice_ref.get("followup_answers")
        answers = answers if isinstance(answers, list) else []
        followup_items = []
        for index, followup in enumerate(followups):
            answer = answer_view(answers[index] if index < len(answers) else None, "查看本题参考答案")
            followup_items.append(f'<li>{html.escape(followup)}{answer}</li>')
        first_followup = f'<ol>{followup_items[0]}</ol>'
        more_followups = (
            f'<details><summary>再看 {len(followups)-1} 道延伸追问</summary><ol start="2">'
            + "".join(followup_items[1:])
            + '</ol></details>' if len(followups) > 1 else ""
        )
        extension_html = (
            '<div class="practice-followups generated"><strong>延伸追问 · 练习题，非面试原话</strong>'
            + first_followup + more_followups + '</div>'
        )
    else:
        extension_html = ""
    related_links = []
    for source in related_sources or []:
        url = safe_url(str(source.get("url") or ""))
        if not url:
            continue
        source_title = str(source.get("title") or "技术核验资料")
        section = str(source.get("section") or "").strip()
        label_text = f"{source_title} · {section}" if section else source_title
        related_links.append(external_link(url, label_text))
    sources_html = (
        '<p class="practice-docs"><strong>相关文档：</strong>' + "、".join(related_links) + '</p>'
        if related_links else
        '<p class="muted"><strong>相关文档：</strong>暂无与本题绑定的已核验资料链接。</p>'
    )
    category = str(question.get("category") or "未分类")
    tags = tags if isinstance(tags, dict) else {}
    tag_groups = (
        ("领域", tags.get("domain", [])),
        ("意图", tags.get("intent", [])),
        ("对象/场景", tags.get("object_scene", [])),
        ("约束", tags.get("constraints", [])),
        ("复用", [str(tags.get("reusability") or "未标注")]),
    )
    tag_html = '<div class="question-tags">' + "".join(
        f'<span class="tag">{html.escape(name)}：{html.escape("、".join(str(value) for value in values))}</span>'
        for name, values in tag_groups if isinstance(values, list) and values
    ) + "</div>"
    search_text = " ".join([
        str(case.get("company") or ""), str(case.get("role") or ""), str(case.get("round") or ""),
        category, search_tags, str(question.get("question") or ""), original, candidate_answer, notes,
        " ".join(str(value) for _, values in tag_groups for value in values),
    ]).lower()
    recommended = answer_view(practice_ref.get("reference_answer"), "查看推荐答案")
    if not recommended and fallback_standards:
        fallback_parts = []
        for fallback_standard in fallback_standards:
            answer_title = str(fallback_standard.get("title") or "成熟答案")
            answer_id = str(fallback_standard.get("answer_id") or "")
            fallback_parts.append(
                f'<a href="#standard-answer-{html.escape(quote(answer_id), quote=True)}">'
                f'{html.escape(answer_title)}（{"已确认" if fallback_standard.get("status") == "confirmed" else "未核对草稿"}）</a>'
            )
        recommended = (
            f'<div class="reference-answer-list"><strong>相关跨场参考答案（{len(fallback_parts)} 份）：</strong>'
            + "、".join(fallback_parts)
            + '<p class="muted">打开一次完整答案；它不替代本题的现场回答。</p></div>'
        )
    if not recommended:
        recommended = '<p class="answer-missing"><strong>推荐答案：</strong>尚未整理或尚未完成来源核验。</p>'
    history_details = (
        '<details class="practice-history-details">'
        f'<summary>{"查看现场回答、AI 点评与参考答案" if from_case else "查看旧回答与 AI 点评"}</summary>'
        f'{answer_html}{notes_html}'
        f'{case_reference_view(practice_ref, fallback_standards or []) if from_case else ""}'
        f'{case_answer_save_form(str(case["case_id"]), qid, practice_ref, fallback_standards or []) if from_case else ""}'
        '</details>'
    )
    practice_widget = ""
    home_reference_html, reference_for_ai = home_reference_view(practice_ref, fallback_standards or []) if home_focus else ("", "")
    save_form = home_answer_save_form(str(case["case_id"]), qid, practice_ref,
                                      fallback_standards or []) if home_focus else ""
    ai_copy_note = (
        "复制内容含同题组各场原题与现场回答、已有参考答案和你的补充；发送前可先核对。"
        if home_grouped else
        "复制内容含本题及相关场次的现场回答摘录、已有参考答案和你的补充；发送前可先核对。"
    )
    if not from_case:
        practice_history = practice_attempt_history(case["path"], qid)
        practice_widget = (
            f'<div class="answer-workbench" data-practice-widget data-draft-key="supplement:{html.escape(str(case["case_id"]), quote=True)}:{html.escape(qid, quote=True)}" data-case-id="{html.escape(str(case["case_id"]), quote=True)}" '
            f'data-home-group-id="{html.escape(home_group_id, quote=True)}" '
            f'data-question-id="{html.escape(qid, quote=True)}">'
            '<h4>和 AI 一起打磨回答</h4>'
            '<p>可直接用你的现场回答做底稿；有新的想法或事实，在下面补充。AI 生成前会先区分事实和建议。</p>'
            f'{home_reference_html}'
            '<label>我现在想补充或改写的内容（可选）'
            '<textarea data-practice-answer rows="5" placeholder="例如：我当时漏讲了自己的职责、判断依据或失败边界。留空则用现场回答。"></textarea></label>'
            '<div class="draft-controls"><span data-draft-status role="status">补充内容默认仅保存在当前标签页</span><button type="button" data-draft-clear>清除补充草稿</button></div>'
            '<button type="button" data-ai-improve>复制给 AI 打磨答案</button>'
            f'<p class="ai-copy-note">{ai_copy_note}</p>'
            '<span class="ai-improve-result" data-ai-result role="status"></span>'
            '<textarea class="ai-prompt-preview" data-ai-prompt-preview hidden readonly rows="12" aria-label="复制给 AI 的改进请求"></textarea>'
            f'<textarea data-ai-question hidden>{html.escape(home_group_label if home_grouped else str(question.get("question") or ""))}</textarea>'
            f'<textarea data-ai-original hidden>{"" if home_grouped else html.escape(original)}</textarea>'
            f'<textarea data-ai-past-answer hidden>{"" if home_grouped else html.escape(candidate_answer_for_ai)}</textarea>'
            f'<textarea data-ai-related-answers hidden>{html.escape(related_prompt)}</textarea>'
            f'<textarea data-ai-reference hidden>{html.escape(reference_for_ai)}</textarea>'
            f'<textarea data-ai-diagnosis hidden>{"" if home_grouped else html.escape(notes)}</textarea>'
            f'{save_form}'
            '<details class="practice-recorder"><summary>需要时记录一次练习</summary>'
            '<div class="timer-line"><button type="button" data-timer-start>开始计时</button>'
            '<button type="button" data-timer-stop disabled>停止</button><strong data-timer-display>00:00</strong></div>'
            '<p class="muted">先在上方填写本次回答；计时与自评仅用于个人练习记录。</p>'
            '<label>自评<select data-practice-rating><option value="">请选择</option>'
            '<option value="1">1 · 无法回答</option><option value="2">2 · 缺口明显</option>'
            '<option value="3">3 · 基本完整</option><option value="4">4 · 稳定</option><option value="5">5 · 可应对追问</option>'
            '</select></label><label>备注（可选）<input data-practice-note type="text" placeholder="例如：缺少指标或失败边界"></label>'
            '<button type="button" data-practice-save>保存本次练习</button>'
            '<p class="practice-save-note">优先直接保存到本机私有档案，状态会标为“已入库 · 待审核”。服务暂时不可用时，才下载“待应用”文件。</p>'
            '<span data-practice-result></span></details>' + practice_history + '</div>'
        )
    if home_focus:
        source_label = f'{html.escape(str(case["company"]))} · {html.escape(str(case["round"]))}'
        source_meta = '' if home_grouped else f'<div class="practice-meta">{source_label}</div>'
        context_summary = (
            f'查看同题组原题与旧回答 · {related_case_count} 场'
            if home_grouped else
            '查看原题、旧回答与参考资料'
            + (f' · 另有 {related_case_count} 场相关问法' if related_case_count else '')
        )
        context_body = (
            related_html if home_grouped else
            f'<p><a href="{html.escape(base + "#practice-" + qid, quote=True)}">查看本场完整复盘 →</a></p>'
            f'<p class="muted">{html.escape(qid)} · 问答树整理后的练习题</p>'
            f'{tag_html}{parent_html}{original_html}{history_details}{related_html}'
            f'<p class="practice-links">提问：{segment_links(question_ids, base)} · 回答：{segment_links(answer_ids, base)}</p>'
            f'{sources_html}{point_html}{recorded_html}{extension_html}'
        )
        return (
            f'<article class="practice-card home-practice-card" id="{html.escape(card_id, quote=True)}" '
            f'data-question-row data-search="{html.escape(search_text, quote=True)}">'
            f'{source_meta}'
            f'<h3>{html.escape(home_group_label if home_grouped else question_title)}</h3>'
            f'<details class="practice-context"><summary>{context_summary}</summary>'
            f'{context_body}'
            f'</details>{practice_widget}</article>'
        )
    return (
        f'<article class="practice-card" id="{html.escape(card_id, quote=True)}" data-question-row '
        f'data-search="{html.escape(search_text, quote=True)}">'
        f'<div class="practice-meta">{html.escape(str(case["date"]))} · {html.escape(str(case["company"]))} · '
        f'{html.escape(str(case["round"]))} · {html.escape(category)} · {html.escape(score_text)}</div>'
        f'<h3>{title}</h3>'
        f'{practice_widget}'
        f'<p class="muted">{html.escape(qid)} · 问答树整理后的练习题；原话见下方片段。</p>'
        f'{tag_html}'
        f'{parent_html}{original_html}{history_details}'
        f'<p class="practice-links">提问：{segment_links(question_ids, base)} · 回答：{segment_links(answer_ids, base)}</p>'
        f'{sources_html}{point_html}{recorded_html}{extension_html}{recommended if not from_case else ""}'
        '</article>'
    )


def render_case_practice(case: dict[str, Any]) -> str:
    items = practice_items(case["path"])
    if not items:
        return '<section id="practice"><h2>用本场问题练习</h2><p class="muted">暂无可用的问题树。</p></section>'
    segments = transcript_segments(case["path"])
    answers = case_answer_map(case["path"])
    sources = question_source_map(case["path"])
    root = case["path"].parent.parent
    tags_map = question_tag_map(root)
    standards_by_question = standard_answer_question_map(root)
    focus = read_json(case["path"].parent.parent / "profile" / "practice-focus.json", {})
    refs = {}
    if isinstance(focus, dict) and isinstance(focus.get("groups"), list):
        for group in focus["groups"]:
            if isinstance(group, dict) and isinstance(group.get("questions"), list):
                for ref in group["questions"]:
                    if isinstance(ref, dict) and ref.get("case_id") == case["case_id"]:
                        refs[str(ref.get("question_id", ""))] = ref
    def render_item(item: dict[str, Any]) -> str:
        qid = str(item["question"].get("question_id", ""))
        return question_card(case, item, segments, True, {**answers.get(qid, {}), **refs.get(qid, {})},
                             sources.get(qid, []),
                             fallback_standards=standards_by_question.get((str(case["case_id"]), qid)),
                             tags=tags_map.get((str(case["case_id"]), qid)))
    first = "".join(render_item(item) for item in items[:3])
    rest = "".join(render_item(item) for item in items[3:])
    more = f'<details><summary>查看本场其余 {len(items)-3} 道题</summary><div class="practice-grid">{rest}</div></details>' if rest else ""
    return f'''<section id="practice" class="case-section practice-section"><div class="section-heading"><div><span class="section-kicker">练习重点</span><h2>用本场问题练习</h2></div><span class="section-note">先练评分较低的题</span></div>
<p class="muted">先显示本场技术与编码题中复盘评分较低的题目；评分是既有 AI 复盘判断，只用于定位练习，不跨岗位比较。题目可能经过归纳，请以逐字稿为准。</p>
<div class="practice-grid">{first}</div>{more}</section>'''


def home_practice_candidates(root: Path, cases: list[dict[str, Any]]) -> list[tuple[dict[str, Any], dict[str, Any], str]]:
    if not cases:
        return []
    available = [(case, practice_items(case["path"])) for case in cases]
    available = [(case, items) for case, items in available if items]
    if not available:
        return []
    available_by_key = {
        (str(candidate["case_id"]), str(entry["question"].get("question_id") or "")): (candidate, entry)
        for candidate, entries in available for entry in entries
    }
    answer_by_question = standard_answer_question_map(root)
    preferences = question_preference_map(root)
    tags = question_tag_map(root)
    case_answers = {str(case["case_id"]): case_answer_map(case["path"]) for case, _ in available}

    def needs_answer(key: tuple[str, str]) -> bool:
        if key not in available_by_key or effective_question_preference(root, preferences, *key) != "normal":
            return False
        curated = case_answers.get(key[0], {}).get(key[1], {}).get("curated_answer")
        if isinstance(curated, dict) and curated.get("status") == "confirmed":
            return False
        return not any(answer.get("status") == "confirmed" for answer in answer_by_question.get(key, []))

    candidates: list[tuple[dict[str, Any], dict[str, Any], str]] = []
    used_keys: set[tuple[str, str]] = set()
    used_answer_ids: set[str] = set()
    for goal in review_plan_goals(root, 1000):
        representative = goal.get("representative", {})
        if not isinstance(representative, dict):
            continue
        key = (str(representative.get("case_id") or ""), str(representative.get("question_id") or ""))
        if not needs_answer(key):
            continue
        case, item = available_by_key[key]
        candidates.append((case, item, str(goal.get("title") or "")))
        used_keys.add(key)
        for answer in answer_by_question.get(key, []):
            used_answer_ids.add(str(answer.get("answer_id") or ""))
            used_keys.update(
                (str(ref.get("case_id") or ""), str(ref.get("question_id") or ""))
                for ref in answer.get("source_questions", []) if isinstance(ref, dict)
            )
        break

    answers = [answer for topic_answers in standard_answer_map(root).values() for answer in topic_answers]
    answers.sort(key=lambda answer: (
        -len({str(ref.get("case_id") or "") for ref in answer.get("source_questions", []) if isinstance(ref, dict)}),
        str(answer.get("title") or ""),
    ))
    for answer in answers:
        if len(candidates) >= 5:
            break
        answer_id = str(answer.get("answer_id") or "")
        if answer.get("status") == "confirmed" or answer_id in used_answer_ids:
            continue
        refs = answer.get("source_questions", [])
        if not isinstance(refs, list):
            continue
        ref_keys = [
            (str(ref.get("case_id") or ""), str(ref.get("question_id") or ""))
            for ref in refs if isinstance(ref, dict)
        ]
        if len({key[0] for key in ref_keys}) < 2:
            continue
        eligible_refs = [
            key for key in ref_keys if key not in used_keys and needs_answer(key)
        ]
        if not eligible_refs:
            continue
        text_counts: dict[str, int] = {}
        for key in eligible_refs:
            text = str(available_by_key[key][1]["question"].get("question") or "")
            normalized = re.sub(r"[\W_]+", "", text.lower())
            text_counts[normalized] = text_counts.get(normalized, 0) + 1
        title_text = re.sub(r"[\W_]+", "", str(answer.get("title") or "").lower())
        title_pairs = {title_text[index:index + 2] for index in range(len(title_text) - 1)}

        def representative_score(key: tuple[str, str]) -> tuple[int, int, int, int]:
            text = str(available_by_key[key][1]["question"].get("question") or "")
            normalized = re.sub(r"[\W_]+", "", text.lower())
            question_pairs = {normalized[index:index + 2] for index in range(len(normalized) - 1)}
            reuse = str(tags.get(key, {}).get("reusability") or "")
            return (
                text_counts.get(normalized, 0), len(title_pairs & question_pairs),
                {"high": 2, "medium": 1}.get(reuse, 0), -len(text),
            )
        selected_ref = max(eligible_refs, key=representative_score)
        next_case, next_item = available_by_key[selected_ref]
        candidates.append((next_case, next_item, str(answer.get("title") or "")))
        used_keys.update(ref_keys)
        used_answer_ids.add(answer_id)
    if not candidates:
        for key, (case, item) in available_by_key.items():
            if needs_answer(key):
                candidates.append((case, item, ""))
                break
    return candidates


def render_history_practice(root: Path, cases: list[dict[str, Any]]) -> str:
    candidates = home_practice_candidates(root, cases)
    if not candidates:
        return (f'<section id="practice"><h2>当前没有待打磨的题目</h2>'
                f'<p>已保存的答案仍可在<a href="{ANSWERS_HTML}">答案库</a>查看；'
                f'也可以从<a href="{QUESTION_BANK_HTML}">完整题库</a>选题继续练习。'
                '答案已确认不代表已经掌握。</p></section>')
    focus = read_json(root / "profile" / "practice-focus.json", {})
    focus_refs: dict[tuple[str, str], dict[str, Any]] = {}
    if isinstance(focus, dict) and isinstance(focus.get("groups"), list):
        for group in focus["groups"]:
            if not isinstance(group, dict) or not isinstance(group.get("questions"), list):
                continue
            for ref in group["questions"]:
                if isinstance(ref, dict):
                    focus_refs[(str(ref.get("case_id") or ""), str(ref.get("question_id") or ""))] = ref
    standard_by_question = standard_answer_question_map(root)
    cluster_payload = read_json(root / "profile" / "question-clusters.json", {})
    clusters = cluster_payload.get("clusters", []) if isinstance(cluster_payload, dict) else []
    grouped_info = {
        (str(ref.get("case_id") or ""), str(ref.get("question_id") or "")): (
            str(cluster.get("cluster_id") or ""), str(cluster.get("label") or "")
        )
        for cluster in clusters
        if isinstance(cluster, dict) and cluster.get("status") in {"pending", "confirmed"}
        for ref in cluster.get("questions", [])
        if isinstance(ref, dict)
    }
    tags_by_question = question_tag_map(root)
    cards = []
    for index, (case, item, direction) in enumerate(candidates):
        case_id = str(case["case_id"])
        qid = str(item["question"].get("question_id") or "")
        practice_ref = {**case_answer_map(case["path"]).get(qid, {}), **focus_refs.get((case_id, qid), {})}
        related_html, related_prompt, related_case_count = home_related_questions(root, cases, case_id, qid)
        group_id, group_label = grouped_info.get((case_id, qid), ("", ""))
        group_label = group_label or str(item["question"].get("question") or "题组名称待核对")
        card = question_card(
            case, item, transcript_segments(case["path"]), False, practice_ref,
            question_source_map(case["path"]).get(qid, []),
            fallback_standards=standard_by_question.get((case_id, qid)),
            tags=tags_by_question.get((case_id, qid)), home_focus=True,
            related_html=related_html, related_prompt=related_prompt,
            related_case_count=related_case_count,
            home_grouped=bool(group_id), home_group_id=group_id,
            home_group_label=group_label,
        )
        cards.append(
            f'<div data-home-question data-question-title="{html.escape(group_label or str(item["question"].get("question") or ""), quote=True)}"'
            f'{" hidden" if index else ""}>'
            + (f'<p class="home-question-direction">{html.escape(direction)}</p>' if direction else "")
            + card + '</div>'
        )
    next_button = (
        '<div class="home-next"><p>保存并确认的题目会在刷新后移出首页待打磨列表；'
        '仍可在答案库和题库查看。确认答案不代表已经掌握。</p>'
        '<div class="home-navigation" aria-label="练习主题导航">'
        '<button type="button" data-home-previous disabled>上一题</button>'
        f'<span data-home-position aria-live="polite">第 1 / {len(cards)} 题</span>'
        '<button type="button" data-home-next>下一题</button>'
        '</div><span data-home-next-hint role="status"></span></div>'
    )
    return f'''<section id="practice"><h2>先把这题答完整</h2>
<p class="muted">结合不同场次的旧回答和已保存的参考稿，整理一版可复用答案。需要更多题目时，<a href="{QUESTION_BANK_HTML}">打开完整题库</a>。</p>
<div class="practice-grid" data-home-questions>{''.join(cards)}</div>{next_button}</section>'''


def discover_target_interview_preps(root: Path) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    prep_dir = root / "profile" / "target-interview-preps"
    if prep_dir.is_dir():
        for path in sorted(prep_dir.glob("*.json")):
            payload = read_json(path, {})
            if isinstance(payload, dict) and isinstance(payload.get("target"), dict):
                prepared = dict(payload)
                prepared["_slug"] = path.stem
                payloads.append(prepared)
    legacy = read_json(root / "profile" / "target-interview-prep.json", {})
    if isinstance(legacy, dict) and isinstance(legacy.get("target"), dict):
        prepared = dict(legacy)
        prepared["_slug"] = "legacy"
        payloads.append(prepared)
    deduped: dict[tuple[str, str], dict[str, Any]] = {}
    for payload in payloads:
        target = payload["target"]
        key = (str(target.get("company") or ""), str(target.get("role") or ""))
        deduped.setdefault(key, payload)
    return sorted(
        deduped.values(),
        key=lambda value: str(value.get("updated_at") or ""),
        reverse=True,
    )


def target_interview_prep_filename(payload: dict[str, Any]) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", str(payload.get("_slug") or "legacy").lower()).strip("-")
    return f"{PREPARATION_HTML_PREFIX}{slug or 'legacy'}.html"


def target_prep_evidence_href(root: Path | None, case_id: str, question_id: str) -> str | None:
    if root is None:
        return None
    for manifest_path in (root / "interviews").glob("*/manifest.json"):
        manifest = read_json(manifest_path, {})
        if str(manifest.get("case_id") or "") == case_id:
            relative = manifest_path.parent.relative_to(root).as_posix()
            return f"{relative}/interview-review.html#practice-{quote(question_id)}"
    return None


def render_target_interview_prep(payload: dict[str, Any], root: Path | None = None) -> str:
    if not isinstance(payload, dict) or not isinstance(payload.get("target"), dict):
        return ""
    target = payload["target"]
    company = str(target.get("company") or "目标公司")
    role = str(target.get("role") or "目标岗位")
    summary = str(target.get("summary") or "")
    cards: list[str] = []
    for item in payload.get("questions", []):
        if not isinstance(item, dict) or not str(item.get("question") or "").strip():
            continue
        qid = str(item.get("question_id") or f"target-q-{len(cards) + 1:03d}")
        source_type = str(item.get("source_type") or "延伸练习题")
        priority = str(item.get("priority") or "P1")
        short = str(item.get("short_30s") or "待补充")
        standard = str(item.get("standard_2m") or "")
        risks = [str(value) for value in item.get("risks", []) if str(value).strip()]
        followups = [value for value in item.get("followups", []) if isinstance(value, dict)]
        sources = [value for value in item.get("sources", []) if isinstance(value, dict)]
        evidence_refs = [value for value in item.get("evidence_refs", []) if isinstance(value, dict)]
        details: list[str] = []
        if standard:
            details.append(f'<details><summary>查看 2 分钟版</summary><p>{html.escape(standard).replace(chr(10), "<br>")}</p></details>')
        if followups:
            rows = "".join(
                f'<li><strong>{html.escape(str(value.get("question") or "追问"))}</strong>'
                f'<p>{html.escape(str(value.get("answer") or "待补充"))}</p></li>'
                for value in followups
            )
            details.append(f'<details><summary>查看追问（{len(followups)}）</summary><ol>{rows}</ol></details>')
        if risks:
            details.append('<details><summary>风险与待补证据</summary><ul>' + "".join(f'<li>{html.escape(value)}</li>' for value in risks) + '</ul></details>')
        source_links: list[str] = []
        for source in sources:
            url = safe_url(str(source.get("url") or ""))
            title = str(source.get("title") or "公开来源")
            if url:
                source_links.append(f'<li>{external_link(url, title)}</li>')
        if source_links:
            details.append('<details><summary>公开依据</summary><ul>' + "".join(source_links) + '</ul></details>')
        if evidence_refs:
            evidence_rows: list[str] = []
            for evidence in evidence_refs:
                case_id = str(evidence.get("case_id") or "")
                question_id = str(evidence.get("question_id") or "")
                href = target_prep_evidence_href(root, case_id, question_id)
                label_text = f"{case_id} / {question_id}"
                evidence_rows.append(
                    f'<li><a href="{html.escape(href, quote=True)}">{html.escape(label_text)}</a></li>'
                    if href else f'<li>{html.escape(label_text)}</li>'
                )
            details.append('<details><summary>个人经历依据</summary><ul>' + "".join(evidence_rows) + '</ul></details>')
        cards.append(
            f'<article class="target-prep-card" id="{html.escape(qid, quote=True)}">'
            f'<div class="target-prep-meta"><span class="priority">{html.escape(priority)}</span>'
            f'<span class="status unverified">{html.escape(source_type)}</span></div>'
            f'<h3>{html.escape(str(item.get("question") or ""))}</h3>'
            f'<p><strong>30 秒版：</strong>{html.escape(short).replace(chr(10), "<br>")}</p>'
            + "".join(details) + '</article>'
        )
    reverse = [str(value) for value in payload.get("reverse_questions", []) if str(value).strip()]
    reverse_html = (
        '<details class="target-prep-reverse"><summary>反问环节</summary><ol>'
        + "".join(f'<li>{html.escape(value)}</li>' for value in reverse) + '</ol></details>'
        if reverse else ""
    )
    company_source_links: list[str] = []
    for source in payload.get("company_sources", []):
        if not isinstance(source, dict):
            continue
        url = safe_url(str(source.get("url") or ""))
        title = str(source.get("title") or "公司公开来源")
        if url:
            company_source_links.append(f'<li>{external_link(url, title)}</li>')
    company_sources_html = (
        '<details><summary>公司与岗位公开来源</summary><ul>'
        + "".join(company_source_links)
        + '</ul></details>'
        if company_source_links else ""
    )
    jd = payload.get("jd")
    jd_html = '<p class="muted">未保存岗位 JD 文字；以下依据现有摘要准备。</p>'
    if isinstance(jd, dict) and str(jd.get("text") or "").strip():
        source_type = str(jd.get("source_type") or "来源未标注")
        source_note = str(jd.get("source_note") or "")
        source_url = safe_url(str(jd.get("source_url") or ""))
        source_label = external_link(source_url, source_type) if source_url else html.escape(source_type)
        jd_html = (
            '<section id="target-jd" class="target-prep-jd"><h3>岗位 JD</h3>'
            f'<p class="muted">来源：{source_label}</p>'
            + (f'<p class="muted">{html.escape(source_note)}</p>' if source_note else "")
            + f'<pre>{html.escape(str(jd["text"]))}</pre></section>'
        )
    if not cards and not reverse_html:
        return ""
    return (
        '<section id="target-prep" class="target-prep"><div class="topic-heading">'
        f'<h2>{html.escape(company)} · 面试准备</h2><span class="status unverified">{html.escape(str(payload.get("status") or "pending"))}</span></div>'
        f'<p><strong>目标岗位：</strong>{html.escape(role)}</p>'
        + (f'<p class="muted">{html.escape(summary)}</p>' if summary else "")
        + '<p class="muted">招聘方参考题、历史原题和延伸练习题已分开标注；草稿不代表真实面试原话。</p>'
        + company_sources_html
        + jd_html
        + f'<div class="target-prep-grid">{"".join(cards)}</div>{reverse_html}</section>'
    )


def render_target_prep_entry(root: Path) -> str:
    payloads = discover_target_interview_preps(root)
    if not payloads:
        return ""
    names = [str(item["target"].get("company") or "目标公司") for item in payloads]
    return (
        '<section id="target-prep-entry" class="target-prep">'
        '<div class="topic-heading"><h2>公司面试准备</h2>'
        f'<span class="status unverified">{len(payloads)} 家</span></div>'
        f'<p>{html.escape("、".join(names))}</p>'
        f'<p><a class="primary-link" href="{PREPARATIONS_HTML}">打开公司面试准备 →</a></p></section>'
    )


def render_interview_preparations(root: Path) -> str:
    payloads = discover_target_interview_preps(root)
    has_interviews = bool(discover_cases(root))
    navigation = (
        f'<nav class="topnav" aria-label="本页导航"><a href="{HISTORY_HTML}">面试总览</a>'
        f'<a href="{QUESTION_BANK_HTML}">完整题库</a><a href="{ANSWERS_HTML}">已生成答案</a></nav>'
        if has_interviews else ""
    )
    cards: list[str] = []
    for payload in payloads:
        target = payload["target"]
        company = str(target.get("company") or "目标公司")
        role = str(target.get("role") or "目标岗位")
        summary = str(target.get("summary") or "")
        question_count = len([
            item for item in payload.get("questions", [])
            if isinstance(item, dict) and str(item.get("question") or "").strip()
        ])
        cards.append(
            '<article class="target-prep-card">'
            f'<div class="target-prep-meta"><span class="priority">{question_count} 题</span>'
            f'<span class="status unverified">{html.escape(str(payload.get("status") or "pending"))}</span></div>'
            f'<h2>{html.escape(company)}</h2><p><strong>目标岗位：</strong>{html.escape(role)}</p>'
            + (f'<p class="muted">{html.escape(summary)}</p>' if summary else "")
            + f'<p><a class="primary-link" href="{target_interview_prep_filename(payload)}">打开该公司准备 →</a></p>'
            '</article>'
        )
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
{SECURITY_META}<title>公司面试准备</title><style>{STYLE}</style></head><body data-interview-preparations data-store-id="{archive_id(root)}"><main>
{navigation}
<header class="hero"><div class="eyebrow">Interview System · Company Preparation</div><h1>公司面试准备</h1>
<p>共 {len(payloads)} 家目标公司。招聘方参考题、历史原题和延伸练习题分开标注。</p></header>
{privacy_notice(compact=True)}
<section class="target-prep"><div class="target-prep-grid">{''.join(cards) or '<p class="muted">暂无目标公司准备。</p>'}</div></section>
<p class="foot">本页是准备稿，不是历史面试记录；未确认内容不会自动升级为成熟答案。</p>
</main><script>{SCRIPT}</script></body></html>"""


def render_interview_preparation(root: Path, payload: dict[str, Any]) -> str:
    target = payload["target"]
    company = str(target.get("company") or "目标公司")
    role = str(target.get("role") or "目标岗位")
    interview_links = (
        f'<a href="{HISTORY_HTML}">面试总览</a><a href="{QUESTION_BANK_HTML}">完整题库</a>'
        if discover_cases(root) else ""
    )
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
{SECURITY_META}<title>{html.escape(company)}面试准备</title><style>{STYLE}</style></head><body data-interview-preparation data-store-id="{archive_id(root)}"><main>
<nav class="topnav" aria-label="本页导航"><a href="{PREPARATIONS_HTML}">公司列表</a>{interview_links}</nav>
<header class="hero"><div class="eyebrow">Interview System · Company Preparation</div><h1>{html.escape(company)}</h1>
<p>{html.escape(role)} · 单公司面试准备</p></header>
{privacy_notice(compact=True)}
{render_target_interview_prep(payload, root)}
<p class="foot">本页是准备稿，不是历史面试记录；未确认内容不会自动升级为成熟答案。</p>
</main><script>{SCRIPT}</script></body></html>"""


def transcript_view(case_path: Path) -> str:
    source = case_path / "transcript.revised.json"
    if not source.is_file():
        source = case_path / "transcript.raw.json"
    transcript = read_json(source, {})
    blocks: list[str] = []
    for item in transcript.get("segments", []):
        segment_id = html.escape(str(item.get("segment_id", "")))
        speaker_label = html.escape(
            str(item.get("speaker_label") or item.get("speaker_id") or "未标注")
        )
        role = html.escape(label("role", item.get("role") or "unknown"))
        text = html.escape(str(item.get("text_corrected") or item.get("text_raw") or ""))
        blocks.append(
            f'<article class="segment" id="{segment_id}">'
            f'<div><code>{segment_id}</code> · {speaker_label} · {role}</div><p>{text}</p></article>'
        )
    return "".join(blocks) or '<p class="muted">暂无逐字稿片段。</p>'


def history_rows(cases: list[dict[str, Any]], current: str | None, from_case: bool) -> str:
    cards: list[str] = []
    for item in cases:
        name = str(item["name"])
        if from_case:
            href = f"../{quote(name)}/{CASE_HTML}"
        else:
            href = f"interviews/{quote(name)}/{CASE_HTML}"
        title = f'{item["company"]} · {item["role"]}'
        link = f'<a class="history-title" href="{href}">{html.escape(title)}</a>'
        next_action = item.get("next_action") or []
        action = next_action[0] if next_action else "本场未单列下一步，打开复盘查看。"
        search = " ".join(
            str(item.get(key, ""))
            for key in ("date", "company", "role", "round", "result", "excerpt")
        ).lower()
        search += " " + display_result(str(item["result"])).lower() + " " + action.lower()
        row_class = " current" if item["case_id"] == current else ""
        result_text = display_result(str(item["result"]))
        result_class = "missing" if result_is_missing(str(item["result"])) else "recorded"
        compact_action = " ".join(str(action).split())
        action_more = len(compact_action) > 260
        action_preview = compact_action[:260].rstrip() + ("…" if action_more else "")
        action_markup = f'<div class="history-next"><strong>下一步：</strong>{html.escape(action_preview)}'
        if action_more:
            action_markup += (
                '<details class="history-next-more"><summary>展开完整下一步</summary>'
                f'<div>{inline_markdown(action)}</div></details>'
            )
        action_markup += '</div>'
        cards.append(
            f'<article class="history-card{row_class}" data-history-row data-history-result-status="{result_class}" '
            f'data-search="{html.escape(search, quote=True)}">'
            f'<div class="history-meta"><span>{html.escape(str(item["date"]))}</span> · '
            f'<span class="history-round">轮次：{html.escape(str(item["round"]))}</span> · '
            f'<span class="history-result {result_class}">结果：{html.escape(result_text)}</span></div>'
            f'<h3>{link}</h3>{action_markup}'
            f'<a class="history-open" href="{href}">打开本场复盘 →</a></article>'
        )
    return "".join(cards)


STYLE = """
.merge-membership{margin:4px 0 0 28px;color:#31577d;font-size:12px;font-weight:700}.merge-membership a{font-weight:700}
.question-tags{display:flex;flex-wrap:wrap;gap:5px;margin:8px 0}.question-tags .tag{background:#f1f5f9;color:#475569;font-size:11px}.preference-actions{display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin:10px 0;padding:8px 10px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:9px}.preference-actions button{border:1px solid #cbd5e1;border-radius:7px;padding:5px 8px;background:#fff;color:#334155;font:inherit;font-size:12px;cursor:pointer}.preference-actions button[data-preference-choice="low_priority"]{color:#9a3412}.preference-actions button[data-preference-choice="ignored"]{color:#b91c1c}.preference-actions button[aria-pressed="true"]{box-shadow:0 0 0 2px #bfdbfe;background:#eff6ff;font-weight:700}.preference-label{font-size:12px;font-weight:700;color:#475569}.preference-actions [data-preference-result]{font-size:12px;color:var(--muted)}.long-tail-section{margin-top:16px}.long-tail-section>summary{font-size:18px}.cluster-card .preference-actions{margin-top:0}.cluster-action-bar{display:flex;align-items:flex-start;gap:10px;flex-wrap:nowrap;overflow-x:auto;margin-top:12px;padding:10px 0 2px;border-top:1px solid var(--line)}.cluster-action-bar>.review-actions{display:grid;grid-template-columns:repeat(4,max-content);align-items:center;gap:8px;flex:0 0 auto;margin:0}.cluster-action-bar>.preference-actions{flex:0 0 auto;margin:0}.cluster-action-bar>.review-actions [data-review-result]{grid-column:1/-1;min-width:0;flex-basis:auto;white-space:normal}.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}.merge-builder{margin:16px 0;padding:16px 18px;background:#fff;border:1px solid var(--line);border-radius:14px}.merge-builder>summary{cursor:pointer;font-weight:800;color:#1f4770}.merge-builder>label,.merge-form-grid label{display:block;margin-top:10px}.merge-builder input[type="search"],.merge-builder input[type="text"],.merge-builder select,.merge-builder textarea{width:100%;padding:8px;border:1px solid #cbd5e1;border-radius:7px;font:inherit}.merge-selection-summary{display:flex;gap:12px;flex-wrap:wrap;margin:10px 0;color:var(--muted);font-size:12px}.merge-options{max-height:360px;overflow:auto;border:1px solid var(--line);border-radius:9px;padding:6px;margin-top:8px}.merge-option{padding:8px 6px;border-bottom:1px solid #edf1f5}.merge-option:last-child{border-bottom:0}.merge-option[hidden]{display:none}.merge-option label{display:grid;grid-template-columns:auto minmax(0,1fr);column-gap:8px;align-items:start;cursor:pointer}.merge-option small{grid-column:2;color:var(--muted);font-size:11px;margin-top:3px}.merge-question-text{line-height:1.45}.merge-form-grid{display:grid;grid-template-columns:minmax(0,2fr) minmax(180px,1fr);gap:10px}.merge-builder button[data-merge-queue]{margin-top:12px;border:0;border-radius:8px;padding:8px 11px;background:var(--blue);color:#fff;font:inherit;font-weight:700;cursor:pointer}.merge-builder button.secondary{background:#64748b}.merge-builder [data-merge-result]{margin-left:8px;color:var(--muted);font-size:12px}
:root{--ink:#172033;--muted:#64748b;--paper:#f5f7fb;--card:#fff;--line:#dfe5ef;--blue:#2557d6;--cyan:#0e7490;--green:#087f5b;--amber:#b45309;--red:#c2413a}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.7 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}a{color:var(--blue);text-decoration:none}a:hover{text-decoration:underline}main{width:min(1180px,calc(100% - 28px));margin:auto;padding:24px 0 64px}.topnav{display:flex;gap:16px;align-items:center;margin:0 0 14px;font-size:13px}.hero{padding:30px;border-radius:22px;background:linear-gradient(135deg,#172554,#1d4ed8 58%,#0e7490);color:#fff;box-shadow:0 18px 42px #1e3a8a29}.hero h1{font-size:clamp(27px,5vw,44px);line-height:1.15;margin:6px 0}.eyebrow{text-transform:uppercase;letter-spacing:.12em;font-size:12px;opacity:.76}.meta{display:flex;flex-wrap:wrap;gap:8px;margin-top:17px}.badge{border:1px solid #ffffff42;background:#ffffff17;border-radius:999px;padding:4px 10px;font-size:13px}.notice{margin-top:16px;padding:13px 15px;border-left:4px solid var(--amber);background:#fff8e8;color:#70420a;border-radius:8px}section{margin-top:28px}.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:17px;box-shadow:0 3px 12px #0f172a08}.grid{display:grid;gap:14px}.scores{grid-template-columns:repeat(4,minmax(0,1fr))}.label{color:var(--muted);font-size:12px}.metric{font-size:30px;font-weight:760}.metric span{font-size:13px;color:var(--muted)}.track{height:8px;background:#e6ebf3;border-radius:99px;overflow:hidden}.track i{display:block;height:100%;background:linear-gradient(90deg,var(--cyan),var(--blue))}h2{font-size:22px;margin:0 0 12px}h3{font-size:18px;margin:22px 0 8px}h4{font-size:16px;margin:18px 0 7px}blockquote{margin:12px 0;padding:10px 14px;border-left:4px solid #93c5fd;background:#eff6ff;color:#1e3a8a;border-radius:7px}code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;background:#eef2f7;border-radius:5px;padding:1px 5px}.table-wrap{overflow:auto;border:1px solid var(--line);border-radius:12px;background:#fff}table{width:100%;border-collapse:collapse;min-width:760px}th,td{text-align:left;vertical-align:top;padding:11px;border-bottom:1px solid var(--line)}th{background:#eef2f7;color:#334155;font-size:13px}.status,.tag{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px;background:#e8eefc;color:#1d4ed8}.status.strong,.status.correct{background:#d1fae5;color:#047857}.status.medium,.status.partial{background:#e0f2fe;color:#0369a1}.status.weak,.status.incorrect{background:#ffedd5;color:#c2410c}.status.unverified{background:#e2e8f0;color:#475569}.status.personal{background:#ede9fe;color:#5b21b6}details{background:#fff;border:1px solid var(--line);border-radius:10px;padding:0 14px;margin:9px 0}summary{cursor:pointer;padding:12px 0;font-weight:650}.segment{padding:11px 0;border-bottom:1px solid var(--line);scroll-margin-top:10px}.segment p{margin:5px 0}.review{background:#fff;border:1px solid var(--line);border-radius:14px;padding:20px}.review h2:first-child{margin-top:0}.search{width:100%;max-width:520px;padding:11px 13px;border:1px solid #cbd5e1;border-radius:10px;margin:0 0 12px;font:inherit}.current{background:#eff6ff}.muted{color:var(--muted)}.foot{margin-top:24px;color:var(--muted);font-size:13px}.empty{display:none;padding:12px;color:var(--muted)}
.jd-text{white-space:pre-wrap;overflow-wrap:anywhere;margin:0;font:inherit}
.topnav{position:sticky;top:0;z-index:5;padding:10px 12px;background:#f5f7fbf2;border:1px solid var(--line);border-radius:10px;backdrop-filter:blur(8px);flex-wrap:wrap}
section{scroll-margin-top:72px}
.focus-section,.next-actions{margin-top:24px}
.focus-main{background:#eaf2ff;border:1px solid #bfd2f7;border-left:5px solid var(--blue);border-radius:14px;padding:20px 22px}
.focus-main ol{margin:8px 0 12px;padding-left:1.35rem;font-size:17px;font-weight:620;line-height:1.6}
.focus-main li+li{margin-top:7px}
.focus-label{font-size:13px;font-weight:760;color:#254c96;letter-spacing:.02em}
.focus-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;margin-top:14px}
.focus-small{background:#fff;border:1px solid var(--line);border-radius:14px;padding:16px}
.focus-small p{margin:8px 0;line-height:1.6}
.focus-small a,.focus-main a{font-size:14px;font-weight:650}
.action-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}
.action-card{background:#fff;border:1px solid var(--line);border-radius:14px;padding:18px;box-shadow:0 3px 12px #0f172a08}
.action-card h3{margin:7px 0 12px;line-height:1.4}
.action-card p{margin:8px 0;font-size:14px;line-height:1.55}
.priority{display:inline-block;background:#fff0df;color:#8b4100;border-radius:999px;padding:2px 9px;font-size:12px;font-weight:800}
.acceptance{color:#37516a}
.primary-link{display:inline-block;background:var(--blue);color:#fff;padding:9px 14px;border-radius:9px;font-weight:700}
.primary-link:hover{color:#fff}
.deep-link{margin-top:4px;font-size:14px;font-weight:650}
.search-line{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin:7px 0 14px}
.search-line .search{margin:0}.search-line span{color:var(--muted);font-size:13px}.search-clear{min-height:38px;border:1px solid #cbd5e1;border-radius:8px;padding:7px 10px;background:#fff;color:#334155;font:inherit;cursor:pointer}.search-clear[hidden]{display:none}.history-filter{display:flex;align-items:center;gap:6px;color:var(--muted);font-size:13px}.history-filter select{min-height:38px;border:1px solid #cbd5e1;border-radius:8px;padding:6px 8px;background:#fff;font:inherit;color:#334155}
.history-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:13px}
.history-card{background:#fff;border:1px solid var(--line);border-radius:14px;padding:17px;min-width:0}
.history-card h3{margin:7px 0 8px;font-size:17px;line-height:1.4}
.history-card p{margin:0 0 8px;font-size:14px;line-height:1.55}
.history-meta{display:flex;flex-wrap:wrap;gap:5px;align-items:center}.history-round{font-weight:750;color:#31577d}.history-result{font-weight:750}.history-result.recorded{color:#31577d}.history-result.missing{color:#8b5e00}.history-next{overflow-wrap:anywhere}.history-next-more{margin-top:7px;padding:0 9px;background:#f8fafc;border-radius:7px}.history-next-more summary{padding:7px 0;font-size:12px;color:#31577d}.history-next-more>div{padding:0 0 8px}
.history-card[hidden]{display:none}.history-meta{color:var(--muted);font-size:12px}
.history-open{font-size:13px;font-weight:700}.current{border-color:#8bb2f0;background:#f4f8ff}
#practice{background:#fff;border-radius:16px;padding:24px 30px}
#practice>details{border:0;border-top:1px solid var(--line);border-radius:0;padding:0;margin-top:12px}
.practice-grid{display:block}.practice-group{margin-top:28px}.practice-group h3{margin:0 0 4px}
.practice-card{background:transparent;border:0;border-bottom:1px solid var(--line);border-radius:0;padding:21px 0 23px;min-width:0;scroll-margin-top:76px}
.practice-card:last-child{border-bottom:0}.practice-card h3{margin:5px 0 8px;line-height:1.42}.practice-card p{margin:7px 0;font-size:14px;line-height:1.58}
.practice-meta{font-size:12px;color:var(--muted);font-weight:650}.practice-original{border-left:3px solid #7aa8b7;padding:4px 0 4px 12px}
.practice-answer{border-left:3px solid var(--blue);padding:4px 0 4px 12px;margin:10px 0}.practice-answer p{white-space:pre-wrap}
.practice-diagnosis{border-left:3px solid var(--amber);padding:4px 0 4px 12px}.practice-docs{overflow-wrap:anywhere}
.answer-missing{background:#fff8e8;border-radius:8px;padding:9px 11px;color:#70420a}
.practice-links{overflow-wrap:anywhere}.practice-card details{margin-top:10px}.practice-parent{color:#36526a}
.practice-point{color:#294d74}.practice-followups{border-top:1px solid var(--line);margin-top:11px;padding-top:9px;font-size:14px}
.practice-followups strong{font-size:13px;color:#244a71}.practice-followups ol,.practice-followups ul{margin:6px 0 4px;padding-left:1.4rem}
.practice-followups li+li{margin-top:5px}.practice-followups details{margin-top:7px}.practice-followups.generated{border-left:3px solid var(--cyan);padding-left:11px}
.reference-answer{background:#f3f7fa;border:0;padding:0 13px 12px}.reference-answer summary{color:#174f78}.reference-answer ol{padding-left:1.4rem}
.reference-answer li+li{margin-top:8px}.answer-sources{font-size:13px;overflow-wrap:anywhere}.answer-code{white-space:pre;overflow:auto;background:#e8eef2;border-radius:7px;padding:12px;font-size:12px;line-height:1.5}
.bank-layout{display:grid;grid-template-columns:280px minmax(0,1fr);gap:24px;align-items:start;margin-top:24px}
.bank-sidebar{position:sticky;top:72px;background:#fff;border:1px solid var(--line);border-radius:16px;padding:17px;max-height:calc(100vh - 92px);overflow:auto}
.bank-sidebar h2{font-size:17px;margin:0 0 10px}.bank-sidebar .search{max-width:none;margin:0}.bank-sidebar .search-line{display:block;margin-bottom:16px}.bank-sidebar [data-question-count]{display:block;margin-top:6px}
.practice-guide{font-size:13px;line-height:1.55;background:#eff6ff;border-radius:9px;padding:10px;margin:12px 0 16px;color:#294d74}
.bookmark-list{display:grid;gap:5px}.bookmark{display:flex;justify-content:space-between;gap:10px;padding:8px 9px;border-radius:8px;color:#26364d;font-size:13px;line-height:1.35}
.bookmark:hover{background:#eef4ff;text-decoration:none}.bookmark span:last-child{white-space:nowrap;color:var(--muted);font-size:11px}
.bank-content{min-width:0}.topic-group{background:#fff;border-radius:16px;padding:22px 28px;margin:0 0 22px;scroll-margin-top:78px}
.topic-heading{display:flex;justify-content:space-between;gap:16px;align-items:baseline;border-bottom:1px solid var(--line);padding-bottom:10px}.topic-heading h2{margin:0}.topic-frequency{font-size:13px;color:var(--muted);white-space:nowrap}
.standard-answer{margin:18px 0 4px;padding:18px;border:1px solid #b9d2ec;border-left:5px solid var(--blue);border-radius:13px;background:#f7fbff}
.standard-answer[hidden]{display:none}.standard-heading{display:flex;justify-content:space-between;gap:16px;align-items:flex-start}.standard-heading h3{margin:2px 0 0}
.standard-kicker{font-size:12px;font-weight:800;letter-spacing:.08em;color:#24549b}.standard-versions{display:grid;grid-template-columns:minmax(0,.72fr) minmax(0,1.28fr);gap:14px;margin-top:14px}
.standard-versions>div{background:#fff;border:1px solid var(--line);border-radius:10px;padding:13px}.standard-versions h4{margin:0 0 7px}.standard-versions p{white-space:pre-wrap;margin:0}
.standard-followups h4{margin-bottom:7px}.standard-followups details{background:#fff}.standard-limitations{margin-top:13px;padding:11px 13px;background:#fff8e8;color:#70420a;border-radius:9px}
.standard-limitations ul{margin:5px 0 0;padding-left:1.3rem}.standard-evidence{font-size:13px;overflow-wrap:anywhere}
.standard-review{margin-top:12px;padding:9px 11px;border-radius:8px;background:#eef4ff;color:#294d74}.standard-review.confirmed{background:#e7f8f0;color:#176448}
.bookmark-title{font-size:13px;margin:16px 0 6px;color:#475569}.cluster-section{background:#fff;border-radius:16px;padding:22px 28px;margin:0 0 22px;scroll-margin-top:78px}
.cluster-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin-top:16px}.cluster-card{border:1px solid var(--line);border-radius:12px;padding:15px;scroll-margin-top:78px}
.cluster-card[hidden]{display:none}.cluster-heading{display:flex;justify-content:space-between;gap:10px;align-items:flex-start}.cluster-heading h3{font-size:16px;margin:0}.cluster-frequency{font-size:13px;color:#31577d;font-weight:700;margin:7px 0}
.cluster-card details{border:0;border-top:1px solid var(--line);border-radius:0;padding:0;margin-top:10px}.cluster-card ul{padding-left:1.2rem}.cluster-card li+li{margin-top:7px}.cluster-card li span{display:block;color:var(--muted);font-size:12px}
.review-center,.learning-dashboard,.pending-group{background:#fff;border:1px solid var(--line);border-radius:16px;padding:22px 28px;margin:0 0 22px}.review-entry{display:flex;align-items:center;justify-content:space-between;gap:18px;background:#eef4ff;border:1px solid #c9daf0;border-radius:14px;padding:17px 20px;margin:0 0 22px}.review-entry h2,.review-entry p{margin:0}.review-entry p{margin-top:4px}.review-entry .primary-link{white-space:nowrap}.review-toolbar,.review-actions,.timer-line{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.review-toolbar{padding:12px;background:#f7f9fc;border-radius:10px;margin-top:14px;position:sticky;top:10px;z-index:6}.review-toolbar[hidden]{display:none}.review-more{font-size:13px;margin-left:auto}.review-more summary{cursor:pointer}.review-more label{display:block;margin-top:8px}.review-more button{margin-top:8px}.review-toolbar button,.review-actions button,.practice-recorder button{border:0;border-radius:8px;padding:8px 11px;background:var(--blue);color:#fff;font:inherit;font-size:13px;font-weight:700;cursor:pointer}.review-toolbar .secondary,.review-actions button[data-review-status="pending"]{background:#64748b}.review-actions .danger{background:var(--red)}.review-actions .undo{background:#fff;color:#475569;border:1px solid #cbd5e1}.review-actions button[aria-pressed="true"]{box-shadow:0 0 0 3px #bfdbfe;transform:translateY(-1px)}.review-actions button[aria-pressed="true"]::before{content:"✓ "}.review-actions [hidden]{display:none}.review-actions [data-review-result]{flex-basis:100%;padding:7px 9px;border-radius:7px;background:#f8fafc}.review-actions [data-review-result].selected{color:#176448;background:#e7f8f0;font-weight:700}.review-actions span,.review-toolbar span,.practice-recorder span{font-size:12px;color:var(--muted)}.review-list{display:grid;gap:8px;padding-bottom:12px}.review-row{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:14px;padding:11px;border-bottom:1px solid var(--line)}.review-row p{margin:3px 0}.review-row small{color:var(--muted)}.review-evidence{margin:8px 0;padding:8px 10px;background:#f8fafc;border-left:3px solid #cbd5e1;border-radius:6px}.review-evidence p{margin:4px 0}.pending-backlog{background:#f8fafc;border:1px solid var(--line);border-radius:14px;padding:0;margin:0 0 22px}.pending-backlog>summary{cursor:pointer;padding:17px 20px;font-weight:800;color:#334155}.pending-backlog[open]>summary{border-bottom:1px solid var(--line)}.pending-backlog>.pending-group{margin:18px;border-radius:12px}.priority-note{margin:-8px 0 16px;color:#475569}
.external-note{font-size:.78em;color:var(--muted);white-space:nowrap}
.answer-editor label,.practice-recorder label{display:grid;gap:5px;margin:10px 0;font-size:13px;font-weight:700}.answer-editor textarea,.answer-editor input,.practice-recorder textarea,.practice-recorder input,.practice-recorder select{width:100%;border:1px solid #cbd5e1;border-radius:8px;padding:9px;font:inherit;background:#fff}.technical-details{margin:12px 0!important;padding:9px 11px!important;border:1px dashed #cbd5e1!important;border-radius:9px!important;background:#f8fafc}.technical-details summary{color:#475569;font-size:13px}.practice-recorder{background:#f8fbff}.timer-line strong{font-variant-numeric:tabular-nums;font-size:18px}.dashboard-grid{display:grid;grid-template-columns:minmax(0,.9fr) minmax(0,1.1fr);gap:18px}.review-queue{padding-left:1.3rem}.review-queue li+li{margin-top:9px}.review-queue span{display:block;color:var(--muted);font-size:12px}.learning-dashboard table{min-width:520px}
.preference-menu{display:inline-block;position:relative;vertical-align:middle;margin:6px 0}.preference-menu>summary{list-style:none;cursor:pointer;border:1px solid #cbd5e1;border-radius:7px;padding:5px 9px;background:#fff;color:#334155;font:inherit;font-size:12px;white-space:nowrap}.preference-menu>summary::-webkit-details-marker{display:none}.preference-menu>summary:focus-visible{outline:3px solid #bfdbfe;outline-offset:1px}.preference-menu[open]>summary{background:#eff6ff;border-color:#93c5fd}.preference-menu-options{position:absolute;z-index:5;display:grid;gap:5px;min-width:150px;margin-top:4px;padding:7px;background:#fff;border:1px solid #cbd5e1;border-radius:8px;box-shadow:0 8px 24px #0f172a22}.preference-menu-options button{border:1px solid #cbd5e1;border-radius:6px;padding:5px 8px;background:#fff;color:#334155;font:inherit;font-size:12px;text-align:left;cursor:pointer;white-space:nowrap}.preference-menu-options button[data-preference-choice="low_priority"]{color:#9a3412}.preference-menu-options button[data-preference-choice="ignored"]{color:#b91c1c}.preference-menu-options button[aria-pressed="true"]{box-shadow:0 0 0 2px #bfdbfe;background:#eff6ff;font-weight:700}.cluster-card .preference-menu{margin:0}.cluster-action-bar{display:flex;align-items:center;gap:8px;flex-wrap:wrap;overflow:visible;margin-top:12px;padding:8px 0 2px;border-top:1px solid var(--line)}.cluster-action-bar>.review-actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap;flex:0 0 auto;margin:0}.cluster-action-bar>.preference-menu{flex:0 0 auto;margin:0}.cluster-action-feedback{margin:5px 0 0;padding:5px 8px;border-radius:6px;background:#f8fafc;color:var(--muted);font-size:12px}.cluster-action-feedback.selected{color:#176448;background:#e7f8f0;font-weight:700}
@media(max-width:760px){main{width:min(100% - 18px,1180px);padding-top:10px}.hero{padding:22px;border-radius:16px}.scores{grid-template-columns:repeat(2,minmax(0,1fr))}.review{padding:15px}}
@media(max-width:760px){.focus-grid,.history-grid,.action-grid,.bank-layout,.standard-versions,.cluster-grid,.dashboard-grid,.review-steps,.merge-form-grid{grid-template-columns:1fr}.review-row{grid-template-columns:1fr}.review-entry{align-items:flex-start;flex-direction:column}.bank-sidebar{position:static}.bank-sidebar{max-height:none}.focus-main{padding:17px}#practice,.topic-group,.cluster-section,.review-center,.learning-dashboard,.pending-group{padding:18px}.topic-heading{display:block}.topic-frequency{white-space:normal}.cluster-action-bar{padding-bottom:4px}}
@media print{body{background:#fff}.topnav,.search,script{display:none!important}main{width:100%;padding:0}.hero{box-shadow:none;border-radius:0}section{break-inside:avoid}}

.home-practice-card .practice-context{margin:11px 0 0;padding:0;border:0;background:transparent}
.home-practice-card .practice-context>summary{display:inline-flex;align-items:center;gap:7px;min-height:38px;padding:7px 11px;border:1px solid #cbd9e8;border-radius:8px;background:#f8fbff;color:#31577d;font-weight:700;line-height:1.4;list-style:none;cursor:pointer}
.home-practice-card .practice-context>summary::-webkit-details-marker{display:none}
.home-practice-card .practice-context>summary::before{content:"▸";font-size:12px}
.home-practice-card .practice-context[open]>summary::before{content:"▾"}
.home-practice-card .practice-context>summary:focus-visible{outline:3px solid #93c5fd;outline-offset:2px}

/* 单场结果页：按“结论 → 行动 → 表现 → 证据”组织，减少首次阅读噪音。 */
body[data-interview-case]{--paper:#f6f8fc;--line:#e3e8f0}
body[data-interview-case] main{width:min(1080px,calc(100% - 32px));padding-top:18px}
body[data-interview-case] .topnav{gap:7px;padding:8px 10px;background:#ffffffeb;box-shadow:0 5px 18px #1720330b}
body[data-interview-case] .topnav a{padding:5px 9px;border-radius:7px;color:#475569}
body[data-interview-case] .topnav a:hover{background:#eef4ff;color:var(--blue);text-decoration:none}
body[data-interview-case] .case-hero{display:grid;grid-template-columns:minmax(0,1fr) 220px;gap:22px;align-items:stretch;padding:26px 28px;background:linear-gradient(118deg,#172554 0%,#1e40af 62%,#0e7490 100%)}
body[data-interview-case] .case-hero h1{font-size:clamp(25px,4vw,38px);margin:8px 0 9px}
body[data-interview-case] .case-hero p{margin:0;opacity:.82}
body[data-interview-case] .case-hero-result{display:flex;flex-direction:column;justify-content:center;gap:3px;padding:17px 18px;border:1px solid #ffffff38;border-radius:13px;background:#ffffff14}
body[data-interview-case] .case-hero-result span{font-size:12px;opacity:.75}
body[data-interview-case] .case-hero-result strong{font-size:25px;line-height:1.3}
body[data-interview-case] .case-hero-result small{margin-top:5px;opacity:.72}
body[data-interview-case] .case-section{margin-top:22px}
body[data-interview-case] .section-heading{display:flex;justify-content:space-between;align-items:flex-end;gap:16px;margin-bottom:11px}
body[data-interview-case] .section-heading h2{margin:1px 0 0;font-size:21px}
body[data-interview-case] .section-kicker{font-size:12px;color:#41638f;font-weight:800;letter-spacing:.08em}
body[data-interview-case] .section-note{font-size:12px;color:var(--muted);white-space:nowrap}
body[data-interview-case] #focus{margin-top:22px;padding:22px;border:1px solid #c9daf0;border-radius:16px;background:#fff;box-shadow:0 8px 24px #17203308}
body[data-interview-case] #focus>h2{font-size:21px}
body[data-interview-case] #focus .focus-main{background:#eef4ff;border-left-width:4px;padding:17px 19px}
body[data-interview-case] #focus .focus-grid{grid-template-columns:repeat(2,minmax(0,1fr))}
body[data-interview-case] .performance-section{padding:20px 22px;border:1px solid var(--line);border-radius:16px;background:#fff}
body[data-interview-case] .performance-section .score-card{box-shadow:none;border-color:#e6ebf2;background:#fbfcfe}
body[data-interview-case] .practice-section{padding:22px;border:1px solid var(--line);border-radius:16px;background:#fff}
body[data-interview-case] .practice-section .practice-card{padding:18px 0 20px}
body[data-interview-case] .materials-section{padding:22px;border:1px solid var(--line);border-radius:16px;background:#f9fafc}
body[data-interview-case] .materials-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin-top:15px}
body[data-interview-case] .material-card{margin:0;padding:15px 16px;border:1px solid var(--line);border-radius:12px;background:#fff}
body[data-interview-case] .material-card h3{margin:0 0 7px;font-size:16px}
body[data-interview-case] .material-card details{margin:0;border:0;padding:0}
body[data-interview-case] .material-card details summary{padding:6px 0;color:#31577d}
body[data-interview-case] #history{margin-top:24px;padding:22px;border:1px solid var(--line);border-radius:16px;background:#fff}
body[data-interview-case] #evidence{margin-top:24px;padding:22px;border:1px solid var(--line);border-radius:16px;background:#fff}
body[data-interview-case] #evidence>h2{font-size:20px}
@media(max-width:760px){body[data-interview-case] main{width:min(100% - 18px,1080px)}body[data-interview-case] .case-hero{grid-template-columns:1fr;padding:22px}.case-hero-result{min-height:0}body[data-interview-case] #focus .focus-grid,body[data-interview-case] .materials-grid{grid-template-columns:1fr}body[data-interview-case] .section-heading{display:block}body[data-interview-case] .section-note{display:block;margin-top:3px}}
body[data-interview-history] main{width:min(1180px,calc(100% - 32px));padding-top:18px}
body[data-interview-history] .overview-hero{padding:27px 30px;background:linear-gradient(118deg,#172554 0%,#1e40af 60%,#0e7490 100%)}
body[data-interview-history] .overview-hero h1{font-size:clamp(28px,5vw,42px);margin:6px 0 7px}
body[data-interview-history] .overview-hero p{margin:0;opacity:.84}
body[data-interview-history] .overview-links{display:flex;gap:9px;flex-wrap:wrap;margin-top:17px}
body[data-interview-history] .secondary-link{display:inline-block;padding:8px 13px;border:1px solid #ffffff5c;border-radius:9px;color:#fff;font-weight:700}
body[data-interview-history] .secondary-link:hover{background:#ffffff18;text-decoration:none}
body[data-interview-history] #overview{display:grid;gap:0}
body[data-interview-history] #overview>#history{order:1}
body[data-interview-history] #overview>#practice{order:2}
body[data-interview-history] #overview>.review-entry{order:3}
body[data-interview-history] #overview>#next-actions{order:4}
body[data-interview-history] #overview>.deep-link{order:5}
body[data-interview-history] #history{padding:22px;border:1px solid var(--line);border-radius:16px;background:#fff}
@media(max-width:760px){body[data-interview-history] main{width:min(100% - 18px,1180px)}body[data-interview-history] .overview-hero{padding:22px}.overview-links{gap:7px}}
"""


# 旧版 CSS 仍随 Skill 源码保留以兼容历史分支，但新页面不输出旧偏好控件的选择器。
STYLE = (STYLE
         .replace("data-preference-choice", "data-legacy-choice")
         .replace("data-preference-result", "data-legacy-result")
         .replace("preference-menu-options", "legacy-choice-options")
         .replace("preference-menu", "legacy-choice-menu")
         .replace("preference-actions", "legacy-choice-actions")
         .replace("preference-label", "legacy-choice-label"))
STYLE += """
.relation-locked{color:#64748b;font-size:11px}
.cluster-add-assigned{cursor:default;background:#f6f8fb}.cluster-add-assigned>span,.cluster-add-assigned>small{grid-column:1/-1}.cluster-add-assigned a{color:#2458bd}
.related-relation-copy{display:grid;gap:2px;flex:1 1 280px;min-width:0;line-height:1.45}.related-relation-copy>span{overflow-wrap:anywhere}.related-relation .relation-remove{flex:0 0 auto}
.session-stale-banner{display:flex;align-items:center;justify-content:space-between;gap:12px;margin:0 0 14px;padding:10px 13px;border:1px solid #f0b86b;border-radius:9px;background:#fff8e8;color:#70420a;font-size:13px}.session-stale-banner button{border:0;border-radius:7px;padding:7px 10px;background:#b45309;color:#fff;font:inherit;font-weight:700;cursor:pointer}.session-stale-banner[hidden]{display:none}.review-apply-confirm{display:flex;align-items:center;gap:8px;flex-wrap:wrap;flex-basis:100%;padding:9px 10px;border:1px solid #f0b86b;border-radius:8px;background:#fff8e8;color:#70420a}.review-apply-confirm[hidden]{display:none}.review-apply-confirm strong{flex:1 1 320px;font-size:13px}
.privacy-compact{margin:0 0 18px;padding:10px 14px;border:1px solid #e5d2a9;border-radius:10px;background:#fffaf0;color:#70420a;font-size:13px}.privacy-compact>summary{cursor:pointer;font-weight:700}.privacy-compact p{margin:9px 0 0}.review-center{margin-bottom:12px}.review-center h2{margin:0 0 6px}.review-center p{margin:0}.review-toolbar{margin:0 0 12px;border:1px solid #c9daf0;box-shadow:0 6px 18px #17203312}.review-more[open]{width:100%}
.answer-workbench{margin:14px 0;padding:17px 18px;border:1px solid #b9d2ec;border-radius:12px;background:#f6faff}.answer-workbench h4{margin:0 0 6px;font-size:17px;color:#173b68}.answer-workbench>p{margin:6px 0 10px;font-size:13px;line-height:1.5}.answer-workbench>label{display:grid;gap:6px;font-size:13px;font-weight:700}.answer-workbench textarea:not([hidden]){width:100%;box-sizing:border-box;padding:10px;border:1px solid #b8c8da;border-radius:8px;background:#fff;font:inherit;line-height:1.5;resize:vertical;max-width:100%}.answer-workbench button[data-ai-improve]{margin-top:10px;border:0;border-radius:8px;padding:10px 14px;background:#2458bd;color:#fff;font:inherit;font-weight:700;cursor:pointer;min-height:40px}.answer-workbench .ai-copy-note{color:#52647b;font-size:12px}.ai-improve-result{display:block;margin:6px 0;color:#176448;font-size:13px}.ai-improve-result:empty{display:none}.answer-workbench .practice-recorder{margin-top:14px;padding:10px 0 0;border:0;border-top:1px solid #cfdded;background:transparent}.answer-workbench .practice-recorder>summary{cursor:pointer;color:#475569;font-size:13px;font-weight:700;min-height:44px;display:flex;align-items:center}.answer-workbench .practice-recorder .muted{font-size:12px}
.answer-confirm{margin-top:16px;padding:12px 14px;border:1px solid #cfdded;border-radius:10px;background:#fff}.answer-confirm>summary{cursor:pointer;font-weight:700;color:#173b68;min-height:44px;display:flex;align-items:center}.answer-confirm>p{margin:9px 0;font-size:13px;color:#52647b}.answer-confirm>label:not(.answer-facts-check){display:grid;gap:5px;margin:10px 0;font-size:13px;font-weight:700}.answer-confirm textarea{width:100%;box-sizing:border-box;padding:9px;border:1px solid #b8c8da;border-radius:8px;font:inherit;line-height:1.5;resize:vertical;max-width:100%}.answer-confirm [data-answer-full]{min-height:12rem}.answer-facts-check{display:flex;align-items:flex-start;gap:7px;margin:10px 0}.answer-facts-check input{margin-top:3px}.answer-confirm button{border:0;border-radius:8px;padding:9px 14px;background:#176448;color:#fff;font:inherit;font-weight:700;cursor:pointer;min-height:40px}.answer-confirm button:disabled{opacity:.6;cursor:wait}.answer-confirm [data-confirm-result]{display:block;margin-top:7px;font-size:13px}
.practice-recorder button,.review-toolbar button,.review-actions button{min-height:40px}
.answer-editor textarea{resize:vertical;max-width:100%}.answer-editor [data-edit-standard]{min-height:12rem}.draft-controls{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:8px 0;color:var(--muted);font-size:12px}.draft-controls button{min-height:36px;border:1px solid #cbd5e1;border-radius:7px;padding:6px 9px;background:#fff;color:#475569;font:inherit;cursor:pointer}.draft-controls button[hidden]{display:none}.draft-controls [data-draft-status]{overflow-wrap:anywhere}
.case-reference{margin:14px 0 4px;padding:13px 15px;border:1px solid #c9daf0;border-radius:10px;background:#f6faff}.case-reference-heading{display:flex;align-items:center;gap:9px;flex-wrap:wrap}.case-reference-heading>strong{color:#173b68}.case-reference-heading>span{font-size:11px;color:#52647b}.case-reference-title{margin:7px 0 0;font-weight:700}.case-reference-caution{margin:5px 0 10px;color:#52647b;font-size:12px}.case-reference-short{padding:10px 12px;border-radius:8px;background:#fff}.case-reference-short p,.case-reference>details p{margin:5px 0;white-space:pre-wrap}.case-reference>details{margin:9px 0 0;padding:8px 10px;border:1px solid #dce7f2;border-radius:8px;background:#fff}.case-reference>details>summary{cursor:pointer;font-weight:700}.case-reference .reference-answer{border:0;background:transparent;padding:8px 0 0;margin:0}.case-reference>p:last-child{margin:9px 0 0;font-size:12px}.case-reference-empty{margin:12px 0 0;color:#64748b;font-size:12px}
.home-reference{margin:14px 0;padding:14px 16px;background:#fff;border:1px solid #cfdded;border-radius:11px}.home-reference-heading{display:flex;align-items:center;justify-content:space-between;gap:10px}.home-reference-heading h4{margin:0}.home-reference-title{font-weight:700;color:#334155}.home-reference-short{margin:10px 0;padding:11px 13px;background:#f1f6ff;border-radius:9px}.home-reference-short p{white-space:pre-wrap}.home-reference>details{margin:10px 0;padding:0 12px}.home-reference>details p{white-space:pre-wrap}.home-reference>.muted{font-size:12px}.home-reference-empty{margin:14px 0;padding:10px 12px;background:#fff;border-radius:8px;color:#52647b;font-size:13px}
.related-questions{margin:18px 0;padding:14px 16px;background:#f8fbff;border:1px solid #d5e2f1;border-radius:10px}.related-questions h4{margin:0 0 4px;font-size:15px}.related-questions>.muted{margin:3px 0 11px;font-size:12px}.related-questions>ul{list-style:none;margin:0;padding:0;display:grid;gap:7px}.related-question{margin:0;border:1px solid #dce7f2;border-radius:9px;background:#fff;overflow:hidden}.related-question>summary{display:grid;gap:3px;position:relative;cursor:pointer;list-style:none;padding:10px 32px 10px 12px}.related-question>summary::-webkit-details-marker{display:none}.related-question>summary::after{content:"⌄";position:absolute;right:12px;top:16px;color:#52647b;font-size:20px;line-height:1}.related-question[open]>summary::after{transform:rotate(180deg)}.related-question[open]>summary{border-bottom:1px solid #e4ebf4}.related-meta{color:#52647b;font-size:11px;font-weight:700}.related-title{font-size:13px;font-weight:700;line-height:1.45;display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;overflow:hidden}.related-link-state{font-size:11px;color:#64748b}.related-answer-body{padding:11px 13px 13px}.related-answer-body>strong{font-size:12px}.related-answer-text{max-height:230px;overflow:auto;margin:6px 0 10px;padding:10px 11px;border-radius:7px;background:#f6f8fb;color:#334155;font-size:13px;line-height:1.65;white-space:pre-wrap;overflow-wrap:anywhere}.related-answer-body>a{font-size:12px}.related-relation{display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap;margin-bottom:9px;padding:8px 9px;background:#f1f7ff;border-radius:7px;color:#31577d;font-size:12px}.relation-remove{border:1px solid #cbd5e1;border-radius:6px;padding:4px 7px;background:#fff;color:#9f1239;font:inherit;font-size:11px;cursor:pointer}.relation-remove[disabled]{opacity:.6;cursor:wait}
.cluster-membership-manager{margin-top:12px;padding:10px 12px;border:1px dashed #b8cce2;border-radius:9px;background:#fbfdff}.cluster-membership-manager>summary{cursor:pointer;font-weight:700;color:#31577d}.cluster-membership-manager label{display:block;margin-top:8px;font-size:12px}.cluster-membership-manager input[type="search"]{width:100%;padding:7px;border:1px solid #cbd5e1;border-radius:6px;font:inherit}.cluster-add-options{max-height:220px;overflow:auto;margin:8px 0;border:1px solid #e2e8f0;border-radius:7px}.cluster-add-option{display:grid!important;grid-template-columns:auto minmax(0,1fr);column-gap:7px;padding:7px 8px;border-bottom:1px solid #edf1f5;cursor:pointer}.cluster-add-option:last-child{border-bottom:0}.cluster-add-option[hidden]{display:none!important}.cluster-add-option small{grid-column:2;color:#64748b;font-size:11px}.cluster-membership-manager>button{margin-top:8px;border:0;border-radius:6px;padding:7px 9px;background:var(--blue);color:#fff;font:inherit;font-size:12px;font-weight:700;cursor:pointer}.cluster-membership-manager>button[disabled]{opacity:.6;cursor:wait}.cluster-add-result,.cluster-add-count,.cluster-membership-empty{margin-left:8px;color:var(--muted);font-size:12px}.cluster-membership-empty{margin-top:10px}
[data-home-question][hidden]{display:none}.home-question-direction{margin:5px 0;color:#31577d;font-size:13px;font-weight:700}.home-next{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-top:14px;padding:14px 16px;background:#f6faff;border:1px solid #c9daf0;border-radius:10px}.home-next p{flex:1 1 320px;margin:0;color:#475569;font-size:13px}.home-navigation{display:flex;align-items:center;gap:9px;flex:0 0 auto}.home-navigation span{min-width:72px;text-align:center;color:#31577d;font-size:13px;font-variant-numeric:tabular-nums}.home-next button{flex:0 1 auto;border:0;border-radius:8px;padding:9px 12px;background:#2458bd;color:#fff;font:inherit;font-weight:700;cursor:pointer;text-align:left;min-height:40px}.home-next button:disabled{opacity:.5;cursor:not-allowed}.home-next button:focus-visible{outline:3px solid #93c5fd;outline-offset:2px}
.home-goal-grid{grid-template-columns:minmax(0,1fr)}.home-goal-grid .goal-card{padding:18px;background:#f6faff}.goal-details{margin-top:10px;font-size:13px;color:#475569}.goal-details>summary{cursor:pointer;font-weight:700}.goal-details p{margin:8px 0}.more-goals{margin-top:14px;border-top:1px solid var(--line);padding-top:12px}.more-goals>summary{cursor:pointer;font-weight:700;color:#31577d}.more-goals .goal-grid{grid-template-columns:repeat(2,minmax(0,1fr));margin-top:12px}.home-practice-card .practice-recorder{margin:12px 0}.home-practice-card .practice-context{margin-top:14px;border-top:1px solid var(--line);padding-top:12px}.home-practice-card .practice-context>summary{cursor:pointer;color:#31577d;font-weight:700}.practice-save-note{font-size:12px;color:var(--muted);margin:7px 0}.overview-hero{margin-bottom:18px}
.next-prep{margin:0 0 22px;padding:22px 28px;background:#fff;border:1px solid #c9daf0;border-radius:16px}.goal-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.goal-card{padding:16px;border:1px solid var(--line);border-radius:12px;background:#fbfdff}.goal-heading{display:flex;justify-content:space-between;gap:10px;align-items:flex-start}.goal-heading h3{margin:0;font-size:16px;line-height:1.45}.goal-card p{margin:9px 0;font-size:13px;line-height:1.6}.goal-card .acceptance{color:#31577d}.goal-sources{margin-top:10px;border-top:1px solid var(--line);padding-top:8px}.goal-sources summary{font-size:12px;color:#31577d}.goal-sources ul{padding-left:1.1rem;font-size:12px;color:var(--muted)}.goal-sources li+li{margin-top:5px}.cluster-on-demand{margin:0 0 22px;background:#f8fafc;border:1px solid var(--line);border-radius:14px;padding:0}.cluster-on-demand>summary{cursor:pointer;padding:16px 20px;font-weight:800;color:#334155}.secondary-details,.history-archive{margin:0 0 22px;padding:0;background:#f8fafc;border:1px solid var(--line);border-radius:14px}.secondary-details>summary,.history-archive>summary{cursor:pointer;padding:16px 20px;font-weight:800;color:#334155}.secondary-details[open]>summary,.history-archive[open]>summary{border-bottom:1px solid var(--line)}.history-archive>#history{margin:18px;padding:22px}.history-archive #history{border-radius:12px}
.target-prep{margin-top:24px;padding:22px 24px;background:#fff;border:1px solid #bfdbfe;border-radius:16px}.target-prep-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.target-prep-card{padding:17px;border:1px solid var(--line);border-radius:12px;background:#fbfdff}.target-prep-card h3{margin:10px 0}.target-prep-meta{display:flex;gap:8px;align-items:center}.target-prep-card details{background:#fff}.target-prep-reverse{margin-top:16px}.target-prep-jd{margin:18px 0;padding:17px;border:1px solid var(--line);border-radius:12px;background:#fbfdff}.target-prep-jd h3{margin:0 0 8px}.target-prep-jd pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit;line-height:1.65;margin:12px 0 0}
.standard-answer-library{margin:0 0 18px;padding:0;background:#f8fafc;border:1px solid var(--line);border-radius:14px}.standard-answer-library>summary{cursor:pointer;padding:16px 20px;font-weight:800;color:#334155}.standard-answer-library>p,.standard-answer-library>.standard-answer{margin-left:18px;margin-right:18px}.standard-answer-library>.standard-answer:last-child{margin-bottom:18px}.standard-answer{padding:0}.standard-answer>.standard-heading{cursor:pointer;padding:14px 16px;list-style:none}.standard-answer>.standard-heading::-webkit-details-marker{display:none}.standard-answer>.standard-heading::after{content:"＋";font-size:18px;color:#31577d}.standard-answer[open]>.standard-heading::after{content:"－"}.standard-answer>.standard-body{padding:0 16px 16px}.standard-versions{display:block}.standard-versions>details{margin-top:10px;padding:12px 13px;background:#fff;border:1px solid var(--line);border-radius:10px}.standard-versions>details>summary{cursor:pointer;font-weight:700}.standard-versions>details>p{margin-top:9px}
.answer-entry{display:flex;align-items:center;justify-content:space-between;gap:16px;margin:0 0 22px;padding:17px 20px;background:#fff;border:1px solid #c9daf0;border-radius:14px}.answer-entry h2,.answer-entry p{margin:0}.answer-entry p{margin-top:4px;color:var(--muted);font-size:13px}.answer-hub-section{background:#fff;border:1px solid var(--line);border-radius:16px;padding:22px 28px;margin:0 0 22px}.answer-hub-section h2{margin:0 0 5px}.answer-hub-section>.muted{margin:0 0 16px}.answer-hub-section>.standard-answer{margin:9px 0}.saved-question-answer{margin:9px 0;padding:0;border:1px solid var(--line);border-radius:11px;background:#f8fafc;scroll-margin-top:75px}.saved-question-answer>summary{cursor:pointer;padding:14px 16px;font-weight:700;line-height:1.45}.saved-question-answer>summary small{display:block;margin-top:4px;color:var(--muted);font-weight:400}.saved-question-answer>.saved-answer-body{padding:0 16px 15px}.saved-question-answer .reference-answer{margin:0;padding:12px 14px}.answer-hub-empty{display:none;color:var(--muted)}
@media(max-width:760px){.goal-grid,.more-goals .goal-grid,.target-prep-grid{grid-template-columns:1fr}.next-prep,.target-prep{padding:18px}.history-archive>#history{margin:12px;padding:18px}}
"""


SCRIPT = """
(() => {
  const openTarget = (hash) => {
    if (!hash || hash.length < 2) return;
    let target;
    try { target = document.getElementById(decodeURIComponent(hash.slice(1))); }
    catch (_) { return; }
    if (!target) return;
    if (target.hidden) target.hidden = false;
    let parent = target.closest('details');
    while (parent) { parent.open = true; parent = parent.parentElement.closest('details'); }
    target.scrollIntoView({block: 'start'});
  };
  document.addEventListener('click', event => {
    const link = event.target.closest('a[href^="#"]');
    if (link) requestAnimationFrame(() => openTarget(link.getAttribute('href')));
  });
  if (location.hash) openTarget(location.hash);
  const bindSearch = (inputSelector, rowSelector, emptySelector, countSelector, unit) => {
    const input = document.querySelector(inputSelector);
    if (!input) return;
    const rows = [...document.querySelectorAll(rowSelector)];
    const empty = document.querySelector(emptySelector);
    const count = document.querySelector(countSelector);
    const clear = input.closest('.search-line, .bank-sidebar')?.querySelector('[data-search-clear]');
    const apply = () => {
      const query = input.value.trim().toLowerCase();
      let visible = 0;
      rows.forEach(row => {
        const show = !query || (row.dataset.search || '').includes(query);
        row.hidden = !show;
        if (show) visible += 1;
      });
      if (empty) empty.style.display = visible ? 'none' : 'block';
      if (count) count.textContent = `显示 ${visible} / ${rows.length} ${unit}`;
      if (clear) clear.hidden = !query;
    };
    input.addEventListener('input', apply);
    if (clear) clear.addEventListener('click', () => { input.value = ''; apply(); input.focus(); });
    apply();
  };
  bindSearch('[data-answer-search]', '[data-standard-answer],[data-saved-question-answer]', '[data-answer-empty]', '[data-answer-count]', '份');
  const historyInput = document.querySelector('[data-history-search]');
  if (historyInput) {
    const historyRows = [...document.querySelectorAll('[data-history-row]')];
    const historyEmpty = document.querySelector('[data-history-empty]');
    const historyCount = document.querySelector('[data-history-count]');
    const historyFilter = document.querySelector('[data-history-result-filter]');
    const historyClear = historyInput.closest('.search-line')?.querySelector('[data-search-clear]');
    const applyHistory = () => {
      const query = historyInput.value.trim().toLowerCase();
      const resultFilter = historyFilter?.value || 'all';
      let visible = 0;
      historyRows.forEach(row => {
        const show = (!query || (row.dataset.search || '').includes(query))
          && (resultFilter === 'all' || row.dataset.historyResultStatus === resultFilter);
        row.hidden = !show;
        if (show) visible += 1;
      });
      if (historyEmpty) historyEmpty.style.display = visible ? 'none' : 'block';
      if (historyCount) historyCount.textContent = `显示 ${visible} / ${historyRows.length} 场`;
      if (historyClear) historyClear.hidden = !query;
    };
    historyInput.addEventListener('input', applyHistory);
    historyFilter?.addEventListener('change', applyHistory);
    historyClear?.addEventListener('click', () => { historyInput.value = ''; applyHistory(); historyInput.focus(); });
    applyHistory();
  }
  const reviewSearch = document.querySelector('[data-review-search]');
  if (reviewSearch) {
    const reviewRows = [...document.querySelectorAll('[data-review-search-row]')];
    const reviewCount = document.querySelector('[data-review-count]');
    const reviewClear = reviewSearch.closest('.search-line')?.querySelector('[data-search-clear]');
    const applyReviewSearch = () => {
      const query = reviewSearch.value.trim().toLowerCase();
      let visible = 0;
      reviewRows.forEach(row => {
        const show = !query || (row.dataset.search || '').includes(query);
        row.hidden = !show;
        if (show) visible += 1;
      });
      if (reviewCount) reviewCount.textContent = `显示 ${visible} / ${reviewRows.length} 项`;
      if (reviewClear) reviewClear.hidden = !query;
    };
    reviewSearch.addEventListener('input', applyReviewSearch);
    reviewClear?.addEventListener('click', () => { reviewSearch.value = ''; applyReviewSearch(); reviewSearch.focus(); });
    applyReviewSearch();
  }
  const questionInput = document.querySelector('[data-question-search]');
  if (questionInput) {
    const rows = [...document.querySelectorAll('[data-question-row]')];
    const standards = [...document.querySelectorAll('[data-standard-answer]')];
    const clusters = [...document.querySelectorAll('[data-question-cluster]')];
    const clusterSection = document.querySelector('[data-cluster-section]');
    const longTail = document.querySelector('[data-long-tail]');
    const standardLibrary = document.querySelector('[data-standard-library]');
    const groups = [...document.querySelectorAll('[data-topic-group]')];
    const bookmarks = [...document.querySelectorAll('[data-bookmark]')];
    const clusterBookmarks = [...document.querySelectorAll('[data-cluster-bookmark]')];
    const empty = document.querySelector('[data-question-empty]');
    const count = document.querySelector('[data-question-count]');
    const clear = questionInput.closest('.search-line, .bank-sidebar')?.querySelector('[data-search-clear]');
    const applyQuestions = () => {
      const query = questionInput.value.trim().toLowerCase();
      let visible = 0;
      let visibleStandards = 0;
      let visibleClusters = 0;
      rows.forEach(row => {
        const show = !query || (row.dataset.search || '').includes(query);
        row.hidden = !show;
        if (show) visible += 1;
      });
      standards.forEach(answer => {
        const show = !query || (answer.dataset.search || '').includes(query);
        answer.hidden = !show;
        if (show) visibleStandards += 1;
      });
      clusters.forEach(cluster => {
        const show = !query || (cluster.dataset.search || '').includes(query);
        cluster.hidden = !show;
        if (show) visibleClusters += 1;
      });
      if (clusterSection) clusterSection.hidden = visibleClusters === 0;
      groups.forEach(group => {
        group.hidden = !group.querySelector('[data-question-row]:not([hidden]),[data-standard-answer]:not([hidden])');
      });
      if (longTail && query && visible) longTail.open = true;
      if (standardLibrary && query && visibleStandards) standardLibrary.open = true;
      bookmarks.forEach(bookmark => {
        const target = document.getElementById(bookmark.dataset.bookmark || '');
        bookmark.hidden = !target || target.hidden;
      });
      clusterBookmarks.forEach(bookmark => {
        const target = document.getElementById(bookmark.dataset.clusterBookmark || '');
        bookmark.hidden = !target || target.hidden;
      });
      if (empty) empty.style.display = visible || visibleStandards || visibleClusters ? 'none' : 'block';
      if (count) count.textContent = `显示 ${visible} / ${rows.length} 题 · ${visibleClusters} 组高频问法 · ${visibleStandards} 份成熟答案`;
      if (clear) clear.hidden = !query;
    };
    questionInput.addEventListener('input', applyQuestions);
    clear?.addEventListener('click', () => { questionInput.value = ''; applyQuestions(); questionInput.focus(); });
    applyQuestions();
  }
  const archiveId = document.body.dataset.storeId || 'unknown';
  const actionKey = `interview-system-review-actions-v2:${archiveId}`;
  const persistenceKey = `interview-system-review-persistence-v1:${archiveId}`;
  const legacyActionKey = 'interview-system-review-actions-v1';
  const archiveChangeKey = `interview-system-archive-change-v1:${archiveId}`;
  const tabId = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  const showStaleBanner = () => {
    let banner = document.querySelector('[data-session-stale]');
    if (!banner) {
      banner = document.createElement('div');
      banner.className = 'session-stale-banner';
      banner.dataset.sessionStale = '1';
      banner.innerHTML = '<span>另一标签页已保存本机档案，本页内容可能已过期。</span><button type="button">刷新本页</button>';
      banner.querySelector('button').addEventListener('click', () => window.location.reload());
      const main = document.querySelector('main');
      const nav = main?.querySelector('.topnav');
      if (main && nav) main.insertBefore(banner, nav.nextSibling);
    }
    banner.hidden = false;
  };
  if (typeof window.addEventListener === 'function') {
    window.addEventListener('storage', event => {
      if (event.key !== archiveChangeKey || !event.newValue) return;
      try {
        const change = JSON.parse(event.newValue);
        if (change.source !== tabId) showStaleBanner();
      } catch (_) {}
    });
  }
  const announceArchiveChange = (change) => {
    try {
      localStorage.setItem(archiveChangeKey, JSON.stringify({ ...change, source: tabId, at: Date.now() }));
    } catch (_) {}
  };
  const draftControllers = new WeakMap();
  const draftPrefix = `interview-system-session-draft-v1:${archiveId}:`;
  const draftTtlMilliseconds = 30 * 24 * 60 * 60 * 1000;
  const bindSessionDraft = (container, selectors, sessionOnly = false) => {
    const keyPart = container.dataset.draftKey;
    if (!keyPart) return;
    const fields = selectors.map(selector => container.querySelector(selector)).filter(Boolean);
    fields.forEach((field, index) => { field.dataset.draftField ||= `field-${index}`; });
    const status = container.querySelector('[data-draft-status]');
    const clearButton = container.querySelector('[data-draft-clear]');
    const persistentToggle = container.querySelector('[data-draft-persist]');
    const defaults = new Map(fields.map(field => [field, field.value]));
    const sessionKey = draftPrefix + keyPart;
    const persistentKey = `${sessionKey}:local`;
    const optInKey = `${sessionKey}:opt-in`;
    const getStorage = name => {
      try { return typeof window[name] === 'undefined' ? null : window[name]; }
      catch (_) { return null; }
    };
    const session = getStorage('sessionStorage');
    const local = getStorage('localStorage');
    let persistent = false;
    try { persistent = local?.getItem(optInKey) === '1'; } catch (_) { persistent = false; }
    if (persistentToggle) persistentToggle.checked = persistent;
    const setStatus = message => { if (status) status.textContent = message; };
    const removeStored = (clearOptIn = false) => {
      try { session?.removeItem(sessionKey); } catch (_) {}
      try { local?.removeItem(persistentKey); } catch (_) {}
      if (clearOptIn) {
        try { local?.removeItem(optInKey); } catch (_) {}
      }
    };
    const clear = (message = '已清除本页草稿', restore = true) => {
      removeStored(true);
      persistent = false;
      if (persistentToggle) persistentToggle.checked = false;
      if (restore) fields.forEach(field => { field.value = defaults.get(field) || ''; });
      if (clearButton) clearButton.hidden = true;
      setStatus(message);
    };
    const readPayload = (store, key) => {
      try {
        const raw = store?.getItem(key);
        if (!raw) return null;
        const payload = JSON.parse(raw);
        if (!payload || !payload.values || typeof payload.values !== 'object') return null;
        if (Number(payload.expires_at || 0) && Number(payload.expires_at) < Date.now()) {
          store.removeItem(key);
          return null;
        }
        return payload;
      } catch (_) { return null; }
    };
    const currentValues = () => Object.fromEntries(fields.map(field => [field.dataset.draftField, field.value]));
    const hasChanges = () => fields.some(field => field.value !== (defaults.get(field) || ''));
    const writePayload = () => {
      const target = persistent ? local : session;
      const key = persistent ? persistentKey : sessionKey;
      if (!target) {
        setStatus(persistent ? '浏览器拒绝本机持久保存；当前内容仍未写入档案' : '当前浏览器不支持会话恢复；内容仍未写入档案');
        return false;
      }
      try {
        if (!hasChanges()) {
          target.removeItem(key);
          if (clearButton) clearButton.hidden = true;
          setStatus(persistent ? '草稿未修改；后续编辑仅保留在当前本机入口' : '草稿未修改；内容仍未写入档案');
          return true;
        }
        target.setItem(key, JSON.stringify({
          saved_at: Date.now(),
          expires_at: persistent ? Date.now() + draftTtlMilliseconds : null,
          values: currentValues(),
        }));
        if (clearButton) clearButton.hidden = false;
        setStatus(persistent ? '草稿已保存到当前本机入口；服务重启换端口后不保证恢复，尚未写入档案' : '草稿已保存，仅保存在当前标签页；尚未写入档案');
        return true;
      } catch (_) {
        setStatus(persistent ? '本机持久保存失败；内容仍未写入档案' : '会话草稿保存失败；内容仍未写入档案');
        return false;
      }
    };
    let restored = false;
    const persistentPayload = persistent ? readPayload(local, persistentKey) : null;
    const sessionPayload = readPayload(session, sessionKey);
    const payload = persistentPayload || sessionPayload;
    if (payload) {
      fields.forEach(field => {
        const value = payload.values[field.dataset.draftField];
        if (typeof value === 'string') { field.value = value; restored = true; }
      });
    }
    if (sessionOnly) {
      persistent = false;
      try { local?.removeItem(persistentKey); local?.removeItem(optInKey); } catch (_) {}
      if (restored) writePayload();
    }
    if (restored) {
      if (clearButton) clearButton.hidden = false;
      setStatus(persistentPayload ? '已恢复本机草稿；尚未写入档案' : '已恢复当前标签页草稿；尚未写入档案');
    } else if (clearButton) {
      clearButton.hidden = true;
    }
    fields.forEach(field => {
      field.addEventListener('input', writePayload);
      field.addEventListener('change', writePayload);
    });
    if (clearButton) clearButton.addEventListener('click', () => clear());
    if (persistentToggle) persistentToggle.addEventListener('change', () => {
      if (persistentToggle.checked) {
        if (!local) {
          persistentToggle.checked = false;
          setStatus('当前浏览器不支持本机持久保存；内容仍未写入档案');
          return;
        }
        try {
          local.setItem(optInKey, '1');
          persistent = true;
          writePayload();
        } catch (_) {
          persistentToggle.checked = false;
          persistent = false;
          setStatus('本机持久保存失败；内容仍未写入档案');
        }
      } else {
        try {
          if (hasChanges() && session) {
            session.setItem(sessionKey, JSON.stringify({saved_at: Date.now(), expires_at: null, values: currentValues()}));
          }
          local?.removeItem(persistentKey);
          local?.removeItem(optInKey);
          persistent = false;
          if (clearButton) clearButton.hidden = !hasChanges();
          setStatus('已改为仅当前标签页保存；尚未写入档案');
        } catch (_) {
          setStatus('无法切换草稿保存方式；当前内容仍未写入档案');
        }
      }
    });
    draftControllers.set(container, {clear});
  };
  document.querySelectorAll('[data-practice-widget][data-draft-key]').forEach(widget => {
    bindSessionDraft(widget, ['[data-practice-answer]', '[data-practice-rating]', '[data-practice-note]']);
  });
  document.querySelectorAll('[data-answer-confirm][data-draft-key]').forEach(form => {
    bindSessionDraft(form, ['[data-answer-short]', '[data-answer-full]'], true);
  });
  const ttlMilliseconds = 30 * 24 * 60 * 60 * 1000;
  let queuedActions = [];
  let persistent = false;
  try { persistent = localStorage.getItem(persistenceKey) === '1'; }
  catch (_) { persistent = false; }
  const readQueue = () => {
    try {
      const raw = (persistent ? localStorage : sessionStorage).getItem(actionKey);
      if (!raw) return [];
      const payload = JSON.parse(raw);
      if (Array.isArray(payload)) return payload;
      if (!payload || !Array.isArray(payload.actions)) return [];
      if (persistent && Number(payload.expires_at || 0) < Date.now()) {
        localStorage.removeItem(actionKey); return [];
      }
      return payload.actions;
    } catch (_) { return []; }
  };
  queuedActions = readQueue();
  if (!Array.isArray(queuedActions)) queuedActions = [];
  const persistQueue = () => {
    const storage = persistent ? localStorage : sessionStorage;
    const now = Date.now();
    try {
      storage.setItem(actionKey, JSON.stringify({
        saved_at: now,
        expires_at: persistent ? now + ttlMilliseconds : null,
        actions: queuedActions,
      }));
    } catch (_) {
      const message = document.querySelector('[data-action-message]');
      if (message) message.textContent = '浏览器拒绝保存；当前队列只保留在页面内存中';
    }
  };
  const actionIdentity = action => action.kind === 'practice_attempt'
    ? `${action.kind}:${action.case_id}:${action.question_id}` : `${action.kind}:${action.id}`;
  const saveQueue = action => {
    const identity = actionIdentity(action);
    queuedActions = queuedActions.filter(item => actionIdentity(item) !== identity);
    queuedActions.push(action);
    persistQueue();
    updateQueue();
  };
  const updateQueue = () => {
    document.querySelectorAll('[data-action-count]').forEach(node => {
      node.textContent = `已选择 ${queuedActions.length} 项`;
    });
    const toolbar = document.querySelector('[data-review-toolbar]');
    if (toolbar) toolbar.hidden = queuedActions.length === 0 && !(actionMessage && actionMessage.textContent);
  };
  const reviewStatusLabels = {
    confirmed: '确认正确',
    pending: '暂不确定',
    rejected: '不应采用',
  };
  const reviewIdentity = item => `${item.dataset.reviewKind}:${item.dataset.reviewId}`;
  const updateReviewState = item => {
    const selected = queuedActions.find(action => actionIdentity(action) === reviewIdentity(item));
    item.querySelectorAll('[data-review-status]').forEach(button => {
      const active = Boolean(selected && button.dataset.reviewStatus === selected.status);
      button.setAttribute('aria-pressed', active ? 'true' : 'false');
    });
    const undo = item.querySelector('[data-review-undo]');
    if (undo) undo.hidden = !selected;
    const result = item.querySelector('[data-review-result]')
      || item.closest('[data-question-cluster]')?.querySelector('[data-review-result]');
    if (!result) return;
    result.classList.toggle('selected', Boolean(selected));
    result.hidden = item.dataset.reviewKind === 'question_cluster' ? !selected : false;
    result.textContent = selected
      ? (item.dataset.reviewKind === 'question_cluster'
        ? '已暂存，导出后生效'
        : `已选择：${reviewStatusLabels[selected.status] || selected.status}；尚未写入档案`)
      : '尚未选择；导出并应用后才会生效';
  };
  document.querySelectorAll('[data-review-status]').forEach(button => {
    button.addEventListener('click', () => {
      const item = button.closest('[data-review-item]');
      if (!item) return;
      const result = item.querySelector('[data-review-result]')
        || item.closest('[data-question-cluster]')?.querySelector('[data-review-result]');
      const editor = item.closest('.answer-editor');
      const clusterScope = item.closest('[data-question-cluster]');
      let patch = null;
      try {
        if (editor && item.dataset.reviewKind === 'standard_answer') {
          patch = {
            title: editor.querySelector('[data-edit-title]').value,
            basis: editor.querySelector('[data-edit-basis]').value,
            short_30s: editor.querySelector('[data-edit-short]').value,
            standard_2m: editor.querySelector('[data-edit-standard]').value,
            followups: JSON.parse(editor.querySelector('[data-edit-followups]').value),
            limitations: editor.querySelector('[data-edit-limitations]').value.split('\\n').map(value => value.trim()).filter(Boolean),
            sources: JSON.parse(editor.querySelector('[data-edit-sources]').value),
            source_questions: JSON.parse(editor.querySelector('[data-edit-source-questions]').value),
          };
        } else if (item.dataset.reviewKind === 'question_cluster') {
          const clusterEditor = editor || clusterScope;
          if (!clusterEditor) throw new Error('找不到题组编辑区域');
          patch = {
            label: clusterEditor.querySelector('[data-edit-cluster-label]').value,
            note: clusterEditor.querySelector('[data-edit-cluster-note]').value,
            questions: JSON.parse(clusterEditor.querySelector('[data-edit-cluster-questions]').value),
          };
        }
      } catch (error) {
        if (result) {
          result.hidden = false;
          result.textContent = `JSON 格式错误：${error.message}`;
        }
        return;
      }
      const action = {
        kind: item.dataset.reviewKind,
        id: item.dataset.reviewId,
        expected_status: item.dataset.reviewCurrent,
        status: button.dataset.reviewStatus,
        reason: button.dataset.reviewStatus === 'confirmed'
          ? '本人核对后确认' : button.dataset.reviewStatus === 'rejected'
            ? '本人核对后决定不采用' : '本人暂不确定',
      };
      if (patch) action.patch = patch;
      saveQueue(action);
      updateReviewState(item);
    });
  });
  document.querySelectorAll('[data-review-undo]').forEach(button => {
    button.addEventListener('click', () => {
      const item = button.closest('[data-review-item]');
      if (!item) return;
      const identity = reviewIdentity(item);
      queuedActions = queuedActions.filter(action => actionIdentity(action) !== identity);
      persistQueue();
      updateQueue();
      updateReviewState(item);
    });
  });
  document.querySelectorAll('[data-merge-builder]').forEach(builder => {
    const input = builder.querySelector('[data-merge-search-input]');
    const options = [...builder.querySelectorAll('[data-merge-option]')];
    const count = builder.querySelector('[data-merge-count]');
    const result = builder.querySelector('[data-merge-result]');
    const undoMerge = builder.querySelector('[data-merge-undo]');
    let lastMergeActionId = '';
    let activeClusters = {};
    try { activeClusters = JSON.parse(builder.dataset.mergeActiveClusters || '{}') || {}; }
    catch (_) { activeClusters = {}; }
    let activeClusterStatuses = {};
    try { activeClusterStatuses = JSON.parse(builder.dataset.mergeClusterStatuses || '{}') || {}; }
    catch (_) { activeClusterStatuses = {}; }
    const update = () => {
      const query = (input ? input.value : '').trim().toLowerCase();
      let selected = 0;
      options.forEach(option => {
        const checkbox = option.querySelector('[data-merge-checkbox]');
        if (checkbox && checkbox.checked) selected += 1;
        option.hidden = Boolean(query && !(option.dataset.mergeSearch || '').includes(query));
      });
      if (count) count.textContent = `已选择 ${selected} 题`;
    };
    if (input) input.addEventListener('input', update);
    options.forEach(option => {
      const checkbox = option.querySelector('[data-merge-checkbox]');
      if (checkbox) checkbox.addEventListener('change', update);
    });
    const queue = builder.querySelector('[data-merge-queue]');
    if (queue) queue.addEventListener('click', () => {
      const selected = options.filter(option => {
        const checkbox = option.querySelector('[data-merge-checkbox]');
        return checkbox && checkbox.checked;
      });
      const label = (builder.querySelector('[data-merge-label]') || {}).value || '';
      const reason = (builder.querySelector('[data-merge-reason]') || {}).value || '';
      if (selected.length < 2) {
        if (result) result.textContent = '至少选择 2 道原始题面后才能合并';
        return;
      }
      if (!label.trim() || !reason.trim()) {
        if (result) result.textContent = '请填写新题组名称和合并理由';
        return;
      }
      const selectedKeys = new Set(selected.map(option => `${option.dataset.mergeCaseId}:${option.dataset.mergeQuestionId}`));
      const supersedes = [];
      const partialClusters = [];
      Object.entries(activeClusters).forEach(([clusterId, refs]) => {
        const members = Array.isArray(refs) ? refs.map(ref => `${ref[0]}:${ref[1]}`) : [];
        const matched = members.filter(ref => selectedKeys.has(ref)).length;
        if (matched > 0 && matched < members.length) partialClusters.push(clusterId);
        if (members.length > 0 && matched === members.length) supersedes.push(clusterId);
      });
      if (partialClusters.length) {
        if (result) result.textContent = `旧题组 ${partialClusters.join('、')} 只选中了部分成员；请全选或取消这些题目`;
        return;
      }
      const topicSelect = builder.querySelector('[data-merge-topic]');
      const action = {
        kind: 'question_cluster_create',
        id: `manual-merge-${Date.now().toString(36)}`,
        status: 'pending',
        label: label.trim(),
        topic: topicSelect ? topicSelect.value : 'other',
        note: reason.trim(),
        reason: reason.trim(),
        supersedes,
        expected_superseded_statuses: Object.fromEntries(supersedes.map(clusterId => [clusterId, activeClusterStatuses[clusterId] || 'pending'])),
        questions: selected.map(option => ({
          case_id: option.dataset.mergeCaseId,
          question_id: option.dataset.mergeQuestionId,
        })),
      };
      saveQueue(action);
      lastMergeActionId = action.id;
      if (undoMerge) undoMerge.hidden = false;
      selected.forEach(option => {
        const checkbox = option.querySelector('[data-merge-checkbox]');
        if (checkbox) checkbox.checked = false;
      });
      update();
      if (result) result.textContent = '已加入本次修改；点击顶部“下载选择”后，回到对话应用到档案';
    });
    if (undoMerge) undoMerge.addEventListener('click', () => {
      if (!lastMergeActionId) return;
      queuedActions = queuedActions.filter(action => action.kind !== 'question_cluster_create' || action.id !== lastMergeActionId);
      persistQueue();
      updateQueue();
      undoMerge.hidden = true;
      if (result) result.textContent = '已撤销本次手动合并；尚未写入档案';
    });
    update();
  });
  document.querySelectorAll('[data-relation-remove]').forEach(button => {
    button.addEventListener('click', async () => {
      if (location.protocol !== 'http:' || location.hostname !== '127.0.0.1') {
        button.closest('.related-relation')?.append(' 当前是只读页面，请通过本机保存入口打开。');
        return;
      }
      const dissolvesCluster = button.dataset.relationDissolvesCluster === 'true';
      const confirmation = dissolvesCluster
        ? '确认移出这道原题并解散整个题组？组内原题和现场回答仍保留在各自原场次，并恢复为未归组。'
        : `确认${button.textContent.trim()}？原题和现场回答仍保留在原场次。`;
      if (!window['confirm'](confirmation)) return;
      button.disabled = true;
      const body = {
        relation_kind: button.dataset.relationKind,
        relation_id: button.dataset.relationId,
        relation_status: button.dataset.relationStatus,
        expected_fingerprint: button.dataset.relationFingerprint,
        case_id: button.dataset.relationCaseId,
        question_id: button.dataset.relationQuestionId,
      };
      const relation = button.closest('.related-relation');
      try {
        const response = await fetch('/api/question-relations/remove', {
          method: 'POST', credentials: 'same-origin',
          headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
        });
        const payload = await response.json();
        if (!response.ok || payload.status !== 'saved') {
          if (relation) relation.append(` ${payload.error || '保存失败，请刷新页面重试。'}`);
          button.disabled = false;
          return;
        }
        if (relation) relation.textContent = payload.dissolved_cluster
          ? '已移出并解散题组；页面将刷新。'
          : '已从题组移出；页面将刷新。';
        window.setTimeout(() => window.location.reload(), 350);
      } catch (_) {
        if (relation) relation.append(' 未连接到本机保存服务，尚未修改档案。');
        button.disabled = false;
      }
    });
  });
  document.querySelectorAll('[data-cluster-membership]').forEach(manager => {
    const input = manager.querySelector('[data-cluster-add-search-input]');
    const options = [...manager.querySelectorAll('[data-cluster-add-option]')];
    const count = manager.querySelector('[data-cluster-add-count]');
    const result = manager.querySelector('[data-cluster-add-result]');
    const update = () => {
      const query = (input?.value || '').trim().toLowerCase();
      let selected = 0;
      options.forEach(option => {
        const checkbox = option.querySelector('[data-cluster-add-checkbox]');
        if (checkbox?.checked) selected += 1;
        option.hidden = Boolean(query && !(option.dataset.clusterAddSearch || '').includes(query));
      });
      if (count) count.textContent = `已选择 ${selected} 题`;
    };
    input?.addEventListener('input', update);
    options.forEach(option => option.querySelector('[data-cluster-add-checkbox]')?.addEventListener('change', update));
    manager.querySelector('[data-cluster-add]')?.addEventListener('click', async event => {
      const selected = options.filter(option => option.querySelector('[data-cluster-add-checkbox]')?.checked);
      if (!selected.length) { if (result) result.textContent = '请至少选择 1 道未归组原题。'; return; }
      if (location.protocol !== 'http:' || location.hostname !== '127.0.0.1') {
        if (result) result.textContent = '当前是只读页面，请通过本机保存入口打开。';
        return;
      }
      if (!window['confirm'](`确认把 ${selected.length} 道未归组原题加入指定题组？`)) return;
      const button = event.currentTarget;
      button.disabled = true;
      const questions = selected.map(option => ({
        case_id: option.querySelector('[data-cluster-add-checkbox]').dataset.caseId,
        question_id: option.querySelector('[data-cluster-add-checkbox]').dataset.questionId,
      }));
      try {
        const response = await fetch('/api/question-clusters/add', {
          method: 'POST', credentials: 'same-origin',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            cluster_id: manager.dataset.clusterId,
            expected_status: manager.dataset.clusterStatus,
            expected_fingerprint: manager.dataset.clusterFingerprint,
            questions,
          }),
        });
        const payload = await response.json();
        if (!response.ok || payload.status !== 'saved') {
          if (result) result.textContent = payload.error || '保存失败，请刷新页面重试。';
          button.disabled = false;
          return;
        }
        if (result) result.textContent = '已保存到本机档案，页面将刷新。';
        window.setTimeout(() => window.location.reload(), 350);
      } catch (_) {
        if (result) result.textContent = '未连接到本机保存服务，尚未修改档案。';
        button.disabled = false;
      }
    });
    update();
  });
  const downloadActions = actions => {
    try {
      const payload = {schema_version: 1, generated_at: new Date().toISOString(), actions};
      const blob = new Blob([JSON.stringify(payload, null, 2) + '\\n'], {type: 'application/json'});
      const link = document.createElement('a');
      link.href = URL.createObjectURL(blob);
      link.download = `interview-review-actions-${new Date().toISOString().replace(/[:.]/g, '-')}.json`;
      document.body.appendChild(link);
      link.click(); link.remove();
      window.setTimeout(() => URL.revokeObjectURL(link.href), 1000);
      return true;
    } catch (_) { return false; }
  };
  document.querySelectorAll('[data-answer-confirm]').forEach(answerForm => {
    const initialShort = answerForm.querySelector('[data-answer-short]');
    const initialFull = answerForm.querySelector('[data-answer-full]');
    answerForm.dataset.initialShort = initialShort?.value || '';
    answerForm.dataset.initialFull = initialFull?.value || '';
    const factsCheckbox = answerForm.querySelector('[data-confirm-facts]');
    [initialShort, initialFull].forEach(field => field?.addEventListener('input', () => {
      factsCheckbox.checked = false;
      answerForm.querySelector('[data-confirm-save]').textContent = '保存并确认到档案';
      answerForm.querySelector('[data-confirm-result]').textContent = '有未保存修改，请核对事实后点击保存。';
    }));
    answerForm.querySelector('[data-confirm-save]').addEventListener('click', async () => {
      const result = answerForm.querySelector('[data-confirm-result]');
      const button = answerForm.querySelector('[data-confirm-save]');
      const short = answerForm.querySelector('[data-answer-short]').value.trim();
      const full = answerForm.querySelector('[data-answer-full]').value.trim();
      if (!short || !full) { result.textContent = '请先填写两个版本。'; return; }
      if (!factsCheckbox.checked) {
        result.textContent = '请先核对个人事实，再勾选确认。'; return;
      }
      if (location.protocol !== 'http:' || location.hostname !== '127.0.0.1') {
        result.textContent = '当前是只读文件页面。请通过本机保存入口打开，再点击保存。';
        return;
      }
      button.disabled = true;
      initialShort.disabled = true;
      initialFull.disabled = true;
      factsCheckbox.disabled = true;
      result.textContent = '正在写入本机档案…';
      try {
        const isCase = answerForm.dataset.answerKind === 'case';
        const response = await fetch(isCase ? '/api/case-answers/confirm' : '/api/answers/confirm', {
          method: 'POST', credentials: 'same-origin',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({answer_id: answerForm.dataset.answerId,
            case_id: answerForm.dataset.caseId, question_id: answerForm.dataset.questionId,
            expected_hash: answerForm.dataset.answerFingerprint,
            short_30s: short, standard_2m: full, confirmed_facts: true})
        });
        const payload = await response.json();
        if (!response.ok || payload.status !== 'saved') {
          result.textContent = payload.error || '保存失败，请重试。';
          return;
        }
        answerForm.dataset.answerFingerprint = payload.fingerprint;
        answerForm.dataset.answerState = 'confirmed';
        answerForm.dataset.initialShort = short;
        answerForm.dataset.initialFull = full;
        button.textContent = '已保存 ✓';
        const scope = answerForm.closest('.practice-card') || answerForm.parentElement;
        const isCaseAnswer = answerForm.dataset.answerKind === 'case';
        const savedAnswerId = answerForm.dataset.answerId || '';
        let savedView = [...(scope?.querySelectorAll('[data-saved-answer-view]') || [])]
          .find(view => view.dataset.savedAnswerKind === (isCaseAnswer ? 'case' : 'standard')
            && (isCaseAnswer || view.dataset.savedAnswerId === savedAnswerId));
        if (!savedView && answerForm.parentElement) {
          savedView = document.createElement('section');
          savedView.className = isCaseAnswer ? 'case-reference' : 'home-reference';
          savedView.dataset.savedAnswerView = '1';
          savedView.dataset.savedAnswerKind = isCaseAnswer ? 'case' : 'standard';
          if (!isCaseAnswer) savedView.dataset.savedAnswerId = savedAnswerId;
          answerForm.parentElement.insertBefore(savedView, answerForm);
        }
        if (savedView) {
          const heading = isCaseAnswer ? '我的练习答案' : '跨场参考答案';
          const scopeLabel = isCaseAnswer ? '已确认 · 本题专用' : '已确认 · 跨场整理';
          const viewClass = isCaseAnswer ? 'case-reference' : 'home-reference';
          savedView.className = viewClass;
          savedView.innerHTML = `<div class="${isCaseAnswer ? 'case-reference-heading' : 'home-reference-heading'}"><strong>${heading}</strong><span>${scopeLabel}</span></div><div class="${isCaseAnswer ? 'case-reference-short' : 'home-reference-short'}"><strong>30 秒版</strong><p data-saved-answer-short></p></div><details><summary>查看 2 分钟版</summary><p data-saved-answer-full></p></details>`;
          savedView.querySelector('[data-saved-answer-short]').textContent = short;
          savedView.querySelector('[data-saved-answer-full]').textContent = full;
        }
        savedView?.querySelectorAll('.status').forEach(node => {
          node.textContent = '已确认';
          node.classList.remove('unverified');
          node.classList.add('strong');
        });
        draftControllers.get(answerForm)?.clear('已保存到档案', false);
        announceArchiveChange({
          kind: 'answer',
          answer_id: answerForm.dataset.answerId || '',
          case_id: answerForm.dataset.caseId || '',
          question_id: answerForm.dataset.questionId || '',
          fingerprint: payload.fingerprint,
        });
        if (payload.page_refresh_required) {
          result.textContent = payload.warning || '答案已写入档案，但页面没有重建成功；请手动刷新并检查页面。';
        } else {
          result.innerHTML = '已保存并确认到本机档案。 <button type="button" data-refresh-after-save>刷新看最新页面</button>';
          result.querySelector('[data-refresh-after-save]').addEventListener('click', () => window.location.reload());
        }
      } catch (_) {
        result.textContent = '未连接到本机保存服务，答案尚未保存。请重新打开保存入口。';
      } finally {
        button.disabled = false;
        initialShort.disabled = false;
        initialFull.disabled = false;
        factsCheckbox.disabled = false;
      }
    });
  });
  document.querySelectorAll('[data-practice-widget]').forEach(widget => {
    const improve = widget.querySelector('[data-ai-improve]');
    if (improve) improve.addEventListener('click', async () => {
      const current = widget.querySelector('[data-practice-answer]').value.trim();
      const past = widget.querySelector('[data-ai-past-answer]').value.trim();
      const result = widget.querySelector('[data-ai-result]');
      const preview = widget.querySelector('[data-ai-prompt-preview]');
      const related = widget.querySelector('[data-ai-related-answers]')?.value.trim() || '';
      if (!current && !past && !related) {
        result.textContent = '没有可用的现场回答。请先写下自己的想法。';
        return;
      }
      const question = widget.querySelector('[data-ai-question]').value.trim();
      const original = widget.querySelector('[data-ai-original]').value.trim();
      const diagnosis = widget.querySelector('[data-ai-diagnosis]').value.trim();
      const reference = widget.querySelector('[data-ai-reference]')?.value.trim() || '';
      const groupId = widget.dataset.homeGroupId || '';
      const prompt = [
        '请基于我的真实回答，帮我打磨这道面试题。',
        '请先指出已说清的内容、缺少的关键论证和需要我核实的事实；再写一版 30 秒回答和一版 2 分钟回答，最后列出 2 个可能追问。',
        '只用我提供的经历作为个人事实；不要编造职责、数据、事故和项目结果。技术补充请与个人经历分开标注，无法核实的数字或判断写成“待核实”。若我的新补充与现场回答冲突，先指出冲突。',
        groupId ? `题组定位：${groupId}` : `档案定位：${widget.dataset.caseId} / ${widget.dataset.questionId}`,
        `题目：${question}`,
        original ? `面试官原话摘录：${original}` : '',
        current ? `我现在的补充或改写（优先采用，但仍需核实）：${current}` : '',
        past ? `我的历史现场回答（原始记录摘录）：${past}` : '',
        related ? `${groupId ? '同题组各场' : '其他场次的相关'}原题和旧回答（关联可能未经核对，请先比较问法并指出冲突）：${related}` : '',
        reference ? `已有 AI 参考答案（只供比较，不作为已核实的个人事实）：${reference}` : '',
        diagnosis ? `既有 AI 点评（仅作线索，不是事实）：${diagnosis}` : '',
      ].filter(Boolean).join('\\n\\n');
      preview.value = prompt;
      preview.hidden = true;
      let copied = false;
      try {
        if (navigator.clipboard?.writeText) {
          await navigator.clipboard.writeText(prompt);
          copied = true;
        }
      } catch (_) {}
      if (!copied) {
        try {
          preview.hidden = false;
          preview.focus();
          preview.select();
          copied = document.execCommand('copy');
        } catch (_) {}
      }
      if (copied) {
        preview.hidden = true;
        result.textContent = '已复制。回到本对话粘贴并发送，我会基于你的回答生成改进版。';
      } else {
        preview.hidden = false;
        result.textContent = '浏览器未允许自动复制；请复制下方内容并发到本对话。';
      }
    });
    let startedAt = 0;
    let elapsed = 0;
    let timer = null;
    const display = widget.querySelector('[data-timer-display]');
    const start = widget.querySelector('[data-timer-start]');
    const stop = widget.querySelector('[data-timer-stop]');
    const paint = () => {
      const seconds = elapsed + (startedAt ? Math.floor((Date.now() - startedAt) / 1000) : 0);
      display.textContent = `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
      widget.dataset.duration = String(seconds);
    };
    start.addEventListener('click', () => {
      clearSavedPractice();
      if (startedAt) return;
      startedAt = Date.now(); start.disabled = true; stop.disabled = false;
      timer = window.setInterval(paint, 250); paint();
    });
    stop.addEventListener('click', () => {
      clearSavedPractice();
      if (!startedAt) return;
      elapsed += Math.max(1, Math.floor((Date.now() - startedAt) / 1000));
      startedAt = 0; window.clearInterval(timer); timer = null;
      start.disabled = false; stop.disabled = true; paint();
    });
    const makeAttemptId = () => {
      const random = (window.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`).replace(/[^a-f0-9]/gi, '');
      return `practice-${random.slice(0, 32).padEnd(16, '0')}`;
    };
    const practiceButton = widget.querySelector('[data-practice-save]');
    const clearSavedPractice = () => {
      if (practiceButton.dataset.practiceSaved !== '1') return;
      practiceButton.dataset.practiceSaved = '0';
      practiceButton.disabled = false;
      practiceButton.textContent = '保存本次练习';
      widget.dataset.attemptId = '';
      widget.dataset.savedPracticePayload = '';
    };
    [
      widget.querySelector('[data-practice-answer]'),
      widget.querySelector('[data-practice-rating]'),
      widget.querySelector('[data-practice-note]'),
    ].forEach(input => input?.addEventListener('input', clearSavedPractice));
    widget.querySelector('[data-practice-save]').addEventListener('click', async () => {
      if (startedAt) stop.click();
      const answer = widget.querySelector('[data-practice-answer]').value.trim();
      const rating = Number(widget.querySelector('[data-practice-rating]').value);
      const duration = Number(widget.dataset.duration || 0);
      const result = widget.querySelector('[data-practice-result]');
      const button = widget.querySelector('[data-practice-save]');
      if (!answer || !rating || duration < 1) {
        result.textContent = '请先计时、填写回答并选择自评'; return;
      }
      const payloadKey = JSON.stringify({answer, duration, rating,
        note: widget.querySelector('[data-practice-note]').value.trim()});
      if (widget.dataset.savedPracticePayload && widget.dataset.savedPracticePayload !== payloadKey) {
        widget.dataset.attemptId = '';
      }
      const action = {kind: 'practice_attempt', case_id: widget.dataset.caseId,
        question_id: widget.dataset.questionId, answer, duration_seconds: duration,
        self_rating: rating, note: widget.querySelector('[data-practice-note]').value.trim(),
        attempt_id: widget.dataset.attemptId || makeAttemptId()};
      widget.dataset.attemptId = action.attempt_id;
      let saved = false;
      button.disabled = true;
      result.textContent = '正在写入本机私有档案…';
      try {
        if (location.protocol !== 'http:' || location.hostname !== '127.0.0.1') {
          throw new TypeError('本地服务不可用');
        }
        const response = await fetch('/api/practice-attempts/record', {
          method: 'POST', credentials: 'same-origin',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(action),
        });
        let payload = {};
        try { payload = await response.json(); } catch (_) {}
        if (!response.ok || payload.status !== 'recorded') {
          result.textContent = payload.error || '练习记录未保存，请按提示修改后重试。';
          return;
        }
        result.textContent = payload.deduplicated
          ? '本次练习已入库 · 待审核；重复请求已自动忽略。'
          : '本次练习已入库 · 待审核。';
        button.textContent = '已入库 ✓';
        button.dataset.practiceSaved = '1';
        widget.dataset.savedPracticePayload = payloadKey;
        draftControllers.get(widget)?.clear('已入库；本页练习草稿已清除', false);
        saved = true;
      } catch (_) {
        result.textContent = downloadActions([action])
          ? '本机服务未响应：已下载待应用文件，当前状态未确认；文件沿用同一编号，应用后再确认是否入库。'
          : '本机服务不可用，且待应用文件下载失败；当前尚未保存。';
      } finally {
        button.disabled = saved;
      }
    });
  });
  const homeQuestions = [...document.querySelectorAll('[data-home-question]')];
  const homePrevious = document.querySelector('[data-home-previous]');
  const homeNext = document.querySelector('[data-home-next]');
  const homePosition = document.querySelector('[data-home-position]');
  const homeHint = document.querySelector('[data-home-next-hint]');
  if (homeNext && homeQuestions.length) {
    let activeIndex = 0;
    const updateHomeNavigation = () => {
      const atStart = activeIndex === 0;
      const atEnd = activeIndex === homeQuestions.length - 1;
      if (homePrevious) homePrevious.disabled = atStart;
      homeNext.disabled = atEnd;
      if (homePosition) homePosition.textContent = `第 ${activeIndex + 1} / ${homeQuestions.length} 题`;
      const next = homeQuestions[activeIndex + 1];
      const nextTitle = next ? (next.dataset.questionTitle || '下一题').trim() : '';
      homeNext.textContent = next ? `下一题：${nextTitle.length > 32 ? nextTitle.slice(0, 32) + '…' : nextTitle}` : '已到最后一题';
      if (homeHint) homeHint.textContent = atEnd ? '已到最后一道代表题；如需更多题目，请打开完整题库。' : '';
    };
    const showHomeQuestion = index => {
      homeQuestions[activeIndex].hidden = true;
      activeIndex = index;
      homeQuestions[activeIndex].hidden = false;
      document.getElementById('practice')?.scrollIntoView({block: 'start'});
      updateHomeNavigation();
    };
    homePrevious?.addEventListener('click', () => {
      if (activeIndex > 0) showHomeQuestion(activeIndex - 1);
    });
    homeNext.addEventListener('click', () => {
      if (activeIndex < homeQuestions.length - 1) showHomeQuestion(activeIndex + 1);
    });
    updateHomeNavigation();
  }
  const applyButton = document.querySelector('[data-apply-actions]');
  const applyReview = document.querySelector('[data-apply-review]');
  const applySummary = document.querySelector('[data-apply-summary]');
  const confirmApplyButton = document.querySelector('[data-confirm-apply-actions]');
  const cancelApplyButton = document.querySelector('[data-cancel-apply-actions]');
  const actionMessage = document.querySelector('[data-action-message]');
  const actionTypeLabels = {
    standard_answer: '成熟答案', question_cluster: '高频问法', question_cluster_create: '新题组',
    profile_event: '档案提醒', practice_attempt: '练习记录', question_preference: '题目偏好',
  };
  const actionSummary = actions => {
    const counts = {};
    actions.forEach(action => {
      const label = actionTypeLabels[action.kind] || '其他操作';
      counts[label] = (counts[label] || 0) + 1;
    });
    const details = Object.entries(counts).map(([label, count]) => `${label} ${count} 项`).join('、');
    return `将把 ${actions.length} 项修改写入当前本机档案：${details}。服务端会先检查旧状态；检查阶段任一项过期都会拒绝本次应用，磁盘故障需核对档案。`;
  };
  const hideApplyReview = () => { if (applyReview) applyReview.hidden = true; };
  if (applyButton) applyButton.addEventListener('click', () => {
    if (!queuedActions.length) {
      if (actionMessage) actionMessage.textContent = '当前没有待应用操作';
      return;
    }
    if (applySummary) applySummary.textContent = actionSummary(queuedActions);
    if (applyReview) applyReview.hidden = false;
    confirmApplyButton?.focus();
  });
  cancelApplyButton?.addEventListener('click', hideApplyReview);
  if (confirmApplyButton) confirmApplyButton.addEventListener('click', async () => {
    if (!queuedActions.length) { hideApplyReview(); return; }
    if (location.protocol !== 'http:' || location.hostname !== '127.0.0.1') {
      if (actionMessage) actionMessage.textContent = '当前是只读文件页面。请通过本机保存入口打开，再应用审核结果。';
      hideApplyReview();
      return;
    }
    const actions = [...queuedActions];
    confirmApplyButton.disabled = true;
    if (actionMessage) actionMessage.textContent = '正在检查旧状态并写入本机档案…';
    try {
      const response = await fetch('/api/review-actions/apply', {
        method: 'POST', credentials: 'same-origin',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({schema_version: 1, actions}),
      });
      let payload = {};
      try { payload = await response.json(); } catch (_) {}
      if (!response.ok || payload.status !== 'applied') {
        if (actionMessage) actionMessage.textContent = payload.error || '审核结果应用失败，可能已部分写入；请核对档案后刷新页面。';
        hideApplyReview();
        return;
      }
      queuedActions = [];
      persistQueue();
      if (actionMessage) actionMessage.textContent = payload.warning || `已应用 ${actions.length} 项审核结果；页面已重建，请刷新查看最新内容。`;
      updateQueue();
      document.querySelectorAll('[data-review-item]').forEach(updateReviewState);
      hideApplyReview();
      announceArchiveChange({kind: 'review-actions', count: actions.length});
    } catch (_) {
      if (actionMessage) actionMessage.textContent = '未连接到本机保存服务，审核结果尚未应用。请重新打开保存入口。';
      hideApplyReview();
    } finally {
      confirmApplyButton.disabled = false;
    }
  });
  const exportButton = document.querySelector('[data-export-actions]');
  if (exportButton) exportButton.addEventListener('click', () => {
    const message = document.querySelector('[data-action-message]');
    if (!queuedActions.length) { if (message) message.textContent = '当前没有待导出操作'; return; }
    if (!downloadActions(queuedActions)) { if (message) message.textContent = '尚未下载，请重试'; return; }
    if (message) message.textContent = '已发起下载；确认文件存在后回到对话说“应用刚导出的审核结果”';
  });
  const clearButton = document.querySelector('[data-clear-actions]');
  if (clearButton) clearButton.addEventListener('click', () => {
    queuedActions = [];
    try {
      sessionStorage.removeItem(actionKey);
      localStorage.removeItem(actionKey);
      localStorage.removeItem(persistenceKey);
      localStorage.removeItem(legacyActionKey);
    } catch (_) {}
    persistent = false;
    const toggle = document.querySelector('[data-persist-actions]');
    if (toggle) toggle.checked = false;
    updateQueue();
    document.querySelectorAll('[data-review-item]').forEach(updateReviewState);
    const message = document.querySelector('[data-action-message]');
    if (message) message.textContent = '当前档案队列和旧版本地队列已清空';
  });
  const persistenceToggle = document.querySelector('[data-persist-actions]');
  if (persistenceToggle) {
    persistenceToggle.checked = persistent;
    persistenceToggle.addEventListener('change', () => {
      const previous = persistent ? localStorage : sessionStorage;
      persistent = persistenceToggle.checked;
      try {
        previous.removeItem(actionKey);
        if (persistent) localStorage.setItem(persistenceKey, '1');
        else localStorage.removeItem(persistenceKey);
      } catch (_) {}
      persistQueue();
      const message = document.querySelector('[data-action-message]');
      if (message) message.textContent = persistent
        ? '已选择本机持久保存；服务重启换端口后不保证恢复'
        : '已改为仅当前浏览器会话保存';
    });
  }
  updateQueue();
  document.querySelectorAll('[data-review-item]').forEach(updateReviewState);
})();
"""


def render_history_section(cases: list[dict[str, Any]], current: str | None, from_case: bool) -> str:
    rows = history_rows(cases, current, from_case)
    return f"""
<section id="history">
  <h2>{"其他场次" if from_case else "逐场查看"}</h2>
  <p class="muted">每场先显示一条已保存的下一步；打开后可查看 JD、技术核验和逐字稿。</p>
  <div class="search-line history-controls"><label for="history-search">搜索公司、岗位、技术或待办</label>
    <input id="history-search" class="search" data-history-search type="search" placeholder="例如 MySQL、示例项目、管控平台">
    <button type="button" class="search-clear" data-search-clear hidden>清除搜索</button>
    <label class="history-filter">结果筛选<select data-history-result-filter><option value="all">全部结果</option><option value="recorded">已记录结果</option><option value="missing">尚未记录结果</option></select></label>
    <span data-history-count aria-live="polite"></span></div>
  <div class="history-grid">{rows}</div>
  <div class="empty" data-history-empty>没有匹配的历史面试。</div>
</section>"""


def pending_review_items(root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Return pending answers, clusters, and profile facts in stable display order."""
    standards = read_json(root / "profile" / "standard-answers.json", {})
    clusters = read_json(root / "profile" / "question-clusters.json", {})
    state = read_json(root / "profile" / "state.json", {})
    pending_answers = [
        item for item in standards.get("answers", [])
        if isinstance(item, dict) and item.get("status") == "pending"
    ]
    pending_clusters = [
        item for item in clusters.get("clusters", [])
        if isinstance(item, dict) and item.get("status") == "pending"
    ]
    pending_events = []
    for values in state.get("by_type", {}).values() if isinstance(state.get("by_type"), dict) else []:
        for item in values if isinstance(values, list) else []:
            if isinstance(item, dict) and item.get("status") == "pending":
                pending_events.append(item)
    pending_answers.sort(key=lambda item: (
        -len(item.get("source_questions", [])) if isinstance(item.get("source_questions"), list) else 0,
        str(item.get("title") or item.get("answer_id") or ""),
    ))
    def cluster_frequency_key(item: dict[str, Any]) -> tuple[int, int, str]:
        refs = item.get("questions", [])
        refs = refs if isinstance(refs, list) else []
        case_ids = {
            str(ref.get("case_id") or "")
            for ref in refs
            if isinstance(ref, dict) and ref.get("case_id")
        }
        return (-len(case_ids), -len(refs), str(item.get("label") or item.get("cluster_id") or ""))

    pending_clusters.sort(key=cluster_frequency_key)
    pending_events.sort(key=lambda item: (str(item.get("type") or ""), str(item.get("key") or "")))
    return pending_answers, pending_clusters, pending_events


def split_priority_review(
    pending_answers: list[dict[str, Any]],
    pending_clusters: list[dict[str, Any]],
    pending_events: list[dict[str, Any]],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    """Keep generated candidates available without assigning review chores."""
    return [], pending_answers, pending_clusters, pending_events, []


def _clip_review_text(value: str, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit] + ("…" if len(text) > limit else "")


def _profile_event_context(item: dict[str, Any], case_by_id: dict[str, dict[str, Any]]) -> tuple[str, str]:
    """Locate question/answer evidence for a profile reminder without inventing missing context."""
    case = case_by_id.get(str(item.get("case_id") or ""))
    evidence_ids = {str(value) for value in item.get("evidence", []) if value}
    if not case or not evidence_ids:
        return "", ""
    segments = transcript_segments(case["path"])
    qa = read_json(case["path"] / "qa-tree.json", {})
    matches: list[tuple[int, str, str]] = []
    for question in qa.get("questions", []):
        if not isinstance(question, dict):
            continue
        question_ids = {str(value) for value in question.get("question_evidence", []) if value}
        answer_ids = {str(value) for value in question.get("answer_evidence", []) if value}
        overlap = len(evidence_ids & (question_ids | answer_ids))
        if not overlap:
            continue
        original = " ".join(
            str(segments[segment_id].get("text_corrected") or segments[segment_id].get("text_raw") or "").strip()
            for segment_id in question_ids
            if segment_id in evidence_ids and segment_id in segments
            and segments[segment_id].get("role") == "interviewer"
        )
        answer = " ".join(
            str(segments[segment_id].get("text_corrected") or segments[segment_id].get("text_raw") or "").strip()
            for segment_id in answer_ids
            if segment_id in evidence_ids and segment_id in segments
            and segments[segment_id].get("role") == "candidate"
        )
        matches.append((overlap, original, answer))
    if matches:
        _, original, answer = max(matches, key=lambda value: (value[0], len(value[1]), len(value[2])))
        return _clip_review_text(original, 420), _clip_review_text(answer, 900)
    original = " ".join(
        str(segments[segment_id].get("text_corrected") or segments[segment_id].get("text_raw") or "").strip()
        for segment_id in evidence_ids
        if segment_id in segments and segments[segment_id].get("role") == "interviewer"
    )
    answer = " ".join(
        str(segments[segment_id].get("text_corrected") or segments[segment_id].get("text_raw") or "").strip()
        for segment_id in evidence_ids
        if segment_id in segments and segments[segment_id].get("role") == "candidate"
    )
    return _clip_review_text(original, 420), _clip_review_text(answer, 900)


def render_profile_event_rows(events: list[dict[str, Any]], cases: list[dict[str, Any]]) -> str:
    case_by_id = {str(case["case_id"]): case for case in cases}
    rows = []
    for item in events:
        event_id = str(item.get("event_id") or "")
        event_type = label("profile_type", item.get("type"))
        original, answer = _profile_event_context(item, case_by_id)
        context_html = (
            f'<div class="review-evidence"><p><strong>原始问题：</strong>{html.escape(original)}</p>'
            f'<p><strong>我的现场回答：</strong>{html.escape(answer)}</p></div>'
            if original or answer else
            '<div class="review-evidence"><p class="muted"><strong>原始问题和我的回答：</strong>未能从现有证据可靠定位，请查看对应面试原文。</p></div>'
        )
        rows.append(
            '<article class="review-row" data-review-search-row data-search="'
            + html.escape(" ".join([event_type, str(item.get("reason") or ""), str(item.get("key") or ""), str(item.get("case_id") or "")]).lower(), quote=True)
            + '"><div><strong>'
            f'{html.escape(event_type or "复盘提醒")}</strong>'
            f'{context_html}'
            f'<p><strong>AI 提醒：</strong>{html.escape(str(item.get("reason") or "无说明"))}</p>'
            f'<small>{html.escape(str(item.get("key") or event_id))} · {html.escape(str(item.get("case_id") or ""))}</small></div>'
            f'{review_buttons("profile_event", event_id, "pending")}</article>'
        )
    return "".join(rows)


def render_mistake_book_entry(root: Path) -> str:
    payload = read_json(root / "profile" / "mistake-book.json", {})
    entries = payload.get("entries") if isinstance(payload.get("entries"), list) else []
    if not entries:
        return ""
    return (
        '<section class="review-entry"><div><h2>错题本</h2>'
        f'<p>{len(entries)} 条现场说错的技术结论，按知识主题整理成复习卡，带对照来源。</p></div>'
        f'<a href="{MISTAKE_BOOK_HTML}">去复习 →</a></section>'
    )


def render_pending_review_entry(root: Path) -> str:
    return (
        '<section class="review-entry"><div><h2>需要纠错时再核对资料</h2>'
        '<p>练习不用先审核。自动整理的候选资料已收起，可按需查看或修正。</p></div>'
        f'<a href="{PENDING_REVIEW_HTML}">按需核对 →</a></section>'
    )


def render_review_center(root: Path, cases: list[dict[str, Any]]) -> str:
    pending_answers, pending_clusters, pending_events = pending_review_items(root)
    _, deferred_answers, deferred_clusters, deferred_events, _ = split_priority_review(
        pending_answers,
        pending_clusters,
        pending_events,
    )
    case_by_id = {str(case["case_id"]): case for case in cases}
    deferred_answer_cards = "".join(
        render_standard_answer(answer, case_by_id, editable=True)
        for answer in deferred_answers
    )
    deferred_cluster_ids = {str(item.get("cluster_id") or "") for item in deferred_clusters}
    deferred_cluster_section, _, _ = render_question_clusters(
        root,
        cases,
        editable=True,
        status_filter={"pending"},
        cluster_ids=deferred_cluster_ids,
        section_id="deferred-clusters",
        section_title="历史候选问题分组",
        section_intro="这些分组是自动生成的候选草稿，不影响练习；发现分错时再修改。",
        question_page=QUESTION_BANK_HTML,
    )
    deferred_event_rows = render_profile_event_rows(deferred_events, cases)
    return (
        '<section id="review-center" class="review-center"><h2>按需核对资料</h2>'
        '<p>这些内容是自动整理的候选资料，不是今天的待办。准备和练习可以直接继续；发现事实、答案或合并有误时再修改。</p></section>'
        '<div class="review-toolbar" data-review-toolbar hidden><span data-action-count>已选择 0 项</span>'
        '<button type="button" data-apply-actions>在本机应用选择</button><button type="button" data-export-actions>下载选择</button><span data-action-message aria-live="polite" role="status"></span>'
        '<div class="review-apply-confirm" data-apply-review hidden role="group" aria-label="确认应用审核结果">'
        '<strong data-apply-summary></strong><button type="button" data-confirm-apply-actions>确认应用</button><button type="button" class="secondary" data-cancel-apply-actions>取消</button></div>'
        '<details class="review-more"><summary>更多操作</summary>'
        '<button type="button" class="secondary" data-clear-actions>清空选择</button>'
        '<label><input type="checkbox" data-persist-actions> 关闭页面后保留选择（当前本机入口；服务重启换端口后不保证恢复）</label>'
        '</details></div>'
        + render_merge_builder(root, cases, builder_id="pending-manual-merge")
        + '<details class="pending-backlog" id="historical-candidates"><summary>查看候选资料（按需）</summary>'
        + '<div class="search-line"><label for="review-search">搜索历史候选</label><input id="review-search" class="search" data-review-search type="search" placeholder="问题、提醒、答案或场次"><button type="button" class="search-clear" data-search-clear hidden>清除搜索</button><span data-review-count aria-live="polite"></span></div>'
        + '<section class="pending-group" id="pending-answers"><div class="topic-heading"><h2>跨场答案草稿</h2>'
        f'<span class="topic-frequency">{len(deferred_answers)} 项</span></div>'
        + '<p class="muted">这是 AI 根据多场回答整理的参考答法。确认它，表示你认可文字内容和事实边界，可作为以后练习或面试准备的答案；本轮不要求处理。</p>'
        + (deferred_answer_cards if deferred_answer_cards else '<p class="muted">暂无其余成熟答案。</p>')
        + '</section>'
        + deferred_cluster_section
        + '<section class="pending-group" id="pending-facts"><div class="topic-heading"><h2>AI 给你的复习提醒</h2>'
        f'<span class="topic-frequency">{len(deferred_events)} 项</span></div>'
        + '<p class="muted">这是 AI 根据面试记录整理的提醒：哪些知识点可能要复习、哪些回答容易被追问、哪些简历说法需要补证据。它只是提醒，不是已经确认的结论；本轮可以先跳过。</p>'
        + (f'<div class="review-list">{deferred_event_rows}</div>' if deferred_event_rows else '<p class="muted">暂无长期档案待核验资料。</p>')
        + '</section></details>'
    )


def render_learning_dashboard(root: Path) -> str:
    plan = read_json(root / "profile" / "review-plan.json", {})
    if not isinstance(plan, dict) or plan.get("schema_version") != 1:
        return '<section id="learning-dashboard"><h2>练习计划与趋势</h2><p class="muted">尚未生成练习计划；运行复习计划构建器后显示。</p></section>'
    goal_rows = []
    for goal in review_plan_goals(root, 3):
        representative = goal.get("representative", {})
        anchor = f'practice-{representative.get("case_id", "")}-{representative.get("question_id", "")}'
        goal_rows.append(
            f'<li><a href="{QUESTION_BANK_HTML}#{html.escape(anchor, quote=True)}">{html.escape(str(goal.get("title") or ""))}</a>'
            f'<span>{html.escape(str(goal.get("evidence_status") or ""))} · {html.escape(str(goal.get("why_priority") or ""))}</span></li>'
        )
    trend_rows = []
    for item in plan.get("trends", [])[-16:]:
        if not isinstance(item, dict):
            continue
        average = item.get("average_score")
        trend_rows.append(
            f'<tr><td>{html.escape(str(item.get("interview_date") or ""))}</td>'
            f'<td>{html.escape(str(item.get("topic_label") or ""))}</td>'
            f'<td>{html.escape(str(item.get("question_count") or ""))}</td>'
            f'<td>{html.escape(str(average if average is not None else "—"))}</td></tr>'
        )
    reference_health = read_json(root / "profile" / "reference-health.json", {})
    audit = read_json(root / "profile" / "standard-answer-audit.json", {})
    candidate_report = read_json(root / "profile" / "question-cluster-candidates.json", {})
    health_text = (
        f'资料链接 {reference_health.get("source_count", 0)} 个，需复核 {reference_health.get("review_due", 0)} 个；'
        f'成熟答案缺失 {audit.get("missing_standard_answers", 0)} 组；'
        f'尚未归并问题 {candidate_report.get("unassigned_before", 0)} 道。'
    )
    return (
        '<section id="learning-dashboard" class="learning-dashboard"><div class="topic-heading"><h2>练习计划与跨场趋势</h2>'
        f'<span class="topic-frequency">{html.escape(str(plan.get("generated_at") or ""))}</span></div>'
        f'<p class="muted">{html.escape(str(plan.get("method") or ""))}</p><p>{html.escape(health_text)}</p>'
        '<div class="dashboard-grid"><div><h3>去重后的训练目标</h3><ol class="review-queue">'
        + "".join(goal_rows) + '</ol></div><div><h3>分主题表现快照</h3>'
        + ('<div class="table-wrap"><table><thead><tr><th>日期</th><th>主题</th><th>题数</th><th>平均分</th></tr></thead><tbody>' + "".join(trend_rows) + '</tbody></table></div>' if trend_rows else '<p class="muted">暂无可比较的评分记录。</p>')
        + '</div></div></section>'
    )


def render_case(case: dict[str, Any], cases: list[dict[str, Any]]) -> str:
    case_path: Path = case["path"]
    root = case_path.parents[1]
    manifest = read_json(case_path / "manifest.json", {})
    scores = read_json(case_path / "scores.json", {})
    claims = read_json(case_path / "claims.json", {})
    matrix = read_json(case_path / "jd-matrix.json", {})
    review = read_text(case_path / "review.md")
    warnings = manifest.get("warnings", [])
    warning_html = "".join(f"<li>{html.escape(str(item))}</li>" for item in warnings)
    warning_block = (
        f'<div class="notice"><strong>材料与方法限制</strong><ul>{warning_html}</ul></div>'
        if warning_html
        else ""
    )
    meta = "".join(
        [
            badge(str(case["round"])),
            badge(f'结果：{display_result(str(case["result"]))}'),
            badge(f'说话人：{label("speaker_status", case["speaker_status"])}'),
            badge(f'分析：{label("analysis_status", case["analysis_status"])}'),
        ]
    )
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
{SECURITY_META}<title>{html.escape(case['company'])} · {html.escape(case['role'])} · 面试复盘</title><style>{STYLE}</style></head>
<body data-interview-case="{html.escape(case['case_id'], quote=True)}" data-store-id="{archive_id(root)}"><main class="case-page">
<nav class="topnav" aria-label="本页导航"><a href="../../{HISTORY_HTML}">面试总览</a><a href="#practice">本场练习</a><a href="#materials">原始材料</a></nav>
<header class="hero case-hero"><div class="case-hero-copy"><div class="eyebrow">Interview System · Offline Review</div>
<h1>{html.escape(case['company'])}<br>{html.escape(case['role'])}</h1>
<p>{html.escape(case['datetime'])}</p><div class="meta">{meta}</div></div><aside class="case-hero-result"><span>本场结果</span><strong>{html.escape(display_result(str(case['result'])))}</strong><small>{html.escape(str(case['date']))} · {html.escape(str(case['round']))}</small></aside></header>
{privacy_notice()}
{warning_block}
{reference_source_notice(manifest)}
{review_focus(review)}
<section id="performance" class="case-section performance-section"><div class="section-heading"><div><span class="section-kicker">快速概览</span><h2>表现概览</h2></div><span class="section-note">只看本场，不做跨岗位排名</span></div><div class="grid scores">{score_cards(scores.get('overall', {}))}</div></section>
{render_case_practice(case)}
<section id="materials" class="case-section materials-section"><div class="section-heading"><div><span class="section-kicker">需要时再看</span><h2>证据与材料</h2></div><span class="section-note">原始资料默认收起</span></div><p class="muted">这里保留完整复盘、岗位要求、技术核验和逐字稿，适合核对依据，不是首次阅读入口。</p>
<div class="materials-grid"><section id="review" class="material-card"><h3>完整复盘</h3><details><summary>展开完整复盘与原始建议</summary><div class="review">{markdown_to_html(review)}</div></details></section>
<section id="jd" class="material-card"><h3>岗位 JD 原文</h3><details><summary>展开本场保存的 JD 原文</summary>{jd_source(case_path, manifest)}</details></section>
<section id="jd-matrix" class="material-card"><h3>JD 能力矩阵</h3><details><summary>展开逐项岗位对照</summary>{jd_table(matrix)}</details></section>
<section id="claims" class="material-card"><h3>技术核验</h3><details><summary>展开逐条技术结论与来源</summary>{claims_table(claims)}</details></section></div></section>
{render_history_section(cases, case['case_id'], True)}
<section id="evidence"><h2>逐字稿证据</h2><details><summary>展开全部片段</summary>{transcript_view(case_path)}</details></section>
<p class="foot">本页由本地结构化产物生成。HTML 是展示视图，不替代原始逐字稿、JSON、JD、简历或技术来源。</p>
</main><script>{SCRIPT}</script></body></html>"""


def render_history_index(root: Path, cases: list[dict[str, Any]]) -> str:
    target_prep = render_target_prep_entry(root)
    target_nav = f'<a href="{PREPARATIONS_HTML}">公司面试准备</a>' if target_prep else ''
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
{SECURITY_META}<title>面试总览</title><style>{STYLE}</style></head><body data-interview-history data-store-id="{archive_id(root)}"><main>
<nav class="topnav" aria-label="本页导航">{target_nav}<a href="#practice">练习</a><a href="#history-archive">历史面试</a></nav>
<header class="hero overview-hero"><div class="eyebrow">Interview System · Local Archive</div><h1>面试练习</h1>
<p>从一道真实题目开始；已有参考答案放在题目旁。历史面试记录可随时查看。</p></header>
{privacy_notice(compact=True)}
<div id="overview">
{target_prep}
{render_history_practice(root, cases)}
{render_mistake_book_entry(root)}
{render_pending_review_entry(root)}
<details id="history-archive" class="history-archive"><summary>查看全部 {len(cases)} 场历史档案与搜索</summary>{render_history_section(cases, None, False)}</details>
</div>
<p class="foot">结果仅用于历史记录，不用于生成通过率或推断单一题目的因果。原始问题、逐字稿和旧版本均保留；本页只是派生展示。</p>
</main><script>{SCRIPT}</script></body></html>"""


def render_answers(root: Path, cases: list[dict[str, Any]]) -> str:
    case_by_id = {str(case["case_id"]): case for case in cases}
    standard_map = standard_answer_map(root)
    standards = [
        answer for topic in TOPIC_LABELS
        for answer in standard_map.get(topic, [])
    ]
    standard_cards = "".join(render_standard_answer(answer, case_by_id) for answer in standards)
    focus = read_json(root / "profile" / "practice-focus.json", {})
    focus_refs: dict[tuple[str, str], dict[str, Any]] = {}
    if isinstance(focus, dict):
        for group in focus.get("groups", []):
            if not isinstance(group, dict):
                continue
            for ref in group.get("questions", []):
                if isinstance(ref, dict):
                    focus_refs[(str(ref.get("case_id") or ""), str(ref.get("question_id") or ""))] = ref
    question_cards: list[str] = []
    followup_count = 0
    for case in cases:
        answer_map = case_answer_map(case["path"])
        for entry in practice_items(case["path"]):
            question = entry["question"]
            qid = str(question.get("question_id") or "")
            saved_entry = answer_map.get(qid, {})
            curated = saved_entry.get("curated_answer")
            if isinstance(curated, dict) and isinstance(curated.get("versions"), dict):
                versions = curated["versions"]
                answer_html = (f'<p><strong>30 秒版：</strong>{html.escape(str(versions.get("short_30s") or ""))}</p>'
                               f'<details><summary>查看 2 分钟版</summary><p>{html.escape(str(versions.get("standard_2m") or ""))}</p></details>')
            else:
                answer_html = answer_view(saved_entry.get("reference_answer"), "查看参考答法")
            if not answer_html:
                continue
            title = str(question.get("question") or "")
            focus_ref = focus_refs.get((str(case["case_id"]), qid), {})
            followups = focus_ref.get("followups", [])
            followup_answers = focus_ref.get("followup_answers", [])
            followup_items: list[str] = []
            if isinstance(followups, list) and isinstance(followup_answers, list):
                for prompt, followup_answer in zip(followups, followup_answers):
                    followup_html = answer_view(followup_answer, "查看参考答法")
                    if isinstance(prompt, str) and prompt.strip() and followup_html:
                        followup_items.append(f'<li>{html.escape(prompt)}{followup_html}</li>')
            followup_count += len(followup_items)
            followup_section = (
                f'<details class="saved-followups"><summary>查看 {len(followup_items)} 道延伸追问答案</summary>'
                f'<ol>{"".join(followup_items)}</ol></details>'
            ) if followup_items else ""
            search = " ".join((title, str(case["company"]), str(case["date"]),
                               " ".join(str(item) for item in followups) if isinstance(followups, list) else "")).lower()
            source_href = f'interviews/{quote(str(case["name"]))}/{CASE_HTML}#practice-{quote(qid)}'
            question_cards.append(
                f'<details class="saved-question-answer" id="saved-answer-{html.escape(str(case["case_id"]), quote=True)}-{html.escape(qid, quote=True)}" '
                f'data-saved-question-answer data-search="{html.escape(search, quote=True)}">'
                f'<summary>{html.escape(title)}<small>{html.escape(str(case["company"]))} · {html.escape(str(case["date"]))}</small></summary>'
                '<div class="saved-answer-body">'
                + answer_html.replace('<details class="reference-answer">', '<details class="reference-answer" open>', 1)
                + followup_section
                + f'<p><a href="{html.escape(source_href, quote=True)}">查看本场问题与现场回答 →</a></p>'
                '</div></details>'
            )
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
{SECURITY_META}<title>已生成答案</title><style>{STYLE}</style></head><body data-interview-answers data-store-id="{archive_id(root)}"><main>
<nav class="topnav" aria-label="本页导航"><a href="{HISTORY_HTML}">面试总览</a><a href="#cross-case-answers">跨场答案</a><a href="#question-answers">逐题答案</a><a href="{QUESTION_BANK_HTML}">完整题库</a></nav>
<header class="hero"><div class="eyebrow">Interview System · Saved Answers</div><h1>已生成答案</h1>
<p>共 {len(standards)} 份跨场参考答案、{len(question_cards)} 道逐题参考答案、{followup_count} 道延伸追问答案。点题目即可查看；草稿状态会明确标注。</p></header>
{privacy_notice(compact=True)}
<div class="search-line"><label for="answer-search">找答案</label><input id="answer-search" class="search" data-answer-search type="search" placeholder="输入问题、项目或公司"><button type="button" class="search-clear" data-search-clear hidden>清除搜索</button><span data-answer-count aria-live="polite"></span></div>
<section class="answer-hub-section" id="cross-case-answers"><h2>跨场参考答案</h2><p class="muted">从多场相近问题整理；先看 30 秒版，再按需展开 2 分钟版和依据。</p>{standard_cards or '<p class="muted">暂无跨场参考答案。</p>'}</section>
<section class="answer-hub-section" id="question-answers"><h2>逐题参考答案</h2><p class="muted">针对某场具体提问生成；现场回答可从题目下方进入核对。</p>{''.join(question_cards) or '<p class="muted">暂无逐题参考答案。</p>'}</section>
<p class="answer-hub-empty" data-answer-empty>没有匹配的答案。</p>
<p class="foot">参考答案与现场回答分开保存。未核对草稿可用于练习，涉及个人职责和数字时仍需核对事实。</p>
</main><script>{SCRIPT}</script></body></html>"""


def mistake_book_source_digest(root: Path, cases: list[dict[str, Any]]) -> str:
    """标识错题本依赖的原始文件；条数相同的内容修订也会改变摘要。"""
    paths = [root / "profile" / "mistake-book-notes.json"]
    for case in sorted(cases, key=lambda item: str(item["case_id"])):
        paths.extend(case["path"] / name for name in ("manifest.json", "qa-tree.json", "claims.json"))
    digest = hashlib.sha256()
    for path in paths:
        digest.update(str(path.relative_to(root)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes() if path.is_file() else b"<missing>")
        digest.update(b"\0")
    return digest.hexdigest()


def render_mistake_book(root: Path, cases: list[dict[str, Any]]) -> str:
    payload = read_json(root / "profile" / "mistake-book.json", {})
    entries = payload.get("entries") if isinstance(payload.get("entries"), list) else []
    entries = [item for item in entries if isinstance(item, dict)]
    # 错题本是派生视图；claims 变化后若没重跑构建器，这里必须显式提示，避免页面看起来是最新的。
    live_incorrect = 0
    for case in cases:
        claims = read_json(case["path"] / "claims.json", {})
        live_incorrect += sum(
            1 for item in claims.get("claims", [])
            if isinstance(item, dict) and str(item.get("verdict") or "") == "incorrect"
        )
    stale_notice = ""
    if payload and payload.get("source_digest") != mistake_book_source_digest(root, cases):
        stale_notice = (
            '<div class="notice">错题本与当前来源文件不一致'
            f'（页面 {len(entries)} 条，当前判错 {live_incorrect} 条）。'
            '请运行 <code>python3 scripts/build_mistake_book.py --root &lt;data-root&gt;</code> 后重新渲染。</div>'
        )

    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in entries:
        grouped.setdefault(str(item.get("topic_label") or "其他问题"), []).append(item)

    def source_list(item: dict[str, Any]) -> str:
        rows: list[str] = []
        for source in item.get("sources", []):
            if not isinstance(source, dict):
                continue
            url = safe_url(str(source.get("url") or ""))
            title = str(source.get("title") or "来源")
            head = external_link(url, title) if url else html.escape(title)
            section = str(source.get("section") or "").strip()
            relation = str(source.get("relation") or "").strip()
            meta = " · ".join(part for part in (section, relation) if part)
            summary = str(source.get("summary") or "").strip()
            meta_html = f'<span class="muted"> · {html.escape(meta)}</span>' if meta else ""
            summary_html = f'<p>{html.escape(summary)}</p>' if summary else ""
            rows.append(f'<li>{head}{meta_html}{summary_html}</li>')
        if not rows:
            return '<p class="muted">该结论没有登记外部来源。</p>'
        return f'<ul class="mistake-source-list">{"".join(rows)}</ul>'

    def card(item: dict[str, Any]) -> str:
        entry_id = str(item.get("entry_id") or "")
        meta = " · ".join(
            part for part in (
                str(item.get("date") or ""),
                str(item.get("company") or ""),
                str(item.get("role") or ""),
                str(item.get("round") or ""),
            ) if part
        )
        href = str(item.get("case_href") or "")
        question = html.escape(str(item.get("question") or "（未记录原题）"))
        title = f'<a href="{html.escape(href, quote=True)}">{question}</a>' if href else question
        correction = str(item.get("correction") or "").strip()
        hook = str(item.get("memory_hook") or "").strip()
        recall = str(item.get("recall_prompt") or "").strip()
        version = str(item.get("version_context") or "").strip()
        searchable = " ".join(
            str(item.get(field) or "") for field in
            ("question", "claim", "correction", "why_wrong", "memory_hook", "company", "role", "topic_label")
        ).lower()
        version_html = f'<p class="mistake-meta">版本上下文：{html.escape(version)}</p>' if version else ""
        return (
            f'<article class="mistake-card" data-mistake-entry="{html.escape(entry_id, quote=True)}"'
            f' data-search="{html.escape(searchable, quote=True)}">'
            '<div class="mistake-head"><div>'
            f'<h3>{title}</h3><p class="mistake-meta">{html.escape(meta)}</p>'
            f'{version_html}'
            '</div><span class="status incorrect">错误</span></div>'
            f'<blockquote class="mistake-claim"><strong>我的说法：</strong>{html.escape(str(item.get("claim") or ""))}</blockquote>'
            f'<p class="mistake-why"><strong>错在哪：</strong>{html.escape(str(item.get("why_wrong") or ""))}</p>'
            + (f'<div class="mistake-fix"><strong>正确说法</strong><p>{html.escape(correction)}</p></div>' if correction
               else '<p class="mistake-why muted"><strong>正确说法：</strong>尚未整理，先按下方来源自行核对。</p>')
            + (f'<p class="mistake-hook"><strong>记忆点：</strong>{html.escape(hook)}</p>' if hook else "")
            + (f'<p class="mistake-recall"><strong>复述提示：</strong>{html.escape(recall)}</p>' if recall else "")
            + f'<details class="mistake-sources"><summary>依据来源（{len(item.get("sources") or [])}）</summary>{source_list(item)}</details>'
            '<label class="mistake-done"><input type="checkbox" data-mistake-done> 已复习</label>'
            '</article>'
        )

    sections: list[str] = []
    for topic_label, items in grouped.items():
        sections.append(
            f'<section class="mistake-topic"><div class="topic-heading">'
            f'<h2>{html.escape(topic_label)}</h2>'
            f'<span class="topic-frequency">{len(items)} 条</span></div>'
            + "".join(card(item) for item in items)
            + '</section>'
        )
    total = len(entries)
    corrected = sum(1 for item in entries if str(item.get("correction") or "").strip())
    case_count = len({str(item.get("case_id") or "") for item in entries})
    body = "".join(sections) if sections else (
        '<section class="mistake-topic"><h2>暂无错题</h2>'
        + ('<p class="muted">各场 claims.json 里还没有 verdict=incorrect 的结论。</p>' if not live_incorrect
           else '<p class="muted">档案里已有判错的结论，但错题本尚未生成；请先运行构建器。</p>')
        + '</section>'
    )
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
{SECURITY_META}<title>错题本</title><style>{STYLE}{MISTAKE_STYLE}</style></head><body data-interview-mistakes data-store-id="{archive_id(root)}"><main>
<nav class="topnav" aria-label="本页导航"><a href="{HISTORY_HTML}">面试总览</a><a href="{QUESTION_BANK_HTML}">完整题库</a><a href="{ANSWERS_HTML}">已生成答案</a></nav>
<header class="hero"><div class="eyebrow">Interview System · Mistake Book</div><h1>错题本</h1>
<p>{total} 条现场判错的结论，按知识主题分组。每条都带「我的说法」和对照来源，复习时先自己复述再展开。</p></header>
{privacy_notice(compact=True)}
{stale_notice}
<section class="mistake-summary">
<div><span class="label">错题</span><span class="metric">{total}<span> 条</span></span></div>
<div><span class="label">已整理正确说法</span><span class="metric">{corrected}<span> 条</span></span></div>
<div><span class="label">涉及场次</span><span class="metric">{case_count}<span> 场</span></span></div>
<div><span class="label">本机已复习</span><span class="metric" data-mistake-progress>0<span> / {total}</span></span></div>
</section>
<div class="mistake-toolbar">
<input id="mistake-search" class="search" data-mistake-search type="search" placeholder="搜索题目、说法、正确说法或公司">
<button type="button" class="search-clear" data-mistake-clear hidden>清除搜索</button>
<label class="mistake-filter"><input type="checkbox" data-mistake-pending> 只看未复习</label>
<span data-mistake-count aria-live="polite"></span>
</div>
{body}
<p class="mistake-empty" data-mistake-empty hidden>没有匹配的错题。</p>
<p class="foot">错题本只是派生视图：判定与来源来自各场 claims.json，正确说法来自 profile/mistake-book-notes.json。改判定请回到对应场次；「已复习」只保存在本机浏览器。</p>
</main><script>{SCRIPT}</script><script>{MISTAKE_SCRIPT}</script></body></html>"""


MISTAKE_STYLE = """
.mistake-summary{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-top:18px}
.mistake-summary>div{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px;display:flex;flex-direction:column;gap:2px}
.mistake-summary .metric{font-size:26px}
.mistake-toolbar{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-top:20px}
.mistake-toolbar .search{margin:0}
.mistake-filter{display:flex;align-items:center;gap:6px;font-size:13px;color:var(--muted)}
.mistake-topic{margin-top:26px}
.mistake-card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px 18px;margin:12px 0;box-shadow:0 3px 12px #0f172a08}
.mistake-card[hidden]{display:none}
.mistake-card.done{opacity:.62}
.mistake-head{display:flex;justify-content:space-between;align-items:flex-start;gap:12px}
.mistake-head h3{margin:0 0 4px;font-size:17px}
.mistake-meta{margin:0;color:var(--muted);font-size:12px}
.mistake-claim{margin:12px 0 10px;padding:10px 14px;border-left:4px solid #f0b4a8;background:#fff4f1;color:#7c2d12;border-radius:7px}
.mistake-why{margin:8px 0}
.mistake-fix{margin:10px 0;padding:12px 14px;border-left:4px solid #6ee7b7;background:#ecfdf5;border-radius:8px}
.mistake-fix strong{display:block;color:#047857;margin-bottom:4px}
.mistake-fix p{margin:0}
.mistake-hook{margin:10px 0 0;padding:8px 12px;background:#eef2ff;border-radius:8px;color:#3730a3}
.mistake-recall{margin:6px 0 0;color:#475569;font-size:13px}
.mistake-sources{margin-top:12px}
.mistake-sources summary{font-size:13px;color:#31577d}
.mistake-source-list{margin:6px 0 0;padding-left:18px;font-size:13px}
.mistake-source-list p{margin:2px 0 8px;color:var(--muted)}
.mistake-done{display:inline-flex;align-items:center;gap:6px;margin-top:12px;font-size:13px;color:var(--muted);cursor:pointer}
.mistake-empty{padding:16px;color:var(--muted)}
@media print{.mistake-toolbar,.mistake-done{display:none!important}}
@media (max-width:760px){.mistake-summary{grid-template-columns:repeat(2,minmax(0,1fr))}}
"""

MISTAKE_SCRIPT = """
(function(){
  const cards=[...document.querySelectorAll('[data-mistake-entry]')];
  const KEY='interview-system:mistake-done:v1';
  const search=document.querySelector('[data-mistake-search]');
  const clear=document.querySelector('[data-mistake-clear]');
  const pending=document.querySelector('[data-mistake-pending]');
  const progress=document.querySelector('[data-mistake-progress]');
  const count=document.querySelector('[data-mistake-count]');
  const empty=document.querySelector('[data-mistake-empty]');
  let done={};
  try{const raw=JSON.parse(localStorage.getItem(KEY)||'{}');if(raw&&typeof raw==='object')done=raw;}catch(error){done={};}
  const save=()=>{try{localStorage.setItem(KEY,JSON.stringify(done));}catch(error){/* 隐私模式下忽略 */}};
  const refresh=()=>{
    let reviewed=0;
    for(const card of cards){
      const id=card.dataset.mistakeEntry||'';
      const box=card.querySelector('[data-mistake-done]');
      const isDone=Boolean(done[id]);
      if(box)box.checked=isDone;
      card.classList.toggle('done',isDone);
      if(isDone)reviewed+=1;
    }
    if(progress)progress.innerHTML=reviewed+'<span> / '+cards.length+'</span>';
    return reviewed;
  };
  const apply=()=>{
    const term=(search&&search.value||'').trim().toLowerCase();
    const onlyPending=Boolean(pending&&pending.checked);
    let shown=0;
    for(const card of cards){
      const id=card.dataset.mistakeEntry||'';
      const matches=!term||(card.dataset.search||'').includes(term);
      const visible=matches&&(!onlyPending||!done[id]);
      card.hidden=!visible;
      if(visible)shown+=1;
    }
    if(count)count.textContent=shown+' 条';
    if(empty)empty.hidden=shown>0;
    if(clear)clear.hidden=!term;
  };
  cards.forEach(card=>{
    const box=card.querySelector('[data-mistake-done]');
    if(!box)return;
    box.addEventListener('change',()=>{
      const id=card.dataset.mistakeEntry||'';
      if(box.checked)done[id]=new Date().toISOString().slice(0,10);else delete done[id];
      save();refresh();apply();
    });
  });
  if(search)search.addEventListener('input',apply);
  if(pending)pending.addEventListener('change',apply);
  if(clear)clear.addEventListener('click',()=>{if(search)search.value='';apply();if(search)search.focus();});
  refresh();apply();
})();
"""


def render_pending_review(root: Path, cases: list[dict[str, Any]]) -> str:
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
{SECURITY_META}<title>按需核对资料</title><style>{STYLE}</style></head><body data-interview-pending-review data-store-id="{archive_id(root)}"><main>
<nav class="topnav" aria-label="本页导航"><a href="{HISTORY_HTML}">面试总览</a><a href="{QUESTION_BANK_HTML}">完整题库</a><a href="#pending-manual-merge">合并同类问题</a><a href="#historical-candidates">候选资料</a></nav>
<header class="hero"><div class="eyebrow">Interview System · Reference Check</div><h1>按需核对资料</h1>
<p>练习不用先审核。发现自动整理的内容有误，再打开候选资料修正。</p></header>
{privacy_notice(compact=True)}
{render_review_center(root, cases)}
<p class="foot">有修改时，选择后点“下载选择”，再回到对话说“应用刚导出的审核结果”。应用前不会改写档案。</p>
</main><script>{SCRIPT}</script></body></html>"""


def render_question_bank_content(root: Path, cases: list[dict[str, Any]], *, question_page: str = HISTORY_HTML) -> str:
    grouped: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    topic_cases: dict[str, set[str]] = {}
    case_views: dict[
        str,
        tuple[
            dict[str, dict[str, Any]],
            dict[str, dict[str, Any]],
            dict[str, list[dict[str, str]]],
        ],
    ] = {}
    answer_count = 0
    source_count = 0
    standard_answers = standard_answer_map(root)
    standard_by_question = standard_answer_question_map(root)
    tags_map = question_tag_map(root)
    standard_answer_count = sum(len(values) for values in standard_answers.values())
    case_by_id = {str(case["case_id"]): case for case in cases}
    cluster_section, cluster_bookmarks, cluster_count = render_question_clusters(
        root, cases, question_page=question_page
    )
    cluster_on_demand = (
        f'<details class="cluster-on-demand"><summary>查看跨场高频问法（{cluster_count} 组，按需展开）</summary>{cluster_section}</details>'
        if cluster_section else ""
    )
    for case in cases:
        case_id = str(case["case_id"])
        segments = transcript_segments(case["path"])
        answers = case_answer_map(case["path"])
        sources = question_source_map(case["path"])
        case_views[case_id] = (segments, answers, sources)
        for entry in practice_items(case["path"]):
            qid = str(entry["question"].get("question_id") or "")
            topic = question_topic(entry["question"])
            grouped.setdefault(topic, []).append((case, entry))
            topic_cases.setdefault(topic, set()).add(case_id)
            if answers.get(qid, {}).get("reference_answer") or (case_id, qid) in standard_by_question:
                answer_count += 1
            if sources.get(qid):
                source_count += 1
    topic_order = sorted(
        grouped,
        key=lambda topic: (-len(topic_cases.get(topic, set())), -len(grouped[topic]), TOPIC_LABELS[topic]),
    )
    bookmarks: list[str] = []
    sections: list[str] = []
    for topic in topic_order:
        label_text = TOPIC_LABELS[topic]
        case_count = len(topic_cases.get(topic, set()))
        question_count = len(grouped[topic])
        bookmarks.append(
            f'<a class="bookmark" data-bookmark="topic-{topic}" href="#topic-{topic}">'
            f'<span>{html.escape(label_text)}</span><span>{case_count} 场 · {question_count} 题</span></a>'
        )
        cards = []
        for case, entry in grouped[topic]:
            qid = str(entry["question"].get("question_id") or "")
            segments, answers, sources = case_views[str(case["case_id"])]
            cards.append(question_card(
                case,
                entry,
                segments,
                False,
                answers.get(qid),
                sources.get(qid, []),
                label_text,
                standard_by_question.get((str(case["case_id"]), qid)),
                tags=tags_map.get((str(case["case_id"]), qid)),
            ))
        sections.append(
            f'<section class="topic-group" id="topic-{topic}" data-topic-group>'
            f'<div class="topic-heading"><h2>{html.escape(label_text)}</h2>'
            f'<span class="topic-frequency">覆盖 {case_count} / {len(cases)} 场 · 共 {question_count} 题</span></div>'
            f'<div class="practice-grid">{"".join(cards)}</div></section>'
        )
    total_questions = sum(len(values) for values in grouped.values())
    pending_answer_count = sum(
        1 for answers in standard_answers.values() for answer in answers
        if answer.get("status") == "pending"
    )
    answer_cards = "".join(
        render_standard_answer(answer, case_by_id)
        for topic in topic_order
        for answer in standard_answers.get(topic, [])
    )
    answer_library = (
        f'<details class="standard-answer-library" id="answer-library" data-standard-library>'
        f'<summary>查看跨场参考答案（{standard_answer_count} 份）</summary>'
        f'<p class="muted">其中 {pending_answer_count} 份是未核对草稿，可先用于练习；按需展开完整答案与来源。</p>'
        f'{answer_cards}</details>'
    ) if answer_cards else ""
    question_summary = (
        f'共 {total_questions} 道真实面试题，{cluster_count} 组跨场高频问法，'
        f'{standard_answer_count} 份跨场参考答案（{pending_answer_count} 份未核对草稿），{answer_count} 道可找到参考答法，'
        f'{source_count} 道绑定了技术核验资料。'
    )
    return f"""<div id="question-bank" class="bank-layout">
<aside class="bank-sidebar"><h2>搜索与书签</h2>
<div class="search-line"><label for="question-search">搜索全部题目</label><input id="question-search" class="search" data-question-search type="search" placeholder="问题、公司、岗位、回答或诊断"><button type="button" class="search-clear" data-search-clear hidden>清除搜索</button><span data-question-count aria-live="polite"></span></div>
<div class="practice-guide"><strong>答题方法</strong><br>先写自己的版本，再用 AI 打磨；旧回答、参考答案和证据按需展开。代码题还要运行边界测试。</div>
{f'<h3 class="bookmark-title">高频问法</h3><nav class="bookmark-list" aria-label="跨场高频重复问法">{cluster_bookmarks}</nav>' if cluster_bookmarks else ''}
<h3 class="bookmark-title">主题</h3><nav class="bookmark-list" aria-label="按主题覆盖频率排序的书签">{"".join(bookmarks)}</nav></aside>
<div class="bank-content"><div class="question-bank-summary"><strong>题库概览</strong><span>{html.escape(question_summary)}</span></div>{render_next_prep(root, cases, section_id="question-bank-next-prep", question_page=question_page, compact=True)}{answer_library}{render_merge_builder(root, cases, builder_id="bank-manual-merge")}{cluster_on_demand}<h2>全部面试题（按主题频率排序）</h2><p class="muted">高频问法按跨场语义归并统计；主题仍按覆盖的面试场次数排序，题数用于次级排序。主题归类基于可解释的关键词规则，不代表语义完全相同。每题保留原始场次证据。</p>
<details class="long-tail-section" data-long-tail><summary>查看全部原题与长尾题（{total_questions} 道）</summary>
<p class="muted">原题保留场次、问答树、标签和证据链接；当前页面只提供练习记录，不会删除真实记录。</p>
{"".join(sections)}</details><div class="empty" data-question-empty>没有匹配的面试题。</div></div></div>"""


def render_question_bank(root: Path, cases: list[dict[str, Any]]) -> str:
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
{SECURITY_META}<title>完整面试题库</title><style>{STYLE}</style></head><body data-interview-question-bank data-store-id="{archive_id(root)}"><main>
<nav class="topnav" aria-label="本页导航"><a href="{HISTORY_HTML}">面试总览</a><a href="{MISTAKE_BOOK_HTML}">错题本</a><a href="{ANSWERS_HTML}">已生成答案</a><a href="{PENDING_REVIEW_HTML}">按需核对</a></nav>
<header class="hero"><div class="eyebrow">Interview System · Full Question Bank</div><h1>完整面试题库</h1>
<p>全量题目、跨场高频问法和练习记录集中在此页；总览页只保留一道入口练习，避免首次打开加载全部内容。</p></header>
{privacy_notice(compact=True)}
{render_question_bank_content(root, cases, question_page=QUESTION_BANK_HTML)}
<p class="foot">本页是独立题库，不改变原始逐字稿、分析 JSON 或候选草稿状态。</p>
</main><script>{SCRIPT}</script></body></html>"""


class SmokeParser(HTMLParser):
    pass


def smoke_check(document: str, required: list[str]) -> list[str]:
    errors: list[str] = []
    parser = SmokeParser()
    try:
        parser.feed(document)
        parser.close()
    except Exception as exc:  # HTMLParser rarely fails, keep diagnostic explicit.
        errors.append(f"HTML 解析失败：{exc}")
    for marker in required:
        if marker not in document:
            errors.append(f"HTML 缺少标记：{marker}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="生成统一离线面试复盘与历史索引 HTML")
    parser.add_argument("--root", required=True, type=Path)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--case", type=Path)
    group.add_argument("--all", action="store_true")
    args = parser.parse_args()

    os.umask(0o077)
    root = args.root.expanduser().resolve()
    compatibility = scan_store(root)
    if compatibility["status"] != "compatible":
        print(json.dumps({"status": "error", "rendered": [], "errors": compatibility["issues"]}, ensure_ascii=False))
        return 1
    standard_answer_issues = validate_standard_answers(root)
    if standard_answer_issues:
        print(json.dumps({"status": "error", "rendered": [], "errors": standard_answer_issues}, ensure_ascii=False))
        return 1
    question_cluster_issues = validate_question_clusters(root)
    if question_cluster_issues:
        print(json.dumps({"status": "error", "rendered": [], "errors": question_cluster_issues}, ensure_ascii=False))
        return 1
    question_tag_issues = validate_question_tags(root) + validate_question_preferences(root)
    if question_tag_issues:
        print(json.dumps({"status": "error", "rendered": [], "errors": question_tag_issues}, ensure_ascii=False))
        return 1
    cases = discover_cases(root)
    if not cases:
        print(json.dumps({"status": "error", "errors": ["没有可呈现的面试场次"]}, ensure_ascii=False))
        return 1

    if args.all:
        selected = cases
    else:
        target = args.case.expanduser().resolve()
        selected = [item for item in cases if item["path"].resolve() == target]
        if not selected:
            print(json.dumps({"status": "error", "errors": [f"场次不在数据目录中：{target}"]}, ensure_ascii=False))
            return 1

    stale = []
    for item in selected:
        manifest = read_json(item["path"] / "manifest.json", {})
        if manifest.get("analysis_status") == "stale":
            stale.append((item, "分析产物已过期，请先重算并通过 validate_case.py --refresh-analysis"))
        elif manifest.get("analysis_status") == "current":
            for error in current_errors(item["path"], manifest):
                stale.append((item, error))
    if stale:
        errors = [
            f'{item["case_id"]}: {error}' for item, error in stale
        ]
        print(
            json.dumps(
                {
                    "status": "error",
                    "rendered": [],
                    "history_index": str(root / HISTORY_HTML),
                    "question_bank": str(root / QUESTION_BANK_HTML),
                    "answers": str(root / ANSWERS_HTML),
                    "pending_review": str(root / PENDING_REVIEW_HTML),
                    "mistakes": str(root / MISTAKE_BOOK_HTML),
                    "preparations": str(root / PREPARATIONS_HTML),
                    "history_cases": len(cases),
                    "errors": errors,
                },
                ensure_ascii=False,
            )
        )
        return 1

    errors: list[str] = []
    rendered: list[str] = []
    for case in selected:
        document = render_case(case, cases)
        case_errors = smoke_check(document, ["data-interview-case", 'id="jd"', 'id="jd-matrix"', 'id="history"', case["case_id"]])
        if case_errors:
            errors.extend(f'{case["case_id"]}: {item}' for item in case_errors)
            continue
        destination = case["path"] / CASE_HTML
        write_text(destination, document)
        rendered.append(str(destination))

    history = render_history_index(root, cases)
    errors.extend(smoke_check(history, ["data-interview-history", 'id="history"', 'data-history-search', "先把这题答完整"]))
    index_path = root / HISTORY_HTML
    question_bank = render_question_bank(root, cases)
    errors.extend(smoke_check(question_bank, ["data-interview-question-bank", 'id="question-bank"', "完整面试题库", "全部面试题", f'href="{HISTORY_HTML}"']))
    question_bank_path = root / QUESTION_BANK_HTML
    answers = render_answers(root, cases)
    errors.extend(smoke_check(answers, ["data-interview-answers", 'id="cross-case-answers"', 'id="question-answers"']))
    answers_path = root / ANSWERS_HTML
    pending_review = render_pending_review(root, cases)
    errors.extend(smoke_check(pending_review, ["data-interview-pending-review", 'id="review-center"', "下载选择"]))
    pending_review_path = root / PENDING_REVIEW_HTML
    mistake_book = render_mistake_book(root, cases)
    errors.extend(smoke_check(mistake_book, ["data-interview-mistakes", "错题本", 'data-mistake-search', f'href="{HISTORY_HTML}"']))
    mistake_book_path = root / MISTAKE_BOOK_HTML
    preparations = render_interview_preparations(root)
    errors.extend(smoke_check(preparations, ["data-interview-preparations", "公司面试准备", f'href="{HISTORY_HTML}"']))
    preparations_path = root / PREPARATIONS_HTML
    preparation_pages: list[tuple[Path, str]] = []
    for payload in discover_target_interview_preps(root):
        preparation = render_interview_preparation(root, payload)
        company = str(payload["target"].get("company") or "目标公司")
        errors.extend(smoke_check(preparation, ["data-interview-preparation", company, f'href="{PREPARATIONS_HTML}"']))
        preparation_pages.append((root / target_interview_prep_filename(payload), preparation))
    if not errors:
        write_text(index_path, history)
        write_text(question_bank_path, question_bank)
        write_text(answers_path, answers)
        write_text(pending_review_path, pending_review)
        write_text(mistake_book_path, mistake_book)
        write_text(preparations_path, preparations)
        for preparation_path, preparation in preparation_pages:
            write_text(preparation_path, preparation)

    status = "ok" if not errors else "error"
    print(
        json.dumps(
            {
                "status": status,
                "rendered": rendered,
                "history_index": str(index_path),
                "question_bank": str(question_bank_path),
                "answers": str(answers_path),
                "pending_review": str(pending_review_path),
                "mistakes": str(mistake_book_path),
                "preparations": str(preparations_path),
                "preparation_pages": [str(path) for path, _ in preparation_pages],
                "history_cases": len(cases),
                "errors": errors,
            },
            ensure_ascii=False,
        )
    )
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
