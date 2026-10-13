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
from check_standard_answers import validate_standard_answers
from labels_zh import label


CASE_HTML = "interview-review.html"
HISTORY_HTML = "interview-history.html"
QUESTION_BANK_HTML = "interview-question-bank.html"
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
    category = str(question.get("category") or "").lower()
    text = str(question.get("question") or "")

    if category in {"algorithm", "coding"}:
        return "algorithm"
    if category == "ai" or any(value in text for value in (" AI", "AI ", "Agent", "ReAct")):
        return "ai"
    if category == "messaging" or any(value in text for value in ("消息队列", "消费方", "上游限流")):
        return "messaging"
    if any(value in text for value in ("自我介绍", "个人经历", "近期工作", "近年负责")):
        return "self-intro"
    if category in {"motivation", "career", "compensation", "logistics", "role_fit"} or any(
        value in text for value in ("离职", "职业规划", "薪资", "新机会", "岗位如何理解", "岗位的意愿", "愿意去杭州", "迁往杭州", "为什么考虑目标公司", "符合预期")
    ):
        return "career"
    if category in {"collaboration", "leadership", "organization"} or any(
        value in text for value in ("团队如何组织", "团队分工", "职责边界", "不配合", "说服", "意见不一致", "推进里程碑")
    ):
        return "collaboration"
    if category == "product" or any(value in text for value in ("推广", "接入最大痛点", "业务信任", "方案目前怎样共存")):
        return "product"
    if any(value in text for value in ("JVM", "GC", "CMS", "G1", "Java 线程", "HikariCP", "Druid", "DBCP", "垃圾回收")):
        return "java"
    if any(value in text for value in ("监控", "告警", "排障", "故障处理", "定位和解决", "定位、止损", "磁盘一键清理", "核心指标", "业务不当使用")):
        return "observability"
    if category == "data-migration" or any(value in text for value in ("数据迁移", "迁移流程", "新旧库", "双写", "最终一致", "配置中心")):
        return "migration"
    if category in {"performance"} or any(value in text for value in ("性能提升", "性能优化", "SQL 改写性能", "SQL 突然变慢", "慢 SQL", "执行计划", "IO 次数", "减少了什么成本", "重复计算", "大事务")):
        return "performance"
    if any(value in text for value in ("高可用", "容灾", "切流", "故障判定", "主库故障", "选主", "限流、降级和熔断", "数据丢失", "恢复时间", "读从读不到", "半同步", "单元化", "调整路由权重")):
        return "availability"
    if any(value in text for value in ("分库分表", "分库/分表", "分片", "跨节点查询", "跨分片", "Group By", "Limit", "Distinct", "路由算法", "哈希扩容")):
        return "sharding"
    if any(value in text for value in ("Vitess", "ShardingSphere", "数据库中间件", "数据库代理层", "分布式数据库", "数据库网关", "VTTablet", "Gen4 Planner", "SQL 解析层", "语法树", "影子压测")):
        return "middleware"
    if category == "database" or any(value in text for value in ("MySQL", "InnoDB", "锁等待", "意向锁", "Gap Lock", "唯一索引", "主从延迟", "主从、主库")):
        return "mysql"
    if category in {"system_design", "design", "architecture"}:
        return "system-design"
    if "关键技术工作" in text:
        return "project-role"
    if category in {"project", "experience", "problem_solving", "background", "behavioral"}:
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


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        handle.write(value)
        temporary = handle.name
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


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


def privacy_notice() -> str:
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


