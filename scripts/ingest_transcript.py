from __future__ import annotations

import argparse
import json
import os
from datetime import date
from pathlib import Path
from typing import Any

from check_data_compatibility import require_store_compatible
from common import (
    SCHEMA_VERSION,
    copy_private_file,
    now_iso,
    parse_transcript,
    read_json,
    read_supported_text,
    render_transcript,
    safe_component,
    sha256_text,
    write_json,
    write_private_text,
)
from labels_zh import round_label


def find_duplicate(root: Path, transcript_hash: str) -> Path | None:
    interviews = root / "interviews"
    if not interviews.exists():
        return None
    for manifest_path in interviews.glob("*/manifest.json"):
        try:
            manifest = read_json(manifest_path)
        except (OSError, ValueError):
            continue
        if manifest.get("transcript_sha256") == transcript_hash:
            return manifest_path.parent
    return None


def store_optional_document(
    source: Path | None, case_path: Path, name: str
) -> tuple[dict[str, Any] | None, str | None]:
    if source is None:
        return None, None
    source = source.expanduser().resolve()
    if not source.is_file():
        return None, f"{name} 文件不存在：{source}"

    private = case_path / "private"
    private.mkdir(parents=True, exist_ok=True)
    destination = private / f"{name}{source.suffix.lower()}"
    copy_private_file(source, destination)

    try:
        if source.suffix.lower() == ".pdf":
            from pdf_ocr import extract_pdf

            text, page_methods = extract_pdf(source)
            text = text.strip()
        else:
            text = read_supported_text(source, allow_pdf=True).strip()
            page_methods = None
    except RuntimeError as exc:
        return (
            {
                "original": str(destination.relative_to(case_path)),
                "text": None,
                "extraction_status": "failed",
            },
            f"{name} 文本提取失败：{exc}",
        )

    markdown_path = case_path / f"{name}.md"
    write_private_text(markdown_path, f"# {name.upper()}\n\n{text}\n")
    ocr_warning = (
        f"{name} 包含 OCR 页面；请核对识别文字后再用于分析"
        if page_methods and any(item["method"] != "text_layer" for item in page_methods)
        else None
    )
    return (
        {
            "original": str(destination.relative_to(case_path)),
            "text": markdown_path.name,
            "extraction_status": "ok",
            "pdf_pages": page_methods,
        },
        ocr_warning,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="导入面试文稿并生成可追溯逐字稿")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--transcript", required=True, type=Path)
    parser.add_argument("--company")
    parser.add_argument("--role")
    parser.add_argument("--round", dest="round_name")
    parser.add_argument("--date", default=str(date.today()))
    parser.add_argument("--jd", type=Path)
    parser.add_argument("--resume", type=Path)
    args = parser.parse_args()

    os.umask(0o077)
    root = args.root.expanduser().resolve()
    if not (root / "config.json").is_file():
        raise RuntimeError("数据目录尚未初始化，请先运行 init_store.py")
    require_store_compatible(root)

    source = args.transcript.expanduser().resolve()
    if not source.is_file():
        raise RuntimeError(f"文稿不存在：{source}")
    if source.suffix.lower() == ".pdf":
        from pdf_ocr import extract_pdf

        text, transcript_pdf_pages = extract_pdf(source)
    else:
        text = read_supported_text(source, allow_pdf=False)
        transcript_pdf_pages = None
    normalized = "\n".join(line.rstrip() for line in text.replace("\r\n", "\n").split("\n")).strip()
    if not normalized:
        raise RuntimeError("文稿内容为空")
    transcript_hash = sha256_text(normalized)

    duplicate = find_duplicate(root, transcript_hash)
    if duplicate is not None:
        manifest = read_json(duplicate / "manifest.json")
        print(
            json.dumps(
                {
                    "status": "duplicate",
                    "case_id": manifest["case_id"],
                    "case_path": str(duplicate),
                },
                ensure_ascii=False,
            )
        )
        return 0

    company = safe_component(args.company, "未知公司")
    role = safe_component(args.role, "未知岗位")
    round_name = round_label(args.round_name)
    base_name = f"{safe_component(args.date, str(date.today()))}-{company}-{role}-{safe_component(round_name, '轮次待确认')}"
    case_id = f"int-{args.date.replace('-', '')}-{transcript_hash[:8]}"
    case_path = root / "interviews" / base_name
    if case_path.exists():
        case_path = root / "interviews" / f"{base_name}-{transcript_hash[:6]}"
    case_path.mkdir(parents=True, exist_ok=False)
    case_path.chmod(0o700)

    private = case_path / "private"
    private.mkdir(mode=0o700)
    raw_copy = private / f"original-transcript{source.suffix.lower()}"
    copy_private_file(source, raw_copy)
    write_private_text(case_path / "source.md", f"# 原始文稿文本 · {case_id}\n\n{normalized}\n")

    segments, speakers = parse_transcript(normalized)
    if not segments:
        raise RuntimeError("无法从文稿生成任何分段")

    transcript = {
        "schema_version": SCHEMA_VERSION,
        "case_id": case_id,
        "created_at": now_iso(),
        "source_sha256": transcript_hash,
        "segments": segments,
    }
    write_json(case_path / "transcript.raw.json", transcript)
    write_private_text(case_path / "transcript.md", render_transcript(case_id, segments))

    roles = {item.get("confirmed_role") for item in speakers if item.get("confirmed_role")}
    fully_labeled = all(item.get("confirmed_role") for item in speakers)
    mapping_status = (
        "confirmed"
        if fully_labeled and {"candidate", "interviewer"}.issubset(roles)
        else "needs_confirmation"
    )
    for speaker in speakers:
        examples = [
            {
                "segment_id": segment["segment_id"],
                "text": segment["text_raw"][:160],
            }
            for segment in segments
            if segment["speaker_id"] == speaker["speaker_id"]
        ][:2]
        speaker["examples"] = examples
    write_json(
        case_path / "speaker-map.json",
        {
            "schema_version": SCHEMA_VERSION,
            "case_id": case_id,
            "status": mapping_status,
            "speakers": speakers,
        },
    )

    warnings: list[str] = []
    jd_info, jd_warning = store_optional_document(args.jd, case_path, "jd")
    resume_info, resume_warning = store_optional_document(args.resume, case_path, "resume")
    warnings.extend(item for item in (jd_warning, resume_warning) if item)

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "case_id": case_id,
        "created_at": now_iso(),
        "date": args.date,
        "company": args.company,
        "role": args.role,
        "round": round_name,
        "result": "无结果",
        "hr_feedback": None,
        "transcript_sha256": transcript_hash,
        "inputs": {
            "transcript_original": str(raw_copy.relative_to(case_path)),
            "transcript_text": "source.md",
            "transcript_pdf_pages": transcript_pdf_pages,
            "jd": jd_info,
            "resume": resume_info,
        },
        "speaker_status": mapping_status,
        "analysis_status": "not_started",
        "warnings": warnings,
    }
    write_json(case_path / "manifest.json", manifest)

    print(
        json.dumps(
            {
                "status": "created",
                "case_id": case_id,
                "case_path": str(case_path),
                "segments": len(segments),
                "speakers": len(speakers),
                "speaker_status": mapping_status,
                "warnings": warnings,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
