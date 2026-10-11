from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from analysis_state import current_errors, fingerprint, refresh_errors
from common import SchemaCompatibilityError, now_iso, read_json, read_supported_text, sha256_file, sha256_text, write_json


QUESTION_DIMS = {"correctness", "completeness", "structure", "clarity"}
OVERALL_DIMS = {"professional", "credibility", "structure", "stability"}
KNOWLEDGE_DIMS = {"accuracy", "completeness", "retrieval_stability", "expression"}
VERDICTS = {"correct", "partial", "incorrect", "unverified"}
CHANGE_TYPES = {
    "answer_version",
    "knowledge_state",
    "interview_pattern",
    "algorithm_weakness",
    "resume_risk",
    "outcome",
}
STATUSES = {"pending", "confirmed", "rejected"}
COVERAGE_VALUES = {"none", "partial", "full"}


def load_required(path: Path, errors: list[str]) -> Any | None:
    if not path.is_file():
        errors.append(f"缺少文件：{path.name}")
        return None
    try:
        return read_json(path)
    except SchemaCompatibilityError as exc:
        errors.append(str(exc))
        return None
    except (OSError, ValueError) as exc:
        errors.append(f"{path.name} 不是有效 JSON：{exc}")
        return None


def valid_score(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 5


def valid_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def validate_case_file(
    case_path: Path,
    relative: Any,
    label: str,
    errors: list[str],
    *,
    require_private: bool = False,
) -> Path | None:
    if not isinstance(relative, str) or not relative.strip():
        errors.append(f"{label} 路径为空")
        return None
    candidate = Path(relative)
    if candidate.is_absolute():
        errors.append(f"{label} 必须使用 case 内相对路径")
        return None
    if require_private and (not candidate.parts or candidate.parts[0] != "private"):
        errors.append(f"{label} 必须保存在 case/private/ 中")
        return None
    resolved = (case_path / candidate).resolve()
    try:
        resolved.relative_to(case_path)
    except ValueError:
        errors.append(f"{label} 路径越出 case 目录")
        return None
    if not resolved.is_file():
        errors.append(f"{label} 文件不存在：{relative}")
        return None
    return resolved


def validate_reference_record(
    case_path: Path,
    record: Any,
    label: str,
    errors: list[str],
    *,
    strict_metadata: bool = True,
) -> None:
    if not isinstance(record, dict):
        errors.append(f"{label} 必须为对象")
        return
    validate_case_file(
        case_path,
        record.get("original"),
        f"{label} 文件",
        errors,
        require_private=True,
    )
    digest = record.get("sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        errors.append(f"{label} sha256 无效")
    if record.get("timestamp_coverage") not in COVERAGE_VALUES:
        errors.append(f"{label} timestamp_coverage 无效")
    if record.get("speaker_labels") not in COVERAGE_VALUES:
        errors.append(f"{label} speaker_labels 无效")
    if strict_metadata:
        for field in ("registered_at", "source_label", "use"):
            if not str(record.get(field, "")).strip():
                errors.append(f"{label} 缺少 {field}")


def validate_supplemental_audio_provenance(
    case_path: Path,
    manifest: dict[str, Any],
    raw: dict[str, Any],
    audio: dict[str, Any],
    transcription: dict[str, Any] | None,
    migration: Any,
    errors: list[str],
) -> None:
    """Validate text-first cases whose audio was added only as later evidence."""
    if not isinstance(migration, dict) or migration.get("kind") != "text_first_audio_supplement_v1":
        errors.append("补充音频缺少有效的旧格式迁移记录")
        return
    raw_digest = sha256_file(case_path / "transcript.raw.json")
    if migration.get("raw_file_sha256") != raw_digest:
        errors.append("旧格式迁移记录与不可变原始逐字稿哈希不一致")
    original = validate_case_file(
        case_path,
        manifest.get("inputs", {}).get("transcript_original"),
        "原始文字稿",
        errors,
        require_private=True,
    )
    if original is not None:
        if migration.get("original_file_sha256") != sha256_file(original):
            errors.append("旧格式迁移记录的原始文字稿文件哈希不一致")
        try:
            text = read_supported_text(original, allow_pdf=False)
            normalized = "\n".join(
                line.rstrip() for line in text.replace("\r\n", "\n").split("\n")
            ).strip()
            digest = sha256_text(normalized)
        except (OSError, RuntimeError, UnicodeError) as exc:
            errors.append(f"无法核验原始文字稿：{exc}")
        else:
            if digest != raw.get("source_sha256") or digest != manifest.get("transcript_sha256"):
                errors.append("原始文字稿与逐字稿 source_sha256 不一致")
    audio_path = validate_case_file(
        case_path, audio.get("original"), "原始音频", errors, require_private=True
    )
    if audio_path is not None and sha256_file(audio_path) != audio.get("sha256"):
        errors.append("原始音频实际哈希与 manifest 不一致")
    if raw.get("source_kind") not in (None, "imported_text"):
        errors.append("补充音频场次的原始逐字稿来源必须是文字稿")
    if raw.get("audio_sha256") not in (None, audio.get("sha256")):
        errors.append("补充音频场次的原始逐字稿音频哈希冲突")
    if not isinstance(transcription, dict):
        return
    outputs = transcription.get("raw_outputs")
    output_hashes = migration.get("raw_output_sha256")
    if not isinstance(outputs, list) or not isinstance(output_hashes, dict):
        errors.append("旧格式迁移记录缺少原始转写输出哈希")
        return
    if set(outputs) != set(output_hashes) or transcription.get("primary_raw") not in outputs:
        errors.append("旧格式迁移记录与原始转写输出清单不一致")
    for relative in outputs:
        output = validate_case_file(
            case_path, relative, "原始转写输出", errors, require_private=True
        )
        if output is not None and sha256_file(output) != output_hashes.get(relative):
            errors.append(f"原始转写输出哈希不一致：{relative}")
    supporting = migration.get("supporting_artifact_sha256", {})
    if not isinstance(supporting, dict):
        errors.append("旧格式迁移记录的辅助证据哈希无效")
    else:
        for relative, digest in supporting.items():
            artifact = validate_case_file(
                case_path, relative, "辅助声学证据", errors, require_private=True
            )
            if artifact is not None and sha256_file(artifact) != digest:
                errors.append(f"辅助声学证据哈希不一致：{relative}")


def main() -> int:
    parser = argparse.ArgumentParser(description="校验面试场次产物")
    parser.add_argument("--case", required=True, type=Path)
    parser.add_argument(
        "--stage", choices=("ingest", "analysis", "presentation"), default="analysis"
    )
    parser.add_argument(
        "--refresh-analysis",
        action="store_true",
        help="在重新生成全部分析产物后，将通过校验的场次标记为 current",
    )
    args = parser.parse_args()

    case_path = args.case.expanduser().resolve()
    errors: list[str] = []
    if args.refresh_analysis and args.stage != "analysis":
        errors.append("--refresh-analysis 只能用于 analysis 阶段")
    manifest = load_required(case_path / "manifest.json", errors)
    raw = load_required(case_path / "transcript.raw.json", errors)
    speaker_map = load_required(case_path / "speaker-map.json", errors)
    if not (case_path / "transcript.md").is_file():
        errors.append("缺少文件：transcript.md")

    if not all((manifest, raw, speaker_map)):
        print(json.dumps({"status": "invalid", "errors": errors}, ensure_ascii=False))
        return 1

    case_id = manifest.get("case_id")
    if raw.get("case_id") != case_id or speaker_map.get("case_id") != case_id:
        errors.append("manifest、逐字稿和说话人映射的 case_id 不一致")

    segments = raw.get("segments")
    if not isinstance(segments, list) or not segments:
        errors.append("transcript.raw.json 必须包含非空 segments")
        segments = []
    segment_ids = [item.get("segment_id") for item in segments if isinstance(item, dict)]
    if len(segment_ids) != len(set(segment_ids)):
        errors.append("segment_id 存在重复")
    if any(not item for item in segment_ids):
        errors.append("segment_id 不能为空")
    segment_id_set = set(segment_ids)

    audio = manifest.get("inputs", {}).get("audio")
    supplemental_audio = isinstance(audio, dict) and audio.get("role") == "supplemental_reference"
    migration = manifest.get("legacy_audio_migration") if supplemental_audio else None
    timeline_exceptions = migration.get("timeline_exceptions", []) if isinstance(migration, dict) else []
    if not isinstance(timeline_exceptions, list):
        errors.append("旧格式时间戳例外必须是数组")
        timeline_exceptions = []
    used_exceptions: set[int] = set()

    previous_start: float | None = None
    previous_id: str | None = None
    for segment in segments:
        start = segment.get("start")
        end = segment.get("end")
        if start is not None and (
            not isinstance(start, (int, float))
            or isinstance(start, bool)
            or start < 0
        ):
            errors.append(f"{segment.get('segment_id')} 的 start 必须为非负秒数或 null")
        if end is not None and (
            not isinstance(end, (int, float)) or isinstance(end, bool) or end < 0
        ):
            errors.append(f"{segment.get('segment_id')} 的 end 必须为非负秒数或 null")
        if isinstance(start, (int, float)) and not isinstance(start, bool):
            if isinstance(end, (int, float)) and not isinstance(end, bool) and end < start:
                errors.append(f"{segment.get('segment_id')} 的 end 早于 start")
            if previous_start is not None and start < previous_start:
                matched = [
                    index for index, item in enumerate(timeline_exceptions)
                    if isinstance(item, dict)
                    and item.get("previous_segment_id") == previous_id
                    and item.get("segment_id") == segment.get("segment_id")
                    and item.get("previous_start") == previous_start
                    and item.get("start") == start
                    and str(item.get("reason", "")).strip()
                ]
                if supplemental_audio and len(matched) == 1:
                    used_exceptions.add(matched[0])
                else:
                    errors.append("逐字稿片段时间线不是单调递增")
            previous_start = start
            previous_id = segment.get("segment_id")
    if len(used_exceptions) != len(timeline_exceptions):
        errors.append("旧格式时间戳例外与原始逐字稿不匹配或重复")

    if audio is not None:
        if not isinstance(audio, dict):
            errors.append("manifest.inputs.audio 必须为对象")
        else:
            validate_case_file(
                case_path,
                audio.get("original"),
                "原始音频",
                errors,
                require_private=True,
            )
            audio_hash = audio.get("sha256")
            if not isinstance(audio_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", audio_hash):
                errors.append("原始音频 sha256 无效")
            if audio.get("processing") != "local_only":
                errors.append("音频 processing 必须为 local_only")
            if audio.get("role") not in (None, "supplemental_reference"):
                errors.append("音频 role 无效")
            duration = audio.get("duration_seconds")
            if duration is not None and not (
                isinstance(duration, (int, float))
                and not isinstance(duration, bool)
                and duration >= 0
            ):
                errors.append("音频 duration_seconds 必须为非负秒数或 null")

        transcription = manifest.get("transcription")
        if not isinstance(transcription, dict):
            errors.append("音频 case 缺少 transcription 元数据")
        else:
            for field in ("engine", "model", "language", "primary_raw"):
                if not str(transcription.get(field, "")).strip():
                    errors.append(f"transcription 缺少 {field}")
            validate_case_file(
                case_path,
                transcription.get("primary_raw"),
                "主要转写原始文件",
                errors,
                require_private=True,
            )
            raw_outputs = transcription.get("raw_outputs")
            if not isinstance(raw_outputs, list) or not raw_outputs:
                errors.append("transcription.raw_outputs 必须是非空数组")
            else:
                for index, relative in enumerate(raw_outputs, start=1):
                    validate_case_file(
                        case_path,
                        relative,
                        f"转写原始文件 {index}",
                        errors,
                        require_private=True,
                    )
            prompt = transcription.get("prompt")
            if prompt is not None:
                if not isinstance(prompt, dict):
                    errors.append("transcription.prompt 必须为对象或 null")
                else:
                    validate_case_file(
                        case_path,
                        prompt.get("path"),
                        "转写提示词文件",
                        errors,
                        require_private=True,
                    )
                    prompt_hash = prompt.get("sha256")
                    if not isinstance(prompt_hash, str) or not re.fullmatch(
                        r"[0-9a-f]{64}", prompt_hash
                    ):
                        errors.append("转写提示词 sha256 无效")
            method = transcription.get("speaker_method")
            if method not in {"none", "source_labels", "diarization", "channel_split"}:
                errors.append("transcription.speaker_method 无效")
            confidence = transcription.get("speaker_confidence")
            if confidence is not None and (
                not isinstance(confidence, (int, float))
                or isinstance(confidence, bool)
                or not 0 <= confidence <= 1
            ):
                errors.append("说话人方法置信度必须为 null 或 0–1 数值")

        if supplemental_audio and isinstance(audio, dict):
            validate_supplemental_audio_provenance(
                case_path, manifest, raw, audio, transcription, migration, errors
            )
        else:
            if raw.get("source_kind") != "local_audio_transcription":
                errors.append("音频 case 的 transcript.raw.json 缺少正确 source_kind")
            if isinstance(audio, dict) and raw.get("audio_sha256") != audio.get("sha256"):
                errors.append("逐字稿记录的 audio_sha256 与 manifest 不一致")
            if isinstance(transcription, dict) and speaker_map.get(
                "method"
            ) != transcription.get("speaker_method"):
                errors.append("speaker-map 与 transcription 的说话人方法不一致")

    inputs = manifest.get("inputs", {})
    if isinstance(inputs, dict):
        references = inputs.get("reference_transcripts")
        legacy_reference = inputs.get("reference_transcript")
        if references is not None:
            if not isinstance(references, list) or not references:
                errors.append("inputs.reference_transcripts 必须为非空数组")
            else:
                seen_hashes: set[str] = set()
                for index, record in enumerate(references, start=1):
                    validate_reference_record(
                        case_path, record, f"参考稿 {index}", errors
                    )
                    if isinstance(record, dict):
                        digest = record.get("sha256")
                        if digest in seen_hashes:
                            errors.append(f"参考稿 sha256 重复：{digest}")
                        seen_hashes.add(digest)
                if isinstance(legacy_reference, dict) and isinstance(references[-1], dict):
                    if legacy_reference.get("sha256") != references[-1].get("sha256"):
                        errors.append("legacy reference_transcript 未指向最新参考稿")
        elif legacy_reference is not None:
            validate_reference_record(
                case_path,
                legacy_reference,
                "参考稿",
                errors,
                strict_metadata=False,
            )

    speaker_ids = {
        item.get("speaker_id")
        for item in speaker_map.get("speakers", [])
        if isinstance(item, dict)
    }
    for segment in segments:
        if segment.get("speaker_id") not in speaker_ids:
            errors.append(f"{segment.get('segment_id')} 引用了未知 speaker_id")
        if not str(segment.get("text_raw", "")).strip():
            errors.append(f"{segment.get('segment_id')} 的 text_raw 为空")

    if speaker_map.get("status") == "confirmed":
        roles = {item.get("confirmed_role") for item in speaker_map.get("speakers", [])}
        if not {"candidate", "interviewer"}.issubset(roles):
            errors.append("已确认的说话人映射必须同时包含 candidate 和 interviewer")

    if args.stage == "ingest":
        status = "valid" if not errors else "invalid"
        print(json.dumps({"status": status, "errors": errors}, ensure_ascii=False))
        return 0 if not errors else 1

    if manifest.get("analysis_status") == "stale" and not args.refresh_analysis:
        errors.append(
            "分析产物已过期：请基于最新 transcript.revised.json 重算 qa-tree、claims、scores、profile-patch 和 review"
        )
    if args.refresh_analysis:
        errors.extend(refresh_errors(case_path, manifest))
    elif manifest.get("analysis_status") == "current":
        errors.extend(current_errors(case_path, manifest))

    qa = load_required(case_path / "qa-tree.json", errors)
    claims = load_required(case_path / "claims.json", errors)
    scores = load_required(case_path / "scores.json", errors)
    patch = load_required(case_path / "profile-patch.json", errors)
    review_path = case_path / "review.md"
    if not review_path.is_file() or not review_path.read_text(encoding="utf-8").strip():
        errors.append("review.md 缺失或为空")

    if not all((qa, claims, scores, patch)):
        print(json.dumps({"status": "invalid", "errors": errors}, ensure_ascii=False))
        return 1
    for name, payload in (
        ("qa-tree.json", qa),
        ("claims.json", claims),
        ("scores.json", scores),
        ("profile-patch.json", patch),
    ):
        if payload.get("case_id") != case_id:
            errors.append(f"{name} 的 case_id 不一致")

    questions = qa.get("questions", [])
    question_ids = [item.get("question_id") for item in questions]
    if len(question_ids) != len(set(question_ids)):
        errors.append("question_id 存在重复")
    question_id_set = set(question_ids)
    for question in questions:
        qid = question.get("question_id", "未知问题")
        if question.get("kind") not in {"main", "follow_up"}:
            errors.append(f"{qid} 的 kind 无效")
        parent = question.get("parent_id")
        if question.get("kind") == "follow_up" and parent not in question_id_set:
            errors.append(f"{qid} 的 parent_id 无效")
        confidence = question.get("relation_confidence")
        if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            errors.append(f"{qid} 的 relation_confidence 必须为 0–1")
        for field in ("question_evidence", "answer_evidence"):
            for segment_id in question.get(field, []):
                if segment_id not in segment_id_set:
                    errors.append(f"{qid} 的 {field} 引用了未知片段 {segment_id}")

    claim_ids: set[str] = set()
    for claim in claims.get("claims", []):
        cid = claim.get("claim_id", "未知 claim")
        if cid in claim_ids:
            errors.append(f"claim_id 重复：{cid}")
        claim_ids.add(cid)
        if claim.get("question_id") not in question_id_set:
            errors.append(f"{cid} 引用了未知 question_id")
        verdict = claim.get("verdict")
        if verdict not in VERDICTS:
            errors.append(f"{cid} 的 verdict 无效")
        for segment_id in claim.get("evidence_segments", []):
            if segment_id not in segment_id_set:
                errors.append(f"{cid} 引用了未知片段 {segment_id}")
        sources = claim.get("sources", [])
        if verdict != "unverified" and not sources:
            errors.append(f"{cid} 的 {verdict} 判定缺少来源")
        for source in sources:
            for field in ("title", "section", "accessed_at", "relation", "summary"):
                if not str(source.get(field, "")).strip():
                    errors.append(f"{cid} 的来源缺少 {field}")
            if not valid_url(source.get("url")):
                errors.append(f"{cid} 的来源 URL 无效")

    scored_questions: set[str] = set()
    for score in scores.get("questions", []):
        qid = score.get("question_id")
        scored_questions.add(qid)
        if qid not in question_id_set:
            errors.append(f"评分引用未知问题：{qid}")
        dimensions = score.get("dimensions", {})
        if set(dimensions) != QUESTION_DIMS:
            errors.append(f"{qid} 的评分维度不完整")
            continue
        if not all(valid_score(value) for value in dimensions.values()):
            errors.append(f"{qid} 的评分必须为 0–5")
        total = score.get("total")
        if total != sum(dimensions.values()):
            errors.append(f"{qid} 的 total 与四维分数之和不一致")
        for segment_id in score.get("evidence", []):
            if segment_id not in segment_id_set:
                errors.append(f"{qid} 的评分引用未知片段 {segment_id}")
    if scored_questions != question_id_set:
        errors.append("每个问题都必须且只能有一条评分记录")

    overall = scores.get("overall", {})
    if set(overall) != OVERALL_DIMS or not all(valid_score(value) for value in overall.values()):
        errors.append("整场评分必须包含四个 0–5 维度")

    change_ids: set[str] = set()
    for change in patch.get("changes", []):
        change_id = change.get("change_id", "未知变更")
        if change_id in change_ids:
            errors.append(f"change_id 重复：{change_id}")
        change_ids.add(change_id)
        if change.get("type") not in CHANGE_TYPES:
            errors.append(f"{change_id} 的 type 无效")
        if change.get("status") not in STATUSES:
            errors.append(f"{change_id} 的 status 无效")
        for segment_id in change.get("evidence", []):
            if segment_id not in segment_id_set:
                errors.append(f"{change_id} 引用了未知片段 {segment_id}")
        if change.get("type") == "knowledge_state":
            value = change.get("value", {})
            if value.get("grade") not in {"A", "B", "C", "D", "E"}:
                errors.append(f"{change_id} 的知识等级无效")
            knowledge_scores = value.get("scores", {})
            if set(knowledge_scores) != KNOWLEDGE_DIMS or not all(
                valid_score(item) for item in knowledge_scores.values()
            ):
                errors.append(f"{change_id} 的知识四维评分无效")

    if (case_path / "jd.md").is_file():
        matrix = load_required(case_path / "jd-matrix.json", errors)
        if matrix:
            for item in matrix.get("requirements", []):
                if item.get("coverage") not in {"not_asked", "shallow", "deep"}:
                    errors.append("JD 矩阵 coverage 无效")
                if item.get("performance") not in {"unverified", "strong", "medium", "weak"}:
                    errors.append("JD 矩阵 performance 无效")
                if item.get("next_priority") not in {"high", "medium", "low"}:
                    errors.append("JD 矩阵 next_priority 无效")
                for qid in item.get("evidence_questions", []):
                    if qid not in question_id_set:
                        errors.append(f"JD 矩阵引用未知问题：{qid}")

    if args.stage == "presentation":
        case_html = case_path / "interview-review.html"
        history_html = case_path.parent.parent / "interview-history.html"
        question_bank_html = case_path.parent.parent / "interview-question-bank.html"
        if not case_html.is_file() or not case_html.read_text(encoding="utf-8").strip():
            errors.append("interview-review.html 缺失或为空")
        else:
            document = case_html.read_text(encoding="utf-8")
            for marker in ("data-interview-case", 'id="jd"', 'id="jd-matrix"', 'id="history"', str(case_id)):
                if marker not in document:
                    errors.append(f"interview-review.html 缺少标记：{marker}")
        if not history_html.is_file() or not history_html.read_text(encoding="utf-8").strip():
            errors.append("根目录 interview-history.html 缺失或为空")
        else:
            document = history_html.read_text(encoding="utf-8")
            for marker in ("data-interview-history", 'id="history"'):
                if marker not in document:
                    errors.append(f"interview-history.html 缺少标记：{marker}")
        if not question_bank_html.is_file() or not question_bank_html.read_text(encoding="utf-8").strip():
            errors.append("根目录 interview-question-bank.html 缺失或为空")
        else:
            document = question_bank_html.read_text(encoding="utf-8")
            for marker in ("data-interview-question-bank", 'id="question-bank"', "我的现场回答", "推荐答案"):
                if marker not in document:
                    errors.append(f"interview-question-bank.html 缺少标记：{marker}")

    status = "valid" if not errors else "invalid"
    if status == "valid" and args.stage in {"analysis", "presentation"}:
        manifest["analysis_status"] = "current"
        manifest["analysis_fingerprint"] = fingerprint(case_path, manifest)
        manifest.pop("analysis_invalidated_at_ns", None)
        manifest.pop("analysis_stale_outputs", None)
        manifest["updated_at"] = now_iso()
        write_json(case_path / "manifest.json", manifest)
    print(
        json.dumps(
            {
                "status": status,
                "errors": errors,
                "analysis_status": manifest.get("analysis_status"),
            },
            ensure_ascii=False,
        )
    )
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