def render_plan_summary(root: Path) -> str:
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
{body}<p><a class="primary-link" href="{plan_href(plan, False)}">查看全部待办与证据 →</a></p></section>"""


def cross_case_report_link(root: Path) -> str:
    reports = sorted(root.glob("historical-reanalysis-*.html"), reverse=True)
    if not reports:
        return ""
    return f'<p class="deep-link"><a href="{quote(reports[0].name)}">查看跨场复核与同公司多轮变化 →</a></p>'


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
                "round": str(manifest.get("round") or "未知轮次"),
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


def review_buttons(kind: str, item_id: str, status: str) -> str:
    """Render offline review controls; JavaScript only queues an exportable action."""
    return (
        f'<div class="review-actions" data-review-item data-review-kind="{html.escape(kind, quote=True)}" '
        f'data-review-id="{html.escape(item_id, quote=True)}" data-review-current="{html.escape(status, quote=True)}">'
        '<button type="button" data-review-status="confirmed">确认</button>'
        '<button type="button" data-review-status="pending">保持待确认</button>'
        '<button type="button" class="danger" data-review-status="rejected">驳回</button>'
        '<span data-review-result></span></div>'
    )


def render_standard_answer(answer: dict[str, Any], cases: dict[str, dict[str, Any]]) -> str:
    """Render one cross-case answer while preserving evidence and review state."""
    title = str(answer.get("title") or "成熟答案").strip()
    status = str(answer.get("status") or "pending")
    status_text = "已确认" if status == "confirmed" else "待确认"
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
    review_html = (
        '<p class="standard-review"><strong>确认方式：</strong>核对答案和待补证据后，告诉系统“确认该成熟答案”或直接指出修改项；状态变更会追加修订记录。</p>'
        if status == "pending" else '<p class="standard-review confirmed"><strong>状态：</strong>该版本已由用户确认；后续修改仍须追加修订。</p>'
    )
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
        '<label>常见追问（JSON 数组）<textarea data-edit-followups rows="8">'
        f'{html.escape(json.dumps(followups, ensure_ascii=False, indent=2))}</textarea></label>'
        '<label>待补证据（每行一项）<textarea data-edit-limitations rows="4">'
        f'{html.escape(chr(10).join(limitations))}</textarea></label>'
        '<label>技术资料（JSON 数组）<textarea data-edit-sources rows="6">'
        f'{html.escape(json.dumps(answer.get("sources", []), ensure_ascii=False, indent=2))}</textarea></label>'
        '<label>回答依据（JSON 数组）<textarea data-edit-source-questions rows="6">'
        f'{html.escape(json.dumps(answer.get("source_questions", []), ensure_ascii=False, indent=2))}</textarea></label>'
        f'{review_buttons("standard_answer", answer_id, status)}</details>'
    )
    search = " ".join([
        title, basis, short_answer, standard_answer,
        " ".join(str(item.get("question") or "") + " " + str(item.get("answer") or "")
                 for item in followups if isinstance(item, dict)),
        " ".join(limitations),
    ]).lower()
    return (
        f'<article class="standard-answer" data-standard-answer data-search="{html.escape(search, quote=True)}">'
        '<div class="standard-heading">'
        f'<div><div class="standard-kicker">跨场成熟答案</div><h3>{html.escape(title)}</h3></div>'
        f'<span class="status {"strong" if status == "confirmed" else "unverified"}">{status_text}</span></div>'
        f'<p class="muted">{html.escape(basis)}</p>'
        '<div class="standard-versions">'
        f'<div><h4>30 秒版</h4><p>{html.escape(short_answer)}</p></div>'
        f'<div><h4>2 分钟版</h4><p>{html.escape(standard_answer)}</p></div></div>'
        + (f'<div class="standard-followups"><h4>常见追问</h4>{"".join(followup_html)}</div>' if followup_html else "")
        + limitation_html + review_html + evidence_html + sources_html + editor_html + '</article>'
    )


def render_question_clusters(root: Path, cases: list[dict[str, Any]]) -> tuple[str, str, int]:
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
    rendered = []
    for cluster in clusters:
        if not isinstance(cluster, dict) or cluster.get("status") == "rejected":
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
            links.append(
                f'<li><a href="#{html.escape(anchor, quote=True)}">{html.escape(question)}</a>'
                f'<span>{html.escape(str(case["company"]))}</span></li>'
            )
        note = str(cluster.get("note") or "").strip()
        note_html = f'<p class="muted">{html.escape(note)}</p>' if note else ""
        search = " ".join([label_text, note] + [question for _, _, question in refs]).lower()
        card_id = f'cluster-{cluster_id}'
        cards.append(
            f'<article class="cluster-card" id="{html.escape(card_id, quote=True)}" data-question-cluster '
            f'data-search="{html.escape(search, quote=True)}"><div class="cluster-heading">'
            f'<h3>{html.escape(label_text)}</h3><span class="status {"strong" if status == "confirmed" else "unverified"}">'
            f'{"已确认" if status == "confirmed" else "AI 归并 · 待确认"}</span></div>'
            f'<p class="cluster-frequency">覆盖 {case_count} 场 · {len(refs)} 道真实问题</p>{note_html}'
            f'<details><summary>查看归入本组的问题</summary><ul>{"".join(links)}</ul></details>'
            '<details class="answer-editor"><summary>修订或确认本组</summary>'
            '<label>分组名称<input data-edit-cluster-label type="text" value="'
            f'{html.escape(label_text, quote=True)}"></label>'
            f'<label>归并说明<textarea data-edit-cluster-note rows="3">{html.escape(note)}</textarea></label>'
            '<label>真实问题引用（JSON 数组）<textarea data-edit-cluster-questions rows="8">'
            f'{html.escape(json.dumps(cluster.get("questions", []), ensure_ascii=False, indent=2))}</textarea></label>'
            f'{review_buttons("question_cluster", cluster_id, status)}</details></article>'
        )
        bookmarks.append(
            f'<a class="bookmark" data-cluster-bookmark="{html.escape(card_id, quote=True)}" href="#{html.escape(card_id, quote=True)}">'
            f'<span>{html.escape(label_text)}</span><span>{case_count} 场</span></a>'
        )
    if not cards:
        return "", "", 0
    section = (
        '<section class="cluster-section" id="frequent-questions" data-cluster-section>'
        '<div class="topic-heading"><h2>高频重复问法</h2><span class="topic-frequency">按跨场语义归并排序</span></div>'
        '<p class="muted">这里统计语义相同或面试意图相近的真实问题，不再把整个主题都视为同一道题。待确认分组属于 AI 判断，可继续拆分或合并。</p>'
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


def question_card(case: dict[str, Any], entry: dict[str, Any], segments: dict[str, dict[str, Any]],
                  from_case: bool, practice_ref: dict[str, Any] | None = None,
                  related_sources: list[dict[str, str]] | None = None,
                  search_tags: str = "", fallback_standards: list[dict[str, Any]] | None = None) -> str:
    question = entry["question"]
    score = entry["score"]
    qid = str(question.get("question_id", ""))
    base = "" if from_case else f'interviews/{quote(str(case["name"]))}/{CASE_HTML}'
    card_id = f"practice-{qid}" if from_case else f'practice-{case["case_id"]}-{qid}'
    title = html.escape(str(question.get("question", "")))
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
    candidate_answer = " ".join(part for part in candidate_parts if part)
    candidate_answer = candidate_answer[:1200] + ("…" if len(candidate_answer) > 1200 else "")
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
    search_text = " ".join([
        str(case.get("company") or ""), str(case.get("role") or ""), str(case.get("round") or ""),
        category, search_tags, str(question.get("question") or ""), original, candidate_answer, notes,
    ]).lower()
    recommended = answer_view(practice_ref.get("reference_answer"), "查看推荐答案")
    if not recommended and fallback_standards:
        fallback_parts = []
        multiple = len(fallback_standards) > 1
        for fallback_standard in fallback_standards:
            versions = fallback_standard.get("versions", {}) if isinstance(fallback_standard.get("versions"), dict) else {}
            answer_title = str(fallback_standard.get("title") or "成熟答案")
            summary = f"查看：{answer_title}" if multiple else "查看跨场成熟答案"
            fallback_parts.append(
                f'<details class="reference-answer"><summary>{html.escape(summary)}</summary>'
                f'<p><strong>{html.escape(answer_title)}</strong> · '
                f'{"已确认" if fallback_standard.get("status") == "confirmed" else "待确认"}</p>'
                f'<h4>30 秒版</h4><p>{html.escape(str(versions.get("short_30s") or ""))}</p>'
                f'<h4>2 分钟版</h4><p>{html.escape(str(versions.get("standard_2m") or ""))}</p>'
                '<p class="muted">该答案由多场同类问题归并，不替代本题现场回答；使用前核对适用边界和待补证据。</p></details>'
            )
        recommended = (
            f'<div class="reference-answer-list"><strong>相关跨场成熟答案（{len(fallback_parts)} 份）</strong>'
            + "".join(fallback_parts) + "</div>"
            if multiple else fallback_parts[0]
        )
    if not recommended:
        recommended = '<p class="answer-missing"><strong>推荐答案：</strong>尚未整理或尚未完成来源核验。</p>'
    practice_widget = ""
    if not from_case:
        practice_widget = (
            f'<details class="practice-recorder" data-practice-widget data-case-id="{html.escape(str(case["case_id"]), quote=True)}" '
            f'data-question-id="{html.escape(qid, quote=True)}"><summary>脱稿练习并记录</summary>'
            '<div class="timer-line"><button type="button" data-timer-start>开始计时</button>'
            '<button type="button" data-timer-stop disabled>停止</button><strong data-timer-display>00:00</strong></div>'
            '<label>我的本次回答<textarea data-practice-answer rows="8" placeholder="先脱稿说完，再记录要点或粘贴转写。"></textarea></label>'
            '<label>自评<select data-practice-rating><option value="">请选择</option>'
            '<option value="1">1 · 无法回答</option><option value="2">2 · 缺口明显</option>'
            '<option value="3">3 · 基本完整</option><option value="4">4 · 稳定</option><option value="5">5 · 可应对追问</option>'
            '</select></label><label>备注<input data-practice-note type="text" placeholder="例如：缺少指标或失败边界"></label>'
            '<button type="button" data-practice-save>加入待导出操作</button><span data-practice-result></span></details>'
        )
    return (
        f'<article class="practice-card" id="{html.escape(card_id, quote=True)}" data-question-row '
        f'data-search="{html.escape(search_text, quote=True)}">'
        f'<div class="practice-meta">{html.escape(str(case["date"]))} · {html.escape(str(case["company"]))} · '
        f'{html.escape(str(case["round"]))} · {html.escape(category)} · {html.escape(score_text)}</div>'
        f'<h3>{title}</h3>'
        f'<p class="muted">{html.escape(qid)} · 问答树整理后的练习题；原话见下方片段。</p>'
        f'{parent_html}{original_html}{answer_html}'
        f'{notes_html}<p class="practice-links">提问：{segment_links(question_ids, base)} · 回答：{segment_links(answer_ids, base)}</p>'
        f'{sources_html}{point_html}{recorded_html}{extension_html}{recommended}{practice_widget}'
        '</article>'
    )


def render_case_practice(case: dict[str, Any]) -> str:
    items = practice_items(case["path"])
    if not items:
        return '<section id="practice"><h2>用本场问题练习</h2><p class="muted">暂无可用的问题树。</p></section>'
    segments = transcript_segments(case["path"])
    answers = case_answer_map(case["path"])
    sources = question_source_map(case["path"])
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
                             sources.get(qid, []))
    first = "".join(render_item(item) for item in items[:3])
    rest = "".join(render_item(item) for item in items[3:])
    more = f'<details><summary>查看本场其余 {len(items)-3} 道题</summary><div class="practice-grid">{rest}</div></details>' if rest else ""
    return f'''<section id="practice"><h2>用本场问题练习</h2>
<p class="muted">先显示本场技术与编码题中复盘评分较低的题目；评分是既有 AI 复盘判断，只用于定位练习，不跨岗位比较。题目可能经过归纳，请以逐字稿为准。</p>
<div class="practice-grid">{first}</div>{more}</section>'''


def render_history_practice(root: Path, cases: list[dict[str, Any]]) -> str:
    case_by_id = {str(case["case_id"]): case for case in cases}
    focus = read_json(root / "profile" / "practice-focus.json", {})
    if not isinstance(focus, dict):
        focus = {}
    groups = []
    seen_refs: set[tuple[str, str]] = set()
    if focus.get("schema_version") == 1 and isinstance(focus.get("groups"), list):
        for group in focus.get("groups", []):
            if not isinstance(group, dict) or not isinstance(group.get("questions"), list):
                continue
            cards = []
            for ref in group["questions"]:
                if not isinstance(ref, dict):
                    continue
                key = (str(ref.get("case_id", "")), str(ref.get("question_id", "")))
                if key in seen_refs:
                    continue
                case = case_by_id.get(key[0])
                if not case:
                    continue
                entry = next((item for item in practice_items(case["path"])
                              if item["question"].get("question_id") == key[1]), None)
                if entry:
                    seen_refs.add(key)
                    answer_ref = case_answer_map(case["path"]).get(key[1], {})
                    cards.append(question_card(case, entry, transcript_segments(case["path"]), False,
                                               {**answer_ref, **ref},
                                               question_source_map(case["path"]).get(key[1], [])))
            if cards:
                title = html.escape(str(group.get("title") or "专题练习"))
                groups.append(f'<div class="practice-group"><h3>{title}</h3><div class="practice-grid">{"".join(cards)}</div></div>')
    if not groups:
        cards = []
        for case in cases:
            items = practice_items(case["path"])
            if items:
                qid = str(items[0]["question"].get("question_id", ""))
                cards.append(question_card(case, items[0], transcript_segments(case["path"]), False,
                                           case_answer_map(case["path"]).get(qid),
                                           question_source_map(case["path"]).get(qid, [])))
        if cards:
            groups.append(f'<div class="practice-grid">{"".join(cards)}</div>')
    if not groups:
        return ""
    explanation = ("按已保存的跨场待办手选题目；点击题目进入单场全部问题。" if focus.get("groups")
                   else "每场选一道本场复盘评分较低的技术或编码题；点击题目进入单场全部问题。")
    return f'''<section id="practice"><h2>先用面试问题练习</h2>
<p class="muted">{explanation}题目来自问答树整理，原话与回答可点逐字稿核对；本场评分不用于跨岗位排名。</p>
{"".join(groups)}</section>'''


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
        search += " " + action.lower()
        row_class = " current" if item["case_id"] == current else ""
        cards.append(
            f'<article class="history-card{row_class}" data-history-row data-search="{html.escape(search, quote=True)}">'
            f'<div class="history-meta">{html.escape(str(item["date"]))} · {html.escape(str(item["round"]))} · '
            f'结果：{html.escape(str(item["result"]))}</div>'
            f'<h3>{link}</h3><p><strong>下一步：</strong>{inline_markdown(action)}</p>'
            f'<a class="history-open" href="{href}">打开本场复盘 →</a></article>'
        )
    return "".join(cards)


STYLE = """
:root{--ink:#172033;--muted:#64748b;--paper:#f5f7fb;--card:#fff;--line:#dfe5ef;--blue:#2557d6;--cyan:#0e7490;--green:#087f5b;--amber:#b45309;--red:#c2413a}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.7 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}a{color:var(--blue);text-decoration:none}a:hover{text-decoration:underline}main{width:min(1180px,calc(100% - 28px));margin:auto;padding:24px 0 64px}.topnav{display:flex;gap:16px;align-items:center;margin:0 0 14px;font-size:13px}.hero{padding:30px;border-radius:22px;background:linear-gradient(135deg,#172554,#1d4ed8 58%,#0e7490);color:#fff;box-shadow:0 18px 42px #1e3a8a29}.hero h1{font-size:clamp(27px,5vw,44px);line-height:1.15;margin:6px 0}.eyebrow{text-transform:uppercase;letter-spacing:.12em;font-size:12px;opacity:.76}.meta{display:flex;flex-wrap:wrap;gap:8px;margin-top:17px}.badge{border:1px solid #ffffff42;background:#ffffff17;border-radius:999px;padding:4px 10px;font-size:13px}.notice{margin-top:16px;padding:13px 15px;border-left:4px solid var(--amber);background:#fff8e8;color:#70420a;border-radius:8px}section{margin-top:28px}.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:17px;box-shadow:0 3px 12px #0f172a08}.grid{display:grid;gap:14px}.scores{grid-template-columns:repeat(4,minmax(0,1fr))}.label{color:var(--muted);font-size:12px}.metric{font-size:30px;font-weight:760}.metric span{font-size:13px;color:var(--muted)}.track{height:8px;background:#e6ebf3;border-radius:99px;overflow:hidden}.track i{display:block;height:100%;background:linear-gradient(90deg,var(--cyan),var(--blue))}h2{font-size:22px;margin:0 0 12px}h3{font-size:18px;margin:22px 0 8px}h4{font-size:16px;margin:18px 0 7px}blockquote{margin:12px 0;padding:10px 14px;border-left:4px solid #93c5fd;background:#eff6ff;color:#1e3a8a;border-radius:7px}code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;background:#eef2f7;border-radius:5px;padding:1px 5px}.table-wrap{overflow:auto;border:1px solid var(--line);border-radius:12px;background:#fff}table{width:100%;border-collapse:collapse;min-width:760px}th,td{text-align:left;vertical-align:top;padding:11px;border-bottom:1px solid var(--line)}th{background:#eef2f7;color:#334155;font-size:13px}.status,.tag{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px;background:#e8eefc;color:#1d4ed8}.status.strong,.status.correct{background:#d1fae5;color:#047857}.status.medium,.status.partial{background:#e0f2fe;color:#0369a1}.status.weak,.status.incorrect{background:#ffedd5;color:#c2410c}.status.unverified{background:#e2e8f0;color:#475569}details{background:#fff;border:1px solid var(--line);border-radius:10px;padding:0 14px;margin:9px 0}summary{cursor:pointer;padding:12px 0;font-weight:650}.segment{padding:11px 0;border-bottom:1px solid var(--line);scroll-margin-top:10px}.segment p{margin:5px 0}.review{background:#fff;border:1px solid var(--line);border-radius:14px;padding:20px}.review h2:first-child{margin-top:0}.search{width:100%;max-width:520px;padding:11px 13px;border:1px solid #cbd5e1;border-radius:10px;margin:0 0 12px;font:inherit}.current{background:#eff6ff}.muted{color:var(--muted)}.foot{margin-top:24px;color:var(--muted);font-size:13px}.empty{display:none;padding:12px;color:var(--muted)}
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
.search-line .search{margin:0}.search-line span{color:var(--muted);font-size:13px}
.history-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:13px}
.history-card{background:#fff;border:1px solid var(--line);border-radius:14px;padding:17px;min-width:0}
.history-card h3{margin:7px 0 8px;font-size:17px;line-height:1.4}
.history-card p{margin:0 0 8px;font-size:14px;line-height:1.55}
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
.review-center,.learning-dashboard{background:#fff;border:1px solid var(--line);border-radius:16px;padding:22px 28px;margin:0 0 22px}.review-summary{display:flex;flex-wrap:wrap;gap:8px;margin:14px 0}.review-summary span{background:#eef2f7;border-radius:999px;padding:5px 10px;font-size:13px}.review-toolbar,.review-actions,.timer-line{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.review-toolbar{padding:12px;background:#f7f9fc;border-radius:10px}.review-toolbar button,.review-actions button,.practice-recorder button{border:0;border-radius:8px;padding:8px 11px;background:var(--blue);color:#fff;font:inherit;font-size:13px;font-weight:700;cursor:pointer}.review-toolbar .secondary,.review-actions button[data-review-status="pending"]{background:#64748b}.review-actions .danger{background:var(--red)}.review-actions span,.review-toolbar span,.practice-recorder span{font-size:12px;color:var(--muted)}.review-list{display:grid;gap:8px;padding-bottom:12px}.review-row{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:14px;padding:11px;border-bottom:1px solid var(--line)}.review-row p{margin:3px 0}.review-row small{color:var(--muted)}
.external-note{font-size:.78em;color:var(--muted);white-space:nowrap}
.answer-editor label,.practice-recorder label{display:grid;gap:5px;margin:10px 0;font-size:13px;font-weight:700}.answer-editor textarea,.answer-editor input,.practice-recorder textarea,.practice-recorder input,.practice-recorder select{width:100%;border:1px solid #cbd5e1;border-radius:8px;padding:9px;font:inherit;background:#fff}.practice-recorder{background:#f8fbff}.timer-line strong{font-variant-numeric:tabular-nums;font-size:18px}.dashboard-grid{display:grid;grid-template-columns:minmax(0,.9fr) minmax(0,1.1fr);gap:18px}.review-queue{padding-left:1.3rem}.review-queue li+li{margin-top:9px}.review-queue span{display:block;color:var(--muted);font-size:12px}.learning-dashboard table{min-width:520px}
@media(max-width:760px){main{width:min(100% - 18px,1180px);padding-top:10px}.hero{padding:22px;border-radius:16px}.scores{grid-template-columns:repeat(2,minmax(0,1fr))}.review{padding:15px}}
@media(max-width:760px){.focus-grid,.history-grid,.action-grid,.bank-layout,.standard-versions,.cluster-grid,.dashboard-grid{grid-template-columns:1fr}.review-row{grid-template-columns:1fr}.topnav,.bank-sidebar{position:static}.bank-sidebar{max-height:none}.focus-main{padding:17px}#practice,.topic-group,.cluster-section,.review-center,.learning-dashboard{padding:18px}.topic-heading{display:block}.topic-frequency{white-space:normal}}
@media print{body{background:#fff}.topnav,.search,script{display:none!important}main{width:100%;padding:0}.hero{box-shadow:none;border-radius:0}section{break-inside:avoid}}
"""


SCRIPT = """
(() => {
  const openTarget = (hash) => {
    if (!hash || hash.length < 2) return;
    let target;
    try { target = document.getElementById(decodeURIComponent(hash.slice(1))); }
    catch (_) { return; }
    if (!target) return;
    const section = target.closest('section');
    if (section) section.querySelectorAll('details').forEach(item => { item.open = true; });
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
    };
    input.addEventListener('input', apply);
    apply();
  };
  bindSearch('[data-history-search]', '[data-history-row]', '[data-history-empty]', '[data-history-count]', '场');
  const questionInput = document.querySelector('[data-question-search]');
  if (questionInput) {
    const rows = [...document.querySelectorAll('[data-question-row]')];
    const standards = [...document.querySelectorAll('[data-standard-answer]')];
    const clusters = [...document.querySelectorAll('[data-question-cluster]')];
    const clusterSection = document.querySelector('[data-cluster-section]');
    const groups = [...document.querySelectorAll('[data-topic-group]')];
    const bookmarks = [...document.querySelectorAll('[data-bookmark]')];
    const clusterBookmarks = [...document.querySelectorAll('[data-cluster-bookmark]')];
    const empty = document.querySelector('[data-question-empty]');
    const count = document.querySelector('[data-question-count]');
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
    };
    questionInput.addEventListener('input', applyQuestions);
    applyQuestions();
  }
  const archiveId = document.body.dataset.storeId || 'unknown';
  const actionKey = `interview-system-review-actions-v2:${archiveId}`;
  const persistenceKey = `interview-system-review-persistence-v1:${archiveId}`;
  const legacyActionKey = 'interview-system-review-actions-v1';
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
      node.textContent = `待导出 ${queuedActions.length} 项`;
    });
  };
  document.querySelectorAll('[data-review-status]').forEach(button => {
    button.addEventListener('click', () => {
      const item = button.closest('[data-review-item]');
      if (!item) return;
      const result = item.querySelector('[data-review-result]');
      const editor = item.closest('.answer-editor');
      let patch = null;
      try {
        if (editor && item.dataset.reviewKind === 'standard_answer') {
          patch = {
            title: editor.querySelector('[data-edit-title]').value,
            basis: editor.querySelector('[data-edit-basis]').value,
            short_30s: editor.querySelector('[data-edit-short]').value,
            standard_2m: editor.querySelector('[data-edit-standard]').value,
            followups: JSON.parse(editor.querySelector('[data-edit-followups]').value),
            limitations: editor.querySelector('[data-edit-limitations]').value.split('\n').map(value => value.trim()).filter(Boolean),
            sources: JSON.parse(editor.querySelector('[data-edit-sources]').value),
            source_questions: JSON.parse(editor.querySelector('[data-edit-source-questions]').value),
          };
        } else if (editor && item.dataset.reviewKind === 'question_cluster') {
          patch = {
            label: editor.querySelector('[data-edit-cluster-label]').value,
            note: editor.querySelector('[data-edit-cluster-note]').value,
            questions: JSON.parse(editor.querySelector('[data-edit-cluster-questions]').value),
          };
        }
      } catch (error) {
        if (result) result.textContent = `JSON 格式错误：${error.message}`;
        return;
      }
      const reason = window.prompt('请填写确认、驳回或修订原因（会写入修订记录）：', '人工核对');
      if (!reason || !reason.trim()) return;
      const action = {
        kind: item.dataset.reviewKind,
        id: item.dataset.reviewId,
        expected_status: item.dataset.reviewCurrent,
        status: button.dataset.reviewStatus,
        reason: reason.trim(),
      };
      if (patch) action.patch = patch;
      saveQueue(action);
      if (result) result.textContent = '已加入本机队列，尚未写入档案';
    });
  });
  document.querySelectorAll('[data-practice-widget]').forEach(widget => {
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
      if (startedAt) return;
      startedAt = Date.now(); start.disabled = true; stop.disabled = false;
      timer = window.setInterval(paint, 250); paint();
    });
    stop.addEventListener('click', () => {
      if (!startedAt) return;
      elapsed += Math.max(1, Math.floor((Date.now() - startedAt) / 1000));
      startedAt = 0; window.clearInterval(timer); timer = null;
      start.disabled = false; stop.disabled = true; paint();
    });
    widget.querySelector('[data-practice-save]').addEventListener('click', () => {
      if (startedAt) stop.click();
      const answer = widget.querySelector('[data-practice-answer]').value.trim();
      const rating = Number(widget.querySelector('[data-practice-rating]').value);
      const duration = Number(widget.dataset.duration || 0);
      const result = widget.querySelector('[data-practice-result]');
      if (!answer || !rating || duration < 1) {
        result.textContent = '请先计时、填写回答并选择自评'; return;
      }
      saveQueue({kind: 'practice_attempt', case_id: widget.dataset.caseId,
        question_id: widget.dataset.questionId, answer, duration_seconds: duration,
        self_rating: rating, note: widget.querySelector('[data-practice-note]').value.trim()});
      result.textContent = '已加入本机队列，导出后再写入档案';
    });
  });
  const exportButton = document.querySelector('[data-export-actions]');
  if (exportButton) exportButton.addEventListener('click', () => {
    const message = document.querySelector('[data-action-message]');
    if (!queuedActions.length) { if (message) message.textContent = '当前没有待导出操作'; return; }
    if (!window.confirm('导出 JSON 可能包含个人练习回答和修订内容。请将其保存在受控本地位置。继续导出？')) return;
    const payload = {schema_version: 1, generated_at: new Date().toISOString(), actions: queuedActions};
    const blob = new Blob([JSON.stringify(payload, null, 2) + '\n'], {type: 'application/json'});
    const link = document.createElement('a'); link.href = URL.createObjectURL(blob);
    link.download = `interview-review-actions-${new Date().toISOString().slice(0,10)}.json`;
    link.click(); window.setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    if (message) message.textContent = '已下载；交给 Skill 应用并重建页面';
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
        ? '已选择本机持久保存，队列最长保留 30 天'
        : '已改为仅当前浏览器会话保存';
    });
  }
  updateQueue();
})();
"""


def render_history_section(cases: list[dict[str, Any]], current: str | None, from_case: bool) -> str:
    rows = history_rows(cases, current, from_case)
    return f"""
