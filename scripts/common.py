from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree


SCHEMA_VERSION = 1
VERSIONED_JSON_NAMES = frozenset({
    "config.json",
    "manifest.json",
    "transcript.raw.json",
    "transcript.revised.json",
    "speaker-map.json",
    "qa-tree.json",
    "claims.json",
    "scores.json",
    "profile-patch.json",
    "jd-matrix.json",
    "state.json",
    "session.json",
    "progress.json",
    "next-interview-prep.json",
    "standard-answers.json",
    "question-clusters.json",
    "question-cluster-candidates.json",
    "standard-answer-audit.json",
    "review-plan.json",
    "reference-health.json",
})
KNOWN_CANDIDATE = {"候选人", "面试者", "应聘者", "candidate", "我"}
KNOWN_INTERVIEWER = {"面试官", "hr", "interviewer", "招聘方", "技术面试官"}
TIMECODE_RE = re.compile(r"^\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?$")
COLON_LINE_RE = re.compile(r"^([^:：]{1,30})\s*[:：]\s*(.+)$")
HEADING_RE = re.compile(
    r"^###\s+(s-\d{4})\s+\|\s+([^|]+)\s+\|\s+([^|]+)\s+\|\s+([^|]+)\s+\|\s+([^|]+)\s*$"
)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


class SchemaCompatibilityError(ValueError):
    """A stored JSON schema is not supported by this Skill version."""


def schema_compatibility_issue(path: Path, value: Any) -> str | None:
    versioned = path.name in VERSIONED_JSON_NAMES
    if not isinstance(value, dict):
        return "顶层必须是对象" if versioned else None
    if not versioned and "schema_version" not in value:
        return None
    version = value.get("schema_version")
    if type(version) is not int:
        return "缺少有效的整数 schema_version；不可猜测旧格式"
    if version != SCHEMA_VERSION:
        direction = "较新" if version > SCHEMA_VERSION else "较旧"
        return f"schema_version={version} {direction}，当前仅支持 {SCHEMA_VERSION}；请使用匹配版本或明确的迁移脚本"
    return None


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    issue = schema_compatibility_issue(path, value)
    if issue:
        raise SchemaCompatibilityError(f"{path.name}：{issue}")
    return value


