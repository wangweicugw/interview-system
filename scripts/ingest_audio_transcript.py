#!/usr/bin/env python3
"""Import local audio transcription artifacts without running a speech engine."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
import wave
from datetime import date
from pathlib import Path
from typing import Any

from check_data_compatibility import require_store_compatible
from common import (
    SCHEMA_VERSION,
    copy_private_file,
    format_timecode,
    infer_role,
    now_iso,
    parse_transcript,
    render_transcript,
    safe_component,
    sha256_file,
    sha256_text,
    write_json,
    write_private_text,
)
from ingest_transcript import store_optional_document
from labels_zh import round_label


SUPPORTED_TRANSCRIPTS = {".json", ".srt", ".vtt", ".txt", ".md", ".markdown"}
SUBTITLE_TIMING_RE = re.compile(
    r"(?P<start>\d{1,2}:\d{2}(?::\d{2})?[,.]\d+)\s+-->\s+"
    r"(?P<end>\d{1,2}:\d{2}(?::\d{2})?[,.]\d+)"
)
VOICE_TAG_RE = re.compile(r"^<v(?:\.[^ >]+)*\s+([^>]+)>(.*)$", re.IGNORECASE)
KNOWN_LABEL_RE = re.compile(r"^([^:：]{1,30})\s*[:：]\s*(.+)$")


def as_seconds(value: Any, field: str) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field} 不能是布尔值")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} 不是有效秒数：{value!r}") from exc
    if result < 0:
        raise ValueError(f"{field} 不能小于 0")
    return result


def subtitle_seconds(value: str) -> float:
    normalized = value.replace(",", ".")
    parts = normalized.split(":")
    if len(parts) == 2:
        minutes, seconds = parts
        return int(minutes) * 60 + float(seconds)
    if len(parts) == 3:
        hours, minutes, seconds = parts
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    raise ValueError(f"无效字幕时间：{value}")


def explicit_label(text: str) -> tuple[str | None, str]:
    voice = VOICE_TAG_RE.match(text.strip())
    if voice:
        return voice.group(1).strip(), voice.group(2).strip()
    match = KNOWN_LABEL_RE.match(text.strip())
    if match:
        label = match.group(1).strip()
        role, _ = infer_role(label)
        if role != "unknown":
            return label, match.group(2).strip()
    return None, text.strip()


def split_word_speakers(item: dict[str, Any], index: int) -> list[dict[str, Any]] | None:
    """Keep WhisperX word-level speaker turns instead of one dominant segment label."""
    words = item.get("words")
    if not isinstance(words, list):
        return None
    labels = {
        str(word["speaker"]).strip()
        for word in words
        if isinstance(word, dict) and word.get("speaker")
    }
    if len(labels) < 2:
        return None

    groups: list[dict[str, Any]] = []
    leading = ""
    for word_index, word in enumerate(words, start=1):
        if not isinstance(word, dict):
            continue
        text = str(word.get("word") or word.get("text") or "")
        if not text:
            continue
        start = as_seconds(word.get("start"), f"第 {index} 段第 {word_index} 词 start")
        end = as_seconds(word.get("end"), f"第 {index} 段第 {word_index} 词 end")
        if end is not None and start is None:
            raise ValueError(f"第 {index} 段第 {word_index} 词有 end 但没有 start")
        speaker = str(word.get("speaker") or "").strip() or None
        if groups and all(
            char.isspace() or unicodedata.category(char).startswith("P") for char in text
        ):
            groups[-1]["text"] += text
            if end is not None:
                groups[-1]["end"] = end
            continue
        if start is None and speaker is None:
            if groups:
                groups[-1]["text"] += text
            else:
                leading += text
            continue
        if not groups or groups[-1]["speaker_label"] != speaker:
            groups.append({
                "text": leading + text,
                "start": start,
                "end": end,
                "speaker_label": speaker,
                "source_segment_id": f"json-{index}:word-{word_index}",
            })
            leading = ""
        else:
            groups[-1]["text"] += text
            if start is not None and groups[-1]["start"] is None:
                groups[-1]["start"] = start
            if end is not None:
                groups[-1]["end"] = end
    return [group for group in groups if group["text"].strip()] or None


def parse_json_artifact(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"转写 JSON 无法读取：{exc}") from exc

    if isinstance(payload, list):
        source_segments = payload
    elif isinstance(payload, dict) and isinstance(payload.get("segments"), list):
        source_segments = payload["segments"]
    elif isinstance(payload, dict) and str(payload.get("text", "")).strip():
        source_segments = [{"text": payload["text"]}]
    else:
        raise ValueError("转写 JSON 必须包含非空 segments，或至少包含 text")

    parsed: list[dict[str, Any]] = []
    for index, item in enumerate(source_segments, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"转写 JSON 第 {index} 个 segment 不是对象")
        text = str(item.get("text") or item.get("sentence") or "").strip()
        if not text:
            continue
        word_groups = split_word_speakers(item, index)
        if word_groups:
            parsed.extend(word_groups)
            continue
        start = as_seconds(item.get("start"), f"第 {index} 段 start")
        end = as_seconds(item.get("end"), f"第 {index} 段 end")
        speaker = item.get("speaker_label") or item.get("speaker") or item.get("speaker_id")
        parsed.append(
            {
                "text": text,
                "start": start,
                "end": end,
                "speaker_label": str(speaker).strip() if speaker not in (None, "") else None,
                "source_segment_id": item.get("id") or item.get("segment_id"),
                "confidence": item.get("confidence"),
            }
        )
    return parsed


def parse_subtitle_artifact(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    blocks = re.split(r"\n\s*\n", text)
    parsed: list[dict[str, Any]] = []
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        timing_index = next(
            (index for index, line in enumerate(lines) if "-->" in line), None
        )
        if timing_index is None:
            continue
        timing = SUBTITLE_TIMING_RE.search(lines[timing_index])
        if not timing:
            raise ValueError(f"无法解析字幕时间行：{lines[timing_index]}")
        body = " ".join(lines[timing_index + 1 :]).strip()
        if not body:
            continue
        label, body = explicit_label(body)
        body = re.sub(r"</?[^>]+>", "", body).strip()
        if body:
            parsed.append(
                {
                    "text": body,
                    "start": subtitle_seconds(timing.group("start")),
                    "end": subtitle_seconds(timing.group("end")),
                    "speaker_label": label,
                }
            )
    return parsed


def parse_text_artifact(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8-sig")
    segments, _ = parse_transcript(text)
    return [
        {
            "text": item.get("text_raw", ""),
            "start": item.get("start"),
            "end": item.get("end"),
            "speaker_label": item.get("speaker_label"),
        }
        for item in segments
        if str(item.get("text_raw", "")).strip()
    ]


def parse_artifact(path: Path) -> list[dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_TRANSCRIPTS:
        allowed = "、".join(sorted(SUPPORTED_TRANSCRIPTS))
        raise ValueError(f"不支持的转写格式 {suffix or '无扩展名'}；允许：{allowed}")
    if suffix == ".json":
        parsed = parse_json_artifact(path)
    elif suffix in {".srt", ".vtt"}:
        parsed = parse_subtitle_artifact(path)
    else:
        parsed = parse_text_artifact(path)
    if not parsed:
        raise ValueError("转写结果没有可导入的文本片段")
    return parsed


def validate_timeline(items: list[dict[str, Any]]) -> None:
    previous_start: float | None = None
    for index, item in enumerate(items, start=1):
        start = item.get("start")
        end = item.get("end")
        if end is not None and start is None:
            raise ValueError(f"第 {index} 段有 end 但没有 start")
        if start is not None and end is not None and end < start:
            raise ValueError(f"第 {index} 段 end 早于 start")
        if start is not None and previous_start is not None and start < previous_start:
            raise ValueError(f"第 {index} 段时间早于前一段，时间线不是单调递增")
        if start is not None:
            previous_start = start


def normalize_segments(
    items: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    validate_timeline(items)
    label_to_id: dict[str, str] = {}
    speaker_records: dict[str, dict[str, Any]] = {}
    segments: list[dict[str, Any]] = []
    has_source_labels = False

    for index, item in enumerate(items, start=1):
        label = item.get("speaker_label")
        if label:
            has_source_labels = True
            label = str(label).strip()
            if label not in label_to_id:
                label_to_id[label] = f"spk-{len(label_to_id) + 1:02d}"
            speaker_id = label_to_id[label]
            role, confidence = infer_role(label)
        else:
            label = None
            speaker_id = "unknown"
            role, confidence = "unknown", 0.0

        segment: dict[str, Any] = {
            "segment_id": f"s-{index:04d}",
            "start": item.get("start"),
            "end": item.get("end"),
            "speaker_id": speaker_id,
            "speaker_label": label,
            "role": role,
            "role_confidence": confidence,
            "text_raw": str(item["text"]).strip(),
            "text_corrected": str(item["text"]).strip(),
            "corrections": [],
        }
        if item.get("source_segment_id") not in (None, ""):
            segment["source_segment_id"] = str(item["source_segment_id"])
        if isinstance(item.get("confidence"), (int, float)) and not isinstance(
            item.get("confidence"), bool
        ):
            segment["transcription_confidence"] = item["confidence"]
        segments.append(segment)

        record = speaker_records.setdefault(
            speaker_id,
            {
                "speaker_id": speaker_id,
                "label": label or "未标注",
                "proposed_role": role,
                "confidence": confidence,
                "confirmed_role": role if confidence == 1.0 else None,
                "examples": [],
            },
        )
        if len(record["examples"]) < 2:
            record["examples"].append(
                {"segment_id": segment["segment_id"], "text": segment["text_raw"][:160]}
            )

    method = "source_labels" if has_source_labels else "not_available"
    return segments, list(speaker_records.values()), method


def probe_audio(path: Path) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "format": path.suffix.lower().lstrip(".") or "unknown",
        "duration_seconds": None,
        "sample_rate_hz": None,
        "channels": None,
        "probe_method": "unavailable",
    }
    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path), "rb") as handle:
                frames = handle.getnframes()
                rate = handle.getframerate()
                metadata.update(
                    {
                        "duration_seconds": frames / rate if rate else None,
                        "sample_rate_hz": rate or None,
                        "channels": handle.getnchannels() or None,
                        "probe_method": "python-wave",
                    }
                )
                return metadata
        except (OSError, wave.Error):
            pass

    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return metadata
    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "format=duration:stream=sample_rate,channels",
                "-of",
                "json",
                str(path),
            ],
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return metadata
    if result.returncode != 0:
        return metadata
    try:
        payload = json.loads(result.stdout)
        stream = (payload.get("streams") or [{}])[0]
        duration = (payload.get("format") or {}).get("duration")
        metadata.update(
            {
                "duration_seconds": float(duration) if duration is not None else None,
                "sample_rate_hz": int(stream["sample_rate"])
                if stream.get("sample_rate")
                else None,
                "channels": int(stream["channels"]) if stream.get("channels") else None,
                "probe_method": "ffprobe",
            }
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        pass
    return metadata


def find_duplicate(root: Path, audio_hash: str) -> Path | None:
    for manifest_path in (root / "interviews").glob("*/manifest.json"):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        recorded_audio = (manifest.get("inputs", {}).get("audio") or {}).get("sha256")
        if recorded_audio == audio_hash:
            return manifest_path.parent
    return None


def copy_raw_artifacts(
    primary: Path, extras: list[Path], private: Path
) -> tuple[str, list[str]]:
    primary_target = private / f"transcription.raw{primary.suffix.lower()}"
    copy_private_file(primary, primary_target)
    outputs = [str(primary_target.relative_to(private.parent))]
    for index, source in enumerate(extras, start=1):
        target = private / f"transcription.extra-{index:02d}{source.suffix.lower()}"
        copy_private_file(source, target)
        outputs.append(str(target.relative_to(private.parent)))
    return outputs[0], outputs


def main() -> int:
    parser = argparse.ArgumentParser(
        description="导入本地语音引擎已有转写结果，不运行模型或上传音频"
    )
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--audio", required=True, type=Path)
    parser.add_argument("--transcript", required=True, type=Path)
    parser.add_argument("--raw-output", action="append", default=[], type=Path)
    parser.add_argument("--engine", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--language", default="unknown")
    parser.add_argument("--executed-at")
    parser.add_argument("--prompt-file", type=Path)
    parser.add_argument(
        "--speaker-method",
        choices=("none", "source_labels", "diarization", "channel_split"),
        default="none",
    )
    parser.add_argument("--speaker-confidence", type=float)
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

    audio = args.audio.expanduser().resolve()
    transcript_source = args.transcript.expanduser().resolve()
    extras = [item.expanduser().resolve() for item in args.raw_output]
    prompt_file = args.prompt_file.expanduser().resolve() if args.prompt_file else None
    for path, label in [(audio, "音频"), (transcript_source, "转写结果")]:
        if not path.is_file():
            raise RuntimeError(f"{label}不存在：{path}")
    for path in extras:
        if not path.is_file():
            raise RuntimeError(f"附加原始输出不存在：{path}")
    if prompt_file and not prompt_file.is_file():
        raise RuntimeError(f"提示词文件不存在：{prompt_file}")
    if args.speaker_confidence is not None and not 0 <= args.speaker_confidence <= 1:
        raise RuntimeError("--speaker-confidence 必须为 0–1")

    parsed = parse_artifact(transcript_source)
    segments, speakers, detected_method = normalize_segments(parsed)
    canonical = json.dumps(segments, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    transcript_hash = sha256_text(canonical)
    audio_hash = sha256_file(audio)
    duplicate = find_duplicate(root, audio_hash)
    if duplicate:
        manifest = json.loads((duplicate / "manifest.json").read_text(encoding="utf-8"))
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

    audio_metadata = probe_audio(audio)
    warnings: list[str] = []
    if audio_metadata["duration_seconds"] is None:
        warnings.append("未能读取音频时长；不得从文件时间或转写进度推断面试时长")
    if any(segment["start"] is None for segment in segments):
        warnings.append("部分或全部转写片段没有时间戳；只使用 segment_id 追溯")

    roles = {item.get("confirmed_role") for item in speakers if item.get("confirmed_role")}
    fully_labeled = all(item.get("confirmed_role") for item in speakers)
    mapping_status = (
        "confirmed"
        if fully_labeled and {"candidate", "interviewer"}.issubset(roles)
        else "needs_confirmation"
    )
    if mapping_status == "needs_confirmation":
        warnings.append("说话人角色尚未可靠确认；生成正式追问树前需人工确认")

    speaker_method = args.speaker_method
    if speaker_method == "none" and detected_method == "source_labels":
        speaker_method = "source_labels"
    if speaker_method != "none" and detected_method != "source_labels":
        raise RuntimeError("指定了说话人方法，但转写结果没有任何说话人标签")
    if speaker_method in {"diarization", "channel_split"} and args.speaker_confidence is None:
        warnings.append("说话人方法未提供可校准置信度；记录为 null，角色仍需人工确认")

    company_component = safe_component(args.company, "未知公司")
    role_component = safe_component(args.role, "未知岗位")
    round_name = round_label(args.round_name)
    round_component = safe_component(round_name, "轮次待确认")
    date_component = safe_component(args.date, str(date.today()))
    base_name = f"{date_component}-{company_component}-{role_component}-{round_component}"
    case_id = f"int-{args.date.replace('-', '')}-{audio_hash[:8]}"
    case_path = root / "interviews" / base_name
    if case_path.exists():
        case_path = root / "interviews" / f"{base_name}-{audio_hash[:6]}"

    interviews = root / "interviews"
    interviews.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".audio-ingest-", dir=interviews) as temporary:
        stage = Path(temporary)
        private = stage / "private"
        private.mkdir(mode=0o700)

        audio_target = private / f"audio-original{audio.suffix.lower()}"
        copy_private_file(audio, audio_target)
        primary_raw, raw_outputs = copy_raw_artifacts(transcript_source, extras, private)
        prompt_info = None
        if prompt_file:
            prompt_target = private / f"transcription-prompt{prompt_file.suffix.lower() or '.txt'}"
            copy_private_file(prompt_file, prompt_target)
            prompt_info = {
                "path": str(prompt_target.relative_to(stage)),
                "sha256": sha256_file(prompt_target),
            }

        transcript_payload = {
            "schema_version": SCHEMA_VERSION,
            "case_id": case_id,
            "created_at": now_iso(),
            "source_kind": "local_audio_transcription",
            "source_sha256": transcript_hash,
            "audio_sha256": audio_hash,
            "segments": segments,
        }
        write_json(stage / "transcript.raw.json", transcript_payload)
        write_private_text(stage / "transcript.md", render_transcript(case_id, segments))
        source_lines = [f"# 本地转写文本视图 · {case_id}", ""]
        for segment in segments:
            timecode = format_timecode(segment.get("start"))
            label = segment.get("speaker_label") or "未标注"
            source_lines.append(f"[{timecode}] {label}：{segment['text_raw']}")
        write_private_text(stage / "source.md", "\n".join(source_lines).rstrip() + "\n")

        write_json(
            stage / "speaker-map.json",
            {
                "schema_version": SCHEMA_VERSION,
                "case_id": case_id,
                "status": mapping_status,
                "method": speaker_method,
                "method_confidence": args.speaker_confidence,
                "speakers": speakers,
            },
        )

        jd_info, jd_warning = store_optional_document(args.jd, stage, "jd")
        resume_info, resume_warning = store_optional_document(args.resume, stage, "resume")
        warnings.extend(item for item in (jd_warning, resume_warning) if item)

        imported_at = now_iso()
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "case_id": case_id,
            "created_at": imported_at,
            "date": args.date,
            "company": args.company,
            "role": args.role,
            "round": round_name,
            "result": "无结果",
            "hr_feedback": None,
            "transcript_sha256": transcript_hash,
            "inputs": {
                "transcript_original": primary_raw,
                "transcript_text": "source.md",
                "audio": {
                    "original": str(audio_target.relative_to(stage)),
                    "sha256": audio_hash,
                    "processing": "local_only",
                    **audio_metadata,
                },
                "jd": jd_info,
                "resume": resume_info,
            },
            "transcription": {
                "engine": args.engine,
                "model": args.model,
                "language": args.language,
                "executed_at": args.executed_at,
                "imported_at": imported_at,
                "primary_raw": primary_raw,
                "raw_outputs": raw_outputs,
                "prompt": prompt_info,
                "speaker_method": speaker_method,
                "speaker_confidence": args.speaker_confidence,
            },
            "speaker_status": mapping_status,
            "analysis_status": "not_started",
            "warnings": warnings,
        }
        write_json(stage / "manifest.json", manifest)
        stage.chmod(0o700)
        os.replace(stage, case_path)

    print(
        json.dumps(
            {
                "status": "created",
                "case_id": case_id,
                "case_path": str(case_path),
                "segments": len(segments),
                "speakers": len(speakers),
                "speaker_status": mapping_status,
                "audio_sha256": audio_hash,
                "warnings": warnings,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