<section id="history">
  <h2>{"其他场次" if from_case else "逐场查看"}</h2>
  <p class="muted">每场先显示一条已保存的下一步；打开后可查看 JD、技术核验和逐字稿。</p>
  <label for="history-search">搜索公司、岗位、技术或待办</label>
  <div class="search-line"><input id="history-search" class="search" data-history-search type="search" placeholder="例如 MySQL、示例项目、管控平台"><span data-history-count></span></div>
  <div class="history-grid">{rows}</div>
  <div class="empty" data-history-empty>没有匹配的历史面试。</div>
</section>"""


def render_review_center(root: Path) -> str:
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
    pending_events.sort(key=lambda item: (str(item.get("type") or ""), str(item.get("key") or "")))
    event_rows = []
    for item in pending_events:
        event_id = str(item.get("event_id") or "")
        event_rows.append(
            '<article class="review-row"><div><strong>'
            f'{html.escape(str(item.get("key") or event_id))}</strong>'
            f'<p>{html.escape(str(item.get("reason") or "无说明"))}</p>'
            f'<small>{html.escape(str(item.get("type") or "未分类"))} · {html.escape(str(item.get("case_id") or ""))}</small></div>'
            f'{review_buttons("profile_event", event_id, "pending")}</article>'
        )
    total = len(pending_answers) + len(pending_clusters) + len(pending_events)
    return (
        '<section id="review-center" class="review-center"><div class="topic-heading"><h2>事实与答案确认中心</h2>'
        f'<span class="topic-frequency">共 {total} 项待确认</span></div>'
        '<p class="muted">页面不会直接改写档案。确认、驳回或修订会先进入本机操作队列；导出 JSON 后由 Skill 校验旧状态并追加修订记录。</p>'
        '<div class="review-summary">'
        f'<span>成熟答案 {len(pending_answers)}</span><span>高频问法 {len(pending_clusters)}</span><span>长期档案 {len(pending_events)}</span>'
        '<span data-action-count>待导出 0 项</span></div>'
        '<div class="review-toolbar"><button type="button" data-export-actions>导出操作 JSON</button>'
        '<button type="button" class="secondary" data-clear-actions>清空本机队列</button>'
        '<label><input type="checkbox" data-persist-actions> 在本机持久保存 30 天</label>'
        '<span data-action-message></span></div>'
        + (f'<details><summary>处理 {len(pending_events)} 条长期档案事实</summary><div class="review-list">{"".join(event_rows)}</div></details>' if event_rows else '<p>长期档案暂无待确认项。</p>')
        + '<p class="muted">成熟答案和高频问法可在各自卡片中直接修订或确认。</p></section>'
    )


def render_learning_dashboard(root: Path) -> str:
    plan = read_json(root / "profile" / "review-plan.json", {})
    if not isinstance(plan, dict) or plan.get("schema_version") != 1:
        return '<section id="learning-dashboard"><h2>练习计划与趋势</h2><p class="muted">尚未生成练习计划；运行复习计划构建器后显示。</p></section>'
    queue_rows = []
    for item in plan.get("queue", [])[:10]:
        if not isinstance(item, dict):
            continue
        anchor = f'practice-{item.get("case_id", "")}-{item.get("question_id", "")}'
        queue_rows.append(
            f'<li><a href="#{html.escape(anchor, quote=True)}">{html.escape(str(item.get("question") or ""))}</a>'
            f'<span>{html.escape(str(item.get("practice_status") or ""))} · '
            f'{html.escape("、".join(str(value) for value in item.get("priority_reasons", [])))}</span></li>'
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
        '<div class="dashboard-grid"><div><h3>下一轮优先练习</h3><ol class="review-queue">'
        + "".join(queue_rows) + '</ol></div><div><h3>分主题表现快照</h3>'
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
            badge(f'结果：{case["result"]}'),
            badge(f'说话人：{label("speaker_status", case["speaker_status"])}'),
            badge(f'分析：{label("analysis_status", case["analysis_status"])}'),
        ]
    )
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
{SECURITY_META}<title>{html.escape(case['company'])} · {html.escape(case['role'])} · 面试复盘</title><style>{STYLE}</style></head>
<body data-interview-case="{html.escape(case['case_id'], quote=True)}" data-store-id="{archive_id(root)}"><main>
<nav class="topnav" aria-label="本页导航"><a href="../../{HISTORY_HTML}">历史总览</a><a href="../../{QUESTION_BANK_HTML}">面试题库</a><a href="#focus">下一步</a><a href="#practice">本场练习题</a><a href="#review">完整复盘</a><a href="#jd">岗位 JD</a><a href="#claims">技术核验</a><a href="#evidence">逐字稿</a></nav>
<header class="hero"><div class="eyebrow">Interview System · Offline Review</div>
<h1>{html.escape(case['company'])}<br>{html.escape(case['role'])}</h1>
<p>{html.escape(case['datetime'])}</p><div class="meta">{meta}</div></header>
{privacy_notice()}
{warning_block}
{review_focus(review)}
{render_case_practice(case)}
<section><h2>四维表现</h2><div class="grid scores">{score_cards(scores.get('overall', {}))}</div></section>
<section id="review"><h2>完整复盘</h2><details><summary>展开完整复盘与原始建议</summary><div class="review">{markdown_to_html(review)}</div></details></section>
<section id="jd"><h2>岗位 JD 原文</h2><details><summary>展开本场保存的 JD 原文</summary>{jd_source(case_path, manifest)}</details></section>
<section id="jd-matrix"><h2>JD 能力矩阵</h2><details><summary>展开逐项岗位对照</summary>{jd_table(matrix)}</details></section>
<section id="claims"><h2>技术核验</h2><details><summary>展开逐条技术结论与来源</summary>{claims_table(claims)}</details></section>
{render_history_section(cases, case['case_id'], True)}
<section id="evidence"><h2>逐字稿证据</h2><details><summary>展开全部片段</summary>{transcript_view(case_path)}</details></section>
<p class="foot">本页由本地结构化产物生成。HTML 是展示视图，不替代原始逐字稿、JSON、JD、简历或技术来源。</p>
</main><script>{SCRIPT}</script></body></html>"""