def write_json(path: Path, value: Any) -> None:
    issue = schema_compatibility_issue(path, value)
    if issue:
        raise SchemaCompatibilityError(f"{path.name}：{issue}")
    if path.exists():
        read_json(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temp_name = handle.name
    os.chmod(temp_name, 0o600)
    os.replace(temp_name, path)


def write_private_text(path: Path, value: str) -> None:
    """原子写入私有文本，并确保新旧目标最终均为 0600。"""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        handle.write(value)
        temp_name = handle.name
    os.chmod(temp_name, 0o600)
    os.replace(temp_name, path)


def append_private_text(path: Path, value: str) -> None:
    """追加私有日志；不读取或重写已有内容，并将文件权限收紧为 0600。"""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        data = value.encode("utf-8")
        while data:
            written = os.write(descriptor, data)
            data = data[written:]
    finally:
        os.close(descriptor)


def open_private_text(path: Path):
    """以 0600 打开私有文本输出，供子进程日志等流式写入场景使用。"""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(descriptor, 0o600)
    return os.fdopen(descriptor, "w", encoding="utf-8")


def copy_private_file(source: Path, destination: Path) -> None:
    """复制私有资料但不继承源文件可能过宽的权限。"""
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    destination.chmod(0o600)


def safe_component(value: str | None, fallback: str) -> str:
    text = (value or "").strip() or fallback
    text = re.sub(r"[\\/:*?\"<>|\s]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-.")
    return text[:60] or fallback


def _read_docx(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        xml = archive.read("word/document.xml")
    root = ElementTree.fromstring(xml)
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs: list[str] = []
    for paragraph in root.iter(f"{namespace}p"):
        parts: list[str] = []
        for node in paragraph.iter():
            if node.tag == f"{namespace}t" and node.text:
                parts.append(node.text)
            elif node.tag == f"{namespace}tab":
                parts.append("\t")
        text = "".join(parts).strip()
        if text:
            paragraphs.append(text)
    return "\n".join(paragraphs)


def _read_pdf(path: Path) -> str:
    from pdf_ocr import extract_pdf

    text, _ = extract_pdf(path)
    return text


def read_supported_text(path: Path, *, allow_pdf: bool) -> str:
    suffix = path.suffix.lower()
    if suffix in {".txt", ".md", ".markdown"}:
        return path.read_text(encoding="utf-8-sig")
    if suffix == ".docx":
        return _read_docx(path)
    if suffix == ".pdf" and allow_pdf:
        return _read_pdf(path)
    allowed = "TXT、Markdown、DOCX" + ("、PDF" if allow_pdf else "")
    raise RuntimeError(f"不支持 {suffix or '无扩展名'}；允许：{allowed}")


def parse_timecode(value: str) -> float | None:
    value = value.strip()
    if not TIMECODE_RE.fullmatch(value):
        return None
    parts = [float(item) for item in value.split(":")]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return None


def format_timecode(seconds: float | None) -> str:
    if seconds is None:
        return "--"
    total = int(seconds)
    hours, remain = divmod(total, 3600)
    minutes, secs = divmod(remain, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def infer_role(label: str) -> tuple[str, float]:
    normalized = label.strip().lower()
    if normalized in KNOWN_CANDIDATE or any(
        token in normalized for token in ("候选", "应聘", "面试者")
    ):
        return "candidate", 1.0
    if normalized in KNOWN_INTERVIEWER or any(
        token in normalized for token in ("面试官", "招聘", "hr")
    ):
        return "interviewer", 1.0
    return "unknown", 0.0


def _extract_brackets(line: str) -> tuple[list[str], str]:
    tokens: list[str] = []
    rest = line.strip()
    while rest.startswith("[") and "]" in rest:
        end = rest.find("]")
        tokens.append(rest[1:end].strip())
        rest = rest[end + 1 :].strip()
    return tokens, rest


def parse_transcript(text: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    lines = [line.strip() for line in text.replace("\r\n", "\n").split("\n")]
    lines = [line for line in lines if line]
    colon_counts: dict[str, int] = {}
    for line in lines:
        tokens, rest = _extract_brackets(line)
        if any(parse_timecode(token) is None for token in tokens):
            continue
        match = COLON_LINE_RE.match(rest)
        if match:
            label = match.group(1).strip()
            colon_counts[label] = colon_counts.get(label, 0) + 1

    label_to_id: dict[str, str] = {}
    speakers: list[dict[str, Any]] = []
    segments: list[dict[str, Any]] = []

    def speaker_id(label: str) -> str:
        if label not in label_to_id:
            identifier = f"spk-{len(label_to_id) + 1:02d}"
            role, confidence = infer_role(label)
            label_to_id[label] = identifier
            speakers.append(
                {
                    "speaker_id": identifier,
                    "label": label,
                    "proposed_role": role,
                    "confidence": confidence,
                    "confirmed_role": role if confidence == 1.0 else None,
                }
            )
        return label_to_id[label]

    for index, line in enumerate(lines, start=1):
        tokens, rest = _extract_brackets(line)
        timestamp: float | None = None
        bracket_label: str | None = None
        for token in tokens:
            parsed = parse_timecode(token)
            if parsed is not None:
                timestamp = parsed
            elif token:
                bracket_label = token

        label = bracket_label
        content = rest
        match = COLON_LINE_RE.match(rest)
        if label is None and match:
            tentative = match.group(1).strip()
            known_role, _ = infer_role(tentative)
            if colon_counts.get(tentative, 0) >= 2 or known_role != "unknown":
                label = tentative
                content = match.group(2).strip()
        elif label is not None and match and match.group(1).strip() == label:
            content = match.group(2).strip()

        if label:
            identifier = speaker_id(label)
            speaker = next(item for item in speakers if item["speaker_id"] == identifier)
            role = speaker["confirmed_role"] or speaker["proposed_role"]
            role_confidence = speaker["confidence"]
        else:
            identifier = "unknown"
            role = "unknown"
            role_confidence = 0.0

        segments.append(
            {
                "segment_id": f"s-{index:04d}",
                "start": timestamp,
                "end": None,
                "speaker_id": identifier,
                "speaker_label": label,
                "role": role,
                "role_confidence": role_confidence,
                "text_raw": content,
                "text_corrected": content,
                "corrections": [],
            }
        )

    if any(segment["speaker_id"] == "unknown" for segment in segments):
        speakers.append(
            {
                "speaker_id": "unknown",
                "label": "未标注",
                "proposed_role": "unknown",
                "confidence": 0.0,
                "confirmed_role": None,
            }
        )
    return segments, speakers


def render_transcript(case_id: str, segments: list[dict[str, Any]]) -> str:
    parts = [
        f"# 面试逐字稿 · {case_id}",
        "",
        "> 可编辑每个分段标题下的正文；不要修改分段 ID。原始内容保存在 transcript.raw.json。",
        "",
    ]
    for segment in segments:
        parts.extend(
            [
                "### {id} | {time} | {speaker} | {label} | {role}".format(
                    id=segment["segment_id"],
                    time=format_timecode(segment.get("start")),
                    speaker=segment.get("speaker_id") or "unknown",
                    label=segment.get("speaker_label") or "未标注",
                    role=segment.get("role") or "unknown",
                ),
                "",
                segment.get("text_corrected") or segment.get("text_raw") or "",
                "",
            ]
        )
    return "\n".join(parts).rstrip() + "\n"


def parse_edited_transcript(markdown: str) -> dict[str, str]:
    edits: dict[str, str] = {}
    current: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        if current is None:
            return
        body = "\n".join(buffer).strip()
        edits[current] = body

    for line in markdown.splitlines():
        match = HEADING_RE.match(line)
        if match:
            flush()
            current = match.group(1)
            buffer = []
        elif current is not None:
            buffer.append(line)
    flush()
    return edits