def render_history_index(root: Path, cases: list[dict[str, Any]]) -> str:
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
{SECURITY_META}<title>面试历史总览</title><style>{STYLE}</style></head><body data-interview-history data-store-id="{archive_id(root)}"><main>
<header class="hero"><div class="eyebrow">Interview System · Local Archive</div><h1>面试历史总览</h1>
<p>先看待办，再按场次查证据。共 {len(cases)} 场；只展示本地档案。</p>
<p><a class="primary-link" href="{QUESTION_BANK_HTML}">打开完整面试题库 →</a></p></header>
{privacy_notice()}
{render_plan_summary(root)}
{render_history_practice(root, cases)}
{cross_case_report_link(root)}
{render_history_section(cases, None, False)}
<p class="foot">结果仅用于历史记录，不用于生成通过率或推断单一题目的因果。</p>
</main><script>{SCRIPT}</script></body></html>"""


def render_question_bank(root: Path, cases: list[dict[str, Any]]) -> str:
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
    standard_answer_count = sum(len(values) for values in standard_answers.values())
    case_by_id = {str(case["case_id"]): case for case in cases}
    cluster_section, cluster_bookmarks, cluster_count = render_question_clusters(root, cases)
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
            ))
        sections.append(
            f'<section class="topic-group" id="topic-{topic}" data-topic-group>'
            f'<div class="topic-heading"><h2>{html.escape(label_text)}</h2>'
            f'<span class="topic-frequency">覆盖 {case_count} / {len(cases)} 场 · 共 {question_count} 题</span></div>'
            f'{"".join(render_standard_answer(answer, case_by_id) for answer in standard_answers.get(topic, []))}'
            f'<div class="practice-grid">{"".join(cards)}</div></section>'
        )
    total_questions = sum(len(values) for values in grouped.values())
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
{SECURITY_META}<title>面试题库</title><style>{STYLE}</style></head><body data-interview-question-bank data-store-id="{archive_id(root)}"><main>
<nav class="topnav" aria-label="本页导航"><a href="{HISTORY_HTML}">历史总览</a><a href="#review-center">确认中心</a><a href="#learning-dashboard">练习计划</a><a href="#question-bank">全部题目</a></nav>
<header class="hero"><div class="eyebrow">Interview System · Question Bank</div><h1>面试题库</h1>
<p>共 {total_questions} 道真实面试题，来自 {len(cases)} 场；{cluster_count} 组跨场高频问法，{standard_answer_count} 份跨场成熟答案，{answer_count} 道已有推荐答案，{source_count} 道绑定了技术核验资料。</p></header>
{privacy_notice()}
<div id="question-bank" class="bank-layout">
<aside class="bank-sidebar"><h2>搜索与书签</h2>
<div class="search-line"><label for="question-search">搜索全部题目</label><input id="question-search" class="search" data-question-search type="search" placeholder="问题、公司、岗位、回答或诊断"><span data-question-count></span></div>
<div class="practice-guide"><strong>练习方法</strong><br>先脱稿回答，再对照现场回答与复盘诊断；代码题再运行边界测试。此说明只展示一次。</div>
{f'<h3 class="bookmark-title">高频问法</h3><nav class="bookmark-list" aria-label="跨场高频重复问法">{cluster_bookmarks}</nav>' if cluster_bookmarks else ''}
<h3 class="bookmark-title">主题</h3><nav class="bookmark-list" aria-label="按主题覆盖频率排序的书签">{"".join(bookmarks)}</nav></aside>
<div class="bank-content">{render_review_center(root)}{render_learning_dashboard(root)}{cluster_section}<h2>全部面试题（按主题频率排序）</h2><p class="muted">高频问法按跨场语义归并统计；主题仍按覆盖的面试场次数排序，题数用于次级排序。主题归类基于可解释的关键词规则，不代表语义完全相同。每题保留原始场次证据。</p>
{"".join(sections)}<div class="empty" data-question-empty>没有匹配的面试题。</div></div></div>
<p class="foot">题面来自问答树整理；原始问答以各场逐字稿证据为准。推荐答案是学习材料，不代表当场回答。</p>
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
    errors.extend(smoke_check(history, ["data-interview-history", 'id="history"']))
    index_path = root / HISTORY_HTML
    question_bank = render_question_bank(root, cases)
    errors.extend(smoke_check(question_bank, ["data-interview-question-bank", 'id="question-bank"', "我的现场回答", "推荐答案"]))
    question_bank_path = root / QUESTION_BANK_HTML
    if not errors:
        write_text(index_path, history)
        write_text(question_bank_path, question_bank)

    status = "ok" if not errors else "error"
    print(
        json.dumps(
            {
                "status": status,
                "rendered": rendered,
                "history_index": str(index_path),
                "question_bank": str(question_bank_path),
                "history_cases": len(cases),
                "errors": errors,
            },
            ensure_ascii=False,
        )
    )
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
